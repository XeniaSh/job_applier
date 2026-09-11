# JobApplier — Development Progress

Last updated: 2026-09-11

This file is the live development state for autonomous work.
Update it after every implemented task.

---

## Current overall status

**Stage 1 Greenhouse autofill is implemented against local fixtures, including
Stage 1G–1K live-smoke follow-ups.**

Stage 1K fixed academic-level mapping (Diploma was incorrectly selected) and
audited seniority/recommendation gating for Agoda `7044713`.

**Lever / Ashby / SmartRecruiters Target Company watchers exist but are not wired into `run`.**

Discovery and analysis remain in production use. Autofill is a separate CLI path:

```bash
uv run python -m app autofill target_company:greenhouse:agoda 7044713
```

Live visual confirmation on a real Greenhouse form (product spec items 18–19) still needs the user.

---

## Task currently being worked on

NONE — stopped before another live Greenhouse smoke test (manual user verification required).

## Last successfully verified task

TASK-074 — SKIP vacancies stay out of autonomous application; manual autofill CLI remains
(also TASK-073 academic level; previously TASK-069..072, TASK-062..068, TASK-054..061, TASK-051..053, TASK-040, TASK-041)

---

## Completed this run

Stage 1K after Agoda `7044713` selected **Diploma** for highest academic level:

| Task | Result |
|---|---|
| TASK-073 | Substring alias `"ma"` matched inside `"Diploma"`, so Diploma won over Master's Degree. Awarded degree is now the ATS-independent token `MASTERS`. Russian specialist / master-equivalent maps to `MASTERS`. Completed postgraduate / aspirantura with `doctorate_awarded: false` does **not** become `DOCTORATE`. Greenhouse maps `MASTERS` → visible **"Master's Degree"**, waits, reads the label back, and reports filled only if it persists. |
| TASK-074 | Agoda `7044713` title classifies as `LEAD_MANAGER` and recommendation `SKIP`. Staff/Principal remain stretch `CHECK_MANUALLY`. Senior IC can be `APPLY_NOW` when otherwise eligible. The normal Target Companies send path already excluded SKIP (`allows_autonomous_application_workflow`). There is no autonomous discovery→autofill path yet (Stage 2 not implemented). Explicit CLI `autofill SOURCE EXTERNAL_ID` does not consult recommendation, so `7044713` remains a valid manual smoke-test fixture because it exercises a rich application form. |

Previously completed: Stage 0–1J (TASK-001..TASK-072), TASK-042..044, TASK-049.

OQ defaults used (until the user overrides them):

- OQ-001: live Greenhouse board refetch
- OQ-002: `application_url` = job `absolute_url` / `NormalizedVacancy.url`
- OQ-003: new YAML profile files; markdown/skills/constraints left in place
- OQ-004: headed browser; CLI blocks until Enter; then close
- OQ-005: profile `default_resume`; no `--resume` yet
- OQ-009: missing Chromium binaries → `BrowserSetupError` with `uv run playwright install chromium`

---

## Blocked tasks

### TASK-037..039 — Stage 2 Telegram → Autofill

Not started. `AGENTS.md` still forbids adding Telegram messages unless separately requested. LinkedIn Prepare is unchanged on purpose.

**Required user action:** explicitly allow Stage 2 Telegram autofill (new button/callback, not LinkedIn Prepare).

When Stage 2 is added, it must reuse `allows_autonomous_application_workflow` so SKIP vacancies (including Lead/Manager roles such as Agoda 7044713) cannot be auto-selected for autofill. Manual CLI by source+id stays unrestricted.

### TASK-045 — Workday watcher

Blocked until the user explicitly requests Workday (`OQ-006`, `AGENTS.md`).

### TASK-046 — Additional autofill adapters

Not started. Greenhouse Stage 1 adapter exists; Lever/Ashby/SmartRecruiters autofill adapters can follow one ATS at a time after Stage 2/user request.

### TASK-047..048 — Auto-submit

Forbidden in Stage 1. TASK-048 also needs an explicit request to enable submit.

### TASK-050 — Vacancy snapshot store

Not started. Live Greenhouse resolve was sufficient for Stage 1 tests. Start only if a job disappears before autofill.

---

## Current known issues

### Needs user action (Stage 1 definition of done items 18–19)

1. Update `candidate_profile.local.yaml` from `candidate_profile.example.yaml`:
   - `highest_academic_level: Master's` (or specialist / master-equivalent wording)
   - `postgraduate_studies_completed: true`
   - `doctorate_awarded: false`
   - `sms_interview_updates: false` (override any previous `true`)
   - `prefer_not_to_disclose_gender: true`
   - keep `newsletter_opt_in: false`, relocation willing, question overrides, affiliations
   - `professional_tech_stack` strongest-first, only truthful professional skills
   Agents must not read the local file.
2. Chromium: `uv run playwright install chromium`.
3. Re-run the Agoda smoke test using the command in **Next step** below.
4. Visually confirm **Master's Degree** (not Diploma, not Doctorate) and the other fields listed in `docs/autofill_smoke.md`. Do not bypass CAPTCHA/login.

### Pre-existing test failures (unrelated to this work)

Full suite except `tests/test_run_cli.py` was not re-run in this follow-up. Historical `.env` leakage documented in `AGENTS.md` may still fail:

- `tests/test_config_greenhouse.py::test_telegram_job_feed_min_match_defaults_to_strong_and_accepts_aliases`
- `tests/test_telegram_cli.py::test_target_companies_settings_error_does_not_use_linkedin_chat`
- `tests/test_telegram_destinations.py::test_legacy_chat_id_is_linkedin_fallback`

`tests/test_run_cli.py` was not run (known lock-related hang risk).

### Product gaps still open

- Stage 2 Telegram autofill not implemented
- Lever/Ashby/SmartRecruiters watchers are not in `run` / Telegram
- HH remains CLI-only (`OQ-007`)
- `candidate_profile.md` still tracked in Git (`OQ-010`)
- Artifact cleanup is 14 days for cover letters only; not a CLI command

---

## Build / test status

| Check | Result |
|---|---|
| `uv run ruff check` on changed application/autofill/recommendation/CLI/test files | Passed |
| `uv run pytest tests/application tests/company_watch/test_seniority.py tests/company_watch/test_application_recommendation.py tests/test_run_target_companies.py tests/test_llm_client.py` | 233 passed |
| Live Greenhouse smoke | Stopped for user visual verification of Agoda 7044713 academic level |

Playwright Chromium tests must run outside a restricted sandbox (`chromium_executable_available()` otherwise skips).

---

## Important implementation discoveries

1. Autofill lives under `app/application/autofill/`. Collectors and company watchers do not import it. Playwright is lazy-imported from the CLI command body.
2. Stage 1 resolve uses `DefaultVacancyResolver` → Greenhouse board API. Generic `greenhouse` / LinkedIn / HH sources fail as unsupported.
3. Demographic gender is filled from ApplicationPolicy (`prefer_not_to_disclose_gender`), not from `sensitive.gender`. Ethnicity / disability / veteran remain `SENSITIVE_OPTIONAL` unless `fill_sensitive_fields` is true.
4. Submit is not part of the adapter API. Fixture JS asserts the Submit button was not clicked.
5. Academic matching previously used substring aliases. `"ma"` (Master of Arts) is a substring of `"Diploma"`, so Diploma was selected whenever it appeared before Master's Degree. Matching is now whole-word / canonical tokens (`MASTERS`, `BACHELOR`, `DOCTORATE`, `DIPLOMA`).
6. CandidateProfile still uses `employment.highest_academic_level` as free text, plus explicit `postgraduate_studies_completed` and `doctorate_awarded`. `awarded_academic_level()` is the ATS-independent value (`MASTERS` for this candidate). Greenhouse only maps that token to a visible option.
7. Agoda `7044713` title `Lead Software Engineer - Back End (FinTech) (Bangkok based - Relocation provided)` classifies as `LEAD_MANAGER`. `config/candidate_constraints.yaml` lists `LEAD_MANAGER` under `excluded_seniority`, so recommendation is `SKIP` with reason "lead/manager role is not target IC backend role". STAFF_PLUS / Principal / Staff are stretch `CHECK_MANUALLY`. Senior IC can be `APPLY_NOW` when other signals match.
8. Years-of-experience gaps (for example 10+ required vs ~7 candidate years) cap the matcher decision toward `POTENTIAL_MATCH`; they do **not** by themselves produce recommendation SKIP. Lead/Manager SKIP is seniority, independent of experience years.
9. The normal autonomous Target Companies path sends only `APPLY_NOW` and `CHECK_MANUALLY` (`allows_autonomous_application_workflow`). SKIP is also dropped from re-analysis when the cached recommendation is unchanged. There is currently **no** autonomous call into AutofillService. Explicit CLI autofill by source+id does not read recommendation, so 7044713 remains usable as a manual form fixture.

### Agoda 7044713 fifth smoke findings (this follow-up)

Live form `target_company:greenhouse:agoda` / `7044713` selected:

- **Diploma** for "What is your highest academic level?"

Expected:

- **Master's Degree**

Candidate education facts:

- completed Russian higher education specialist degree (master-equivalent for ATS)
- completed postgraduate / aspirantura studies
- no dissertation defense
- no awarded PhD / Candidate of Sciences / doctorate

7044713 is retained as a **manual** Greenhouse smoke-test vacancy because it exercises a rich application form. It is not a suitable automatic application candidate (Lead / SKIP).

---

## Blockers / open questions that need user input

| ID | Required user action |
|---|---|
| Spec 18–19 | Run the Agoda 7044713 command below and visually confirm Master's Degree (not Diploma / Doctorate) and the other listed fields |
| Stage 2 | Explicitly allow Telegram autofill messages/buttons |
| OQ-006 / TASK-045 | Request Workday if wanted |
| TASK-048 | Request auto-submit if wanted |
| Local profile | Overlay education flags and remaining policy fields from `candidate_profile.example.yaml` (agents must not read the local file unless a task requires it) |

---

## Next step

User: run this live smoke test (do not click Submit):

```bash
uv run python -m app autofill target_company:greenhouse:agoda 7044713
```

See `docs/autofill_smoke.md` for the expected visible values and the fields that should stay unresolved.
