# JobApplier — Development Progress (local template)

This file is a **public template** for local persistent agent/runtime state.

Copy it to `PROGRESS.md` in the repository root, or keep an existing local
`PROGRESS.md`. That local file is gitignored on purpose and must never be
committed.

`PROGRESS.md` may record:

- current PLAN task and last verified task;
- generic blockers and next steps;
- implementation notes that are already appropriate for a public code sample.

Do **not** copy the following into this example file, and do not commit them
in any tracked document:

- real vacancy IDs or application URLs;
- application decisions, submit history, or employer-specific smoke-test logs;
- candidate personal data, local profile values, or contact details;
- Telegram chat IDs, tokens, or other secrets.

If `PROGRESS.md` is missing, reconstruct status from `PLAN.md` and the
repository, then initialize a new local file from this template.

---

## Current overall status

Describe the current product stage in generic terms.

Example:

Stage 1 Greenhouse autofill exists as a separate CLI path. Discovery and
analysis remain in production use. Later stages stay blocked until explicitly
requested.

---

## Task currently being worked on

NONE — or `TASK-NNN` short title.

## Last successfully verified task

`TASK-NNN` — short title and how it was verified (unit tests, fixtures).
Do not record live vacancy identifiers here in the public template.

---

## Completed this run

Brief notes about the last implementation iteration.

Keep them portfolio-safe. Prefer task IDs, module names, and test commands
over operational details.

OQ defaults from `PLAN.md` (until the user overrides them):

- OQ-001: live Greenhouse board refetch
- OQ-002: `application_url` = job `absolute_url` / `NormalizedVacancy.url`
- OQ-003: new YAML profile files; markdown/skills/constraints left in place
- OQ-004: headed browser; CLI blocks until Enter; then close
- OQ-005: profile `default_resume`
- OQ-009: missing Chromium binaries → setup error with `uv run playwright install chromium`

---

## Blocked tasks

List PLAN tasks that are waiting on an explicit user request or a genuine
blocker. Do not include live employer/vacancy identifiers.

---

## Current known issues

### Needs user action

Record only what a later agent needs to ask the user. No personal data.

### Pre-existing test failures (unrelated to current work)

Copy the known unrelated failures from `AGENTS.md` when useful.

### Product gaps still open

Example:

- Stage 2 Telegram autofill not implemented
- extra ATS watchers not wired into `run` / Telegram
- auto-submit not enabled

---

## Build / test status

| Check | Result |
|---|---|
| Focused pytest for the current change | record pass/fail and the command |
| Live ATS smoke | keep results in local `PROGRESS.md` only |

---

## Important implementation discoveries

Record architecture facts that help the next agent (module boundaries, policy
owners, what not to rewrite). Keep them generic.

---

## Blockers / open questions that need user input

| ID | Required user action |
|---|---|
| Stage 2 | Explicitly allow Telegram autofill messages/buttons |
| OQ-006 / TASK-045 | Request Workday if wanted |
| TASK-048 | Request auto-submit if wanted |

---

## Next step

Name the next eligible PLAN task, or the user action that is blocking it.
