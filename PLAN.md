# JobApplier — Implementation Plan

This plan compares `PRODUCT_SPEC.md` with the repository as inspected on 2026-09-10.
It is the work queue for autonomous development.

Do not start from a blank architecture.
Prefer extending working code over rewriting it.
Never combine unrelated tasks into one implementation change.
Implement exactly one PLAN task at a time. A new task is a new iteration, but not necessarily a new agent run.

---

## How to use this plan

1. Read `PRODUCT_SPEC.md`, `AGENTS.md`, and this file.
2. Pick the first `todo` task whose dependencies are `done` and that is not blocked.
3. Implement only that task. Do not start a second task before this one is verified.
4. Run the verification listed on the task.
5. Mark the task done **only if verification succeeds**. If verification fails, fix it or stop with a genuine blocker; do not mark it done.
6. Update `PROGRESS.md` (current task, last verified task, status).
7. Then select the next eligible `todo` task and continue automatically.
8. Do **not** stop merely because one task is complete.

Continue until one of these is true:

- all currently allowed tasks are complete;
- a genuine blocker requires user input;
- required credentials or manual interaction are unavailable;
- continuing would require a forbidden or destructive action.

Blocked or later-stage tasks stay skipped until their dependencies and policy allow them (for example Stage 2 until Stage 1 CLI is verified, Workday until requested, auto-submit until explicitly enabled).

---

## Repository snapshot

### Stack

- Python CLI (`typer`), Python `>=3.13`
- Settings: `pydantic` / `pydantic-settings`, `.env`
- HTTP: `httpx`
- YAML: `PyYAML`
- PDF generation: `fpdf2`
- Tests: `pytest`, `respx`
- Lint: `ruff`
- Persistence: SQLite at `data/jobs.db`
- No Playwright, no browser automation, no LangChain, no extra databases

Entry point:

```bash
uv run python -m app <command>
```

Main modules:

| Area | Path | Role |
|---|---|---|
| CLI | `app/cli.py`, `app/__main__.py` | Commands and `run` loop. `cli.py` is large; new features should not be dumped into it. |
| Config | `app/config.py`, `.env.example` | Environment settings |
| Collectors | `app/collectors/` | LinkedIn email, HH, generic Greenhouse |
| Target Companies | `app/company_watch/` | Config, Greenhouse watcher, prefilter, feasibility, recommendation |
| Analysis | `app/vacancy_analyzer.py`, `app/requirement_matcher.py`, `app/title_rules.py` | LLM extract + deterministic scoring |
| Telegram | `app/telegram/` | Bot API, cards, callbacks, destinations |
| Application prep | `app/application/` | LinkedIn cover letter + resume package |
| Storage | `app/storage/` | Seen jobs, IMAP checkpoint, Telegram/history/prepare cache |
| Profiles | `candidate_profile.md`, `profiles/`, `config/candidate_constraints.yaml`, `resume_profiles.yaml` | Analysis/prep inputs, not autofill profile |

### Existing CLI commands

`review`, `collect-hh`, `collect-linkedin-email`, `collect-greenhouse`, `collect-target-companies-greenhouse`, `analyze-target-companies-greenhouse`, `send-linkedin-telegram`, `prepare-telegram-applications`, `run`, Telegram debug/cache/history commands, IMAP preview/list/reset.

There is no `autofill` command.

---

## Already exists — do not rewrite

These product capabilities already work. Schedule only additive tasks, not replacements.

### A. LinkedIn / current discovery

- IMAP collection of LinkedIn job-alert emails (`app/collectors/linkedin_email_collector.py`)
- HTML parsing with fallbacks (`linkedin_email_parser.py`)
- Title prefilter (`title_filter.py`, `title_rules.py`)
- Normalization to `NormalizedVacancy` with `source=linkedin-email`
- Dedup via `SeenJobsStorage` (`source + external_id`)
- LLM extraction + deterministic matcher (`VacancyAnalyzer`, `requirement_matcher.py`)
- Decisions: `STRONG_MATCH`, `POTENTIAL_MATCH`, `IGNORE`
- Telegram cards, Skip / Prepare / Applied / Open vacancy
- Prepare package: cover letter + resume selection + Telegram delivery
- SQLite delivery state, application history, prepare cache, resume `file_id` cache
- Background `run` loop with lock file, callback polling, prepare worker, aux-message cleanup

### B. Target Companies

- Config: `config/target_companies.yaml` + loader (`company_watch/config_loader.py`)
- Greenhouse watcher reuses shared Greenhouse HTTP helpers
- Source format: `target_company:greenhouse:<board>`
- Stable `external_id` from ATS job id / URL fallback
- Title-only include/exclude prefilter; empty include list does not filter
- `known_hiring_locations` and relocation/language metadata are not hard filters
- Per-company error isolation
- Analysis: existing vacancy analyzer + seniority + feasibility + `APPLY_NOW` / `CHECK_MANUALLY` / `SKIP`
- Analysis cache: `data/target_company_analysis_cache.json`
- Wired into `run` when `TELEGRAM__TARGET_COMPANIES_CHAT_ID` is set
- Separate Telegram destination; no fallback to LinkedIn chat
- Prepare button is intentionally hidden for Target Companies and generic Greenhouse

### C. Shared / supporting

- Shared `NormalizedVacancy` model
- Generic Greenhouse collector (`source=greenhouse`, `GREENHOUSE_BOARDS`) — separate from Target Companies
- HH collector CLI (`source=hh`) — not in `run`
- Centralized Telegram destinations (`app/telegram/destinations.py`)
- Callback source codes: `li`, `gh`, `tcg.<board>`
- Resume profiles YAML + LLM resume selector for LinkedIn prepare
- Candidate constraints YAML for Target Companies recommendations
- `.env.example`, `.gitignore` for `.env` / `data/` / resume PDFs
- `.cursorignore` for `.env`, resume binaries, SQLite, `data/`

---

## Gaps vs PRODUCT_SPEC

### Missing — Stage 1 autofill (main remaining work)

- Structured Candidate Profile YAML (`example` + `local`)
- Autofill layer (`AutofillService`, result model, field classification)
- Playwright / browser session
- Greenhouse ATS adapter (discover, fill, upload, read-back)
- Submit guard
- CLI `autofill source external_id`
- Vacancy resolver for autofill by `source + external_id`
- CAPTCHA / login handoff
- Autofill summary UX
- Autofill tests on fixtures
- Manual smoke test on a real Greenhouse form

### Incomplete — keep, do not replace

- Candidate data is split across markdown, skills YAML, and constraints YAML. Fine for analysis; insufficient for autofill.
- `PreparationService` supports only `linkedin-email`. Target Companies has no prepare/autofill path yet (Stage 2).
- `application_answers` in preparation is a no-op placeholder.
- `data/prepared/` has no retention policy.
- Many Target Companies in YAML are `custom` / `lever` / `ashby` / `smartrecruiters` / `manual`. Only Greenhouse watcher exists.
- Generic Greenhouse jobs in `run` are delivered to the LinkedIn Telegram destination. This matches “current discovery”, not Target Companies.
- HH exists as CLI-only collection.
- Root `candidate_profile.md` is tracked in Git and may contain personal search context. Do not relocate unless requested (`OQ-010`).

### Intentionally later

- Stage 2 Telegram → Autofill
- Stage 3 LLM custom questions
- Stage 4 additional ATS watchers/adapters
- Stage 5 auto-submit
- Workday (`OQ-006`, also forbidden by current `AGENTS.md`)

---

## Implementation constraints for later agents

- Do not modify LinkedIn collection, matcher, Telegram routing, or Target Companies watcher unless a task names that module.
- Put autofill under `app/application/` (or `app/application/autofill/`), not inside collectors.
- Reuse `build_greenhouse_http_client`, `fetch_greenhouse_board_jobs`, `greenhouse_job_to_normalized`.
- Do not add submit capability in Stage 1.
- Do not read `candidate_profile.local.*`, `.env`, resume PDFs, or production SQLite unless the task requires it.
- Use synthetic resume fixtures in tests.
- Do not add live Greenhouse to the ordinary pytest suite.
- Keep changes small: one task, then tests, then the next eligible task.

Suggested default for unspecified product questions, until the user overrides them:

| Open question | Default for Stage 1 |
|---|---|
| OQ-001 vacancy resolve | Live Greenhouse API refetch via existing helpers; fail if the job is gone |
| OQ-002 application URL | Use Greenhouse `absolute_url` as the page to open |
| OQ-003 profile files | Add YAML for autofill; do not replace markdown/skills/constraints |
| OQ-004 browser lifetime | Headed browser; CLI blocks until Enter/Ctrl+C; then close |
| OQ-005 resume | Candidate Profile `default_resume` path; optional `--resume` later if needed |
| OQ-009 Playwright | Fail CLI with setup instructions if browser binaries are missing |

Record any deviation in `PROGRESS.md`.

---

## Task order

```text
TASK-001 AGENTS.md Stage 1 exception
    ↓
TASK-002 ignore local profile files
    ↓
TASK-003..006 structured Candidate Profile
    ↓
TASK-007 AutofillResult models
    ↓
TASK-008..011 vacancy resolve
    ↓
TASK-012..015 mapping / classification (no browser)
    ↓
TASK-016..018 Playwright session
    ↓
TASK-019..028 Greenhouse adapter + submit guard
    ↓
TASK-029..035 AutofillService + CLI + logging + integration test
    ↓
TASK-036 manual smoke runbook
    ↓
TASK-051 website ≠ GitHub
    ↓
TASK-052 reusable custom questions
    ↓
TASK-040..041 short LLM answers (brought forward)
    ↓
TASK-053 Greenhouse cover letter Enter manually
    ↓
TASK-054..061 Stage 1H live-smoke React/policy fixes
    ↓
TASK-062..068 Stage 1I third smoke-test follow-ups
    ↓
TASK-069..072 Stage 1J fourth smoke-test follow-ups
    ↓
TASK-073..074 Stage 1K academic level + seniority gating
    ↓
TASK-037..039 Stage 2 Telegram
    ↓
(Stage 3 LLM remaining polish, if any)
    ↓
TASK-042..046 Stage 4 additional ATS
    ↓
TASK-047..048 Stage 5 auto-submit
    ↓
TASK-049 generated-artifact retention
    ↓
TASK-050 optional vacancy snapshot (only if live resolve is insufficient)
```

---

## Stage 0 — Make autofill implementable

### TASK-001 — Allow Stage 1 autofill in agent instructions

- **Status:** done
- **Depends on:** none
- **Goal:** Update `AGENTS.md` so later agents may implement Stage 1 autofill and Playwright, while auto-submit, Workday, and unrelated rewrites remain forbidden.
- **Area:** `AGENTS.md`
- **Acceptance criteria:**
  - Stage 1 Greenhouse autofill is explicitly in scope.
  - Playwright is allowed as the browser dependency.
  - Auto-submit, CAPTCHA bypass, and Workday remain forbidden unless separately requested.
  - Existing “do not wire unrelated systems” rules remain.
- **Verification:** Read the updated `AGENTS.md`. No application code change. No tests required.

### TASK-002 — Protect local candidate profile files

- **Status:** done
- **Depends on:** none
- **Goal:** Ensure `candidate_profile.local.*` cannot be committed and is ignored by coding agents.
- **Area:** `.gitignore`, `.cursorignore`
- **Acceptance criteria:**
  - `candidate_profile.local.yaml` / `candidate_profile.local.*` are gitignored.
  - `.cursorignore` includes the same pattern.
  - Existing ignores for `.env`, resume binaries, and `data/` remain.
- **Verification:** Inspect ignore files. Do not create or read a real local profile.

---

## Stage 1A — Structured Candidate Profile

### TASK-003 — CandidateProfile schema

- **Status:** done
- **Depends on:** none
- **Goal:** Add a Pydantic model for the ATS-independent structured profile.
- **Area:** new module under `app/application/` or `app/profile/` (do not put fields in the Greenhouse adapter)
- **Acceptance criteria:**
  - Covers identity, professional links, employment, work eligibility, application files, sensitive/voluntary data.
  - Sensitive fields are optional and default to unset / do-not-fill.
  - Work authorization is country-specific and explicit; location does not imply authorization.
  - Extra unknown YAML keys fail validation or are explicitly ignored in one documented way.
- **Verification:** Unit tests for valid payload, missing required identity fields, and “location set but no country authorization”.

### TASK-004 — Example profile YAML

- **Status:** done
- **Depends on:** TASK-003
- **Goal:** Add synthetic `candidate_profile.example.yaml` that is safe for Git and agents.
- **Area:** repository root (or `config/`, but document the path in code)
- **Acceptance criteria:**
  - Values are clearly fake.
  - File loads into the TASK-003 model.
  - Includes `requires_visa_sponsorship` and at least one country-specific eligibility field so mapping tests have data.
- **Verification:** Loader/schema test using the example file.

### TASK-005 — Profile loader (example + local overlay)

- **Status:** done
- **Depends on:** TASK-004
- **Goal:** Load Candidate Profile from example, overlaying `candidate_profile.local.yaml` when present.
- **Area:** profile loader module
- **Acceptance criteria:**
  - Runtime prefers local file when it exists.
  - Tests use example / tmp synthetic files only.
  - Missing example file fails with a clear error.
  - Invalid YAML fails with a clear error.
  - Loader does not log email/phone.
- **Verification:** Tests with tmp_path files: example only, local overlay, missing file, invalid YAML.

### TASK-006 — Profile policy tests

- **Status:** done
- **Depends on:** TASK-005
- **Goal:** Lock the “no silent legal inference” rule.
- **Area:** tests for profile helpers / mapping-ready profile
- **Acceptance criteria:**
  - Location/country alone does not produce a work-authorization answer.
  - `requires_visa_sponsorship` is used only when explicitly set.
  - Unset sensitive fields are not treated as answers.
- **Verification:** Focused unit tests only.

---

## Stage 1B — Autofill domain and vacancy resolve

### TASK-007 — Autofill result and field models

- **Status:** done
- **Depends on:** none
- **Goal:** Add `AutofillResult`, statuses, and field classification types.
- **Area:** `app/application/autofill/` models
- **Acceptance criteria:**
  - Statuses: `READY_FOR_REVIEW`, `NEEDS_MANUAL_INTERVENTION`, `FAILED` (names may match spec).
  - Field classes: `SUPPORTED_DETERMINISTIC`, `UNKNOWN_REQUIRED`, `UNKNOWN_OPTIONAL`, `SENSITIVE_OPTIONAL`, `UNSUPPORTED`.
  - Result includes source, external_id, application_url, filled/unresolved/sensitive/unsupported, warnings, `resume_uploaded`, `submit_performed`.
  - Stage 1 construction always sets `submit_performed=False`.
- **Verification:** Model unit tests.

### TASK-008 — VacancyResolver interface

- **Status:** done
- **Depends on:** TASK-007
- **Goal:** Define `resolve(source, external_id) -> ResolvedVacancy` without Telegram.
- **Area:** autofill vacancy lookup module
- **Acceptance criteria:**
  - Input is `source + external_id`.
  - Output includes at least title, company, url/application_url, source, external_id.
  - Unsupported source returns a typed error / `FAILED`, not a guessed vacancy.
  - No Telegram client import.
- **Verification:** Unit test for unsupported source.

### TASK-009 — Greenhouse Target Company resolver

- **Status:** done
- **Depends on:** TASK-008
- **Goal:** Resolve `target_company:greenhouse:<board>` + job id via existing Greenhouse helpers.
- **Area:** autofill resolver + `app/collectors/greenhouse_collector.py` reuse
- **Acceptance criteria:**
  - Parses board from source.
  - Uses `fetch_greenhouse_board_jobs` / `greenhouse_job_to_normalized` (or a small shared fetch-by-id helper if adding one is smaller than filtering the board list).
  - Returns normalized vacancy and application URL.
  - Missing job → clear failure, no exception leak from CLI later.
  - Does not use `GREENHOUSE_BOARDS` collector path.
- **Verification:** Mocked HTTP/respx test: found job, missing job, bad source.

### TASK-010 — Application URL for Greenhouse

- **Status:** done
- **Depends on:** TASK-009
- **Goal:** Define application URL as Greenhouse `absolute_url` (OQ-002 default).
- **Area:** resolver
- **Acceptance criteria:**
  - `application_url` is the job `url` from `NormalizedVacancy`.
  - Empty URL is a resolve failure.
- **Verification:** Included in TASK-009 tests.

### TASK-011 — Resolve does not parse Telegram

- **Status:** done
- **Depends on:** TASK-009
- **Goal:** Prove lookup uses internal source + id only.
- **Area:** tests
- **Acceptance criteria:**
  - Test constructs resolve from strings only; no formatter/card HTML involved.
- **Verification:** Unit test.

---

## Stage 1C — Mapping and classification (no browser)

### TASK-012 — Resume path resolution

- **Status:** done
- **Depends on:** TASK-005
- **Goal:** Choose the resume file for Stage 1 from the structured profile default.
- **Area:** autofill resume helper
- **Acceptance criteria:**
  - Uses profile default resume path.
  - Missing file is a clear error / result warning, not a crash inside the adapter.
  - Tests use a tiny synthetic fixture file, not real `resumes/*.pdf`.
  - Does not read PDF bytes in tests.
- **Verification:** Unit tests with tmp files.

### TASK-013 — Standard question mapping

- **Status:** done
- **Depends on:** TASK-006, TASK-007
- **Goal:** Map known Greenhouse-style questions to explicit profile fields only.
- **Area:** autofill question mapper
- **Acceptance criteria:**
  - Sponsorship question maps to `requires_visa_sponsorship` when set.
  - Work-authorization-in-country-X maps only when that country is explicit in the profile.
  - Salary / “why this company” / free text stay unknown.
  - No LLM calls.
- **Verification:** Table-driven unit tests.

### TASK-014 — Field classifier

- **Status:** done
- **Depends on:** TASK-013
- **Goal:** Classify a discovered field into the spec categories.
- **Area:** autofill classifier
- **Acceptance criteria:**
  - Identity/contact/links/resume/explicit eligibility → `SUPPORTED_DETERMINISTIC` when profile has the value.
  - Required custom/unknown → `UNKNOWN_REQUIRED`.
  - Optional unknown → `UNKNOWN_OPTIONAL`.
  - Gender/ethnicity/disability/veteran → `SENSITIVE_OPTIONAL` and not filled.
  - Unrecognized control → `UNSUPPORTED`.
- **Verification:** Unit tests per class.

### TASK-015 — Classifier does not guess

- **Status:** done
- **Depends on:** TASK-014
- **Goal:** Empty profile eligibility leaves work-auth questions unresolved.
- **Area:** tests
- **Acceptance criteria:**
  - Profile without country authorization never classifies that question as deterministic.
- **Verification:** Unit test.

---

## Stage 1D — Browser session

### TASK-016 — Add Playwright dependency

- **Status:** done
- **Depends on:** TASK-001
- **Goal:** Add Playwright after confirming it is not already a dependency.
- **Area:** `pyproject.toml`, lockfile
- **Acceptance criteria:**
  - Playwright is declared (runtime or an explicit extra/dev group — prefer runtime if CLI autofill needs it).
  - No LangChain / Celery / new database added.
  - README or command help can wait until TASK-030; a short comment in `PROGRESS.md` is enough here.
- **Verification:** `uv lock` / `uv sync` succeeds. Import playwright in a trivial test or command.

### TASK-017 — BrowserSession lifecycle

- **Status:** done
- **Depends on:** TASK-016
- **Goal:** Wrap Playwright: headed Chromium, open URL, keep context, close on explicit shutdown.
- **Area:** `app/application/autofill/browser.py` (name may vary)
- **Acceptance criteria:**
  - Can open a local fixture HTML file.
  - Close is explicit; no submit helper.
  - Missing browser binaries produce a clear error.
  - No process leak in unit tests (always close in `finally`).
- **Verification:** Test against a local HTML fixture using Playwright; skip/fail clearly if browsers are not installed, without calling live Greenhouse.

### TASK-018 — Keep-open handoff contract

- **Status:** done
- **Depends on:** TASK-017
- **Goal:** Encode OQ-004: after fill, session stays open until CLI wait returns.
- **Area:** BrowserSession + a small wait helper
- **Acceptance criteria:**
  - `keep_open=True` does not close in the service success path.
  - A `close()` path exists for tests and CLI shutdown.
  - Tests mock the wait; they must not hang.
- **Verification:** Unit tests with a fake/mocked session.

---

## Stage 1E — Greenhouse adapter

Use HTML fixtures under `tests/fixtures/autofill/`. Do not hit live Greenhouse in pytest.

### TASK-019 — Greenhouse application form fixtures

- **Status:** done
- **Depends on:** none
- **Goal:** Add a Greenhouse-like HTML fixture with identity fields, resume upload, sponsorship question, one unknown required question, and one sensitive optional question.
- **Area:** `tests/fixtures/autofill/`
- **Acceptance criteria:**
  - Fixture is local HTML, no network.
  - Includes text inputs, a select or radio, file input, checkbox if practical.
- **Verification:** Fixture file exists and is referenced by later tests.

### TASK-020 — Recognize Greenhouse application page

- **Status:** done
- **Depends on:** TASK-017, TASK-019
- **Goal:** Adapter reports whether the page looks like a Greenhouse application form.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - Fixture page → recognized.
  - Unrelated HTML → `UNSUPPORTED_FORM` / `FAILED`, not a crash.
- **Verification:** Fixture tests.

### TASK-021 — Discover form fields

- **Status:** done
- **Depends on:** TASK-020
- **Goal:** List fields with label, type, required, options, current value.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - Uses label / accessible name / name / stable ATS metadata when possible.
  - Returns enough data for classification.
- **Verification:** Fixture discovery test.

### TASK-022 — Fill text and textarea

- **Status:** done
- **Depends on:** TASK-021, TASK-014
- **Goal:** Fill supported text fields and read them back.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - First/last/email/phone/URL fields fill when classified deterministic.
  - Read-back matches what was filled.
- **Verification:** Fixture test.

### TASK-023 — Fill select / dropdown

- **Status:** done
- **Depends on:** TASK-022
- **Goal:** Fill select by visible option / value without guessing.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - Known option is selected.
  - Missing option → unresolved, not a random first option.
- **Verification:** Fixture test.

### TASK-024 — Fill radio and checkbox

- **Status:** done
- **Depends on:** TASK-023
- **Goal:** Fill boolean / choice controls from explicit mapping only.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - Sponsorship yes/no radio or select works from profile.
  - Unmapped checkbox is left untouched.
- **Verification:** Fixture test.

### TASK-025 — Resume upload

- **Status:** done
- **Depends on:** TASK-012, TASK-021
- **Goal:** Attach the resume file input.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - File input receives the synthetic fixture path.
  - Result marks `resume_uploaded=True` only after read-back shows a file.
- **Verification:** Fixture test with a tiny local file.

### TASK-026 — Read-back validation

- **Status:** done
- **Depends on:** TASK-022, TASK-025
- **Goal:** After fill, inspect DOM/values/required empties/browser validation messages.
- **Area:** Greenhouse adapter / validator
- **Acceptance criteria:**
  - Successful Playwright fill is not enough; validator uses read-back.
  - Empty required unknown field appears in `unresolved_required_fields`.
- **Verification:** Fixture test.

### TASK-027 — CAPTCHA / login / challenge detection

- **Status:** done
- **Depends on:** TASK-020
- **Goal:** Detect challenge pages and return `NEEDS_MANUAL_INTERVENTION`.
- **Area:** Greenhouse adapter
- **Acceptance criteria:**
  - Fixture with captcha/login/challenge markers does not attempt fill/submit.
  - No bypass logic.
- **Verification:** Fixture test.

### TASK-028 — Submit guard

- **Status:** done
- **Depends on:** TASK-021
- **Goal:** Stage 1 adapter/service must not click Submit or call form submit.
- **Area:** adapter + tests
- **Acceptance criteria:**
  - Public adapter API has no `submit()` method, or it is absent from Stage 1 service.
  - Integration test asserts submit control was not clicked and `submit_performed is False`.
  - Fixture may include a Submit button that must remain unclicked.
- **Verification:** Fixture test with a click spy / event listener on the submit button.

---

## Stage 1F — Service, CLI, UX

### TASK-029 — AutofillService orchestration

- **Status:** done
- **Depends on:** TASK-009, TASK-014, TASK-018, TASK-026, TASK-028
- **Goal:** Orchestrate resolve → profile → resume → browser → adapter → result. No collector/Telegram/SQLite writes required.
- **Area:** `AutofillService`
- **Acceptance criteria:**
  - One vacancy per call.
  - Adapter owns selectors; service owns profile/policy.
  - `submit_performed` is always false.
  - Browser remains open on success when `keep_open=True`.
  - Failures return `FAILED` / `NEEDS_MANUAL_INTERVENTION` rather than crashing the process when reasonably catchable.
- **Verification:** Service test with fake adapter + fake resolver, plus one fixture integration after TASK-033.

### TASK-030 — CLI `autofill`

- **Status:** done
- **Depends on:** TASK-029
- **Goal:** Add `uv run python -m app autofill SOURCE EXTERNAL_ID`.
- **Area:** new small CLI module imported from `app/cli.py` or `app/__main__.py` (avoid growing `cli.py` further if a sub-app is easy)
- **Acceptance criteria:**
  - Parses `target_company:greenhouse:<board>` and external id.
  - Does not send Telegram.
  - Does not write SQLite unless a later task explicitly needs it.
  - Exits non-zero on resolve/profile/browser setup failure.
- **Verification:** Typer CLI test with fakes; no live network.

### TASK-031 — Console summary

- **Status:** done
- **Depends on:** TASK-030
- **Goal:** Print filled / needs review / unsupported / resume / Submit NOT PERFORMED.
- **Area:** autofill renderer + CLI
- **Acceptance criteria:**
  - Matches the product summary shape.
  - Does not print full email/phone values (labels only, or masked).
- **Verification:** Renderer unit test.

### TASK-032 — PII-safe autofill logging

- **Status:** done
- **Depends on:** TASK-029
- **Goal:** Log source, id, domain, adapter, field labels/classes, outcomes; never full PII/secrets.
- **Area:** autofill logging helpers
- **Acceptance criteria:**
  - Helper redacts email/phone/address.
  - Tests assert redaction.
- **Verification:** Unit tests.

### TASK-033 — Fixture integration test

- **Status:** done
- **Depends on:** TASK-029, TASK-028, TASK-025
- **Goal:** Greenhouse-like fixture → known fields filled, resume uploaded, unknown required detected, submit not performed.
- **Area:** `tests/application/autofill/` (path may vary)
- **Acceptance criteria:**
  - One end-to-end test against local HTML.
  - No live Greenhouse.
  - Asserts the four outcomes in the spec.
- **Verification:** `uv run pytest` on the new test file.

### TASK-034 — Autofill errors stay out of the main pipeline

- **Status:** done
- **Depends on:** TASK-030
- **Goal:** Autofill is a separate CLI path; `run` / collectors / Telegram are unchanged.
- **Area:** CLI wiring review + a small regression test if needed
- **Acceptance criteria:**
  - `run` does not import Playwright at module import if that would slow or break collection (lazy import is acceptable).
  - No collector calls AutofillService.
- **Verification:** Grep/unit assertion that collectors do not import autofill; existing Target Companies / LinkedIn tests still pass.

### TASK-035 — Sensitive optional fields remain empty

- **Status:** done
- **Depends on:** TASK-033
- **Goal:** Fixture sensitive field is not filled even if a value exists in profile, unless an explicit fill policy is set (Stage 1: never fill).
- **Area:** classifier + integration test
- **Acceptance criteria:**
  - Demographic/voluntary control stays at default.
- **Verification:** Fixture assertion.

### TASK-036 — Manual smoke-test runbook

- **Status:** done
- **Depends on:** TASK-030
- **Goal:** Document a real Greenhouse Target Company smoke test without automating live Greenhouse in pytest.
- **Area:** `docs/` (short runbook) or a section in `PROGRESS.md` / `docs/COMMANDS.md`
- **Acceptance criteria:**
  - Lists: pick source+id, run CLI, confirm fields, confirm resume, confirm no submit, confirm browser stays open.
  - Does not instruct bypassing CAPTCHA.
  - Marks visual confirmation as a user step (spec items 18–19).
- **Verification:** Document exists. Do not run live smoke unless the user asks.

---

## Stage 2 — Telegram → Autofill

Do not start until Stage 1 CLI is verified.

### TASK-037 — Telegram Autofill action for Target Company Greenhouse

- **Status:** todo
- **Depends on:** TASK-033
- **Goal:** Add a Telegram action that starts autofill for `target_company:greenhouse:*` using stored source+id.
- **Area:** `app/telegram/`, CLI callback handling
- **Acceptance criteria:**
  - Action appears only for supported Target Company Greenhouse sources.
  - LinkedIn Prepare behavior is unchanged.
  - Routing uses `TelegramDestination`, not scattered source checks.
- **Verification:** Button/callback tests similar to existing `source_supports_prepare` tests.

### TASK-038 — Telegram action uses internal vacancy resolve

- **Status:** todo
- **Depends on:** TASK-037, TASK-009
- **Goal:** Callback loads vacancy via VacancyResolver, not card text.
- **Area:** callback handler
- **Acceptance criteria:**
  - Handler passes source+external_id to resolver.
  - No HTML/card parsing for autofill inputs.
- **Verification:** Handler unit test with fake resolver.

### TASK-039 — Target Companies callback tests

- **Status:** todo
- **Depends on:** TASK-038
- **Goal:** Cover destination chat, compact `tcg.<board>` source, and failure answer to the user.
- **Area:** `tests/test_telegram_*.py` style tests
- **Acceptance criteria:**
  - Wrong chat rejected.
  - Resolve failure does not crash poller.
- **Verification:** Existing Telegram test patterns.

---

## Stage 3 — LLM-assisted custom questions

### TASK-040 — Generated answers with policy/confidence

- **Status:** done
- **Depends on:** TASK-033
- **Goal:** Optional generation for unknown custom questions, never silently treated as deterministic.
- **Area:** autofill + LLM client
- **Acceptance criteria:**
  - Generated answers are marked generated and require review unless a later policy says otherwise.
  - Stage 5 auto-submit must still treat them as needing review unless explicitly permitted.
  - Answers are 1–3 short sentences and may only use candidate/job facts already present.
  - Legal/sensitive/factual-personal questions are never sent to the LLM.
  - For select/radio/multi-select, LLM output is matched against real options; no option → unresolved.
- **Verification:** Unit tests with fake LLM.
- **Note:** Brought forward from Stage 3 by the Agoda 7044713 smoke test. Still not auto-submittable.

### TASK-041 — Review handling for generated answers

- **Status:** done
- **Depends on:** TASK-040
- **Goal:** Surface generated answers in the summary as needs-review.
- **Area:** result renderer / AutofillResult
- **Acceptance criteria:**
  - Generated required answers do not flip status to auto-submittable.
  - Summary lists generated answers separately from deterministic fills.
- **Verification:** Unit tests.

---

## Stage 4 — Additional ATS

Watchers belong in `app/company_watch/watchers/`. Autofill adapters belong in the autofill layer. Do not combine both in one task.

### TASK-042 — Lever Target Company watcher

- **Status:** done
- **Depends on:** none (discovery-only; do not block on autofill)
- **Goal:** Collect Lever companies from `target_companies.yaml` into `NormalizedVacancy`.
- **Area:** `app/company_watch/watchers/`
- **Acceptance criteria:** Same watcher rules as Greenhouse: per-company errors, source `target_company:lever:<slug>`, title filters, no SQLite/Telegram.
- **Verification:** Watcher tests mirroring `tests/company_watch/watchers/test_greenhouse_watcher.py`.
- **Note:** Do not wire into `run`/Telegram in the same change unless a follow-up task exists.

### TASK-043 — Ashby Target Company watcher

- **Status:** done
- **Depends on:** none
- **Goal:** Same as TASK-042 for Ashby.
- **Area:** company_watch watchers
- **Acceptance criteria:** Analogous to TASK-042.
- **Verification:** Focused watcher tests.

### TASK-044 — SmartRecruiters Target Company watcher

- **Status:** done
- **Depends on:** none
- **Goal:** Same as TASK-042 for SmartRecruiters.
- **Area:** company_watch watchers
- **Acceptance criteria:** Analogous to TASK-042.
- **Verification:** Focused watcher tests.

### TASK-045 — Workday Target Company watcher

- **Status:** blocked
- **Depends on:** explicit user request (`OQ-006`)
- **Goal:** Workday watcher.
- **Area:** company_watch watchers
- **Acceptance criteria:** Not started until requested.
- **Verification:** n/a

### TASK-046 — Additional autofill adapters

- **Status:** todo
- **Depends on:** TASK-033 and the matching watcher
- **Goal:** Add autofill adapters only after Greenhouse Stage 1 is done, one ATS per iteration.
- **Area:** autofill adapters
- **Acceptance criteria:** Same Stage 1 behavior: no submit, fixture tests, profile isolation.
- **Verification:** Per-adapter fixture tests.

---

## Stage 5 — Safe auto-submit

### TASK-047 — Auto-submit policy

- **Status:** todo
- **Depends on:** TASK-033, TASK-041
- **Goal:** Compute `AUTO_SUBMIT_SAFE` vs `NEEDS_REVIEW` from AutofillResult without submitting.
- **Area:** autofill policy module
- **Acceptance criteria:**
  - Any unresolved required, generated-unpermitted, captcha, validation error, or unsupported required control → `NEEDS_REVIEW`.
  - Policy is deterministic.
- **Verification:** Unit tests.

### TASK-048 — Auto-submit execution

- **Status:** todo
- **Depends on:** TASK-047 and an explicit user request to enable submit
- **Goal:** Submit only when policy is `AUTO_SUBMIT_SAFE`.
- **Area:** autofill service
- **Acceptance criteria:**
  - Default remains no submit until this task is explicitly executed.
  - `submit_performed` is true only after a real successful submit path.
- **Verification:** Fixture tests; still no live Greenhouse in pytest.

---

## Housekeeping (do not mix into Stage 1 adapter work)

### TASK-049 — Generated artifact retention

- **Status:** done
- **Depends on:** none
- **Goal:** Prevent unbounded growth of `data/prepared/` without deleting `resumes/*.pdf`.
- **Area:** application preparation / cleanup
- **Acceptance criteria:**
  - Durable resumes are untouched.
  - Rebuildable cover-letter artifacts have a documented retention rule (age, or overwrite in place — choose one and test it).
- **Verification:** Unit tests on a tmp artifacts dir.

### TASK-050 — Optional vacancy snapshot store

- **Status:** todo
- **Depends on:** TASK-009 proven insufficient (job gone / need offline resolve)
- **Goal:** Persist enough vacancy fields for autofill without using Telegram as storage.
- **Area:** storage (schema change only in this dedicated task)
- **Acceptance criteria:**
  - Lookup by source+external_id.
  - No Telegram parsing.
  - Do not start this task during Stage 1 unless live resolve is blocked.
- **Verification:** Storage tests.

---

## Stage 1G — Live Greenhouse smoke-test follow-ups (Agoda 7044713)

Do not hardcode Agoda selectors or vacancy-specific answers. Extend mapping, profile, and the Greenhouse adapter generically.

### TASK-051 — Website / Blog / Other is not GitHub

- **Status:** done
- **Depends on:** TASK-013
- **Goal:** GitHub fills only GitHub-specific fields. Website/Blog/Other uses an explicit website/portfolio URL only.
- **Area:** question mapper + CandidateProfile website helper
- **Acceptance criteria:**
  - GitHub/LinkedIn are never used as a website fallback.
  - A GitHub or LinkedIn URL stored in `professional_links.website` is not used for Website/Blog/Other.
  - Missing explicit website → unresolved, even if required.
- **Verification:** Mapper unit tests.

### TASK-052 — Reusable custom questions from structured profile

- **Status:** done
- **Depends on:** TASK-003, TASK-013
- **Goal:** Answer standard reusable application questions from explicit CandidateProfile data only.
- **Area:** CandidateProfile schema, example YAML, question mapper, option matching
- **Acceptance criteria:**
  - Reuse `years_of_experience` (optional `years_of_relevant_experience` override).
  - Explicit fields for academic level, tech stack, field of interest, relocation destinations, employee relationship, privacy consent.
  - Multi-select chooses at most N options that exist in both the profile and the form.
  - Conditional employee-relationship details stay empty when the answer is No.
  - No legal/sensitive inference.
- **Verification:** Profile + mapper unit tests; Greenhouse-like fixture for select/multi-select/relocation/relationship.

### TASK-053 — Greenhouse cover letter via Enter manually

- **Status:** done
- **Depends on:** TASK-022
- **Goal:** Reuse existing cover-letter generation. On Greenhouse, prefer Enter manually, wait for the editor, insert text, read it back.
- **Area:** extracted cover-letter helper, Greenhouse adapter, AutofillService
- **Acceptance criteria:**
  - LinkedIn prepare still uses the same generation path.
  - No cover letter → field unresolved, autofill continues.
  - Submit is not performed.
  - Read-back must confirm the React/textarea value.
- **Verification:** Adapter fixture test with mocked cover-letter text; LinkedIn prepare tests still pass.

---

## Stage 1H — Second live smoke-test follow-ups (Agoda 7044713)

Do not hardcode Agoda selectors or vacancy-specific answers. Represent answers as generic CandidateProfile / application-policy data. Greenhouse only owns React interaction and read-back.

### TASK-054 — Phone country code vs national number

- **Status:** done
- **Depends on:** TASK-022
- **Goal:** If an intl-tel widget has a country selector, select the country there and fill the phone input with the national number only. Strip a known calling code from a stored `+998...` value. Do not strip digits when the calling code is unknown. Read-back must contain the prefix exactly once.
- **Area:** `app/application/autofill/phone.py`, Greenhouse adapter
- **Verification:** Unit tests for `+998` vs national form; fixture with Uzbekistan selector.

### TASK-055 — React multi-select persistence

- **Status:** done
- **Depends on:** TASK-023
- **Goal:** Selecting the next Greenhouse React multi-select option must not replace previous chips. Respect max count (top 3). Use only technologies from CandidateProfile that exist as visible options. After each selection, wait and read chips; success only if all previously selected values remain.
- **Verification:** Fixture where Java, Kotlin, and Spring Boot are chosen sequentially and all three remain.

### TASK-056 — Boolean False maps to visible No

- **Status:** done
- **Depends on:** TASK-023
- **Goal:** Semantic booleans are not HTML `true`/`false` unless those are the visible options. True → Yes / Yes,...; False → No / No,.... React dropdowns: open → click visible option → wait → read back the visible label. If it did not persist, report unresolved.
- **Verification:** Fixture Yes/No React selects for relocation and employee relationship.

### TASK-057 — Generic relocation willingness policy

- **Status:** done
- **Depends on:** TASK-052
- **Goal:** `application_policy.relocation.willing: true` answers relocation-willingness questions YES, including “based in X or open to relocate to X”. No destination whitelist. Does not imply current residence, work authorization, or visa status.
- **Verification:** Mapper tests for Bangkok OR-relocate, unlisted city, and generic “open to relocation”.

### TASK-058 — Undeclared relationships and prior affiliations default to No

- **Status:** done
- **Depends on:** TASK-052
- **Goal:** Ordinary employee-relationship and named prior-affiliation questions answer No unless the profile records otherwise. Explicit `prior_affiliations` (e.g. Deloitte associated: false) is matched by organization name in the question. Conditional follow-ups stay empty and are not unresolved when inactive. Not used for legal, criminal, demographic, or sensitive questions. LLM must not invent these answers.
- **Verification:** Mapper + React fixture for relationship No, Deloitte long-form No, inactive employee name.

### TASK-059 — Truthful application-source preference

- **Status:** done
- **Depends on:** TASK-013
- **Goal:** “How did you hear about us/this job?” uses an ordered preference: Company Website / Careers Website / Careers Page / Direct Application, then LinkedIn, then Other. Never Employee Referral, Recruiter, Agency, Event, University. Match only options present on the form; otherwise unresolved.
- **Verification:** Mapper tests plus React fixture with mixed allowed/forbidden options.

### TASK-060 — Cover letter Enter manually + cover_letter_filled

- **Status:** done
- **Depends on:** TASK-053
- **Goal:** Detect the Cover Letter section, click Enter manually (not Attach), wait for the editor, reuse existing cover-letter generation, fill through Playwright, read back, set `cover_letter_filled`.
- **Verification:** React/Greenhouse fixture: Enter manually → editor → text survives read-back. Service reports `cover_letter_filled`.

### TASK-061 — Centralized React interact / read-back

- **Status:** done
- **Depends on:** TASK-054, TASK-055, TASK-056
- **Goal:** For Greenhouse React controls: detect type, interact like a user, wait for option/editor state, read visible state, compare to the intended semantic value, only then mark filled. No generic sleep-and-hope. Multi-select: expected set ⊆ chips. Single select: visible label matches. Text: input value matches. Phone: prefix once.
- **Area:** `app/application/autofill/react_controls.py`
- **Verification:** Covered by TASK-054..060 fixture tests.

---

## Stage 1I — Third live smoke-test follow-ups (Agoda 7044713)

Do not hardcode Agoda selectors or vacancy-specific answers. CandidateProfile / ApplicationPolicy / question overrides own the answers. Greenhouse only discovers, interacts, and verifies visible state.

### TASK-062 — Cover letter generation complies with validation

- **Status:** done
- **Depends on:** TASK-060
- **Goal:** Reuse existing cover-letter generation. Prompt the model to write a concise letter with normally 1–2 core technologies (not a stack dump). Keep the existing validator cap (do not weaken it). If the first draft fails validation, the retry user message must include the validation reason so the second attempt can correct it.
- **Area:** `prompts/create_cover_letter.md`, `app/llm_client.py`
- **Verification:** Mocked LLM tests: too-many-technologies retry includes the reason; a short valid letter passes. No live LLM.

### TASK-063 — Strict tech-stack matching (Java ≠ JavaScript)

- **Status:** done
- **Depends on:** TASK-055
- **Goal:** Multi-select uses only technologies explicitly listed in CandidateProfile that exist as visible options. “Top 3” means at most 3, not exactly 3. Identifier-safe matching: Java must not select JavaScript. After each selection, read chips back; previous selections must remain.
- **Verification:** Fixture with JavaScript available but absent from skills → Java + Kotlin only.

### TASK-064 — Explicit gender autofill

- **Status:** done
- **Depends on:** TASK-014
- **Goal:** If CandidateProfile has an explicit gender value, map it to the matching visible option and fill it. Do not default to Prefer not to disclose. Other sensitive fields stay unfilled unless `fill_sensitive_fields` is true. Required gender with an explicit value is not unresolved merely because it is sensitive.
- **Verification:** Mapper + React/native select fixture; read-back of the visible label.

### TASK-065 — Newsletter No / SMS Yes application policy

- **Status:** done
- **Depends on:** TASK-013
- **Goal:** Optional/required recruitment-newsletter / other-job-opening questions default to No. SMS/text interview-update questions default to Yes. Configurable on ApplicationPolicy, not Agoda wording.
- **Verification:** Mapper tests + React Yes/No read-back.

### TASK-066 — Reusable question overrides

- **Status:** done
- **Depends on:** TASK-052
- **Goal:** Explicit per-question answers (match on question text contains) for facts that are not universal, e.g. engineering blog = Yes, applied in the past 6 months = No. LLM must not invent these. Adapter stays generic.
- **Verification:** Mapper tests with synthetic override YAML; LLM eligibility false.

### TASK-067 — Current country/region and generic prior employment

- **Status:** done
- **Depends on:** TASK-058
- **Goal:** “In which country/region are you currently based?” maps from CandidateProfile country (location, not citizenship/authorization). Ordinary “presently employed by [company group]” questions default to No via undeclared-affiliation policy. Map to the visible negative option.
- **Verification:** Mapper + React fixture read-back.

### TASK-068 — React live-option fill for source / skills / cover letter persist

- **Status:** done
- **Depends on:** TASK-059, TASK-062, TASK-063
- **Goal:** At fill time, inspect live visible options (How did you hear, tech stack, gender, country). Cover Letter: Enter manually → wait until editor is visible/ready → Playwright fill → wait for React persist → read-back before `cover_letter_filled`.
- **Verification:** Existing cover-letter fixture plus TASK-063; source still selects Company Website when present.

---

## Stage 1J — Fourth live smoke-test follow-ups (Agoda 7044713)

Reuse TASK-062..065 rather than duplicating them. The fourth live run showed TASK-063 persistence working (Java+Kotlin) and TASK-065 React SMS clicks working, but the **desired answers and remaining failures changed**.

Do not hardcode Agoda selectors. CandidateProfile / ApplicationPolicy / question overrides own answers. Greenhouse only discovers, interacts, and verifies visible state.

### TASK-069 — Cover letter pipeline: generation + Cover Letter section Enter manually

- **Status:** done
- **Depends on:** TASK-062, TASK-060
- **Goal:** Trace the full cover-letter path. Generation must retry with the validation reason (do not weaken the 4-area cap). Autofill must locate the Cover Letter **section** (not `#cover_letter` file input, not Resume's Enter manually), click Enter manually there, wait for the editor, fill, and confirm persisted read-back. Each failing stage must be logged (`generation never invoked`, validation rejected, Enter manually not found, editor missing, React cleared).
- **Area:** `app/application/autofill/cover_letter.py`, `greenhouse.py`, `service.py`, `prompts/create_cover_letter.md`, `app/llm_client.py`
- **Verification:** Fixture with Resume Enter manually **and** Cover Letter Enter manually plus a `#cover_letter` file input. Generated text lands in the cover-letter editor only. Missing Enter manually and React-cleared editor are reported as those stages. Mocked generation retry still includes the validation reason.

### TASK-070 — Top-N truthful tech-stack selection at fill time

- **Status:** done
- **Depends on:** TASK-063
- **Goal:** For questions that request top N, intersect the **full** CandidateProfile professional stack with **live** visible options, rank by profile order, and select up to N truthful matches. Do not pre-filter against incomplete discovery-time options (that dropped Spring Boot). Do not pad with JavaScript or any skill absent from the profile.
- **Verification:** >=3 truthful live matches → exactly 3 chips; only 2 profile matches → 2 chips; JavaScript present but not in profile → not selected; chips persist.

### TASK-071 — Gender demographic policy: prefer not to disclose

- **Status:** done
- **Depends on:** TASK-064
- **Goal:** ApplicationPolicy selects Prefer not to disclose (or a semantic equivalent) for demographic gender questions. This overrides filling Female from `sensitive.gender`. Not Agoda-specific. Not sent to the LLM. Required gender with a non-disclosing option counts as answered after read-back.
- **Verification:** Mapper + React/native fixture read-back of Prefer not to disclose even when `sensitive.gender` is Female.

### TASK-072 — SMS recruitment updates default to No

- **Status:** done
- **Depends on:** TASK-065
- **Goal:** ApplicationPolicy `sms_interview_updates` defaults to No. Newsletter remains No. Relocation Yes, employee relationship No, prior affiliation No, source preference, current location, and question overrides are unchanged.
- **Verification:** Mapper + React Yes/No read-back. Existing SMS=Yes tests updated.

---

## Stage 1K — Academic level + seniority gating (Agoda 7044713)

Do not hardcode Agoda selectors. Education is CandidateProfile / ATS-independent. Greenhouse only maps the semantic degree onto a visible option and verifies read-back. Seniority/recommendation policy stays in the existing company-watch models; the Greenhouse adapter does not decide vacancy fit.

### TASK-073 — Awarded academic level is master's-equivalent, not Diploma or Doctorate

- **Status:** done
- **Depends on:** TASK-052
- **Goal:** Highest *awarded* degree is an ATS-independent token (`MASTERS`). Russian specialist / equivalent maps to master's. Completed postgraduate / aspirantura without an awarded doctorate does not become doctorate. Greenhouse maps `MASTERS` onto the visible option "Master's Degree", waits for React state, reads the label back, and reports filled only if that label persists. Diploma must not be selected via substring aliases such as "ma".
- **Area:** `candidate_profile.py`, `options.py`, Greenhouse adapter, example YAML
- **Verification:** Specialist/master-equivalent → Master's Degree; postgraduate without doctorate still Master's; awarded doctorate → Doctorate Degree; bachelor → Bachelor's Degree; Diploma is not an accidental fallback. Fixture read-back of "Master's Degree".

### TASK-074 — SKIP vacancies stay out of autonomous application; manual autofill CLI remains

- **Status:** done
- **Depends on:** none (audit of existing recommendation/seniority; gate already existed at Telegram send)
- **Goal:** Confirm Lead Software Engineer titles (including Agoda 7044713) are `LEAD_MANAGER` / `SKIP`. Staff/Principal remain stretch `CHECK_MANUALLY`. Senior IC can be `APPLY_NOW` when otherwise eligible. The normal autonomous workflow must not auto-apply/autofill SKIP vacancies. Explicit `autofill SOURCE EXTERNAL_ID` must still run for a SKIP smoke-test vacancy. Do not put this gate in the Greenhouse adapter.
- **Area:** `application_recommendation.allows_autonomous_application_workflow`, Target Companies send path, autofill CLI isolation tests
- **Verification:** Lead title SKIP and not Telegram-sent; Senior sent when eligible; AutofillService/CLI do not consult recommendation.

---

## Explicitly out of scope unless requested

- Rewriting LinkedIn parser, matcher, or Telegram prepare flow
- Merging Target Companies into `GREENHOUSE_BOARDS`
- Using `known_hiring_locations` as a hard filter
- Automatic final application submission before TASK-048
- CAPTCHA / Cloudflare / OTP bypass
- Reading real `candidate_profile.local.*`, resume PDFs, or production DB in agent work
- Relocating `candidate_profile.md` (`OQ-010`)
- Promoting HH into `run` (`OQ-007`)
- Web UI
- Workday before an explicit request

---

## Suggested first implementation iteration

Start at **TASK-001**, then continue through subsequent eligible tasks in the same run using the workflow above.
