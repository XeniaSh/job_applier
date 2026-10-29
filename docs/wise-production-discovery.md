# Wise production discovery

Wise is the only SmartRecruiters employer enabled in the regular target-company
pipeline. Its public board is <https://careers.smartrecruiters.com/Wise>.
The completed read-only pilot is documented in `smartrecruiters-wise-pilot.md`;
its historical title-match counts are not delivery quotas.

Discovery uses the existing watcher, normalized source/ID, analysis cache,
candidate constraints, LLM analysis, SQLite delivery records and Telegram path.
The existing selection budgets remain 30 overall and 10 per company per cycle.
The SmartRecruiters watcher is bounded to 100 rows per page, 10 pages and 100
title-matching detail fetches. Bounds and fetch failures are logged.

Wise-specific verification prevents the pilot's explicit sponsorship refusals
and conditional local/visa-transfer wording from becoming general sponsorship
offers. Unknown sponsorship requires manual checking. Ordinary technical,
seniority, language and work-rights exclusions remain in force.

SmartRecruiters cards offer Applied, Skip and Open vacancy. Applications are
completed manually on the employer website; there is no SmartRecruiters Prepare
or submission integration. Other employers, providers and resume configuration
are unchanged.

## First supervised production run

1. Integrate the accepted standalone commit into `main` through the normal
   review process. Stop the old running Job Applier with Ctrl+C and run one
   instance. Keep the existing `data/jobs.db`, analysis cache and private settings.
2. In the normal macOS terminal, run:

   ```sh
   cd ~/Projects/personal/job_applier
   AUTO_SUBMIT_ENABLED=false uv run python -m app run --verbose
   ```

3. Wait for a regular Target Companies cycle. Check the
   `funnel[smartrecruiters]` line: `configured=1` means Wise alone. Review actual
   fetched/title-pass/selected/analyzed/sent counts and the SmartRecruiters bounds
   line. Counts vary with open roles, existing state and ordinary selection rules.
4. Inspect any Wise cards. Public links must point to
   `https://jobs.smartrecruiters.com/Wise/<posting-id>`. Check visible unknown or
   local/transfer eligibility caveats and manual website instructions. No Prepare
   action should be offered. Do not submit an application during verification.
5. Leave the process running for another configured cycle (default interval:
   300 seconds after cycle completion). Previously delivered source/ID/chat
   identities should not send again. Different IDs may appear as the bounded
   selection admits more jobs. Then stop and restart once with the same state to
   check durable SQLite suppression. Finish with Ctrl+C if this is only a trial.

Do not reset cache/delivery records to force cards. A provider fetch error or
bound-truncation diagnostic is evidence to investigate, rather than a reason to
increase polling or replay history.

## Verification and limits

Automated verification and supervised production acceptance are recorded
separately in `PLAN.md` and ignored `PROGRESS.md`. Local checks use isolated
SQLite/cache/configuration, mocked ATS/Telegram HTTP and a stub analyzer; no
production cache or live Telegram notifications are involved.

Existing delivery semantics are preserved: successful SQLite persistence
suppresses repeated IDs across restarts, and the in-process guard protects a
successful send whose record fails to save. A failed persistence write still
leaves the existing cross-restart duplicate risk. Use one running instance.

Existing scoring limitations also remain: the current seniority classifier can
classify a title containing `Software Engineer I` as junior, including some
titles prefixed with Senior. The separate scoring evaluation remains pending;
this integration does not relax that rule or promise every pilot backend match
will be delivered. No New Zealand vacancy was present in the historical Wise
pilot snapshot.
