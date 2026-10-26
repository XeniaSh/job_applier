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
- `office_work.willing: true`
- `privacy_acknowledgement.auto_acknowledge_required: true`
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

## Adyen cross-company follow-up (`7342887`)

Agoda Stage 1 smoke is considered passed. This second run checks that Greenhouse
autofill is reusable on another company.

Selected vacancy (live collect + analysis cache, 2026-09-11):

- Company: Adyen
- Title: Software Engineer (Java) - Unified Platform
- Source: `target_company:greenhouse:adyen`
- External id: `7342887`
- Location: Amsterdam
- Seniority: ordinary IC (`UNKNOWN` — not Lead, Staff, or Junior)
- Recommendation: `APPLY_NOW`

```bash
uv run python -m app autofill target_company:greenhouse:adyen 7342887
```

Do **not** click Submit.

The adapter should fill whatever generic Greenhouse fields exist on this form
(identity, phone, location/country, links, education Master's Degree, resume,
cover letter via Cover Letter **Enter manually**, yes/no and selects that map
from CandidateProfile / ApplicationPolicy). Fields that are absent must be
skipped, not failed.

Adyen-specific custom questions without a profile/policy/override answer should
stay under **Needs review**. Agoda-only questions (engineering blog, Booking
Holdings, Deloitte) are not expected here.

After the first Adyen live run, two generic gaps were fixed in fixtures (do not
treat these as Adyen selectors):

| Field | Expected visible value |
|---|---|
| Are you willing to work 3 or more days per week in the office in Amsterdam? | **Yes** (office/hybrid attendance policy; does **not** mean you live in Amsterdam or have Dutch work authorization) |
| Are you comfortable with a hybrid schedule? / equivalent onsite-hybrid attendance | **Yes** |
| Point of Data Transfer / Acknowledge/Confirm (required applicant privacy / data-transfer notice) | **Checked** (label-backed / hidden native checkbox; not merely clicked) |
| TEXT/SMS interview or recruitment updates | **No** |
| Newsletter / other job openings / talent-pool marketing | **No** / unchecked |
| Work authorization for the Netherlands (or any country not in `work_authorizations`) | Unresolved |
| Criminal history / “I certify the information is true” / other unknown legal declarations | Unresolved / unchecked |

The second live Adyen run confirmed office/hybrid **Yes**. Point of Data Transfer
was still unchecked: Greenhouse exposes it as `multi_value_multi_select` with a
single **Acknowledge/Confirm** option, rendered as a visually hidden checkbox
plus label, not a Yes/No select. Autofill now walks ancestor question copy for
classification, clicks the visible label/option text, and verifies
`input.checked` / `aria-checked` after React settle. If that still fails, the
CLI summary includes a `privacy acknowledgement:` diagnostic block.

Re-run:

```bash
uv run python -m app autofill target_company:greenhouse:adyen 7342887
```

Do **not** click Submit. Coding agents must not run this live headed browser
unless you ask. Report remaining unresolved required fields after visual check.

## Resume attachment false-negative warning (fixed)

Live runs against Elastic (`8315564`), Stripe (`8301379`), and Asana
(`8319678`) logged `Resume was not attached for 'Attach'.` even though the
candidate submitted successfully without manually re-attaching. Confirmed
root cause, from Greenhouse's own public job-application pages (e.g.
`https://job-boards.greenhouse.io/embed/job_app?for=elastic&token=8315564`)
and their bundled `entry.client-*.js`: the current React file-upload widget
removes the original `<input type="file">` entirely once an upload
completes, replacing it with a `.file-upload__filename` readout inside the
same `[role="group"][aria-labelledby="upload-label-{id}"]` wrapper (a
progressbar appears while pending; an `upload-label--error` class / own
`{id}-error` text appears on rejection). The previous verifier
(`_resume_is_attached` in `app/application/autofill/greenhouse.py`) only
ever read the input's `.files`, so it reported a false negative on every
successful modern upload, including after the *second* retry. The actual
reported status of the three live runs above was never captured in logs
available to inspect, so this is a plausible explanation for the warning,
not a confirmed account of what those three runs actually did or reported.

The verifier now also reads the group's own rendered state
(`_resume_widget_status`), scoped strictly to the field's own id — never a
generic `#resume` fallback or another field's group (e.g. Cover Letter,
which renders identical markup) — and falls back to the legacy `.files`
check only when no such group exists at all. This is a confirmed DOM
mechanism, verified against Greenhouse's actual public markup/JS; it does
**not** prove what the three live runs above actually submitted, so treat
the original three incidents as explained, not re-verified.

See `tests/fixtures/autofill/greenhouse_resume_modern_widget.html` and the
`test_modern_widget_*` tests in `tests/application/autofill/test_greenhouse_adapter.py`
for the covered lifecycle (pending, delayed completion, rejection echoing
the attempted filename, wrong/other-field filename, scoping, removal after
success, exact-basename matching).

### Follow-up: failed observation vs. proven absence, and uncertain wording

A fake-DOM regression test found a second false-negative/false-positive
edge in the fix above: `_resume_widget_status`'s own read of the modern
widget could fail (a raised `PlaywrightError`, e.g. a transient page
hiccup) and that was indistinguishable from "no modern group exists at
all" -- which then fell back to trusting the legacy native `<input>`'s
`.files`, possibly reporting a false positive. `_resume_widget_status` now
returns `(status, observation_failed)`; a failed observation fails that
one readback closed (never a whole-Prepare `UNEXPECTED_ERROR`) and leaves
the existing bounded poll in `_wait_for_resume_attached` to retry on the
next tick. See `test_resume_is_attached_fails_closed_on_widget_observation_error_even_with_native_files`
and `test_read_back_returns_none_on_widget_observation_error_even_with_native_files`
in `tests/application/autofill/test_resume_attachment_verification_fake_dom.py`.

The resume diagnostic log lines (`greenhouse_resume stage=upload_attempt` /
`stage=final_verify`) now bound and escape every filename/basename they
print (newlines and overlong names can no longer break a log line into
fake-looking entries) and tag each read with an `evidence_kind` of
`modern`, `legacy`, or `unavailable` instead of a bare `widget_found`
boolean. Bounding is for safe single-line logging only -- a resume
basename can still carry a candidate's own name, so this is never a claim
that the log line is free of personal data.

Greenhouse-only resume warnings that previously asserted a definitive
negative (`Resume was not attached for '...'`, `...please attach it
manually before submitting.`) now say the attachment **could not be
confirmed** and ask the reviewer to check the control's current
attachment/filename before replacing it or submitting -- an unconfirmed
read (upload rejected, final re-check mismatch/disappearance, or a
required resume control that was discovered but never mapped to an
uploadable file) is never the same as having proven the control empty.
Lever/Ashby wording is unchanged. A new warning-level
`greenhouse_resume_selection stage=pre_handoff` log line in
`AutofillService.run` also records the selected variant and configured PDF
basename (never the full path) right after resume resolution, before the
browser even opens, so the selection is visible even on a run that never
reaches the browser at all.

See `tests/application/autofill/test_greenhouse_service_resume_attachment.py`
for real-browser, real-`GreenhouseAdapter` acceptance coverage of all four
forced resume variants, an upload rejected by the widget, a post-upload
removal, and a post-upload filename mismatch.
