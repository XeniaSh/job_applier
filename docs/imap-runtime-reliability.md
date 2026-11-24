# IMAP runtime reliability

## Problem

A regular `run` cycle collects LinkedIn email first, then Target Companies (ATS)
discovery, on one pipeline thread. `imaplib.IMAP4_SSL` was built without a
timeout, so a peer that accepts TCP but never completes the TLS handshake blocked
the cycle indefinitely (observed: >25 min, 0 bytes received on IMAP:993) while
Telegram polling kept working. An infinite block never raises, so the existing
"record the collector error and continue" handling never ran and ATS discovery
never started.

## Settings

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `LINKEDIN_EMAIL_ENABLED` | `true` | `false` skips email discovery in the regular `run` only; ATS discovery, Prepare and Telegram are unaffected. |
| `LINKEDIN_EMAIL_IMAP_TIMEOUT_SECONDS` | `30` | Socket timeout (connect/TLS/auth/read/cleanup) for the IMAP connection. |
| `LINKEDIN_EMAIL_COLLECTION_TIMEOUT_SECONDS` | `75` | Hard total deadline for one IMAP operation including DNS, TLS, reads, cleanup and IPC. |

Timeouts must be positive and finite (zero, negative, `nan` and `inf` are
rejected at startup). After the deadline the helper gets about 2s to
terminate/kill and be reaped, so the worst case is about 77s, inside 90s.

Explicit mail commands (`collect-linkedin-email`, `list-imap-folders`,
`preview-linkedin-email`, `prepare-*`) are not affected by the disable flag; they
use the same timeouts and the same supervised helper.

## Behaviour

- Real IMAP operations (`fetch_linkedin_messages*`, `list_mailboxes`) run in one
  short-lived `python -m app.collectors.imap_helper` process. Config, credentials
  and results travel only over its private stdin/stdout as JSON; nothing is in
  argv, files or logs and stderr is discarded. The helper imports only the IMAP
  client types, never the CLI, storage, profiles or `.env`.
- The parent uses `Popen.communicate(timeout=remaining)`. On timeout, error,
  non-zero exit, malformed output or `KeyboardInterrupt` the helper is
  terminated, killed if needed, and reaped. A helper that cannot be reaped blocks
  the next attempt until it exits.
- There are no in-cycle retries. The next normal cycle retries.
- On any failure no partial batch is returned, so the IMAP checkpoint and dedup
  state are untouched. The next successful sync resumes normally.
- Errors are normalized to `EmailConnectionError` / `EmailAuthenticationError`
  with fixed messages. Raw server text, mail content and credentials are never
  logged. After a network failure the client drops the socket locally instead of
  running protocol close/logout.
- Injected adapters (tests) take the direct in-process path.

## Progress logs

Each source logs, before the blocking call, `Collect start: source=<name>`, then
`Collect done: source=<name> vacancies=N elapsed_ms=M` or
`Collect failed: source=<name> error_type=<ExceptionClass> elapsed_ms=M`.
Sources are `linkedin-email`, `greenhouse` and the four ATS stages
`ats:greenhouse|lever|ashby|smartrecruiters`. No titles, subjects, bodies,
usernames, chat ids or token URLs are logged.

## Immediate workaround (before this patch)

Locally verified: pointing at a closed loopback port (`127.0.0.1:0`) fails in
about 2 ms, so a bad IMAP host/port fails fast instead of hanging. Setting
`LINKEDIN_EMAIL_IMAP_HOST=127.0.0.1` and `LINKEDIN_EMAIL_IMAP_PORT=0` for the
restart works as a stop-gap on the old code.

## Supervised restart (manual; no automated restart)

After cherry-picking the supplied commit into main:

1. Stop the old instance with `Ctrl+C` in its terminal and wait for shutdown.
2. Preserve the private `.env`, including
   `RESUME_PROFILES_PATH=resume_profiles.local.yaml`, the local YAML, all resume
   PDFs, `data/jobs.db` and the existing analysis cache. Do not reset or replace
   them. Remove only temporary loopback host/port overrides if used, restoring
   the original IMAP endpoint; inline overrides from an earlier command do not
   persist to the next command.
3. Start with email discovery disabled to verify ATS progress:

   ```bash
   cd ~/Projects/personal/job_applier
   LINKEDIN_EMAIL_ENABLED=false AUTO_SUBMIT_ENABLED=false uv run python -m app run --verbose
   ```

   Expect `linkedin-email: disabled` and `Collect start/done/failed` for the
   configured `ats:greenhouse`, `ats:lever`, `ats:ashby` and `ats:smartrecruiters`
   stages. Existing caches and delivery records prevent duplicate deliveries.
4. Once ATS progress is confirmed, stop with `Ctrl+C` and re-enable email:

   ```bash
   LINKEDIN_EMAIL_ENABLED=true AUTO_SUBMIT_ENABLED=false uv run python -m app run --verbose
   ```

   Defaults are a 30-second socket timeout and a 75-second collection deadline,
   plus at most about 2 seconds for helper termination/reaping. An IMAP failure
   must be followed by ATS collection rather than indefinite silence. If the
   mail endpoint remains unavailable, keep email discovery disabled.

## Lifecycle protection

- Each in-flight helper is registered while its attempt runs. On normal
  application shutdown (the `run` shutdown path, before the pipeline thread is
  abandoned) and at interpreter `atexit`, owned helpers are terminated/killed and
  reaped; the owning attempt then raises a fixed "IMAP collection cancelled."
  error. This is per-attempt, not a persistent stop flag, so a re-run in the same
  process starts normally.
- The helper also arms its own daemon `threading.Timer` for the remaining budget
  (covering connect through response IPC); on expiry it exits the whole helper
  process with code 124, which the parent maps to the whitelisted timeout error.
  The timer lives only inside the helper and is cancelled once the response is
  flushed. So even if the parent dies abruptly (e.g. `SIGKILL`, which no hook can
  intercept), the helper cannot outlive the budget by more than its startup time.
- After the helper finishes, the parent re-checks the budget before and after
  decoding the response and never returns success (or lets a checkpoint advance)
  past the deadline.

## Other network clients (reviewed, unchanged)

Per-request bounds already exist: Greenhouse/Lever/Ashby/SmartRecruiters
connect 5s / read 20s / write 10s / pool 5s (Greenhouse 3 attempts, backoff 1s,
2s); HH the same; LLM 10/60/15/10; Telegram 5/35/10/5. Only scoped progress logs
were added there. These other HTTP clients are finite per request but there is
no whole-ATS (per-stage) deadline; that is unchanged by this work.
The installed synchronous HTTP transport uses `socket.create_connection`;
OS DNS resolution is not protected by a separate application wall-clock budget.

## Known limits

- The helper's stdout is buffered in the parent until exit (response capped at
  64 MiB); batch size bounds it in practice.
- Message bytes are re-serialized with `Message.as_bytes()` across the process
  boundary, which is faithful for normal mail but not guaranteed byte-identical.
- Per-ATS watcher calls remain synchronous and bounded only by their existing
  per-request timeouts.
- If a healthy but large email batch repeatedly exhausts the total budget,
  reduce the existing `LINKEDIN_EMAIL_BATCH_SIZE`; do not reset checkpoints or
  replay the historical backlog. Incomplete timed-out batches do not advance it.
- IMAP classification diagnostics retain UID/reason codes and counters, without
  subject previews. Message data used for parsing and matching is unchanged.
