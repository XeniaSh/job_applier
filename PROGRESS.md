# JobApplier — Development Progress

Last updated: 2026-09-14

This file is local persistent agent/runtime state. It is gitignored and must
never be committed. The public template is `PROGRESS.example.md`.

Update it after every implemented task.

---

## Current overall status

**Stage 1 Greenhouse autofill is implemented against local fixtures, including
Stage 1G–1N live-smoke follow-ups.**

**Agoda and Adyen have both passed real Greenhouse Stage 1 smoke testing.**
Adyen `7342887` is the cross-company validation of the generic Greenhouse
adapter (not an Adyen-specific implementation).

Adyen vacancy:

- Title: Software Engineer (Java) - Unified Platform
- Source: `target_company:greenhouse:adyen`
- External id: `7342887`

Live Adyen result (user, 2026-09-11):

- Office/hybrid required question autofilled **Yes**
- Point of Data Transfer privacy acknowledgement **checked** after the checkbox fix
- User manually clicked Submit
- Greenhouse accepted the completed form with no missing/invalid required fields
- After Submit, Adyen presented an **email verification challenge** (code sent to
  the candidate email). This is a post-submit verification/challenge boundary,
  **not** an autofill failure. Do not automate or bypass it at this stage.

This confirms successful real end-to-end Greenhouse autofill through form
validation and Submit → verification challenge.

**Lever / Ashby / SmartRecruiters Target Company watchers exist but are not wired into `run`.**

Discovery and analysis remain in production use. Autofill is a separate CLI path.

```bash
uv run python -m app autofill target_company:greenhouse:adyen 7342887
```

---

## Task currently being worked on

NONE — Stage 1 Greenhouse live smoke is complete for Agoda and Adyen.

## Last successfully verified task

TASK-075 — Greenhouse cross-company Stage 1 smoke **PASSED** on Adyen
`7342887` Software Engineer (Java) - Unified Platform (user confirmation,
2026-09-11). Office/hybrid Yes and Point of Data Transfer checked; Greenhouse
accepted Submit; post-submit email verification is a challenge boundary, not an
autofill failure.

Previously: TASK-077 privacy checkbox interaction (fixtures); TASK-076
office/hybrid policy; TASK-074 / Agoda Stage 1 smoke passed sufficiently
(user confirmation, 2026-09-11).

---

## Completed this run

Greenhouse cross-company Stage 1 smoke **PASSED** on Adyen `7342887`.

| Item | Result |
|---|---|
| Vacancy | Adyen Software Engineer (Java) - Unified Platform, `target_company:greenhouse:adyen` / `7342887` |
| Office / hybrid | Required question autofilled **Yes** |
| Privacy acknowledgement | Required Point of Data Transfer / Acknowledge/Confirm **checked** after TASK-077 |
| Submit | User clicked Submit manually. Greenhouse accepted the form; no missing/invalid required fields reported |
| After Submit | Adyen email verification challenge (code sent to candidate email). Post-submit verification/challenge boundary, **not** an autofill failure. Do not automate or bypass |
| Cross-company | Agoda and Adyen both passed real Greenhouse smoke tests; Adyen validates the generic adapter |

Earlier TASK-077 interaction notes (still true of the implementation):

| Item | Result |
|---|---|
| Hidden checkbox | Greenhouse `multi_value_multi_select` with one Acknowledge/Confirm option, rendered as a visually hidden `input[type=checkbox][name=question_*[]]` plus label |
| Interaction | Visible option text, associated label, `role=checkbox`, then `locator.check()` only if a real enabled checkbox is visible. Success is `input.checked` or `aria-checked="true"` after React settle |
| Diagnostics | Failed privacy fills can still add a `privacy acknowledgement:` summary block |

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

### Needs user action

Stage 1 definition of done items 18–19 (Agoda + second-company Adyen live smoke)
are **done**. Keep `candidate_profile.local.yaml` aligned with
`candidate_profile.example.yaml` for future runs (agents must not read the local
file). Chromium: `uv run playwright install chromium` if needed.

Do **not** automate or bypass the post-submit Adyen email verification challenge.

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
| TASK-077 privacy checkbox interaction tests | `uv run pytest tests/application` — 184 passed (Playwright fixtures included) |
| Live Adyen Stage 1 smoke (`7342887`) | **PASSED** (user, 2026-09-11): office/hybrid Yes; Point of Data Transfer checked; Greenhouse accepted Submit; email verification is a post-submit challenge, not an autofill failure |
| Live Agoda Stage 1 smoke | **PASSED** (user confirmation, 2026-09-11) |

---

## Important implementation discoveries

1. Autofill lives under `app/application/autofill/`. Collectors and company watchers do not import it. Playwright is lazy-imported from the CLI command body.
2. Stage 1 resolve uses `DefaultVacancyResolver` → Greenhouse board API. Generic `greenhouse` / LinkedIn / HH sources fail as unsupported. Board slug comes from `target_company:greenhouse:<board>`, so Adyen is `adyen`.
3. Demographic gender is filled from ApplicationPolicy (`prefer_not_to_disclose_gender`), not from `sensitive.gender`. Ethnicity / disability / veteran remain `SENSITIVE_OPTIONAL` unless `fill_sensitive_fields` is true.
4. Submit is not part of the adapter API. Fixture JS asserts the Submit button was not clicked.
5. Academic matching uses whole-word / canonical tokens (`MASTERS`, `BACHELOR`, `DOCTORATE`, `DIPLOMA`).
6. Cover-letter generation takes `vacancy_context(ResolvedVacancy)` (title/company/description from the selected job). It is not Agoda-hardcoded.
7. Company-specific facts belong in profile YAML: `prior_affiliations` (e.g. Deloitte), `question_overrides` (e.g. engineering blog). The Greenhouse adapter only discovers, interacts, and reads back.
8. Office/hybrid is ApplicationPolicy, not location. A Yes on “3 days in the Amsterdam office” does not fill current location as Amsterdam and does not answer Dutch work authorization.
9. Required privacy acknowledgements are classified separately from marketing. “Acknowledge/Confirm” alone is not enough; it must sit in privacy-notice / data-processing / data-transfer context.
10. Latest `data/target_company_analysis_cache.json` (196 entries) has 36 Adyen rows. The only Adyen `APPLY_NOW` Java IC role is `7342887` Software Engineer (Java) - Unified Platform, Amsterdam.
11. Adyen Point of Data Transfer is Greenhouse API type `multi_value_multi_select` with a single Acknowledge/Confirm option (`question_*[]`), not a Yes/No select. Job-board DOM is a visually hidden checkbox + label, often without `.field` / `[class*=question]` wrappers.
12. After Greenhouse accepts Submit, some employers (Adyen on `7342887`) present an email verification challenge. That is a post-submit verification/challenge boundary, not an autofill or required-field failure. Do not automate or bypass it in Stage 1.

### Adyen vacancy ranking (TASK-075)

Preferred for the second smoke test:

1. **Software Engineer (Java) - Unified Platform** `7342887` — Amsterdam, ordinary IC, Java, platform/distributed-systems relevance, cached `APPLY_NOW`. Best match for “would the product actually apply?”
2. Senior Software Engineer (Java) - Financial Products `7573921` — Amsterdam, SENIOR, Java, cached `CHECK_MANUALLY`.
3. Java Software Engineer - Payments `7369512` — Amsterdam, ordinary IC, payments, cached `CHECK_MANUALLY`.
4. Java Software Engineer - CX `6761873` — Amsterdam, ordinary IC, cached `CHECK_MANUALLY`.
5. Software Engineer (Java) - Screening Team `7342892` — Amsterdam, ordinary IC, cached `CHECK_MANUALLY`.

Rejected for this smoke: Staff/Lead/Junior; Bengaluru/Madrid/Chicago/San Francisco/Brazil roles; SKIP recommendations.

---

## Blockers / open questions that need user input

| ID | Required user action |
|---|---|
| Stage 2 | Explicitly allow Telegram autofill messages/buttons |
| OQ-006 / TASK-045 | Request Workday if wanted |
| TASK-048 | Request auto-submit if wanted. Adyen smoke Submit was **manual**; post-submit email verification must not be bypassed |

---

## Next step

Stage 1 Greenhouse live smoke is complete for Agoda and Adyen. No further Adyen
headed re-run is required for TASK-075.

Next product work waits on an explicit request (Stage 2 Telegram autofill,
Workday, or auto-submit). Do not automate the Adyen post-submit email
verification challenge.
