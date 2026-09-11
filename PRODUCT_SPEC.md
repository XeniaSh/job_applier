# JobApplier — Product Specification

This document is the intended product behavior for JobApplier.
It is the source of truth for autonomous development.

Implementation status lives in `PLAN.md` and `PROGRESS.md`, not here.
Do not treat missing code as a reason to drop a requirement.

---

## 1. Product Goal

JobApplier is a local application for automating job search and application preparation.

Целевая задача приложения:

1. автоматически находить вакансии из поддерживаемых источников;
2. нормализовывать и дедуплицировать их;
3. отфильтровывать нерелевантные вакансии;
4. анализировать подходящие вакансии относительно профиля кандидата;
5. ранжировать их и определять, стоит ли откликаться;
6. отправлять подходящие вакансии пользователю в Telegram;
7. автоматически открывать application form;
8. заполнять известные поля;
9. загружать подходящее резюме;
10. обрабатывать стандартные application questions;
11. передавать заполненную форму пользователю для проверки;
12. в дальнейшем — автоматически отправлять заявку только в случаях, которые признаны безопасными для auto-submit.

The application must reduce manual work as much as it can do reliably.
It must not invent critical data.
It must not silently submit applications with uncertain answers.

---

## 2. High-Level User Flow

Target workflow:

```text
Job sources
    ↓
Vacancy collection
    ↓
Normalization / deduplication
    ↓
Prefilter
    ↓
LLM + deterministic analysis
    ↓
Recommendation / ranking
    ↓
Telegram
    ↓
User chooses vacancy
    ↓
Application preparation
    ↓
Browser autofill
    ↓
Validation
    ↓
READY FOR REVIEW
    ↓
User reviews
    ↓
Submit
```

Later stage:

```text
Browser autofill
    ↓
Validation
    ↓
Safe for automatic submission?
    ├── NO  → user review → manual Submit
    └── YES → automatic Submit
```

Auto-submit is not part of the first autofill milestone.

---

## 3. Current Supported Discovery Flows

The product has two logically separate discovery flows.

### 3.1 LinkedIn / Current Discovery

The current pipeline primarily receives vacancies from LinkedIn job alert emails.

It has its own:

- collection;
- normalization;
- filtering;
- analysis;
- Telegram delivery;
- history / dedup logic.

LinkedIn / current discovery remains an independent flow.

HeadHunter (`hh`) collection may exist as an additional CLI/source in the codebase.
It is not a replacement for LinkedIn / current discovery.
It must not be mixed into Target Companies.

Generic Greenhouse collection via `GREENHOUSE_BOARDS` is also not Target Companies.
It belongs to generic/current discovery if used at all.

### 3.2 Target Companies

Target Companies is a separate bounded context.

Configuration source:

```text
config/target_companies.yaml
```

Target Companies may use different ATS systems.
At the current product stage, the implemented watcher is Greenhouse.

Source format:

```text
target_company:greenhouse:<board>
```

Example:

```text
target_company:greenhouse:agoda
```

Target Companies must not be mixed with the generic Greenhouse collector / `GREENHOUSE_BOARDS`.
These are different use cases.

---

## 4. Vacancy Identity

A vacancy must be uniquely identified by at least:

```text
source + external_id
```

Example:

```text
source=target_company:greenhouse:agoda
external_id=6886113
```

A Telegram message is not canonical storage of a vacancy.

Application / autofill flow must obtain vacancy data from internal JobApplier sources.
It must not parse the text of a Telegram card.

---

## 5. Vacancy Analysis

JobApplier must analyze a vacancy against the candidate profile.

Analysis may include:

- соответствие основному стеку;
- seniority;
- required experience;
- conflicting technologies;
- location;
- remote / hybrid / on-site constraints;
- relocation;
- work authorization;
- visa sponsorship;
- feasibility;
- relevant warnings;
- salary, if it is explicitly stated;
- other meaningful requirements.

The result must allow a recommendation to be determined.

Primary recommendation classes:

- `APPLY_NOW`
- `CHECK_MANUALLY`
- `SKIP`

The exact internal model may evolve, but these categories are the product meaning.

LinkedIn / current discovery may continue to use its existing internal decision labels
(`STRONG_MATCH`, `POTENTIAL_MATCH`, `IGNORE`) as long as product behavior remains clear.
Those labels must not be silently treated as Target Companies recommendations.

---

## 6. Telegram

Telegram is the user interface for reviewing found vacancies.

Separate destinations must be supported at least for:

- LinkedIn / current discovery;
- Target Companies.

One Telegram bot may use several chats.

Telegram routing must not be determined by ad-hoc `source` string checks scattered through the application.
Routing must be centralized.

Telegram is not permanent business-data storage.

---

## 7. Candidate Profile

Autofill requires a single structured Candidate Profile.

The Candidate Profile must be independent of any specific ATS.

Minimum data categories:

### Identity

- first name;
- last name;
- email;
- phone;
- current location;
- country.

### Professional links

- LinkedIn;
- GitHub;
- personal website / portfolio.

### Employment

- current title;
- years of experience;
- notice period;
- relocation willingness;
- salary expectations, if the user explicitly configured them.

### Work eligibility

- citizenship;
- known work authorizations;
- visa sponsorship requirement;
- country-specific eligibility answers.

The application must not infer legally significant answers indirectly if the user did not state them explicitly.

Example of a forbidden inference:

```text
current location = Germany
→ therefore authorized to work in Germany
```

without explicit data.

### Application files

- default resume;
- alternative resumes;
- generated vacancy-specific resume, if used;
- cover letter strategy.

### Sensitive / voluntary data

For example:

- gender;
- ethnicity;
- disability;
- veteran status;
- other demographic information.

These fields must not be auto-filled by default.

Existing markdown / skills / constraint files may remain as analysis inputs.
They do not replace the structured Candidate Profile required for autofill.

---

## 8. Candidate Data Privacy

Real personal data must not be hardcoded in Python source.

Preferred model:

```text
candidate_profile.example.yaml
candidate_profile.local.yaml
```

`candidate_profile.example.yaml`:

- synthetic;
- safe for Git;
- may be read by coding agents.

`candidate_profile.local.yaml`:

- contains real data;
- is not committed;
- must not be given to coding agents unless the task requires it.

Secrets must be stored separately, for example in `.env`.

---

## 9. Protected Local Data

Coding agents must not read real sensitive data if the task does not require it.

Protected data includes:

- `.env`
- `candidate_profile.local.*`
- `resumes/*.pdf`
- `resumes/*.docx`
- production SQLite / DB files
- browser profiles
- cookies
- session storage
- debug dumps containing PII

Agents are allowed to:

- know a path;
- check that a file exists;
- check an extension;
- work with a schema;
- read `.env.example`;
- read the example candidate profile;
- use synthetic resume fixtures.

At runtime the application may use real local files.

Example: browser autofill may upload `resumes/java_backend.pdf`
without the coding model reading the PDF contents.

---

## 10. Resume Handling

Original user resumes are durable user data.

Example:

```text
resumes/*.pdf
```

Cleanup mechanisms must not delete them.

Generated application artifacts are a different class of data.

Example:

```text
data/prepared/
```

If a generated artifact can be reproduced from original resume + vacancy + profile,
it should be treated as disposable generated data unless there is another reason to keep it.

`data/prepared/` must not grow without control.

---

## 11. Durable State vs Generated Data

The project must distinguish:

### Durable state

Data that should be preserved when moving the application:

- application history;
- Telegram delivery state;
- dedup / seen state;
- SQLite databases;
- other persistent state;
- analysis cache, if keeping it is economically useful.

### Generated / rebuildable data

For example:

- prepared PDFs;
- prepared TXT;
- temporary generated cover letters;
- temporary browser artifacts.

### Debug / temp

For example:

- debug dumps;
- temporary HTML;
- saved test responses;
- diagnostic emails.

Generated / debug artifacts are not a required part of a project backup.

---

## 12. Autofill

Autofill is a separate application layer.

It must not be part of a vacancy collector.

General model:

```text
Vacancy
    ↓
AutofillService
    ↓
ATS adapter
    ↓
Browser
```

First ATS adapter: Greenhouse.

Later adapters may appear for:

- Lever;
- Ashby;
- SmartRecruiters;
- Workday;
- custom career sites.

A universal ATS framework must not be over-built prematurely.

---

## 13. Stage 1 Autofill Scope

First milestone:

```text
Greenhouse Target Companies vacancy
→ open form
→ inspect
→ autofill
→ upload resume
→ validate
→ leave open for review
```

One vacancy per run.

Initially the workflow must be CLI-first.

Example:

```bash
uv run python -m app autofill target_company:greenhouse:agoda 6886113
```

Telegram integration is added after the CLI workflow is stable.

---

## 14. Stage 1 Autofill Behavior

When autofill starts, the system must:

1. resolve the vacancy by `source + external_id`;
2. obtain the application URL;
3. load the Candidate Profile;
4. determine the resume;
5. start a browser session;
6. open the application page;
7. discover form fields;
8. classify them;
9. fill deterministic fields;
10. fill known reusable questions;
11. upload the resume;
12. inspect the DOM after filling;
13. determine unresolved fields;
14. show a summary;
15. leave the form open for the user.

---

## 15. Field Classification

Every discovered field must be classified.

Minimum categories:

- `SUPPORTED_DETERMINISTIC`
- `UNKNOWN_REQUIRED`
- `UNKNOWN_OPTIONAL`
- `SENSITIVE_OPTIONAL`
- `UNSUPPORTED`

Examples of `SUPPORTED_DETERMINISTIC`:

- First name
- Last name
- Email
- Phone
- Location
- LinkedIn URL
- GitHub URL
- Resume
- explicit sponsorship answer
- explicit work authorization answer

Examples of `UNKNOWN_REQUIRED`:

- custom required question;
- salary without a configured policy;
- job-specific free-text answer.

---

## 16. Standard Questions

Standard application questions may be filled automatically only when the answer is known from the explicit Candidate Profile.

Example:

```text
Will you now or in the future require visa sponsorship?
```

may be filled from:

```text
requires_visa_sponsorship
```

The answer must not be guessed.

Question:

```text
Are you legally authorized to work in country X?
```

may be filled only when explicit country-specific information exists.

Otherwise: `UNKNOWN_REQUIRED`.

---

## 17. Custom Questions

Stage 1 must not invent answers to arbitrary custom questions.

The system must at least determine:

- label;
- type;
- required;
- options;
- current value.

An unknown required question must be given to the user for review.

Later stages may add LLM generation with a confidence / policy layer.

---

## 18. Greenhouse Adapter

Greenhouse-specific browser logic must be isolated.

The adapter is responsible for:

- recognizing a Greenhouse application page;
- discovering form fields;
- mapping supported fields;
- filling controls;
- file upload;
- reading values back;
- detecting validation errors.

The adapter is not responsible for:

- vacancy recommendation;
- matching;
- Telegram routing;
- deciding the user's work eligibility;
- candidate policy;
- deciding whether auto-submit is permitted.

---

## 19. Browser Automation

The preferred browser automation framework is Playwright, if the project does not already have a suitable abstraction.

Before adding a new dependency, existing dependencies must be checked.

Browser automation must support at least:

- text inputs;
- textarea;
- select / dropdown;
- checkbox;
- radio;
- file upload;
- dynamic fields;
- DOM read-back.

Selectors should use semantic / stable identifiers when possible:

- label;
- accessible role / name;
- name;
- stable ATS metadata.

Fragile hardcoded CSS selectors may be used only locally inside an ATS adapter when there is no better option.

---

## 20. Autofill Validation

A successful `.fill()` does not mean the field is considered filled.

After autofill the system must perform read-back.

Check:

- text input values;
- selected option;
- checkbox / radio state;
- file attachment;
- required empty fields;
- browser validation messages.

The result must reflect the actual form state.

---

## 21. Stage 1 Submit Policy

Stage 1 NEVER submits an application.

Forbidden:

- click Submit
- `form.submit()`
- JS submit
- Enter causing form submission
- automatic confirmation that sends the application

The Stage 1 autofill layer preferably should not expose submit capability at all.

After autofill the state is:

```text
READY_FOR_REVIEW
```

This is given to the user.

The user inspects the form and presses Submit.

---

## 22. Why Submit Is Manual Initially

Manual Submit is not a permanent product limitation.
It is a safety mechanism for introducing autofill.

It is especially important to verify:

- visa sponsorship;
- work authorization;
- expected salary;
- relocation;
- notice period;
- uploaded resume;
- custom questions;
- privacy / legal confirmations;
- unexpected dropdown selections.

After autofill is reliable, manual Submit may be partially replaced by policy-based auto-submit.

---

## 23. Future Auto-Submit

A later stage must allow two results:

- `AUTO_SUBMIT_SAFE`
- `NEEDS_REVIEW`

Auto-submit may be allowed only if:

- all required fields are resolved;
- all answers come from trusted deterministic sources or explicitly permitted generation;
- resume is verified;
- DOM read-back is successful;
- no browser validation errors;
- no CAPTCHA;
- no login / security challenge;
- no unresolved legal / work-authorization questions;
- no unsupported required controls.

If any condition is not met: `NEEDS_REVIEW`.

Stage 1 does not implement this functionality, but its architecture must not prevent adding it.

---

## 24. CAPTCHA, Login and Security Challenges

JobApplier must not bypass:

- CAPTCHA;
- Cloudflare / security challenges;
- OTP;
- email verification;
- authentication barriers;
- explicit bot protections.

When such a state is detected:

```text
NEEDS_MANUAL_INTERVENTION
```

The user receives control of the browser.

---

## 25. Autofill Result

Autofill must return a structured result.

Conceptually:

```text
AutofillResult

source
external_id
application_url
status

filled_fields
unresolved_required_fields
unresolved_optional_fields
sensitive_fields
unsupported_fields

warnings

resume_uploaded
submit_performed
```

For Stage 1:

```text
submit_performed = false
```

Possible statuses:

- `READY_FOR_REVIEW`
- `NEEDS_MANUAL_INTERVENTION`
- `FAILED`

Names may be adapted to the existing domain model.

---

## 26. User Feedback After Autofill

The user must see a clear result.

Example:

```text
Autofill completed.

Filled:
- First name
- Last name
- Email
- Phone
- LinkedIn
- Resume
- Sponsorship

Needs review:
- Expected salary
- Why do you want to work here?

Unsupported:
- Custom multi-select question

Resume:
Uploaded successfully

Submit:
NOT PERFORMED
```

---

## 27. Browser Handoff

After successful Stage 1 autofill the browser must remain open.

The user must be able to:

- visually inspect the form;
- change values;
- fill unresolved questions;
- press Submit.

The CLI must not immediately destroy the browser context.

The application must not leave uncontrolled zombie processes after it finishes.

---

## 28. Error Handling

An error in one application form must not damage the main JobApplier pipeline.

Autofill must return understandable diagnostics.

For example:

- `FAILED`
- `NEEDS_MANUAL_INTERVENTION`
- `UNSUPPORTED_FORM`

Browser automation errors must be localized in the application / autofill flow.

---

## 29. Logging and PII

Logs must be sufficient for diagnosis, but must not needlessly store PII.

May be logged:

- source;
- external_id;
- domain;
- ATS adapter;
- field label;
- field classification;
- filled / skipped / unresolved;
- browser errors.

Must not be logged in full:

- email;
- phone;
- home address;
- demographic answers;
- authentication cookies;
- API tokens;
- secrets.

---

## 30. Existing Functionality Must Remain Stable

Autofill development must not break existing functions:

- LinkedIn collection;
- Target Companies collection;
- vacancy normalization;
- matching / scoring;
- analysis;
- recommendations;
- Telegram routing;
- delivery dedup;
- callbacks;
- application history;
- analysis cache.

Do not change those subsystems only for the convenience of new autofill code if a clean boundary can be added.

---

## 31. Architecture Principles

### Separation of concerns

```text
Collectors
≠
Analysis
≠
Telegram
≠
Application preparation
≠
Browser automation
≠
ATS adapters
```

### ATS isolation

ATS-specific selectors and form behavior live inside the ATS adapter.

### Profile isolation

Candidate data does not live inside the Greenhouse adapter.

### No Telegram as storage

Telegram is UI, not the source of truth.

### Explicit uncertainty

Unknown data is marked unknown.

The system must not silently guess answers just to fill a form.

### Safe automation progression

```text
discover
→ analyze
→ autofill
→ validate
→ review
→ eventually safe auto-submit
```

---

## 32. Testing Principles

Browser automation should mainly be tested against fixtures / mock forms.

A live Greenhouse dependency must not be part of the ordinary test suite.

Minimum tests must exist for:

- Candidate Profile validation;
- question mapping;
- field classification;
- form discovery;
- input filling;
- select / radio / checkbox handling;
- resume upload;
- unknown required fields;
- validation / read-back;
- Submit guard.

An integration test must check:

```text
Greenhouse-like fixture
→ autofill
→ known fields filled
→ resume uploaded
→ unknown question detected
→ submit NOT performed
```

In addition, a manual smoke test is performed on a real Greenhouse vacancy.

---

## 33. Stage 1 Definition of Done

Greenhouse Autofill Stage 1 is complete when:

1. The user can choose one Target Companies Greenhouse vacancy.
2. CLI can start autofill by `source + external_id`.
3. The correct application form opens.
4. Candidate Profile is loaded from local configuration.
5. Deterministic identity / contact fields are filled.
6. Resume is uploaded.
7. Known standard questions are filled only from explicit profile data.
8. Unknown required questions are discovered.
9. Sensitive optional fields are not auto-filled without an explicit policy.
10. After filling, DOM / read-back validation is performed.
11. The user receives lists of filled, unresolved, unsupported, and warnings.
12. Submit is not performed automatically.
13. The browser remains open for manual inspection.
14. CAPTCHA / login / security challenge leads to manual handoff.
15. Existing LinkedIn pipeline works without regression.
16. Existing Target Companies pipeline works without regression.
17. Automated tests cover the main autofill behavior.
18. At least one successful smoke test was performed on a real Greenhouse application form.
19. The user visually confirmed that filled values are correct.
20. Automatic application submission is absent in Stage 1.

---

## 34. Subsequent Product Milestones

After Stage 1, development is expected roughly in this order.

### Stage 2 — Telegram → Autofill

Telegram gets an action to start preparation / autofill for a specific vacancy.

Telegram remains the control UI, but vacancy data is taken from internal storage / source.

### Stage 3 — LLM-assisted custom questions

Job-specific answer generation is added.

Any generated answer must have policy / confidence / review handling.

### Stage 4 — Additional ATS

For example:

- Lever;
- Ashby;
- SmartRecruiters;
- Workday.

These are added through separate adapters / watchers.

### Stage 5 — Safe Auto-Submit

A validation / policy layer is added.

Only an application that fully passes safety checks may be submitted automatically.

All others are given to the user.

---

## 35. End-State Product Vision

The end goal of JobApplier:

```text
automatically discover jobs
        ↓
automatically eliminate irrelevant ones
        ↓
analyze fit
        ↓
rank opportunities
        ↓
prepare application
        ↓
fill application form
        ↓
validate answers
        ↓
submit automatically when safe
OR
ask user only when human judgment is needed
```

The user should not manually do mechanical work that the application can do reliably.

JobApplier must distinguish:

```text
"I know this answer"
```

from

```text
"I can guess this answer"
```

The second must not silently become a submitted application.

---

## 36. Open Questions

These points are required by the product but are not fully specified.
Do not silently invent a product decision. Record the chosen implementation in `PROGRESS.md`.

### OQ-001 — Vacancy resolve source for Stage 1

Autofill must resolve `source + external_id` from internal JobApplier sources, not Telegram text.

It is not specified whether Stage 1 should:

- re-fetch the Greenhouse job via the existing board API;
- load a previously persisted vacancy snapshot;
- use `application_history.url` / analysis cache metadata as a fallback.

### OQ-002 — Application URL vs job posting URL

Greenhouse jobs currently expose `absolute_url` as the vacancy URL.
It is not specified whether that URL is always the application form, or whether a separate apply URL is needed.

### OQ-003 — Structured profile vs existing analysis files

The product requires `candidate_profile.example.yaml` / `candidate_profile.local.yaml` for autofill.

The repository already has:

- `candidate_profile.md` / `profiles/candidate_profile.md` for LLM context;
- `profiles/candidate_skills.yml` for deterministic matching;
- `config/candidate_constraints.yaml` for Target Companies recommendations.

It is not specified when or whether these should be merged.
Until explicitly decided, autofill should add structured YAML without replacing the existing analysis files.

### OQ-004 — Browser keep-open vs process lifetime

Stage 1 must leave the browser open for review and must not leave zombie processes.

It is not specified whether the CLI should:

- block until the user presses Enter / Ctrl+C, then close the browser;
- detach a headed browser and exit the CLI;
- use another explicit handoff command.

### OQ-005 — Resume selection for autofill

It is not specified whether Stage 1 should:

- always use the Candidate Profile default resume;
- reuse `resume_profiles.yaml` + the existing LLM resume selector;
- allow a CLI `--resume` override.

### OQ-006 — Workday

Stage 4 lists Workday as a future ATS.
Current repository agent instructions forbid adding Workday unless explicitly requested.
Workday remains out of Stage 1–3 unless the user requests it.

### OQ-007 — HeadHunter in the product flow

HeadHunter collection exists in the codebase.
Section 3 names LinkedIn / current discovery and Target Companies as the two product discovery flows.
Whether HH should be promoted into current discovery / `run` is unspecified.

### OQ-008 — Salary questions

Salary may be analyzed when explicitly stated in a vacancy.
Salary form fields must not be auto-filled without an explicit configured policy.
The exact Candidate Profile schema for salary expectations is not fully specified.

### OQ-009 — Playwright install / headed browser

Playwright is the preferred framework.
It is not specified whether Stage 1 requires a headed Chromium install as a documented runtime prerequisite, or whether the CLI should fail with a clear setup message.

### OQ-010 — Real `candidate_profile.md` in Git

The product says real personal data should not be committed.
A markdown candidate profile currently exists in the repository.
Whether it should later be moved to a local-only file is unspecified.
Do not relocate or rewrite it as part of autofill unless explicitly requested.
