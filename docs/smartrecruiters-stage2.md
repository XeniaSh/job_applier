# SmartRecruiters Prepare Stage 2

Status: **locally verified, opt-in and unregistered**, 2026-11-25. No live
application, production activation, candidate-profile change or submission was
performed. Stage 3 is not started. Source of form truth:
[the verified inspection](smartrecruiters-form-inspection.md). Stage 1 remains
the default behavior; Stage 2 requires an explicitly injected
`SmartRecruitersAdapter(prepare_screening=True)`.

## Preparation and safety

- The shared service supplies its existing single-stage fill pipeline as a
  callback. The adapter runs one bounded sequence: candidate preflight → Next
  → Screening preparation → Back → candidate/resume verification → Next →
  final read-back of both stages. It does not refill to conceal lost state.
- Only the observed exact Next/Back native buttons, in their own navigation
  containers and hooks, are eligible. The captured button, accessible identity,
  non-submitting attributes, enabled/visible state and same-publication route
  are checked. Query strings remain after `/screening`. Duplicate, renamed,
  submit-type or otherwise unverified controls are refused. Unexpected routes,
  hydration failure and challenges stop preparation.
- `allow_auto_submit=False` independently blocks the shared auto-submit branch,
  including with an injected Submit adapter and global auto-submit enabled.
  No Submit adapter, provider registration, resolver or Telegram gate is added.
- Required unresolved fields block the initial Next. Screening can remain
  partial while a Back/Next round trip verifies the available answers and
  attachment. Final verification also accounts for questions that appear
  asynchronously, invalid own controls and changes to the observed inventory.
  Missing expected required Wise groups never count as complete hydration.
- A complete result means `READY_FOR_REVIEW`, all required questions resolved,
  and both stages' persistence verified. It still requires human review and
  manual submission. Unsupported initial structure can return `FAILED`;
  preparation/validation/persistence gaps return `NEEDS_MANUAL_INTERVENTION`.
  Stale filled records are demoted into unresolved fields with notes.

## Answers and reusable components

Generic open-shadow DOM discovery, native fields, custom radios and choice
controls are in `smartrecruiters_screening.py`. Dynamic IDs, complete long
labels, own ARIA/component requiredness and scoped unique options are used;
picker search inputs and duplicate hosts are excluded. Committed choices need
own selected-state evidence or the observed own Clear action. Displayed text
alone is insufficient. Unknown widgets and ambiguous options stay manual.

Wise-specific semantics are in `wise_screening.py`:

- Notice period uses the existing candidate fact. Customer, audit (the observed
  12-month scope) and PWC questions need explicit applicable overrides; the
  generic default for undeclared affiliations cannot answer them.
- Work eligibility uses one verified vacancy country and explicit country
  facts. Citizenship, residence, authorization and sponsorship remain distinct.
  Authorization alone supplies no visa subtype or permanent-residence fact.
  A full applicable choice override can supply the missing assertion, while
  contradictory or duplicate country facts fail closed.
- Office willingness alone, even with residence in the same city, cannot prove
  a commutable distance. A full choice override or explicit willingness to
  relocate to this configured destination plus office willingness is required.
- Required privacy follows the existing acknowledgement policy and respects
  explicit refusal/disabled policy. Optional privacy, marketing, recruiting
  contact, talent-pool and unrelated consent are untouched. Observed required
  survey questions use the existing sensitive/decline policy and actual visible
  choices. Unknown/self-description/language/history controls remain manual.
- Salary-band Yes/No requires enabled explicit candidate preferences and a
  single published band with matching currency, period, gross/net, market and
  role. Inclusive in-band preference → Yes; above maximum → No; below minimum
  → manual. Missing, conflicting, component or ambiguous evidence stays manual.
  Published midpoints and benchmarks never establish acceptance. `salary.py`
  exposes its existing strict parser's endpoints; numeric salary precedence
  and behavior are unchanged. Conflicting vacancy/company/title context fails
  closed. The Yes/No question bypasses numeric salary enrichment.

All unsupported fields, including absent factual profile values, are excluded
from generated answers.

## Resume proof and live limitations

All four resume variants retain the existing selection/validation mechanism,
shared basename and distinct selected bytes. Stage 1's digest, one-write sequence,
non-stale own row, Delete action, enabled input and pending/error checks remain.
Opt-in Stage 2 permits read-only proof checks on the same page/publication's
Screening route. Final Next can itself clear data; final verification includes
the retained candidate controls and attachment, not only Screening answers.

If navigation recreates or detaches the candidate widget, the original proof
may become unavailable. A recreated row with the same filename is rejected;
there is no automatic replacement/re-upload. This yields partial preparation,
even if all answers were filled. Server acknowledgement is still unverified.

The fixtures explicitly model persistence and failure behavior synthetically.
They do not prove the live Angular DOM is retained. Fresh Python browser
acceptance, real custom radio/multiselect selection, conditional branches,
upload persistence/error behavior and anti-bot behavior remain unverified.
The report records disability and veteran semantics but not their complete
prompts; fixture prompts for those two are labelled synthetic transcriptions.
Different live wording or selected-state surfaces must remain manual. No real
profile was read to discover which answers will be available at runtime.

## Verification

389 distinct focused checks passed across final/relevant runs:

- 48 Wise semantics and salary-band decisions;
- 40 Stage 2 browser regressions, including all four PDFs, London/Tallinn,
  observed query navigation, safe controls, validation/unknown required,
  no generation, final read-back/loss/invalidity, late/new requirements,
  partial hydration, replaced/lost resume, ambiguous/uncommitted choices,
  consent and the no-Submit trap with auto-submit enabled;
- 64 existing Stage 1 browser checks;
- 142 existing numeric salary/salary-service checks;
- 92 existing service/resolver/submission checks;
- 3 offline Greenhouse/Lever/Ashby smoke checks.

All Stage 2 network traffic is intercepted; other hosts are aborted. Scoped
Ruff and diff checks pass. No full suite was run. One additional old Ashby
notice-period test asserts that the generic mapper cannot fill that question;
it already can in the unchanged pre-Stage-2 classifier. The old behavior was
independently reproduced in memory; no unrelated test repair is included.

## Claude diagnostics and next stage

The prior blocked Stage 2 session exists, but launcher resume requires a
successful record. That restriction was preserved. One fresh Sonnet 5.5/medium
attempt was made, explicitly requiring Read/Edit/Write. It again attempted a
Bash `python3` heredoc, was stopped at the first denial, and produced no edits.
Codex completed implementation directly under the user's explicit exception.
No additional Claude invocation or permission expansion occurred.

New attempt: session `24245b6a-04c8-48e6-b015-1e813295dc50`, 168.5 seconds, one
denial, observed model Sonnet 5.5; effort requested medium, not independently
emitted. Final usage is unavailable. Native message telemetry observed 18,398
output tokens; launcher stream observed 111. Both are incomplete accounting,
not a billing total. The earlier blocked attempt separately reported 32,089
final output tokens. There is no authoritative combined Stage 2 total.
Sanitized diagnostics are in ignored `data/smartrecruiters-stage2-final-usage.json`.

Revised Stage 3 estimate: **2–3 engineering days plus supervised acceptance**
for same-job resolver/source routing, Wise-only registration/Telegram gating,
and review lifecycle integration. Live widget recreation or different choice
state may require a separately reviewed evidence-backed proof adjustment
before activation. Stage 2 builds on approved Stage 1 (`1e3835f`); its feature
does not depend on the launcher diagnostic commit. Stop here pending approval.
