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

## Minimal batch priority and current cache policy

Ordering is stable and lexicographic, not a numerical relevance-score change:

1. Supported worldwide remote with explicit Russian-language/CIS employer
   metadata, and structured NZ location with explicit visa support.
2. Other supported worldwide remote and explicit visa-plus-relocation support.
3. Other positive hiring-support evidence.
4. Unknown, country-limited or conflicting eligibility evidence.

Company `priority` A/B/C is secondary within these tiers (unknown values last).
Existing `russian_speaking_signal` and `language` metadata are reused; optional
`cis_employer: true` is explicit employer metadata, default false. None proves
personal eligibility. Missing metadata never changes eligibility into rejection.
No existing company facts were guessed or promoted in the catalog.

Target analysis ranks before existing per-company caps and preserves provider
round-robin tie order. Analyzed delivery is sorted again using extracted evidence.
Regular email/Greenhouse batches also sort before analysis and delivery. Unknown
eligible technical matches remain selectable/deliverable; there is no new queue,
hard quota, deletion or suppression threshold. Existing delivery de-dup and
polling behavior remain. Batches/cycles remain independent: this is not global
preemption, starvation prevention or a shared scheduler. A larger arbitration
mechanism would require a separate proposal, not this implementation.

Telegram cards now distinguish “Manual confirmation required” from posting-side
support, with an explicit reminder to verify personal eligibility. This does not
claim citizenship, residency, authorization or visa success.

For each normally encountered undelivered target vacancy with an unchanged
description hash, reuse the cached **technical evaluation** but recompute current
feasibility, seniority and recommendation in memory. Do this before cached-SKIP
pruning too, so old Engineer I exclusions can recover. No LLM call, cache reset,
version-wide invalidation or cache-file rewrite on a cache hit. New analyses use
the usual cache write. Domain gates run independently of cached positives.
The optional remote-alternative flag round-trips with old records defaulting
false. A geographically limited remote alternative with unknown compatibility
stays manual, not incorrectly rejected for lacking a relocation visa; a mere
remote mention cannot turn no-sponsorship into APPLY_NOW.

Limits: already delivered/applied history is not replayed or resent. A cached
technical IGNORE stays IGNORE; correcting an obsolete technical analysis would
need a separately approved identity/hash-specific review, not a cache reset.
Old card text and identity-only explicit Prepare lookups are not retroactively
rewritten by discovery refresh. Explicit/manual Prepare remains a separate
workflow, SmartRecruiters stays default-off, and submission remains manual.

Final local verification for ordering/cache integration:127 focused tests pass
(priority, cache, runtime, generic pipeline, Wise delivery and formatting), with
the previously noted worker-fixture warning. Core eligibility/cache/Wise checks:
89 pass. Additional provider/config/four-resume/IMAP/Telegram regressions:303 pass.
These runs overlap; do not add the counts as a unique-test total. Ruff and diff
checks pass. No full suite or live session was run.
