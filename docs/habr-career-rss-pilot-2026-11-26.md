# Habr Career RSS pilot — 2026-11-26

Historical checkpoint: superseded by the user's revised Phase B acceptance and
[completed default-off integration](habr-career-phaseb.md). Unknown geography
remains CHECK_MANUALLY and no longer blocks source integration.

**Original Phase A checkpoint: Phase B not started; the practical quality
gate is not met.** Supported RSS access works, but this sample does not establish
a new viable cross-border JVM/backend lead. The shortlist is useful for manual
qualification; it is not evidence for automatic regular delivery yet.

No LLM, candidate configuration, production DB/history/cache or Telegram service
was accessed. No browser application session, subscription, authentication,
Prepare, application submission or production activation was performed. Claude
was not invoked. The previously approved four resume profiles, shared policy
rules and SmartRecruiters Prepare configuration were not changed.

## Implementation and supported access

- `app/collectors/habr_rss.py`: bounded read-only RSS client, safe XML parser,
  normalized PARTIAL records and deterministic evaluation.
- `scripts/pilot_habr_rss.py`: standalone diagnostics and a new isolated JSON
  report. Does not import runtime Settings/CLI, DB/storage, LLM or Telegram.
- `config/habr_rss_pilot.yaml`: four configurable queries (Java, Kotlin,
  JVM backend, AI backend), remote filter, page/request/response/item/freshness
  bounds. It is **not read by regular discovery** and cannot activate Habr.

The public search advertises [Java RSS](https://career.habr.com/vacancies/rss?page=1&per_page=25&q=Java&remote=1).
The new [Kotlin search](https://career.habr.com/vacancies?q=Kotlin&remote=1) also
advertises the corresponding `q=Kotlin&remote=1` RSS link. The client uses that
ordinary endpoint and supported `q`, `remote`, `page` parameters. No internal
JSON endpoint, scraper login, challenge workaround or ATS destination is used.

Each request has a 15-second timeout plus an elapsed-stream deadline; read
blocking is also timeout-bounded. Up to eight queries, three pages each, 2 MB
per response and 200 items are hard maxima; shipped bounds are four queries,
two pages, 1 MB and 100 items. No redirects or automatic retries. HTTP 401/403/429
stop the source, including remaining queries; other query errors remain isolated.
Diagnostics contain fixed error codes, status, query index and counters, never
response bodies, headers or exception strings. DTD/entities, malformed/non-UTF-8
XML, untrusted posting links and mismatched IDs fail closed. Public contact
emails/phone strings and HTML scripts are removed from snippets.

Identity is `(habr, numeric vacancy ID)` with canonical HTTPS Habr posting URL.
Queries/page bumps do not create another identity. Cross-query duplicates merge
their query provenance; cross-employer/source copies are not automatically merged
on titles. No persistent dedup state or seen/cache entries are written by Phase A.

## Measured feed results

Final network snapshot: **2026-11-26 08:44 UTC**, eight RSS GETs. Earlier bounded
development snapshots had 190 unique IDs; the final snapshot had 191. These are
changing public feed windows, not separate new-to-user cohorts. Final evaluation
was replayed offline from the same saved contact-redacted snippets after adding
the observed outside-RF restriction wording; no additional feed request was needed.
Artifacts remain ignored under `data/habr-pilot/`:
`measured-20261126.json` and `reviewed-20261126.json`.

| Query | Raw items | New IDs within each page | Result |
| --- | ---: | --- | --- |
| Java | 100 | 50, 50 | Two-page bound reached |
| Kotlin | 35 | 35, 0 | Second page empty |
| JVM backend | 100 | 50, 50 | Two-page bound reached |
| AI backend | 100 | 50, 50 | Two-page bound reached |

Pagination therefore works in this sample. The bound is not proof that the
three larger searches are exhausted. Multiword queries are broad: `AI backend`
returned non-AI and non-backend records; AND semantics are not established.
Do not treat query membership as technology evidence. The previously observed
`per_page=25` response size of 50 was not assumed to mean 25 here.

| Metric | Observed |
| --- | ---: |
| Raw records / unique canonical IDs | 335 / 191 |
| Duplicate observations | 144 (42.99% of raw) |
| Invalid identities / feed errors | 0 / 0 |
| Plausible JVM development titles after title/grade/role/domain checks | 12 |
| AI/backend candidates admitted by existing deterministic title rules | 0 |
| Additional LLM/developer titles requiring manual scope review | 4 |
| Of those, full public descriptions reviewed with substantive AI/backend duties | 2 |
| Explicit unrestricted international remote support in RSS | 0 |
| Remote with unknown geography / with explicit country limits | 189 / 2 |
| JVM shortlist with unknown geography | 12 / 12 |
| Explicit negative role/grade/policy matches | 115 |
| Excluded by current rules, including four AI review discrepancies | 119 |
| Domain-policy exclusions established by available RSS evidence | 0 |
| Missing company / publication date | 0 / 0 |
| Missing location / salary | 77 / 132 |
| Full descriptions supplied by RSS | 0 |
| Within configured 30-day freshness window | 191 |

The remaining 60 records outside the shortlist/AI-review/explicit-negative groups
are other or unclassified titles, not presumed suitable. Two outside-RF records
are a .NET/React Tech Lead and an AI Security Researcher, outside the target JVM/
AI-backend shortlist. Their country limits remain manual; they are not worldwide
permission or evidence of the candidate's residence/authorization.

Publication timestamps span 2026-10-29 to 2026-11-26. Parsed timezone-aware dates
are normalized to UTC. Missing/invalid dates remain unknown; stale and future
dates fail freshness. Dates may represent republishing rather than original creation. The sample
does not measure daily volume, actual new vacancies, interviews or responses.
Only two of the 12 JVM candidates publish salary in RSS; net/gross basis is not
stated. Similar-specialist estimates are never an employer salary offer.

## Manual shortlist and qualification limits

These are canonical public vacancies, **not verified candidate-eligible offers**:

| Employer | Posting | Qualification still required |
| --- | --- | --- |
| CHOICEIT | [Senior Java/Kotlin Developer](https://career.habr.com/vacancies/1000168906) | Full text confirms JVM backend work and remote, but country/contract route is unstated. Hidden B2B entertainment client: confirm domain; do not invent a gambling exclusion or assume acceptable industry |
| РОСГОССТРАХ | [Senior Java-разработчик](https://career.habr.com/vacancies/1000163623) | RSS remote and senior tag; cross-border route and full duties unverified |
| Яндекс | [Java/Kotlin, Финтех](https://career.habr.com/vacancies/1000165674) | Senior tag; actual countries, employment rights and payroll route unknown |
| Яндекс | [Java, B2B Яндекс 360](https://career.habr.com/vacancies/1000163829) | Same geography/contract caveat; ordinary business infrastructure is not excluded fintech |
| RWB | [Java, Wildberries Банк](https://career.habr.com/vacancies/1000169480) | Grade absent; remote badge alone does not permit abroad work |
| IRLIX | [Middle+ Java Backend Developer](https://career.habr.com/vacancies/1000164618) | Public description consists of images; no full-text skills/country validation or OCR performed |
| SENSE | [Java разработчик](https://career.habr.com/vacancies/1000150058) | Prior strategy already documented company-level abroad claim versus Russian-contract uncertainty; not researched again. RSS salary ceiling 450k RUB does not resolve it |

Other JVM shortlist IDs: `1000165768` (БФТ), `1000166832` (Лига Цифровой Экономики),
`1000165261` (Лоция), `1000170786` (ITK academy), `1000167904` (Extyl). Extyl requires
ЦФТ-platform specialization and project/outstaff qualification; do not count it
as a general-purpose JVM match without checking that requirement.

The two reviewed AI-role descriptions are substantively relevant to the revised
AI/backend search: [QSOFT/TEAMLY](https://career.habr.com/vacancies/1000169407)
describes asynchronous service APIs and production RAG/agent integration;
[AGIMA](https://career.habr.com/vacancies/1000170599) describes agents and production
backend systems. Both require Python experience and leave abroad terms unknown.
Candidate Python/AI facts and fit were not inspected or invented. The current
shared Java-first title filter rejects these titles; this is recorded as a
classification gap rather than calling the roles intrinsically unsuitable.
The two other review IDs `1000170429`, `1000170305` have not been full-text vetted.

## Why Phase B stopped

Supported access passes. **Useful cross-border supply and reliable automatic
qualification do not yet pass.** All 12 JVM candidates require geography/contract
confirmation; the new detail checks exposed an undisclosed client domain and
image-only duties. The AI/backend examples also demonstrate a real gap in existing
title admission. Sending snippets straight into regular analysis would lose
important country/domain/skill evidence, while the generic runtime currently
passes FULL to its analyzer. That would reproduce the low-value unknown-eligibility
delivery this strategy is intended to reduce.

No shared filter, pipeline, callback, database or scheduling architecture was
changed to work around these findings. No integration commit exists. Habr has
no runtime registration, Telegram mapping or Prepare route, so production is
disabled by absence of integration, not by an undocumented environment flag.

Smallest next step: manually qualify a few shortlist postings for actual supported
countries and lawful employment routes; design a bounded trusted-page detail
pilot for text-only descriptions. Separately regress the two demonstrated
AI/backend title discrepancies before proposing a narrow evidence-based admission
change. Do not broaden all Python/AI filtering automatically. Resume Phase B when
these checks establish viable leads and a tested PARTIAL/full-text contract.
The user already conditionally approved integration if that gate passes; this
report does not treat missing evidence as approval to bypass the gate.

## Running and configuration

From the installed checkout, with ordinary dependencies present:

```sh
.venv/bin/python scripts/pilot_habr_rss.py --output data/habr-pilot/my-new-report.json
.venv/bin/python scripts/pilot_habr_rss.py --query Java --query Kotlin --max-pages 1 --output data/habr-pilot/my-short-report.json
```

`--config` accepts an explicit pilot YAML path; flags override its queries/pages.
The output must be a new file inside this checkout's `data/habr-pilot/`; existing
files cannot be overwritten. No `.env`, credentials or candidate profile is
needed. Freshness is reported, not silently used to suppress records. A valid
empty RSS feed is distinct from timeout/403/malformed XML in the report.

Installing the pilot commit adds **no regular discovery or Telegram activity**.
There is no production activation command for Phase A. Do not point it at any
production state directory or substitute a runtime/secret YAML for pilot config.

## Offline verification and compatibility

57 focused synthetic tests pass: identity/URL validation, unsafe XML, payload
caps, dates/freshness, metadata/salary/contact handling, misleading title/grade
matches, AI audit discrepancies, country limits, all five shared domain policies,
pagination, cross-query dedup, timeouts/deadlines/access failures and import/output
isolation. The broader focused run passed 214 tests (including 56 of those before
the last outside-RF regression was added), covering feasibility, seniority,
domain rules, all four resume variants/profiles and SmartRecruiters default-off.
These runs overlap. Ruff and diff checks pass. No full-suite or live Telegram test.

Current main `dbaaa0a44ddb5a412837f9c684d919cd91c6f2fc` already contains the prior
approved policy/strategy/lifecycle changes under cherry-picked hashes. Only this
new pilot commit is required from that main; do not reapply the previous nine
commits or launcher changes. Compatibility is checked by applying its patch to
an isolated read-only main archive and comparing every affected file with the
tested worktree. Main/production checkout and refs are untouched.
