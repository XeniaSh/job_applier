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

## Configurable domain policy

`config/discovery_policy.yaml` contains `excluded_domains`, `employer_domains`
and `source_domains`. Supported domain values are `speculative_trading`,
`crypto_exchange`, `gambling`, `defense`, `adult_entertainment`. Empty exclusions
disable this gate. A missing file preserves legacy behavior; a malformed present
file fails visibly. For a custom target-company config, place its policy in the
same directory. Generic regular email/Greenhouse discovery uses the default path.

The approved Exness/Binance/Coinbase identities are configured explicitly;
Binance/Coinbase canonical source identities also cover renamed card labels.
Matching is exact/case-insensitive, never a broad “fintech” substring. Add aliases
deliberately. A narrow English/Russian explicit self-description detector handles
unmapped excluded companies/projects; incidental experience/customer lists,
cyber-defense, ordinary games, banking, payments, lending and infrastructure do
not establish an excluded domain. Ambiguous or unstated industries remain
unclassified; this is not a comprehensive industry classifier.

Known excluded target employers are removed before watcher calls. Vacancy-level
checks precede target analysis/cache reuse and regular email/Greenhouse analysis.
Policy exclusions do not clear analyses, delete history or mark generic jobs seen,
so later policy changes remain reconsiderable. Standalone diagnostic collectors
and explicit old-card Prepare are not redefined as a policy security boundary.
115 focused domain/pipeline/target-runtime tests pass; an existing unrelated
worker-fixture warning about missing resume_profiles.yaml is recorded. Additional
narrow wording/invalid-YAML tests are included. No production policy is activated.
