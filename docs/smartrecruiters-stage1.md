# SmartRecruiters Stage 1 (Wise, page one only)

Status: **accepted locally and unregistered**, 2026-11-25. Codex independently
verified 22 URL checks and 64 offline browser checks across focused runs, plus
143 existing regressions. Scoped Ruff and diff checks pass. Live acceptance is
pending. Evidence: `docs/smartrecruiters-form-inspection.md` (agent-work `7f53124`).

One existing review-handoff fixture fails with the pre-change service too: its
fake browser session lacks the already-required `is_usable` argument. The wider
run also encountered its missing resume-fixture configuration. No unrelated
test-harness repair or full-suite claim is included.

## Scope

- `app/application/autofill/smartrecruiters_url.py`: pure Wise-only URL checks (exact https host,
  no port/credentials/fragment, only the observed `dcr_ci=Wise` query).
- `app/application/autofill/smartrecruiters.py`: `SmartRecruitersAdapter`, page one of the
  OneClick form through open shadow roots. Optional `expected_posting_id` is for future integration.
- Public posting -> application: only a visible, same-tab, same-company link validated against the
  canonical numeric posting is followed; the click is bound to that exact anchor and re-checked
  just before it. The publication UUID is never derived from the numeric ID.
- Filled: first/last name, email + confirmation (both own inputs validated and equal), City
  (exact single option, persistence sampled after blur), phone (dial code resolved from the
  displayed options and the configured international number, exact national digits), LinkedIn,
  and the Resume through the existing four-variant selection service.
- Page-one-only preparation **always** reports `NEEDS_MANUAL_INTERVENTION` with a fixed warning
  (`partial_stage_reason` hook in `AutofillService`), so it can never be auto-submitted.
- Not done: Next/Back/Submit, Screening, consent/import interaction, Telegram button, resolver,
  default adapter dispatch. There is no production button.

## Explicitly unsupported (stay manual)

- Phone: shared or missing calling codes (e.g. `+1`, `+44`), non-international numbers, a dial
  selection that does not persist (closed own selector plus slotted flag/code sampled repeatedly),
  and any formatting that changes the typed digits.
- Facebook, X, Website, Message, Experience/Education history, City "manual residence" mode,
  duplicated or unknown controls: classified `UNSUPPORTED`, never filled and never given
  generated answers; a required one stays in the unresolved list.
- Reusing an existing resume row in place (stale row) is treated as failure, not success.

## Resume proof limits (UI-upload only)

The adapter proves the UI side only: the selected file's SHA-256 + size (computed in the browser
from the File that was written by this attempt, compared with the bytes read from the selected
path), exactly one new write per attempt (the change hook is installed once per input), a new
non-stale row with its Delete action, enabled input, no pending/error markers, and no later write
or row replacement across consecutive samples. Server acknowledgement was **not** observed.
The upload is refused (no file write, no proof) while the widget's input is disabled or a
pending/error marker is present, so a prior in-flight attempt cannot settle under a later one.
Re-selecting the identical file is a browser no-op (no `change` event): it fails closed and
never claims a fresh attachment.
Basename equality alone is never proof: all four variants upload under one shared, configurable
basename (the synthetic tests only happen to use `Resume.pdf`; the real name is configuration).

## Synthetic fixture

`tests/fixtures/autofill/smartrecruiters_page_one.html` mirrors observed selectors; async delays,
reverts, mangling, and upload modes are labelled `[synthetic]`. Tests intercept every
`jobs.smartrecruiters.com` request and abort any other host (no outbound network).

## Remaining effort (rough estimate, engineering days)

The Screening-stage evidence is already complete in the full investigation
(`docs/smartrecruiters-form-inspection.md`); no further mandatory pre-implementation live
investigation is required. Supervised acceptance of Stage 1 is still pending.

- Stage 2 (Screening, roughly 2-4 days): evidence-backed field mapping, Back/Next handling
  without Submit and a screening completeness check.
- Stage 3 (roughly 1-2 days, plus supervised acceptance): resolver, source routing, Telegram
  button gating for supported Wise vacancies only, supervised no-Submit smoke check.

These may change if the actual complexity turns out different.
