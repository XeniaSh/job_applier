# SmartRecruiters Prepare Stage 3

Status: **implemented and verified offline**, 2026-11-26 Pacific/Auckland.
Not deployed or activated in production. Live acceptance requires separate
approval. No live application, Telegram send, profile/configuration change,
cache reset, database migration, filtering change or submission was performed.
Direct Codex implementation; no Claude invocation, subagent or launcher change.

Supervised acceptance was subsequently attempted and is **blocked by an
anti-bot HTTP 403**, with no candidate writes or attachment upload. The browser
was closed at the user's request. A narrow challenge-detection correction is
verified offline; live acceptance and activation remain pending. See the
[live acceptance checkpoint](smartrecruiters-live-acceptance.md) for per-criterion
results, verified private input paths, isolation and the remaining blocker.

## Registered workflow

Only `target_company:smartrecruiters:wise` (`tcs.wise` in Telegram) is supported.
The button also requires a trusted HTTPS public Wise numeric posting URL matching
the card ID. Credentials, ports, query/fragment, API/referral/application paths,
other employers and mismatched IDs are rejected. The callback repeats this
validation against existing history/card context before starting a worker.

The existing production builder, explicit recommendation/lifecycle gates,
candidate loader, four-profile resume policy, background worker and review
registry are reused:

1. Fetch the requested numeric posting's fresh public detail with the existing
   read-only detail/normalization components. Require consistent active identity,
   explicit Wise company identifier, matching public URL and usable role content.
   No listing scan, cached publication UUID or API `applyUrl` shortcut is used.
   Structured EE/GB country codes become the two observed country names; this
   supplies no candidate residence, authorization or sponsorship fact.
2. Bind the registered adapter to resolved source/ID/URL and normalized context
   before candidate loading or browser creation. Reposted/canonical-ID changes
   fail closed. A loaded bound posting can survive goto timeout but is not
   thereby recognized as a usable application form.
3. Follow only that posting's verified visible Apply link. Never guess the
   publication UUID. Run Stage 1 and Stage 2's bounded Next/Back/final read-back
   flow, preserving all salary, phone, factual, consent and attachment rules.
   Other markets may receive partial preparation, not assumed questionnaire or
   country-specific answer compatibility.
4. Report complete/incomplete, unresolved-required count, selected resume and
   detected challenge reason. Inspect an unconfirmed attachment before replacing
   it. New diagnostics use fixed messages, provider/outcome/counts and public
   identity; no candidate values, response snippets or raw browser errors.
5. Keep the browser on its own Done reviewing session. Failed notifications
   release the browser/registry. Done never marks APPLIED. SmartRecruiters blocks
   the auto-submit branch even when globally enabled; it has no Submit adapter.

The discovery/polling threads and IMAP deadlines/retries/reaping are unchanged.
A blocked Wise Prepare worker does not block collection or another callback.

## Previously delivered vacancies

No cards are resent or bulk-refreshed. Eligible SENT identities use the existing
cached recommendation and APPLIED/SKIP/UNKNOWN rules. Old cards gain the button
when existing reconciliation/Undo rebuilds that same message; a later authorized
targeted reconciliation can refresh one old card. There is no new bulk command.
Old “no autofill for Wise” Info text is replaced only during rendering, without
rewriting caches. Missing recommendations, unavailable postings and reposts do
not bypass existing gates.

## Verification

- **58 new checks:** 28 resolver/identity/error, 16 Telegram/card/policy/polling,
  14 production-wiring integration cases. Real profile loading uses a synthetic
  overlay; API, browser and Telegram network leaves are mocked/intercepted.
  All four distinct PDFs, complete/partial, lost/recreated resume, CAPTCHA,
  unsupported/reposted form, missing profile, failed notification, Done cleanup
  and no-Submit paths are covered. Four affected complete/partial cases were
  rerun after tightening normalized-identity binding.
- **107 existing browser checks passed:** Stage 1/2 plus Greenhouse, Lever and
  Ashby smoke cases. Existing focused service/resolver/formatting (100),
  Telegram/discovery/policy (93), salary/phone/other-provider resolver/submission
  (190 across relevant runs), URL/Wise semantics (70), and IMAP/polling/review
  (66 passing) checks passed. Counts overlap; no full-suite claim. Ruff/diff pass.
- Five old `test_review_session_real_lifecycle.py` cases fail because their
  fixture supplies no candidate overlay in this private-data-free worktree.
  The pre-Stage-3 service independently reproduces profile-load failure before
  browser creation. They were not weakened or repaired. The new isolated tests
  supply a synthetic overlay and exercise the real production lifecycle.
- Assertions that SmartRecruiters is unregistered were deliberately updated
  for the approved feature: Wise-only registration, strict URL eligibility,
  no generic artifact Prepare, and other-employer rejection remain verified.

## Compatibility and commit order

Checked with isolated snapshots of current `main`
(`9f15f185ef4fb1e9ec4b36b4868dbedc888efcd0`, the IMAP reliability fix), then
approved Stages 1 and 2. No main working tree/ref was modified. Required feature
patches reproduce the tested application code and 22 relevant implementation/
test files byte-for-byte, excluding the launcher. Required feature
order, only after integration approval:

```text
1e3835fc61d6059fbc30ab91ed4ac0627e3a8f2c  Stage 1
813862e220a70fde8bfff5819b6d6b43bf3f6105  Stage 2
<Stage 3 hash from completion report>     this change
```

Exclude `57e30f53a1063b8c812548420d79786e1fe32e6c` (launcher diagnostics).
For the full inspection report on current main, optionally apply documentation-
only `8568090` then `7f53124` before the three feature commits. The runtime does
not require those research commits; Stage 3 does not modify their report. Skip
commits already integrated. Do not deploy/restart production before approval.

## Supervised live acceptance — separate approval required

1. Approve one current Wise posting and the data/PDF to use. Confirm the local
   candidate overlay and four configured PDFs exist without copying private
   data into chat or diagnostics. Keep `AUTO_SUBMIT_ENABLED=false` for acceptance
   despite the independent adapter guard. Use an isolated supervised runtime.
2. Verify a canonical Wise card exposes Prepare, while another employer and
   malformed/mismatched URL do not. Reconcile only an approved old SENT message;
   never resend the feed, reset delivery state or clear caches. APPLIED and
   missing/SKIP recommendations must block.
3. Click Prepare; acknowledgement must be immediate. Observe a discovery cycle
   and another harmless callback while its browser is open. Check the requested
   posting/canonical identity and actual publication link. Stop on CAPTCHA,
   login/OTP or other verification; let the human handle it, with no bypass or
   automatic resumption of filling.
4. Check identity, email confirmation, committed residence, paired phone/code,
   selected PDF/attachment and page-one validation. Watch Next/Screening/Back/
   Next and compare persistence. Recreated/unavailable upload proof must produce
   incomplete preparation; inspect the current attachment before replacement.
5. Check authorization/sponsorship/relocation, customer/audit/PWC, salary-band
   and sensitive answers against configured facts and published evidence.
   Unknown/ambiguous questions remain manual; optional marketing/talent-pool
   remains unchecked. Confirm required counts/Telegram heading agree with the
   form. Inspect actual radio/multiselect/clear state, exact survey wording and
   conditional requirements. **Never click Submit or equivalent, press Enter
   on the form, or invoke submission-time validation.**
6. Tap Done reviewing; only its browser must close. Check stale Done, registry
   cleanup and no APPLIED write. Record sanitized outcomes and stop for review
   before production activation or wider rollout.

Live DOM retention, server acknowledgement, custom choices, conditional branches,
anti-bot behavior and exact disability/veteran wording remain unverified. Phone/
shared-code, history and manual-city limits from Stages 1/2 remain. UI proof is
not server acknowledgement.

## Effort and usage

Work began **2026-11-25 23:20:21 UTC**. The completion report records elapsed
wall-clock time including verification/approval waits. Codex token, billing,
quota and active-coding-time counters are not exposed; none are estimated.
Claude invocations and subagents for this task: **zero**.
