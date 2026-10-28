# Greenhouse country-control evidence and verification

Evidence collected: 2026-10-27. Local review accepted: 2026-10-28.
Full supervised candidate Prepare acceptance remains pending.

## Scoped public evidence

Fresh temporary browser contexts rendered these public forms. Non-GET requests
and form submission were blocked. Only transient diagnostic country choices
were entered; no applicant data, files, credentials or private profile were used.

| Form | Public URL | Country-control purpose |
| --- | --- | --- |
| Elastic 8315564 | https://job-boards.greenhouse.io/embed/job_app?for=elastic&token=8315564 | `input#country` is phone dialing country; export questions separately concern citizenship and legal permanent residency. |
| Stripe 8301379 | https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8301379 | Phone dialing country is separate from residence `question_70013189` and anticipated-work-country checkboxes. |
| Stripe 6176758 | https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=6176758 | Phone dialing country is separate from residence `question_49865578`. |
| Asana 8319678 | https://job-boards.greenhouse.io/embed/job_app?for=asana&token=8319678 | Phone dialing country is separate from citizenship/permanent-residency export questions. |

### Phone dialing country

The generic Country control is nested under
`.phone-input > .phone-input__country > .select > .select__container`.
Its committed display contains a flag and dialing code. Canada and the United
States persisted different flags (`iti__ca`, `iti__us`) but the same `+1`;
there was no committed semantic country-name attribute or hidden value. A live
ARIA status could remain stale. Neither the dialing code nor that status proves
the selected country. Initial unsuccessful first-open Uzbekistan probes and
their screenshots are not evidence of a committed Uzbekistan selection.

The correction identifies this structural phone picker and reports it as
manual/unsupported, preserving native or visual required markers. It never
fills it from residence, citizenship, travel location, employer or a generated
answer. The actual phone-number input and legacy `.iti` handling are unchanged.

### Residence instructions and committed state

Both Stripe residence questions explicitly instructed selecting Other when the
country was absent at collection time. Their own help is a sibling of `.select`
inside `.field-wrapper`, several ancestors above the input. `aria-describedby`
points to a transient Select... placeholder, which previously masked that help.

Selecting Brazil then typing an unlisted country caused React to unmount its
single-value display while retaining Brazil internally. The generic reader
returned the typed search as a selection. Its page-wide confirmation could also
accept another widget's matching selection after a swallowed target click.

The correction reads only the target's visible committed display with an empty
search input, or proven local semantic metadata for a legacy collapsed display.
It rejects placeholders, typed search, hidden displays, read errors, bare dialing
codes and unrelated selections. Confirmation compares the exact normalized
pre-click option label. Named countries and Other must both survive blur and a
300 ms polling window; failed verification stays manual. Independent read-back
uses the same local evidence and does not cache a prior successful selection.

Help extraction ignores placeholder/live-region nodes and requires an associated
label and a field scope with no other independently editable controls. Other is
allowed only by that question's explicit instruction; missing residence facts
are never inferred. Export, citizenship, permanent-residency and work-country
questions retain their separate semantics and existing mapping.

## Clearing and live limitations

A scoped UI keyboard sequence could clear the observed residence widget, but
this patch does not automate keyboard clearing. The existing local clear-button
path remains; an unproven clear emits the manual-correction warning. A null
read-back alone is not proof that a stale value was cleared.

A country-only public Stripe check on 2026-10-27 observed Brazil and instructed
Other each persisting after blur, correct own-help extraction, and the required
manual phone picker. Clearing remained unconfirmed with a warning. These are
public-control observations, not acceptance of a private candidate's Prepare
session. The bounded automated persistence check cannot certify indefinitely
stable state or downstream application data. Submission remains manual.

## Independent automated verification

Codex executed the tests; Claude's environment denied test execution. Relevant
checks were run once per changed area, with smaller reruns after review fixes.

| Coverage | Result |
| --- | --- |
| Country fake-DOM regressions, including read/blur/wait errors, exact matching, legacy committed metadata, stale clearing and scoped Other | 32 passed across focused runs |
| Real Chromium fixtures, including rendered help scoping, swallowed clicks, unmounted search, blur/async reversion, Other reversion and full service manual-picker boundary with a generator | 15 passed across focused runs |
| Classifier tests (unchanged after their earlier verification) | 22 passed |
| Country/citizenship mapping tests | 15 passed |
| Existing phone/React compatibility subset | 7 passed |
| Native/custom-select and service choice diagnostics | 43 passed; one pre-existing failure below |
| Existing unconfirmed-country service warning | 1 passed |
| Ruff on all changed Python files; `git diff --check` | Passed |

All three country failures from the draft checkpoint were resolved. The unrelated
`test_fill_and_confirm_diagnostic_scoped_to_the_menu_choice_kinds` failure also
reproduced against an isolated committed HEAD archive: its academic-level
expectation disagrees with unchanged service diagnostics. It was left out of
scope. No full test suite, production cache replay or notification run occurred.

## Supervised acceptance checklist

1. In one normal Prepare session, distinguish the phone dialing-country picker
   from residence; complete the phone picker manually and check the dialing code
   against the actual configured phone number.
2. Check residence against an explicit, current profile fact. After tabbing away
   and waiting for the form to settle, the selected country must remain correct.
   Other requires the question's own explicit instruction. Missing or unverified
   facts and any stale-selection warning require manual correction.
3. Review the remaining fields and finish with Done reviewing. Do not press
   Submit during this verification.
