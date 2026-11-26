# SmartRecruiters supervised live acceptance checkpoint

Date: **2026-11-26 Pacific/Auckland**. Result: **blocked by anti-bot access;
acceptance incomplete**. The user requested browser closure after manual
intervention was unavailable. The isolated browser and acceptance process are
closed. No further live attempt was made after that request.

Baseline: Stages 1, 2 and 3 (`1e3835f`, `813862e`, `bb16b18`). Direct Codex;
no Claude invocation, subagent, production activation or change to main.

## Approved vacancy and effective private inputs

The user approved [Wise Senior Software Engineer - Rules Platform, Tallinn,
posting 744000159595669](https://jobs.smartrecruiters.com/Wise/744000159595669-senior-software-engineer-rules-platform).
The live public detail returned HTTP 200, `active=true`, company identifier Wise
and the matching public posting URL. Published compensation is
5750–7083.33 EUR gross/monthly; the role describes Java/Kotlin backend work and
relocation/visa support. These are employer facts, not candidate work rights.
The older search result, posting 744000156787429, points to the current ID;
the test used the current identity without relaxing repost protection.

The user explicitly authorized reading these private files in the main
checkout; none of its Git refs, production databases or runtime state was used:

- Candidate: `~/Projects/personal/job_applier/candidate_profile.local.yaml`.
- Resume configuration: `~/Projects/personal/job_applier/resume_profiles.local.yaml`.
- All four configured PDFs resolve under that checkout's
  `resumes/<profile>/` directories and pass structural PDF validation.
  Profile IDs: `java_backend`, `ai_backend`, `java_backend_nz`, `ai_backend_nz`.

The candidate overlay passes schema validation and has the standard identity
inputs. Values and PDF contents are omitted. The shared resume policy selected
**java_backend** from the live vacancy; no override/default substitution was
used. Before/after checksums confirm the two configuration files and all four
PDFs are unchanged. They were read through explicit paths and were not copied
or committed. Production `.env`/credentials were not loaded.

## Isolation

An ignored diagnostic harness invoked the real Telegram callback dispatcher,
production service builder, fresh public resolver, registered adapter and
headed BrowserSession. Only acceptance configuration and Telegram transport
were replaced; no synthetic candidate was used in live testing.

- Own temporary delivery SQLite/cache directory, deleted at process exit.
- Local Telegram transport only: no real `getUpdates`, Telegram sends, token
  access or competing production poller. The temporary recommendation was
  CHECK_MANUALLY for the explicitly approved test.
- Separate process-local review registry and fresh nonpersistent browser
  context. No connection to production browser profiles or sessions.
- Child-process `AUTO_SUBMIT_ENABLED=false` plus an explicit false runtime
  getter; the SmartRecruiters adapter independently forbids auto-submit.
  Test-only guards also blocked native/final submission mechanisms. Observed
  guard counters stayed zero. No Submit/Enter action was issued.
- Ordinary page-load requests occurred; no candidate field write or resume
  upload occurred. No private profile values, request bodies, challenge tokens
  or raw network URLs were recorded in diagnostics.

The computer-use host was unavailable. Browser observations below came from
the instrumented application BrowserSession, not a connected Chrome session.

## Actual live observations versus offline evidence

| Criterion | Actual live observation | Result / remaining verification |
| --- | --- | --- |
| Public identity/URL validation | Fresh Wise detail and numeric posting match; public posting document HTTP 200 | Passed for approved posting; negative URL cases remain offline evidence |
| Application navigation | Reached a trusted same-company base application URL; application document HTTP 403 | Navigation reached the destination, but no usable form |
| Candidate information and phone country code | No form root or fields available; no candidate writes | Blocked; not verified live |
| Resume selection | Real shared policy chose java_backend; all four configured PDFs validated | Selection passed; attachment not attempted |
| Resume attachment and server acknowledgement | No file uploaded | Both unverified live; UI proof would still not imply server acknowledgement |
| Navigation to Screening | Not reached | Blocked; offline Stage 2 tests only |
| Wise screening mappings | No questions available | Blocked; offline semantic tests only |
| Salary range acceptance | Published band confirmed; no screening answer written | Decision handling not verified live |
| Unknown work authorization/sponsorship | No answers written or inferred | Treatment not verified live; offline factual safety tests only |
| Candidate/answer/attachment persistence through Next/Back | No transition attempted | Blocked; offline persistence tests only |
| Partial/manual reporting | Pre-fix code returned FAILED/UNSUPPORTED_FORM, not completion; challenge was missed | Narrow fix below; corrected handoff verified offline, not re-tested live |
| CAPTCHA/anti-bot | HTTP 403, script from ct.captcha-delivery.com failed with connection reset; no form root; title smartrecruiters.com; notice asked to enable JS/disable ad blocker | Requested manual intervention; user could not proceed and requested closure; no bypass |
| Callback responsiveness | Prepare acknowledgement returned in 7–8 ms in the isolated callback path | Passed locally with real dispatcher and local transport; not live Telegram delivery latency |
| Concurrent discovery | While Prepare worker was active, real Wise watcher with page_limit=10/max_pages=1 returned one normalized vacancy in 2814 ms | Bounded public discovery passed; not a complete production discovery/IMAP cycle |
| Concurrent polling | Existing poll function processed a local-transport Skip callback against temporary state in 5 ms while Prepare worker was active | Isolated polling passed; production polling was untouched and unobserved |
| Browser cleanup through Done | Pre-fix failure closed its own browser; final process exited and private-input checks passed | Failure cleanup observed live; Done handoff only verified offline |
| Application submission | No fields/resume written, no final action issued, guard counters zero; result submit_performed=false | No submission performed |

## Narrow correction and regression verification

Root cause: the existing challenge detector covered common visible CAPTCHA
iframes, markers and titles. This observed block instead used a
`captcha-delivery.com` script, a generic domain title and an empty/instructional
body. Its loader failed before creating a visible CAPTCHA. Hydration therefore
expired and the service classified the page as unsupported.

The SmartRecruiters detector now checks script/iframe **resource hostnames** for
`captcha-delivery.com` or its subdomains. It returns only the existing fixed
`captcha` reason, never resource URLs/query tokens. Lookalike hosts or a domain
name merely embedded in another URL's query do not match. Hydration stops on a
detected challenge so the existing manual-review/Done lifecycle can run without
candidate writes or automatic resumption. No security restrictions, browser
identity or permissions were changed.

**15 focused offline browser regressions passed (23.64 s)**: five new exact-host/
sanitization cases, two new registered posting/application challenge-shell
handoff cases, existing challenge handling, complete preparation, partial
answers, lost attachment and stage challenge cases. Production-wiring tests
assert no candidate writes/upload on challenge, no submission, Done cleanup
and no APPLIED write. Nine affected cases passed again after strengthening the
hydration short-circuit, incomplete-heading and actual Done-handoff assertions
(3.76 s). The Chromium availability probe emitted a Playwright shutdown warning;
pytest exited zero. Scoped Ruff and diff checks pass. No full suite run and
no post-fix live attempt. The correction's commit hash is in the completion
report; this document is part of that commit.

## Readiness and next supervised step

**Not ready for production activation.** The profile/resume inputs are available
and isolation is established, but usable live form access is a blocker. All
candidate, attachment, Screening and persistence criteria still require live
acceptance. Existing offline tests do not close those gaps.

After the user can obtain normal manual access in an approved isolated browser,
resume the remaining Stage 3 acceptance steps under explicit supervision. Do
not bypass challenges, spoof browser identity or automatically resume filling
after human verification. Reconfirm current posting identity and stop before
Submit. No deployment/restart is authorized by this checkpoint.

Required feature order on the previously checked main baseline: Stage 1
`1e3835fc61d6059fbc30ab91ed4ac0627e3a8f2c`, Stage 2
`813862e220a70fde8bfff5819b6d6b43bf3f6105`, Stage 3
`bb16b18a1bd8213986e0e62a26543c65ba910eb2`, then this checkpoint/fix commit.
Exclude launcher `57e30f53a1063b8c812548420d79786e1fe32e6c`.
