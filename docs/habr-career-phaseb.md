# Habr Career Phase B

The user's revised acceptance supersedes the Phase A stop: unknown country or
contract eligibility is CHECK_MANUALLY, not a reason to block source integration.
No production activation, production database modification, Telegram send or LLM call is
authorized during development. No Prepare functionality is added.

## Bounded textual enrichment — verified

`app/collectors/habr_details.py` retrieves only canonical public HTTPS Habr
posting URLs. The observed page publishes JobPosting JSON-LD with a matching
numeric `identifier.value`; only its `description` is extracted. No surrounding
salary benchmarks, recommendations, external Apply links, browser session,
authentication, CAPTCHA workaround or OCR is used.

Text >=160 characters with no image markup is FULL. Short/mixed descriptions
are PARTIAL; absent/mismatched structured data or failed access is UNAVAILABLE.
This is a text-completeness label, not proof of country eligibility or complete
employer disclosure. RSS remains available for every failed/partial detail.
Explicit expired `validThrough` dates are retained for exclusion by the adapter.

Public text is contact-redacted and cached atomically by ID. Full-text TTL is
24 hours by default; partial/unavailable entries retry no sooner than one hour.
Ordinary requests are at least one second apart, with 15-second network/read and
elapsed-stream bounds and a 1 MB response cap. No redirects or automatic retries.
401/403/429 stops additional detail requests, with a one-hour cooldown preserved
across restart even when the RSS interval is shorter. Cache reads
do not consume the configurable detail request budget.

The saved Phase A cohort was reused without querying RSS again. Twelve canonical
posting pages were fetched under isolated `data/habr-pilot/` state: **11 FULL,
one PARTIAL, zero unavailable, zero access errors**. Complete-text yield is
**91.7%**; all 12 retain at least RSS/partial text. IRLIX exposes only 70 characters
in structured description, so remains incomplete; image processing was not used.
Public measured text remains ignored in `phaseb-enrichment-measured.json` and
`phaseb-details.json`. No private candidate or production state was read.

Initial offline verification: ten synthetic tests pass for correct-ID extraction,
HTML isolation, short/image/mismatched/challenge fallback, expiration, persistent
cache, throttling, redirects/access failures and URL rejection.

## Regular discovery — implemented, default off

`HABR_DISCOVERY_ENABLED=false` is the default. Only explicit opt-in registers
`HabrDiscovery` in the existing generic pipeline worker. RSS/detail collection,
analysis and delivery stay off the Telegram poller; Habr failures are isolated
by the same collector boundary as existing sources. Settings/YAML failures disable
Habr with a sanitized error class; other configured collectors continue. The
existing IMAP reliability implementation is unchanged.

The adapter reuses Phase A title/grade/freshness/domain screening before fetching
detail. Python/ML/QA/mobile titles do not gain admission. Only inexpensive JVM
candidates are enriched; the default budget is five detail GETs per hourly RSS
poll, with cache hits free. Failed, short, image-based or missing details retain
RSS and PARTIAL analysis. FULL analysis means the matched textual JobPosting is
present, not that every requirement is known.

Technical analysis uses the existing `VacancyAnalyzer`, prompt and matching/
resume mechanisms, with Russian text passed intact and the actual completeness.
The existing `TargetCompanyAnalysisCache` implementation is reused in the Habr
state directory. Its Habr key includes relevant title/company/location/text and
completeness, excluding publication-date bumps and query overlap. Cached technical
evaluations are reused; current shared eligibility policy is recomputed. Changed
text/coverage can cause a new analysis. A failed analysis backs off for the source
poll interval across restart, without caching upstream exception payloads.

Habr uses shared delivery history as its durable delivered/terminal dedup boundary.
It does not mark a successful analysis seen before delivery: a send failure retries
the cached evaluation rather than losing the vacancy or calling the LLM again.
The existing history/analysis-cache storage format is reused; there is no schema
migration or historical replay. Content hashes and identity remain distinct.
No application history is deleted and no existing cache is reset.

Three public cache/state files under configurable `state_dir` (default `data/habr`)
hold extracted details, technical evaluations and the current pool/poll/batch
checkpoint. Writes are atomic. A malformed discovery checkpoint fails visibly,
not silently reset. Initial eligible cohort IDs and selected batch persist across
restart. The default first-cohort batch cap is **three per source polling interval**;
sent/below-technical-threshold/policy-SKIP members do not starve the remainder. A
minimum-match change can reconsider cached technical evaluations without an LLM.
Once that
cohort is resolved, normal `batch_limit` is five. A send retry uses the same batch;
it does not refill three more cards every generic pipeline cycle.

Source logs report raw/unique/candidate, FULL/PARTIAL/unavailable/expired, request/
cache-hit/feed-error and selected-batch counters. No HTML bodies, headers, recruiter
contacts, private candidate details or upstream exception text enter these logs.

## Eligibility, Telegram and four resume profiles

The existing domain policy runs before analysis, including cached positives.
The shared factual/sponsorship/seniority rules remain authoritative; Habr adds
conservative parsing of observed Russian restrictions and contract wording.
Product users' citizenship, employer addresses, phone codes and temporary location
are not candidate work rights. Generic remote is never promoted to worldwide
from an LLM field alone. Moving assistance cannot replace required visa sponsorship;
negated remote wording cannot invent an alternative route.

Technically eligible Habr cards are **CHECK_MANUALLY**, with source-specific Info:
permitted countries or country limits, contract/entity confirmation, incomplete
description and an undisclosed client/domain where indicated. Explicit ТК РФ
wording is preserved as a contract fact, not interpreted as citizenship/residence
or a blanket cross-border rejection. Unknown entertainment/client domain is manual;
it is not automatically called gambling. Explicit excluded domains/other shared
hard blockers remain excluded. Existing Telegram minimum technical match applies.

Canonical source `habr` maps to callback code `hb`. Cards expose Applied, Skip and
Open; archived cards expose Undo. Actual temporary-DB callback tests cover both
terminal actions and Undo, preserving CHECK_MANUALLY/notices after reconstruction.
Forged Prepare Application, artifact-prepare, resume and cover callbacks are rejected
before entering preparation services. No Habr resolver, upload, browser or Submit
path exists. SmartRecruiters Prepare remains unchanged/default-off.

All four shared resume variants round-trip through cached evaluation and manual
delivery/history: `java_backend`, `ai_backend`, `java_backend_nz`, `ai_backend_nz`.
NZ selection still depends on vacancy evidence under the existing resume policy;
AI selection still requires the existing substantive-duty evidence. No profile,
private overlay, PDF, factual claim or salary mechanism was changed.

## Before/after on the saved 12 JVM candidates

This is a **controlled STRONG_MATCH technical assumption**, not an LLM run,
historical score, actual delivery or guarantee of fit. All 12 go from RSS/PARTIAL
with unknown geography to CHECK_MANUALLY after applying current policy. Enrichment
changes evidence, not unknown eligibility into confirmation. No explicit domain
exclusion is established by these 12 texts; undisclosed clients still need review.
The local replay is `data/habr-pilot/phaseb-policy-replay.json` (ignored).

| Posting / employer | After enrichment | Additional evidence / manual qualification |
| --- | --- | --- |
| [1000165768 — БФТ](https://career.habr.com/vacancies/1000165768) | FULL; CHECK_MANUALLY | Java/Spring project and ТК РФ contract; abroad compatibility unconfirmed |
| [1000166832 — Лига](https://career.habr.com/vacancies/1000166832) | FULL; CHECK_MANUALLY | JVM requirements available; countries/entity unconfirmed |
| [1000163623 — Росгосстрах](https://career.habr.com/vacancies/1000163623) | FULL; CHECK_MANUALLY | Remote/hybrid alternatives; countries and contract route unconfirmed |
| [1000150058 — SENSE](https://career.habr.com/vacancies/1000150058) | FULL; CHECK_MANUALLY | Java duties and ТК РФ contract; company-level abroad claim does not settle this posting |
| [1000165261 — Лоция](https://career.habr.com/vacancies/1000165261) | FULL; CHECK_MANUALLY | Description offers hybrid; reconcile with RSS remote badge |
| [1000168906 — CHOICEIT](https://career.habr.com/vacancies/1000168906) | FULL; CHECK_MANUALLY | Java/Kotlin backend; undisclosed B2B entertainment client/domain and contract route |
| [1000169480 — RWB](https://career.habr.com/vacancies/1000169480) | FULL; CHECK_MANUALLY | Duties available; RSS grade absent, geography/contract unknown |
| [1000165674 — Яндекс Финтех](https://career.habr.com/vacancies/1000165674) | FULL; CHECK_MANUALLY | Duties available; no inferred abroad permission |
| [1000170786 — ITK Academy](https://career.habr.com/vacancies/1000170786) | FULL; CHECK_MANUALLY | General selection for multiple employers, not a confirmed concrete opening; actual employer and remote terms need clarification |
| [1000163829 — Яндекс 360](https://career.habr.com/vacancies/1000163829) | FULL; CHECK_MANUALLY | Java B2B duties available; countries/entity unconfirmed |
| [1000167904 — Extyl](https://career.habr.com/vacancies/1000167904) | FULL; CHECK_MANUALLY | Mandatory ЦФТ specialization and outstaff client; actual technical fit may fail |
| [1000164618 — IRLIX](https://career.habr.com/vacancies/1000164618) | PARTIAL; CHECK_MANUALLY | Structured metadata/short text only; actual duties remain unavailable |

Initial delivery estimate with shipped settings: **0–3 cards on the first poll**,
depending on actual technical analysis/minimum match and shared policy. At most
three cards from the first cohort per hourly polling interval; the saved 12-title
cohort is an upper bound before real analysis, not 12 guaranteed good vacancies.
No LLM analysis was performed to manufacture a more precise forecast. New stock,
duplicate history, exclusions and genuine skill gaps will change actual volume.

## QSOFT and AGIMA: fit assessment, no admission change

The prior public descriptions establish substantive backend/AI work at
[QSOFT/TEAMLY](https://career.habr.com/vacancies/1000169407) and
[AGIMA](https://career.habr.com/vacancies/1000170599); no repeated research was
needed. The **versioned** `candidate_profile.md` and `profiles/candidate_skills.yml`
explicitly do not establish commercial Python backend, Redis or production LLM
API experience. The candidate's internal n8n/Jira/LLM automation and JVM backend
experience are useful adjacent evidence, not substitutes for those requirements.
No private candidate configuration or resume PDF was inspected.

QSOFT requires confident asynchronous Python/FastAPI backend and practical RAG/
agent integration; AGIMA explicitly requires at least three commercial Python
years and practical agent/LLM work. These are substantial mandatory-stack gaps,
so neither should be a primary automatic match on current facts. Employer training
or a JVM variant would need explicit confirmation; no outreach was performed.
The Java-first filter remains unchanged. The justified separate proposal is a
small audit-only label for substantive AI-backend roles with mandatory non-JVM
stack gaps, with the two public cases as controls; it must not automatically admit
Python/ML jobs or switch resumes/claim missing commercial experience.

## Configuration, installation and future activation

`HABR_DISCOVERY_ENABLED=false` keeps every collection/cache/network path inactive.
`HABR_DISCOVERY_CONFIG_PATH` defaults to `config/habr_discovery.yaml`. Review that
YAML before activation: queries/pages/freshness, hourly interval, five detail
requests, 24-hour TTL, one-second request spacing, initial cap three and normal
batch five, plus source-local `state_dir`. Existing Telegram destination and match
threshold are reused; no new bot/channel, scheduler or queue is introduced.

After review/installation and explicit operator activation, use the ordinary
single-instance startup (instruction only; **not run during development**):

```sh
HABR_DISCOVERY_ENABLED=true HABR_DISCOVERY_CONFIG_PATH=config/habr_discovery.yaml uv run python -m app run
```

Restart the existing instance through its usual ownership/lock procedure; do not
start a second poller against the same bot/state. Keep `AUTO_SUBMIT_ENABLED=false`.
Do not reset cache/checkpoints/history to enable this source. Disabling the flag
stops source collection; existing manual Applied/Skip/Undo buttons remain usable.

## Tests, compatibility and limitations

The focused regression run passes **464 tests**, covering Habr RSS/details/runtime,
shared pipeline, Telegram callbacks/Undo/formatting, all four resumes, eligibility,
Greenhouse/Lever/Ashby/SmartRecruiters discovery, default-off Prepare and IMAP.
Final details/source-policy/generic-pipeline review passes **53 focused tests**
(overlap, not additive), including persisted denial cooldown, negated remote,
product-country context and below-threshold backlog progress.
Ruff and diff checks pass. No full suite, live Telegram, production LLM or live
application test was run.

A separate `test_run_cli` run has **one pre-existing failure**:
`test_worker_shutdown_logs_single_pending_preserved_message` lacks
`resume_profiles.yaml` in its temporary cwd; 21 worker-thread warnings in that run
have the same fixture issue. The exact failure reproduces on an untouched archive
of current main `dbaaa0a`. Assertions and production behavior were not changed to
hide it; the 464-test result is a different, explicitly scoped run.

Required cherry-pick order from main `dbaaa0a44ddb5a412837f9c684d919cd91c6f2fc`:

1. `99247d4a2e684f1c34f478d2b1113ebb386a9787` — Phase A pilot prerequisite.
2. `3580027aa875af8755f8ae62d9c4090fad444803` — bounded description enrichment.
3. Phase B integration commit (hash in the delivery report).

Verify by applying ordered patches to a temporary main archive and comparing
affected files/tests, without changing main or the production checkout. Earlier
policy/SmartRecruiters commits are already in this main; launcher changes are
excluded. If Phase A is already installed, omit only that first prerequisite.

Remaining limitations: public structured-data markup can change; FULL is a
conservative length/image heuristic rather than a completeness proof. Cached
details can lag up to their TTL; publication dates can be bumped. RSS windows are
bounded and not exhaustive. Cross-source copies without exact canonical links
still need manual dedup. Technical cache retains the latest identity/hash, so
changed coverage can legitimately reanalyze an unsent posting. Source batches
share the existing analysis budget and collection worker; no global scheduling
or starvation guarantee was added. Country/entity and client/domain questions
remain manual, with no immigration inference or submission path.
