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

Initial verification: 78 tests passed across eligibility conflicts, feasibility,
recommendations and Wise integration. Cache-derived policy refresh is now also
implemented, as described below. Final geography review additionally scopes
"only hire" to geographic context (not "only hire great engineers") and recognizes
negated work-from-anywhere claims. The final relevant core run passes 87 tests.

## Seniority correction

Explicit Senior now precedes the generic Engineer I band. Intern, management,
staff/principal and explicit junior guards still precede Senior. The committed
public-title fixture replays all 49 Wise pilot titles and ten sanitized NZ titles:
12 Wise titles change JUNIOR => SENIOR; the other 37 and all ten NZ titles remain
unchanged. This is not 12 proven lost notifications or a retrospective score.
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

Final local verification for ordering/cache integration: 127 focused tests pass
(priority, cache, runtime, generic pipeline, Wise delivery and formatting), with
the previously noted worker-fixture warning. Core eligibility/cache/Wise checks:
89 pass before the final geographic wording correction; its relevant core run
passes 87 tests. Additional provider/config/four-resume/IMAP/Telegram regressions:
303 pass.
These runs overlap; do not add the counts as a unique-test total. Ruff and diff
checks pass. No full suite or live session was run.

## Before/after evidence and historical replay

| Evidence | Before | After / reason |
| --- | --- | --- |
| Generic worldwide wording plus explicit country-only hiring | APPLY_NOW | CHECK_MANUALLY: preserve restriction; no known candidate-country compatibility |
| Relocation money, explicit no sponsorship, required relocation visa, no supported alternative | APPLY_NOW despite adverse feasibility | SKIP: moving assistance cannot supply the required visa route |
| Limited remote alternative with unknown personal compatibility | Could be rejected as though every route required a relocation visa | CHECK_MANUALLY: retain alternative without claiming eligibility |
| Approved Coinbase/Binance/Exness identity with a positive technical evaluation | Could proceed to analysis/delivery | Domain gate excludes independently of cached technical fit |
| Genuine Engineer I without explicit Senior | JUNIOR / excluded | Unchanged |

Offline replay used the saved 49 public Wise pilot descriptions, with a **fixed
synthetic STRONG_MATCH evaluation** before and after to isolate deterministic
policy. It did not retrieve historical scores, run an LLM or measure production
notification outcomes. Baseline code was the strategy commit `2a8356e` before
these implementation changes. Of the 12 corrected Senior labels, 11 resulting
recommendations change SKIP => CHECK_MANUALLY:

| Wise posting ID | Team/title suffix |
| --- | --- |
| 744000158265079 | Pricing Core |
| 744000158109379 | Risk Control & Governance |
| 744000158101614 | Account Integrity |
| 744000157420370 | Product Eligibility |
| 744000157078266 | Wise for Enterprise — Banks |
| 744000156752984 | Risk Control & Governance |
| 744000150224729 | Personal & Business Pricing |
| 744000141541057 | Fincrime Requests |
| 744000139108668 | Servicing Platform |
| 744000127948469 | Receive |
| 744000108484376 | Payment Processing Platform |

The twelfth, `744000152127649` (Business Onboarding Verification), remains SKIP
because of explicit sponsorship/relocation refusal. The other 37 Wise
recommendations are unchanged in this controlled replay.

All ten sanitized persisted NZ categorical cases retain their recommendations
when replaying their saved flags and original titles. Only four have available
same-hash full descriptions; the other six cannot support a full-text historical
revalidation claim. Actual numeric production scores and historical CV choices
are unavailable. Replay artifacts remain ignored under
`data/strategy-research-20261126/`; the committed public-title fixture supplies
the reproducible seniority regression. These results do not estimate precision,
recall or the number of missed real applications.

## Reproducible verification

Focused ordering/cache/runtime/formatting run: **127 passed**, with the existing
`test_linkedin_only_run_skips_target_companies_without_chat_id` background-thread
warning: its temporary cwd lacks `resume_profiles.yaml`. The worker/profile path
is unchanged; the warning is not presented as a clean full-suite result.

```sh
.venv/bin/pytest tests/company_watch/test_delivery_priority.py tests/company_watch/test_analysis_cache.py tests/test_pipeline_runtime.py tests/test_run_target_companies.py tests/test_smartrecruiters_wise_integration.py tests/test_telegram_formatter.py -q --tb=short
```

Final geographic correction: **87 passed** across eligibility conflicts,
feasibility, recommendations and Wise integration. Separate seniority/replay
run: **70 passed**. Final domain unit run: **30 passed**.

Provider, configuration, four-resume, IMAP and Telegram run: **303 passed**:

```sh
.venv/bin/pytest tests/company_watch/watchers/test_greenhouse_watcher.py tests/company_watch/watchers/test_lever_watcher.py tests/company_watch/watchers/test_ashby_watcher.py tests/company_watch/watchers/test_smartrecruiters_watcher.py tests/company_watch/test_config_loader.py tests/company_watch/test_analysis_cache.py tests/company_watch/test_candidate_constraints.py tests/test_smartrecruiters_prepare_flag.py tests/test_resume_policy.py tests/test_resume_profiles.py tests/test_imap_runtime_reliability.py tests/test_telegram_client.py -q --tb=short
```

These overlapping focused runs are not a unique-test total. No full-suite,
production, external application or live Telegram acceptance was performed.
Claude was not invoked. Codex token/quota telemetry is unavailable; no usage
estimate is asserted.

## Main compatibility and exact integration order

Verified against read-only current main
`715279fe9cba35597eef21b3bfb98fdab111f9c3`: apply the ordered patches to an isolated
`git archive` copy, then compare every affected file with the tested worktree.
No checkout, main-ref update or operation on the production checkout is involved.
The approved SmartRecruiters implementation and default-off flag are already
present in this main; do not cherry-pick the original Stage 1–3 hashes again.

Required order from that main (first two are previously accepted maintenance
and documentation prerequisites, followed by this approved phase):

1. `f4a17af93e280803adf4c7452a1c0eb87c9239f3` — private-independent lifecycle fixtures.
2. `cbf9e783c9cd7b270b385a02398ae0d3d9a9f15d` — original audit/discovery documents and roadmap.
3. `2a8356e8fe896ca9f3f99891a677087387d18e9b` — revised strategy documentation only.
4. `c0519280329e768693f30c1e1ed25e8f9183b5b2` — eligibility conflict correction.
5. `0c1de6834473ab0d089bc5607c39264ae3a3827a` — Senior/Engineer I precedence and replay fixture.
6. `bff5d65e7ae5226235bac22826568a16360463cc` — configured domain exclusions.
7. `286325bff48b54b182fc07bff04595645f4cf135` — stable priority, card notice and cached-policy refresh.
8. `929421d728475f42924a1dbd78ffb1664f5f6028` — geographic wording regression correction.
9. This final documentation commit (hash in the delivery report).

Exclude launcher changes, including `57e30f5`. Older optional NZ research/export
commits are historical context, not runtime prerequisites. Local commits do not
activate these policies in production.

## Remaining scope and next proposal

Geographic/domain detectors are deliberately narrow, not comprehensive legal or
industry classifiers. A richer country/contract/provenance schema remains a
separate task. Batch priority cannot guarantee that lower-volume viable employers
are discovered or analyzed before a different cycle/provider batch. Company
outcome attribution and response/interview tracking are still absent; counts
alone cannot prove improved employment outcomes. F2 lead-warning and F3/F4 AI
evidence issues remain separate audit follow-ups.

Next recommended implementation is checkpoint A of the
[bounded Habr RSS proposal](habr-career-rss-proposal.md): read-only, discovery-only
pilot first, with Telegram/runtime integration separately approved. No Habr, HH,
new employer collector, deployment or production activation is included here.
