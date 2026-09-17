# JobApplier — Implementation Plan

This plan compares `PRODUCT_SPEC.md` with the repository as inspected on 2026-09-10,
and re-audited on 2026-09-16 through TASK-086 (see `PROGRESS.md` for the current
task/status/build details). It is the work queue for autonomous development.

Do not start from a blank architecture.
Prefer extending working code over rewriting it.
Never combine unrelated tasks into one implementation change.
Implement exactly one PLAN task at a time. A new task is a new iteration, but not necessarily a new agent run.

---

## How to use this plan

1. Read `PRODUCT_SPEC.md`, `AGENTS.md`, this file, and local `PROGRESS.md` if it exists.
2. Pick the first `todo` task whose dependencies are `done` and that is not blocked.
3. Implement only that task. Do not start a second task before this one is verified.
4. Run the verification listed on the task.
5. Mark the task done **only if verification succeeds**. If verification fails, fix it or stop with a genuine blocker; do not mark it done.
6. Update local gitignored `PROGRESS.md` (current task, last verified task, status).
   If it is missing, copy `PROGRESS.example.md` and reconstruct state from this plan
   and the repository. Never commit `PROGRESS.md`.
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
- Persistence: SQLite at `data/jobs.db`; `data/target_company_analysis_cache.json` (durable JSON, not SQLite)
- Browser automation: Playwright (`BrowserSession`, Stage 1 Greenhouse autofill). Production runs headed by default; `BrowserSession(headed=False)` exists and is used by tests. Lazy-imported so ordinary collection/analysis does not require it.
- No LangChain, no extra databases, no automated submit

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

`review`, `collect-hh`, `collect-linkedin-email`, `collect-greenhouse`, `collect-target-companies-greenhouse`, `analyze-target-companies-greenhouse`, `send-linkedin-telegram`, `prepare-telegram-applications`, `autofill`, `run`, Telegram debug/cache/history commands, IMAP preview/list/reset.

`autofill SOURCE EXTERNAL_ID` (`app/application/autofill/cli.py`) is the diagnostic-only Stage 1 Greenhouse autofill entry point. It is intentionally ungated: it does not call `PrepareApplicationService`, does not consult recommendation or `application_history`/`APPLIED`, and blocks the terminal on builtin `input()` (Enter/Ctrl+C) instead of a Telegram button.

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
- Target Company Greenhouse cards expose explicit **Prepare application** (`prepapp:tcg.<board>:<id>` → `PrepareApplicationService` with `PrepareIntent.EXPLICIT`). LinkedIn **🛠 Prepare** is unchanged. Generic Greenhouse still has no prepare button.
- Target Companies delivery gate (TASK-085): vacancies whose Greenhouse-discovered apply URL is not the canonical `*.greenhouse.io/.../jobs/<id>` shape (custom-domain embeds, e.g. Elastic-style) are dropped from candidate selection before analysis (`is_canonical_greenhouse_hosted_url`, `app/application/autofill/greenhouse_url.py`) rather than delivered or persisted as `SKIP`; they are simply re-evaluated next cycle. No company is blacklisted by name.

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

### D. Stage 1 Greenhouse autofill (Stage 1A–1P)

- Structured `CandidateProfile` YAML (`candidate_profile.example.yaml` tracked, `candidate_profile.local.yaml` gitignored overlay) — ATS-independent, explicit-only (no legal/citizenship inference from location or relocation willingness)
- `DefaultVacancyResolver`: live Greenhouse board refetch by `target_company:greenhouse:<board>` + external id; other sources fail as unsupported
- Playwright `BrowserSession` (headed by default in production; `headed=False` is supported and used by tests): no `submit()` method anywhere in the adapter surface. Production Stage 1 always builds its result via the `stage1_autofill_result` factory, which discards any caller-supplied `submit_performed` and hardcodes `False`; `Stage1AutofillResult`'s validator likewise coerces `True` back to `False`. The plain `AutofillResult` model itself has no such guard, so this is a factory/subclass guarantee, not a property of every `AutofillResult` instance.
- `GreenhouseAdapter`: form recognition, CAPTCHA/Cloudflare/login challenge detection (→ `NEEDS_MANUAL_INTERVENTION`, no bypass), field discovery, fill + read-back for text/select/radio/checkbox/file-upload/React controls (including React multi-select persistence and the Cover Letter "Enter manually" editor)
- Question mapping/classification (`SUPPORTED_DETERMINISTIC` / `UNKNOWN_REQUIRED` / `UNKNOWN_OPTIONAL` / `SENSITIVE_OPTIONAL` / `UNSUPPORTED`) covering identity, links, resume, phone (country-code vs national number), academic level, tech-stack top-N, gender (`_gender_value` never reads `sensitive.gender`: required Gender always selects a non-disclosure option, optional Gender is always left untouched), relocation/office/hybrid willingness, employee-relationship/prior-affiliation defaults, application-source preference, newsletter/SMS policy, privacy/data-transfer acknowledgements, cover letter (deterministic mapping only; cover letter and unknown-question answers below are LLM-generated, not deterministic)
- Optional LLM-generated answers for unknown custom questions (`ApplicationAnswerGenerator`): always marked `generated`, always needs-review, never for legal/sensitive/factual-personal questions. Choice-type answers (select/radio/combobox/multiselect with real options) are matched against those visible options only; eligible free-text questions instead get a generated free-text answer, which is still flagged for manual review, not auto-submittable
- `AutofillService.run`: resolve → profile → resume → browser → adapter → result orchestration; result renderer (`render_autofill_summary`) and PII-safe logging (`log_autofill_result`)
- Fixture-only test suite (`tests/fixtures/autofill/`, `tests/application/autofill/`); no live Greenhouse in pytest
- Manual smoke-test runbook (`docs/autofill_smoke.md`); two real Greenhouse smoke tests already completed and reported by the user (Agoda `7044713`, Adyen `7342887` — submitted, do not reuse)

### E. Stage 2 Telegram → Prepare → Autofill

- `app/application/prepare_application.py` (`PrepareApplicationService`, `PrepareIntent`): gates on `application_history` `APPLIED` (blocks every intent) then on recommendation (`SKIP` always blocked, `CHECK_MANUALLY` only via `EXPLICIT`, `APPLY_NOW` allowed for both), then calls `AutofillService.run`. Diagnostic CLI `autofill` does not consult this gate.
- Target Company Greenhouse **Prepare application** button → `run_explicit_application_prepare` → recommendation from `TargetCompanyAnalysisCache.get_by_identity` → `PrepareApplicationService.prepare(..., intent=EXPLICIT)`
- `AutofillFailureReason` (`UNSUPPORTED_FORM`, `BROWSER_SETUP_FAILED`, `VACANCY_RESOLVE_FAILED`, `PROFILE_LOAD_FAILED`, `RESUME_RESOLUTION_FAILED`, `UNEXPECTED_ERROR`) drives safe, actionable Telegram failure/manual-intervention text; raw exception text never reaches Telegram
- `ReviewSessionRegistry` (`app/application/autofill/review_session.py`): per-run session id, Telegram/ATS-agnostic; browser stays open until the Telegram **"Done reviewing"** button (`revdone:<session_id>`) is tapped for that specific session (calls `registry.mark_done`), or `run` shutdown's best-effort `close_all()`/`wait_all_discarded()`. A failed `on_ready` Telegram send is caught so it cannot skip the browser handoff. Manually closing the Chromium window itself does not call `mark_done` and does not release `wait_for_done()` — the worker thread stays blocked until a delivered "Done reviewing" tap or process shutdown. See open items in Stage 1Q below.
- "Done reviewing" (or the diagnostic CLI's terminal Enter/Ctrl+C) only closes that browser session; it never submits and never writes `application_history`. `✅ Applied` remains a separate, always-manual Telegram card action.

---

## Gaps vs PRODUCT_SPEC

### Done — Stage 1 autofill, Stage 2 Telegram wiring, Stage 3 LLM answers

Everything listed below was the "main remaining work" as of 2026-09-10 and is now implemented and tested (fixtures + two real live Greenhouse smoke tests; see section D/E above and `PROGRESS.md`):

- Structured Candidate Profile YAML (`example` + `local`)
- Autofill layer (`AutofillService`, result model, field classification)
- Playwright / headed browser session
- Greenhouse ATS adapter (discover, fill, upload, read-back, React controls, challenge detection)
- Submit guard (no `submit()` anywhere in the adapter surface; `submit_performed` is force-`False`)
- CLI `autofill SOURCE EXTERNAL_ID` (diagnostic-only, ungated)
- Vacancy resolver for autofill by `source + external_id`
- CAPTCHA / login handoff (`NEEDS_MANUAL_INTERVENTION`, no bypass)
- Autofill summary UX + PII-safe logging
- Autofill tests on fixtures; no live Greenhouse in pytest
- Manual smoke-test runbook, exercised live twice by the user (Agoda, Adyen)
- Stage 2: Telegram **Prepare application** → `PrepareApplicationService` → `AutofillService`, session-identified browser review handoff, safe failure/manual-intervention text
- Stage 3: optional LLM-generated answers for unknown custom questions, always needs-review

### Incomplete — keep, do not replace

- `PreparationService` supports only `linkedin-email` cover-letter/resume packages. Target Companies uses `PrepareApplicationService` (TASK-078/079) plus Telegram **Prepare application** (TASK-037..039) with `PrepareIntent.EXPLICIT`. These remain two separate mechanisms; do not merge them.
- `application_answers` in the LinkedIn `PreparationService` (not the Stage 1 autofill answer generator) is a no-op placeholder.
- `data/prepared/` retention (TASK-049) is a fixed 14-day age rule for LinkedIn cover-letter artifacts only; there is no CLI command to trigger cleanup on demand.
- Lever/Ashby/SmartRecruiters watchers exist (`app/company_watch/watchers/`, TASK-042..044) but are standalone: not called from `run` or Telegram, so those Target Companies entries are not actually delivered yet. `custom` / `manual` entries have no watcher. Only the Greenhouse watcher is wired end-to-end. There is no autofill adapter for any ATS other than Greenhouse.
- Generic Greenhouse jobs in `run` are delivered to the LinkedIn Telegram destination. This matches “current discovery”, not Target Companies.
- HH exists as CLI-only collection (`OQ-007`), not part of `run`.
- Root `candidate_profile.md` is tracked in Git and may contain personal search context. Do not relocate unless requested (`OQ-010`).
- Open `on_ready`-notification-failure gap in the Telegram review handoff: if the completion notification fails to send, the browser and `ReviewSessionRegistry` entry stay open (correctly), but the "Done reviewing" button/message never reaches the user and there is no retry. Not fixed; do not claim it is.
- A live retest of the TASK-086 fix has not been performed by an agent; only the user's own report exists.

### Intentionally later

- TASK-046: additional-ATS autofill adapters (beyond Greenhouse), one per iteration; depends on TASK-033 (done) and the matching watcher existing — Lever/Ashby/SmartRecruiters watchers (TASK-042..044) already exist, so this task has no unmet dependency, it simply has not been picked up yet
- TASK-047: auto-submit policy computation only (no submit action); depends on TASK-033 and TASK-041 (both done), so it also has no unmet dependency
- TASK-048: auto-submit execution — forbidden until it is both built on a done TASK-047 and explicitly requested; the "requires an explicit request" gate applies to TASK-048, not to computing the TASK-047 policy itself
- Workday (`OQ-006`), including TASK-045 — blocked on an explicit user request

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

Record any deviation in local `PROGRESS.md` (never commit that file).

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
TASK-075 Stage 1L Adyen cross-company Greenhouse smoke
    ↓
TASK-076 Stage 1M office/hybrid + required privacy acknowledgements
    ↓
TASK-077 Stage 1N privacy checkbox interaction / live Adyen remaining gap
    ↓
TASK-078 recommendation → prepare/autofill orchestration
    ↓
TASK-079 application_history APPLIED gate on PrepareApplicationService
    ↓
TASK-037..039 Stage 2 Telegram
    ↓
TASK-080 GitLab unanswered required questions
    ↓
TASK-081 GitLab primary language / OSS / current-location visa
    ↓
TASK-082 GitLab primary-language fill / current-location sponsorship diagnostics / optional gender
    ↓
TASK-083..084 Stage 1P Telegram review handoff + safe failure text
    ↓
TASK-085 Stage 1Q Target Companies unconfirmed-form delivery gate
    ↓
TASK-086 Stage 1Q on_ready exception-safety fix (real live-E2E bug)
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

- **Status:** done
- **Depends on:** TASK-033, TASK-078, TASK-079
- **Goal:** Add a Telegram action that starts autofill for `target_company:greenhouse:*` using stored source+id. Call `PrepareApplicationService` with `PrepareIntent.EXPLICIT`; do not call AutofillService from Telegram directly.
- **Area:** `app/telegram/`, CLI callback handling
- **Acceptance criteria:**
  - Action appears only for supported Target Company Greenhouse sources.
  - LinkedIn Prepare behavior is unchanged.
  - Routing uses `TelegramDestination`, not scattered source checks.
  - Calls `PrepareApplicationService` with `PrepareIntent.EXPLICIT` and injects `TelegramDeliveryStorage` as the lifecycle lookup. APPLIED vacancies surface `Application already submitted`; Telegram does not reimplement the gate.
- **Verification:** Button/callback tests similar to existing `source_supports_prepare` tests.

### TASK-038 — Telegram action uses internal vacancy resolve

- **Status:** done
- **Depends on:** TASK-037, TASK-009
- **Goal:** Callback loads vacancy via VacancyResolver, not card text.
- **Area:** callback handler
- **Acceptance criteria:**
  - Handler passes source+external_id to resolver.
  - No HTML/card parsing for autofill inputs.
- **Verification:** Handler unit test with fake resolver.
- **Implementation note:** Telegram does not import VacancyResolver or Greenhouse. It passes `source + external_id` into `PrepareApplicationService`; `AutofillService.run` resolves the vacancy. Recommendation is loaded with `TargetCompanyAnalysisCache.get_by_identity` (durable JSON, survives restart). Missing recommendation is `UNKNOWN` and fail-closes in the application layer.

### TASK-039 — Target Companies callback tests

- **Status:** done
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

- **Status:** done (Lever adapter)
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

## Stage 1L — Cross-company Greenhouse smoke (Adyen)

Do not hardcode Adyen selectors or vacancy-specific answers. The goal is to prove Stage 1 Greenhouse autofill is reusable, not Agoda-specific.

### TASK-075 — Second live Greenhouse smoke: Adyen Unified Platform

- **Status:** done
- **Depends on:** TASK-074 (Agoda Stage 1 smoke passed sufficiently)
- **Goal:** Select a live Adyen Greenhouse IC vacancy, audit the adapter for company-specific coupling, run fixture tests, and give the user an exact CLI command. Do **not** run the live headed browser from the agent.
- **Area:** `PROGRESS.md`, `docs/autofill_smoke.md`; no adapter change unless leakage is found
- **Selected vacancy:** `Software Engineer (Java) - Unified Platform` / `target_company:greenhouse:adyen` / `7342887` (Amsterdam, ordinary IC, cached recommendation `APPLY_NOW`). Ranked above Senior Financial Products `7573921` (`CHECK_MANUALLY`) because it is the only current Adyen `APPLY_NOW` Java IC role.
- **Acceptance criteria:**
  - Agoda/Booking/Deloitte/engineering-blog answers are not hardcoded in the Greenhouse adapter.
  - CandidateProfile / ApplicationPolicy / question overrides remain the answer owners.
  - Cover-letter generation uses the resolved vacancy context.
  - User runs `uv run python -m app autofill target_company:greenhouse:adyen 7342887` and does not click Submit.
- **Verification:** Static coupling audit + existing autofill/policy/Playwright fixture tests. Live visual confirmation is a user step.

### TASK-076 — Office/hybrid policy + required privacy acknowledgement checkboxes
TASK-077 — Greenhouse privacy checkbox interaction (label-backed / React)

- **Status:** done
- **Depends on:** TASK-075 (Adyen live form remaining gaps)
- **Goal:** Fill office/hybrid attendance questions Yes from generic ApplicationPolicy, and auto-check only required application/recruitment privacy acknowledgements (including Point of Data Transfer / Acknowledge/Confirm). Do not auto-check marketing, SMS, legal certifications, or work-authorization declarations. Greenhouse only discovers, clicks, waits, and verifies visible/checked state.
- **Area:** `ApplicationPolicy`, question mapper, acknowledgement classifier, Greenhouse checkbox interaction / read-back
- **Acceptance criteria:**
  - Office/hybrid willingness (N days in office, hybrid schedule, onsite at the job location) maps to the visible Yes option. Not Amsterdam/Adyen-specific.
  - Office policy does not imply current location or work authorization.
  - Required privacy/data-transfer/privacy-notice acknowledgements are checked only when classified as safe application-privacy and read-back `checked=true`.
  - Newsletter / talent-pool / SMS remain No / unchecked.
  - Unknown legal declarations (criminal, certify-true-and-complete) stay unresolved.
  - Click is not treated as success; Playwright checked/visible state is.
- **Verification:** Mapper unit tests + Playwright fixture with office Yes/No, Point of Data Transfer checkbox, newsletter, SMS, and unsafe legal checkboxes. Do not run the live Adyen browser from the agent.

---

### TASK-077 — Greenhouse privacy checkbox interaction (label-backed / React)

- **Status:** done
- **Depends on:** TASK-076 (office/hybrid Yes verified live; Point of Data Transfer still unchecked)
- **Goal:** The required privacy acknowledgement is discovered, classified, and actually checked in the browser. Do not change ApplicationPolicy. Fix generic Greenhouse checkbox interaction and read-back for hidden/styled/label-backed controls. If it fails again, the CLI summary must show a privacy acknowledgement diagnostic block.
- **Area:** Greenhouse checkbox discovery context, `react_controls` checkbox strategies, autofill summary diagnostics
- **Acceptance criteria:**
  - Ancestor question copy (not only `fieldset` / `.field` / `[class*=question]`) is used so `Acknowledge/Confirm` inherits Point of Data Transfer / Applicant Privacy Notice context.
  - Interaction tries user-like strategies: visible option text, associated label, `role=checkbox`, then `locator.check()` only for a real enabled checkbox. Click is not success; `input.checked` or `aria-checked="true"` after React settle is.
  - A realistic fixture matches a visually hidden `question_*[]` checkbox plus label-backed Acknowledge/Confirm, including React reverting programmatic `.check()`.
  - Newsletter remains unchecked. No Submit. No Adyen-specific selectors.
- **Verification:** Playwright fixture tests + mapper/option unit tests. Do not run the live Adyen browser from the agent.

---

## Stage 1O — Connect recommendation to application preparation

Do not polish individual Greenhouse forms here. Do not click Submit. Do not
wire Telegram UI in the same change.

### TASK-078 — Prepare Application orchestration from a recommended vacancy

- **Status:** done
- **Depends on:** TASK-029, TASK-074, TASK-075
- **Goal:** Connect discovery/recommendation to AutofillService with recommendation safety. Manual `autofill SOURCE EXTERNAL_ID` stays an ungated diagnostic path.
- **Area:** `app/application/prepare_application.py` (application/domain). Do not put this gate in the Greenhouse adapter or Autofill CLI.
- **Acceptance criteria:**
  - `prepare_application(vacancy)` / `PrepareApplicationService.prepare` reuses the same `AutofillService.run` contract as the CLI.
  - `APPLY_NOW` may enter prepare/autofill for autonomous or explicit intent.
  - `CHECK_MANUALLY` does not auto-proceed; it may proceed only with `PrepareIntent.EXPLICIT`.
  - `SKIP` is blocked from both autonomous and explicit product prepare paths.
  - Manual CLI `python -m app autofill SOURCE EXTERNAL_ID` does not consult this gate.
  - Greenhouse adapter has no recommendation logic.
  - No auto-submit; no Telegram button in this task.
- **Verification:** Focused unit tests with a fake AutofillService. No live browser.

### TASK-079 — Block APPLIED vacancies in PrepareApplicationService

- **Status:** done
- **Depends on:** TASK-078
- **Goal:** Product prepare/autofill must consult canonical `application_history` and refuse vacancies that are already APPLIED. Diagnostic CLI `autofill SOURCE EXTERNAL_ID` stays ungated.
- **Area:** `app/application/prepare_application.py`, `TelegramDeliveryStorage.get_history_status`. Do not put this gate in Telegram, Greenhouse, or Autofill CLI.
- **Acceptance criteria:**
  - Lookup is `(source, external_id)` via the existing history repository method, injected like `AutofillRunner`.
  - APPLIED blocks for APPLY_NOW and CHECK_MANUALLY, AUTONOMOUS and EXPLICIT.
  - APPLIED does not reach AutofillService. Blocked reason is `Application already submitted`.
  - SENT / FOUND / PREPARED / SKIPPED / missing row do not add extra lifecycle blocks in this task.
  - Same `external_id` under a different `source` is not a false APPLIED block.
  - Manual CLI autofill does not consult recommendation or application history.
  - No Telegram UI. No Greenhouse fill changes. No schema change.
- **Verification:** Focused unit tests with a fake AutofillService plus tmp SQLite history lookup. No live browser.

### TASK-080 — Fill remaining GitLab Greenhouse required questions from known facts

- **Status:** done
- **Depends on:** TASK-052, TASK-054..061
- **Goal:** Fill four previously unanswered required Greenhouse questions when CandidateProfile/policy already has the facts: current country of residence, location-derived “located in X or Y?”, explicit employment/post-employment restrictions, and prior employment/consulting affiliation wording.
- **Area:** `CandidateProfile` / `ApplicationPolicy`, semantic mapper in `questions.py`, option parsing, Greenhouse boolean-choice fill/readback. No GitLab-specific adapter selectors. No auto-submit. Do not infer citizenship, work authorization, or location from relocation willingness.
- **Acceptance criteria:**
  - “What is your current country of residence?” maps from `identity.country`.
  - “Are you located in the UK or Poland?” is a generic location-derived yes/no from current residence only.
  - Employment-agreement / post-employment-restriction questions use an explicit profile fact; unset does not invent an answer; unrelated criminal/background/export-control/certify questions stay unanswered.
  - “Have you previously worked at or consulted for …?” uses the existing prior-affiliation policy (default No).
  - Greenhouse adapter has no company-name special case.
- **Verification:** Focused mapper, profile, option, and Playwright React-control tests. No live GitLab browser in this task.

### TASK-081 — Primary language, open-source links, GitLab username, current-location visa

- **Status:** done
- **Depends on:** TASK-080
- **Goal:** Fill remaining GitLab required professional questions from explicit profile facts, and stop answering current-location visa/sponsorship questions from an unrelated destination visa option such as Netherlands HSM.
- **Area:** CandidateProfile professional links / employment / country-specific `requires_sponsorship`, semantic mapper, sponsorship option matching. No GitLab-specific adapter selectors. No auto-submit. Do not infer citizenship from residence.
- **Acceptance criteria:**
  - Primary programming language/framework uses the strongest explicit language (Java), not a stack dump.
  - Open-source project links use only explicit `open_source_urls`; missing URLs stay unresolved; GitHub/website are not invented substitutes.
  - GitLab username fills only when explicit; optional absence is not an error.
  - “sponsorship for a visa to remain in your current location” is not answered from global/NL destination sponsorship policy.
  - Generic relocation questions still use the existing relocation policy.
- **Verification:** Focused mapper, profile, option, live-choice, and Playwright tests. No live GitLab browser.

### TASK-082 — GitLab primary-language fill, current-location sponsorship diagnostics, optional gender

- **Status:** done
- **Depends on:** TASK-081
- **Goal:** Make an explicit `employment.primary_programming_language` value reach a required free-text primary language/framework control; keep current-location sponsorship unresolved without an Uzbekistan-specific fact and explain why; leave optional Gender untouched while required Gender still selects a non-disclosure option.
- **Area:** Semantic mapper, Greenhouse text/React fill + readback, autofill summary notes. No GitLab-specific adapter selectors. No auto-submit. Do not answer current-location sponsorship from the global flag or a Netherlands fact.
- **Acceptance criteria:**
  - Required free-text “primary programming language and/or framework” fills Java from CandidateProfile and survives React interaction/readback.
  - Current-location sponsorship with residence Uzbekistan does not consume a Netherlands country-specific fact; missing Uzbekistan fact stays unresolved with reason `current-location sponsorship requires country-specific fact for Uzbekistan`.
  - Optional Gender remains untouched; required Gender selects Prefer not to disclose / Decline to self identify / equivalent. Requiredness comes from discovered field metadata (`required` / `aria-required`), not label guessing. Actual `sensitive.gender` is never filled.
  - Existing required Agoda Gender* behavior still works.
- **Verification:** Focused mapper, classifier, option, summary, live-choice, and Playwright React-control tests. No live GitLab browser.

---

## Stage 1P — Telegram-triggered browser review handoff + safe failure text

An audit of the already-completed Stage 2 Telegram → Prepare → Autofill wiring
(TASK-037..039, TASK-078, TASK-079) found two real gaps not previously
tracked: the browser handoff used builtin `input()` even for the
Telegram-triggered background thread, and FAILED/NEEDS_MANUAL_INTERVENTION
detail was collapsed into a bare "Preparation failed." string before
reaching Telegram. This stage fixes both without touching the underlying
Stage 2 wiring, ATS adapter, or any recommendation/lifecycle gate.

### TASK-083 — Session-identified browser review handoff for Telegram

- **Status:** done
- **Depends on:** TASK-037..039
- **Goal:** Replace the terminal-`input()` browser handoff for the
  Telegram-triggered prepare path with an explicit Telegram "Done reviewing"
  action, keyed by an opaque per-run session id, so multiple concurrent
  preparations never share stdin and closing one session never closes
  another.
- **Area:** `app/application/autofill/review_session.py` (new,
  Telegram/ATS-agnostic registry), `app/application/explicit_prepare_runtime.py`
  (injectable `wait_for_review`), `app/telegram/client.py` (Done-reviewing
  button + callback-data helpers), `app/cli.py` (`_dispatch_target_company_application_prepare`
  wiring, `_handle_review_done_callback`, shutdown-time `close_all()` /
  `wait_all_discarded()`). Diagnostic CLI (`app/application/autofill/cli.py`)
  is untouched and keeps the original `input()` handoff.
- **Acceptance criteria:**
  - Telegram-triggered `AutofillService.run` never calls builtin `input()`.
  - The browser stays open (no `session.close()`) until "Done reviewing" is
    tapped for that specific session id.
  - Two concurrent preparations get independent session ids; closing one
    never touches the other's browser.
  - A duplicate "Done reviewing" tap, or one for an unknown/already-closed
    session, answers gracefully (no exception, no hang).
  - `run` shuts down cleanly: pending sessions are signaled and given a
    bounded grace period to close before the process exits.
  - No auto-submit; no new code path can click Submit.
- **Verification:** `tests/application/autofill/test_review_session.py`,
  `tests/application/autofill/test_review_handoff.py`,
  `tests/test_telegram_target_company_prepare.py` (Done-reviewing cases),
  `tests/application/autofill/test_pipeline_isolation.py` (submit-capability
  scan), `tests/application/autofill/test_cli.py` (diagnostic CLI unchanged).

### TASK-084 — Safe Telegram text for autofill failure / manual intervention

- **Status:** done
- **Depends on:** TASK-037..039
- **Goal:** Stop collapsing `FAILED` results into a bare "Preparation
  failed." and `NEEDS_MANUAL_INTERVENTION` into a bare "Manual review
  needed." Map a coded `AutofillFailureReason` (not raw exception text) to a
  concise, safe, actionable Telegram message, and surface the known
  CAPTCHA/Cloudflare/login challenge token when present.
- **Area:** `app/application/autofill/models.py` (`AutofillFailureReason`,
  `AutofillResult.failure_reason`), `app/application/autofill/service.py`
  (sets the reason on every FAILED path), `app/telegram/application_prepare.py`
  (`format_application_prepare_failed_text`, challenge label in
  `format_application_prepare_completed_text`).
- **Acceptance criteria:**
  - `UNSUPPORTED_FORM` explains that automatic filling is not supported for
    this ATS/form yet.
  - Browser setup failure (including a missing Chromium install) explains
    that browser setup failed, without leaking the raw Playwright exception
    text.
  - A generic/unexpected error never leaks exception class names or message
    text into Telegram; that detail stays in `logger.exception` /
    `log_autofill_result`.
  - A detected security challenge names the challenge type
    (CAPTCHA / Cloudflare / login) when known, and stays generic-but-safe for
    an unrecognized token.
  - Unresolved-required-field count summary is unchanged (not regressed).
  - CLI diagnostic summary output (`render_autofill_summary`) is unchanged.
- **Verification:** `tests/test_application_prepare_formatting.py`,
  `tests/application/autofill/test_service.py` (failure_reason per
  exception type).

---

## Stage 1Q — Target Companies delivery gate + real live-E2E bugfix (2026-09-15/16)

An audit of the already-wired Telegram → Prepare → Autofill pipeline found one
more Target Companies delivery gap (unconfirmed-form embeds), and the user's
first live Telegram "Done reviewing" smoke test found one real bug in the
already-shipped TASK-083 review handoff. This stage fixes both. Two related
items surfaced by this audit are recorded as open below, deliberately without
a task number or a prescribed fix.

### TASK-085 — Target Companies delivery gate for unconfirmed Greenhouse forms

- **Status:** done
- **Depends on:** none (audit of existing Target Companies candidate selection)
- **Goal:** Stop selecting Target Companies vacancies whose Greenhouse-discovered apply URL is not Greenhouse's own canonical hosted job-board shape (custom-domain embeds, e.g. Elastic-style), so `AutofillService` does not run against a page `GreenhouseAdapter.recognize` has not been confirmed to handle. Per `greenhouse_url.py`'s own docstring, a non-canonical URL is *unconfirmed*, not *unsupported* — the embed could still work; the check simply cannot tell without loading the page in a browser.
- **Area:** `app/application/autofill/greenhouse_url.py` (new, pure — `is_canonical_greenhouse_hosted_url`, no browser/LLM/network), `_run_target_companies_cycle` candidate-selection loop (third pre-selection drop, alongside `_target_company_already_delivered` / `_is_unchanged_cached_skip`)
- **Acceptance criteria:**
  - Only `NormalizedVacancy.url` (already known from discovery) is inspected; no extra fetch.
  - Non-canonical-shape vacancies are dropped from that cycle's candidates before analysis/delivery — not persisted as `SKIP`, not delivered, no recommendation recorded. Re-evaluated next cycle.
  - New `dropped_unsupported_form` counter on `_TargetCompaniesCycleResult`, logged in the existing "Target companies: ..." summary line.
  - No company is blacklisted by name; the check is generic to any non-`*.greenhouse.io/.../jobs/<id>` apply URL.
  - `greenhouse.py`/adapter recognition, LinkedIn delivery, and recommendation policy are unchanged.
- **Verification:** Unit tests for `is_canonical_greenhouse_hosted_url` plus a `_run_target_companies_cycle` test asserting the drop and counter.

### TASK-086 — `on_ready` exception-safety fix (real live-E2E bug, Adyen Senior Java Engineer)

- **Status:** done
- **Depends on:** TASK-083, TASK-084
- **Goal:** Fix a real bug the user hit on their first live Telegram "Done reviewing" smoke test: a failed `on_ready` Telegram send (transient network error) let the exception propagate out of `AutofillService.run` before the real browser handoff (`complete_browser_handoff`/`wait_for_review`) was ever entered, so the browser stayed open (nothing called `session.close()`) but the caller's `finally: registry.discard(session_id)` still removed the review-session entry — the next "Done reviewing" tap then answered `NOT_FOUND` for a browser that was genuinely still open.
- **Area:** `app/application/autofill/service.py` (`AutofillService.run`)
- **Acceptance criteria:**
  - The `on_ready(result)` call is wrapped in try/except; a failed notification is logged (`on_ready notification failed for ...; continuing browser handoff`) and never skips `complete_browser_handoff`.
  - A failed notification never causes premature discard of the registry entry while the browser is genuinely open. (The entry still stays registered until `mark_done`/`close_all`; `ReviewSessionRegistry.wait_for_done` does not itself monitor whether the browser process is still running, so a manually closed Chromium window is not detected as such — see the still-open `on_ready` notification-failure gap in `PROGRESS.md`.)
  - No change to the diagnostic CLI's `input()`-based handoff.
- **Verification:** New real-lifecycle test (`tests/test_review_session_real_lifecycle.py`) drives the actual production wiring (`build_prepare_application_service`, not a hand-rolled fake) with only browser/LLM/network faked, so it exercises the same path that failed live. Full suite re-run (see `PROGRESS.md` Build/test status).
- **Note:** The real Adyen/Agoda outcomes referenced in `PROGRESS.md` (successful autofill, manual Submit, email verification) are the user's own reports from the live session, not independently re-verified by an agent.

### Open items from this audit (not fixed here, no task number assigned)

1. **Live retest of the TASK-086 fix.** The exact scenario that found the TASK-086 bug (trigger **Prepare application** on a non-`APPLIED` Target Company Greenhouse vacancy, not Adyen `7342887`; confirm the completion message + "Done reviewing" button arrive, the browser stays open, and tapping the button closes it, including a second/duplicate tap) has **not** been re-verified live since TASK-086 landed — do not claim it has. This needs a human, not an agent.
2. **`on_ready` notification-failure gap.** If the `on_ready` Telegram send fails, the browser handoff and `ReviewSessionRegistry` entry are correctly kept alive, but the "Done reviewing" button/message never reaches the user and there is no retry before `wait_for_review()` blocks. The session is then only released by a delivered "Done reviewing" tap or by `run` shutdown's best-effort `close_all()` — manually closing the Chromium window does not release it. Not designed or started here; no retry-vs-re-request approach is prescribed.

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

TASK-001 through TASK-044, TASK-049, and TASK-051 through TASK-086 are done
(see status fields above and `PROGRESS.md` for the verified build/test
baseline). The remaining tasks are not all done and are not all equally
blocked:

- TASK-045 (Workday watcher) is `blocked` on an explicit user request
  (`OQ-006`).
- TASK-046 (additional autofill adapters) is `todo` with no unmet
  dependency: it depends on TASK-033 (done) and a matching watcher, and
  Lever/Ashby/SmartRecruiters watchers (TASK-042..044) already exist.
- TASK-047 (auto-submit policy computation only) is `todo` with no unmet
  dependency: it depends on TASK-033 and TASK-041 (both done).
- TASK-048 (auto-submit execution) is `todo` and depends on TASK-047 being
  done plus an explicit user request to enable submit.
- TASK-050 (optional vacancy snapshot store) is `todo`, gated on TASK-009's
  live resolve first proving insufficient.

The Stage 1Q audit above also left two items open without an assigned task
number or priority — a live retest of the TASK-086 fix, and the
`on_ready`-notification-failure gap — see that section and `PROGRESS.md` for
details. For a fresh run, read `PROGRESS.md` first for current blockers,
then pick the next eligible task per the workflow and dependency notes
above.
