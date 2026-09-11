# Greenhouse autofill smoke test (manual)

Stage 1 autofill is CLI-first. This runbook is a real Greenhouse Target Company check.
Do **not** bypass CAPTCHA, Cloudflare, login, or other security challenges.

Automated tests use local HTML fixtures only. They do not open live Greenhouse pages.

## Prerequisites

1. `uv sync`
2. `uv run playwright install chromium`
3. `candidate_profile.local.yaml` with real identity data (untracked)
4. A resume file at the profile `application_files.default_resume` path

## Steps

1. List current Greenhouse vacancies **with ids**. The default collect command
   only prints counts. Use `--show-vacancies`:

   ```bash
   uv run python -m app collect-target-companies-greenhouse --company Agoda --show-vacancies --limit 5
   ```

   Copy two fields from one vacancy:

   - `source:` e.g. `target_company:greenhouse:agoda`
   - `external_id:` e.g. `6886113`

   You can also take the numeric id from a Greenhouse URL:
   `https://job-boards.greenhouse.io/agoda/jobs/6886113` → `6886113`.

2. Run autofill with those two values:

   ```bash
   uv run python -m app autofill target_company:greenhouse:agoda 6886113
   ```

3. Confirm the application form opened in a headed Chromium window.
4. Confirm deterministic identity/contact fields are filled.
5. Confirm the resume uploaded.
6. Confirm unknown required questions are listed under **Needs review**.
7. Confirm **Submit: NOT PERFORMED**.
8. Confirm the browser stays open until you press Enter (or Ctrl+C).
9. Visually check filled values, then submit yourself if you want to apply.

## Agoda follow-up (`7044713`)

This vacancy is a **manual Greenhouse form smoke-test fixture**. The public
title is a Lead Software Engineer role, classified `LEAD_MANAGER` with
recommendation `SKIP`. The normal autonomous pipeline does not send it to
Telegram or auto-select it for application. Explicit CLI autofill by
`source + external_id` remains allowed so the rich application form can be
tested.

After Stage 1K (academic level Master's Degree, seniority gating audit), re-run:

```bash
uv run python -m app autofill target_company:greenhouse:agoda 7044713
```

Copy any missing structured fields from `candidate_profile.example.yaml` into
`candidate_profile.local.yaml`, including `application_policy`:

- `relocation.willing: true`
- `default_no_undeclared_affiliations: true`
- `newsletter_opt_in: false`
- `sms_interview_updates: false` (override any previous `true`)
- `prefer_not_to_disclose_gender: true`
- `prior_affiliations` with Deloitte `associated: false`
- `application_source_preference` (Company Website → LinkedIn → Other)
- `question_overrides`: engineering blog = yes; applied + past 6 months = no
- `employee_relationship.has_relationship: false`
- `professional_tech_stack` with only real professional skills, strongest first
  (Java, Kotlin, then other truthful backend skills that may appear on the form).
  Do not list JavaScript unless that is true.
- explicit website that is **not** GitHub/LinkedIn
- years, education (`highest_academic_level: Master's`, `postgraduate_studies_completed: true`, `doctorate_awarded: false`), field of interest, privacy consent if you want those filled

`sensitive.gender` is factual profile data. Application forms now use
Prefer not to disclose and do not send Female.

LLM answers and the cover letter also need a working `.env` LLM configuration.

Visually verify on the live form (do not click Submit):

### Should be populated

| Field | Expected visible value |
|---|---|
| First / Last / Email | Profile identity |
| Phone country selector | Profile country (e.g. Uzbekistan +998) |
| Phone text input | **National number only** (no second +998). Composed visible phone contains the calling code exactly once. |
| Country / Location | Profile country / current location |
| In which country/region are you currently based? | Profile **country** (current location, not citizenship / work authorization) |
| LinkedIn / GitHub | Those URLs only |
| Website / Blog / Other | Explicit website/portfolio, or empty — never GitHub/LinkedIn |
| Years of relevant experience | Matching range from profile years |
| Highest academic level | **Master's Degree**. Semantic profile value is `MASTERS` (specialist / master-equivalent awarded degree). Completed postgraduate / aspirantura without an awarded doctorate must **not** become Doctorate Degree. Diploma must not be selected. |
| Tech stack (top 3) | **Exactly 3** chips when the profile has ≥3 truthful matches among **visible form options**; fewer only when fewer matches exist. Ranked by profile order. For this candidate that is typically **Java**, **Kotlin**, and the next profile skill that exists on the form (often **Spring Boot** if offered). **JavaScript must not** be selected unless the profile lists professional JavaScript. Selecting the next chip must not remove earlier chips. |
| Preferred field of interest | Matching profile interest |
| Based in Bangkok **or** open to relocate | **Yes** (general willingness; does not mean you live in Bangkok) |
| Personal relationship with a current employee | **No**; employee name / how-known stay empty |
| Associated with Deloitte / auditor conflict | Visible **No, I am not…** option (not literal `false`) |
| Presently employed by Booking Holdings (or similar named group) | Visible **No** |
| How did you hear about this job? | First truthful option present: Company Website / Careers Website / Careers Page / Direct Application, else LinkedIn, else Other. Never Referral / Recruiter / Agency / Event / University |
| Gender | **Prefer not to disclose** (or the form's equivalent). Do not select Female. |
| Have you read our engineering blog? | **Yes** (from `question_overrides`) |
| Have you applied … in the past 6 months? | **No** (from `question_overrides`) |
| TEXT/SMS interview updates | **No** |
| Email me about other job openings / newsletters | **No** |
| Privacy / data processing | Only if explicitly `true` in the profile |
| Cover Letter | **Enter manually** in the Cover Letter section (not Resume, not Attach/Dropbox/Google Drive); generated letter visible in the editor (concise, normally 1–2 core technologies) |
| Resume | Attached |

### Intentionally unresolved / untouched

- Expected salary / compensation (unless you later set `fill_salary`)
- Work authorization / visa for a country not listed in `work_authorizations`
- Ethnicity / disability / veteran / other sensitive fields other than the gender policy above
- Employee name and “how do you know them” while relationship is No (inactive, not a review item)
- Website if you only have GitHub/LinkedIn URLs
- How did you hear… if the form offers only forbidden sources
- Cover letter if LLM generation is unavailable or both generation attempts fail validation (the summary warning must name the failing stage)
- Arbitrary custom questions with no profile/policy/override answer
- A third tech-stack chip if the live form does not offer a third truthful profile skill
- Submit must remain unclicked; browser stays open until Enter

Cover-letter generation was verified in automated tests with a mocked LLM:
retry after “too many technologies” includes the validation reason, and a short
valid letter with two core technologies passes validation. Manual-entry UI tests
require clicking Enter manually inside the Cover Letter section and persisting
the editor text. Live generation still depends on a working `.env` LLM configuration.

Visual confirmation of a live form (product spec items 18–19) is a user step.
Coding agents must not run this live smoke test unless you ask.

If a CAPTCHA or login wall appears, take over the browser. Do not attempt a bypass.
