# Employment discovery policy — first implementation phase

All changes in this document are local/offline implementations awaiting review
and production activation. No cache reset, bulk LLM reanalysis, private candidate
configuration update, production DB change or application submission is included.
SmartRecruiters Prepare remains default-off. Four resume families and selection
rules are unchanged. No Habr/HH/new collector implementation is included.

## Eligibility conflict correction

Specific extracted work modes and explicit geographic limits defeat generic
worldwide marketing. Unparsed location/timezone limits remain manual. Location
metadata alone is never an authorization fact. Moving assistance cannot override
an explicit sponsorship refusal when sponsorship is required.

APPLY_NOW describes supported vacancy-side evidence, never a legal guarantee.
CHECK_MANUALLY retains unknown country/authorization compatibility and complex
alternative routes. SKIP distinguishes an explicit refusal of required support
from unknown support. Existing technical, language and seniority blockers remain.
An unrestricted worldwide remote route can remain supported without employer
visa sponsorship; conflicting local-rights text stays manual. No country rights
are inferred from residence, employer address, phone or nationality.

Intentional behavior changes in old tests: required local rights or permanent
residence with unknown sponsorship/personal status now stays manual rather than
asserting the candidate is ineligible. The Wise synthetic rights/transfer-only
case becomes CHECK_MANUALLY; its integration tests still assert exact delivery,
dedup, restart, retry, cache and enabled/disabled Prepare behavior. Explicit Wise
refusals remain SKIP. Fixtures were not changed to invent eligibility evidence.

Focused verification: 78 tests pass across eligibility conflicts, feasibility,
recommendations and Wise integration. Ruff passes. Cache-derived recommendation
refresh will be handled with the policy integration changes later in this phase;
the technical evaluation must remain reusable without an LLM call.

## Seniority correction

Explicit Senior now precedes the generic Engineer I band. Intern, management,
staff/principal and explicit junior guards still precede Senior. The committed
public-title fixture replays all49 Wise pilot titles and ten sanitized NZ titles:
12 Wise titles change JUNIOR => SENIOR; the other37 and all ten NZ titles remain
unchanged. This is not12 proven lost notifications or a retrospective score.
Genuine Engineer I stays JUNIOR and its existing exclusion test remains intact.
70 focused seniority/recommendation/conflict tests pass; Ruff passes.
