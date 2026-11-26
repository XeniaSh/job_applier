# Discovery expansion — NZ and international backend opportunities

Date: 2026-11-26. **Research only. No source/configuration/filter implementation,
production collection, cache reset, LLM reanalysis, notification or submission.**
Public job feeds/searches were queried through ordinary HTTP; no browser session,
candidate configuration, account, CAPTCHA bypass or candidate-submission API was
used. One public Xero Apply link was resolved with a read-only GET to verify its
Ashby destination; there was no form interaction. Workday POSTs below are public
**job-search** queries, not applications.
SmartRecruiters Prepare remains paused and default-off.

## Existing coverage and measured yield

The effective loader merges `config/target_companies.yaml` with
`config/greenhouse_target_boards.yaml`. Counting the first file alone is wrong:
51 explicit entries become **84 loaded companies**, with **67 runtime-supported
sources: 43 Greenhouse, 4 Lever, 19 Ashby, 1 Wise SmartRecruiters**. Other entries
are custom/manual or unapproved sources, not operational collectors. Canva is
configured but is outside the Wise-only runtime allowlist. Workday and Workable
have no runtime collector. Source coverage and Prepare coverage are different.

The [October NZ audit](nz-discovery-audit.md) inspected all then-supported 66
feeds: 155 NZ-location-metadata records, nine title passes, and persisted analysis
for all nine. Thin suitable supply, specialist/level mismatches and unknown
sponsorship were evidenced; analysis starvation was not. Historical per-cycle
production yield is not available beyond that sanitized export. A single raw
stock count cannot establish daily new-to-user notifications.

For a bounded refresh, six previously NZ-relevant feeds were read successfully
today. Existing normalizers and the **unchanged** role prefilter were replayed
offline. NZ uses structured country/name/secondary-location evidence, not HQ
or description addresses; a worldwide talent pool with an NZ office is excluded.

| Existing source | Public records, all locations | NZ metadata matches | Current title passes |
| --- | ---: | ---: | ---: |
| [Stripe](https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true) | 772 | 3 | 0 |
| [Databricks](https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true) | 874 | 3 | 0 |
| [Everbridge](https://api.lever.co/v0/postings/everbridge?mode=json) | 26 | 0 | 0 |
| [Airwallex](https://api.ashbyhq.com/posting-api/job-board/airwallex) | 508 | 3 | 0 |
| [Partly](https://api.ashbyhq.com/posting-api/job-board/partly.com) | 29 | 14 | 3 |
| [Halter](https://api.ashbyhq.com/posting-api/job-board/halter) | 271 | 119 | 6 |
| **Sample total** | **2,480** | **142** | **9** |

The known Halter Australia-relocation ad remains among NZ metadata matches;
excluding that known destination exception leaves 141 and the same nine title
passes. Other nonengineering descriptions were not individually location-audited.
These are not 142 verified physical-NZ vacancies or nine qualified Java jobs.
Only these six sources were refreshed, not all 67; new NZ roles elsewhere may
exist. Two of the nine title passes are Lead roles and others are generalist or
infrastructure-heavy. No current production delivery history was read.

Of ten previously exported NZ identities, seven remain in the sampled feeds
and only four descriptions match the old hashes. Of today's nine title passes,
three IDs are absent from that old export and two older IDs have changed text;
five are therefore new/changed **relative to that export**, not proven unseen
by the user. Normal hash-aware cache behavior, not a reset/replay, is appropriate.

## Ranked expansions

Ranking balances relevant employment opportunities, NZ priority, existing
interfaces and observed implementation risks. Effort is an estimate for a
bounded discovery pilot plus tests; production integration/approval is separate.
Volumes below are current advertised stock or samples, **not expected jobs per
day, distinct headcount, eligible roles or net-new-to-user deliveries**.

| Rank | Expansion | Measured opportunity / incremental relevance | Effort and maintenance |
| --- | --- | --- | --- |
| 1 | **Datacom — public Workable feed** | 188 location rows collapse to **77 distinct shortcodes**, **34 with NZ locations**. Two distinct NZ contract ads explicitly require Java/Spring; one also requires MuleSoft. A Java lead and a .NET-centric developer are separate lower-fit cases. Expect at most 1–2 immediate conditional IC leads from the reviewed subset, not 34 backend leads. | 1–2 days for a bounded standalone watcher/pilot using a documented public endpoint; roughly another 1–2 days for separately approved manual-delivery integration. Medium maintenance; preserve all locations while deduplicating by shortcode. |
| 2 | **Xero — reuse Ashby** | Official careers links now point to `jobs.ashbyhq.com/xero`; old Lever API returns 404. Fresh [Ashby feed](https://api.ashbyhq.com/posting-api/job-board/xero): **112 postings, 17 primary NZ locations**. Two reviewed NZ/ANZ remote builder roles involve agent tooling or actual AI workflows; C#/.NET or Python/TypeScript scope makes them conditional stretches, not strong Java matches. Sponsorship unknown. | 0.5–1 day for an existing-adapter, company-scoped discovery pilot and reviewed title selection. Low incremental parser maintenance. Do not add all “Engineer” titles without checking noise/LLM cost. |
| 3 | **Endava — reuse SmartRecruiters discovery** | Complete public list: **163 records, 10 Java/Kotlin/JVM titles, zero NZ**. Two reviewed active IC descriptions confirm Java/Spring in Romania and Java/Kotlin in London; the remaining explicit-JVM titles include consultants, full-stack and leads. Immigration/relocation support is unconfirmed. A bounded pilot could yield a few international review leads; no eligible-volume forecast is supported. | 0.5–1 day for standalone pilot; 1–2 more for independently approved employer allowlist and trusted manual URL/delivery handling. Existing collector reused; Prepare stays off and Wise-only. |
| 4 | **EROAD-first Workday discovery pilot** | EROAD lists **33 total**, eight Java-search hits. One reviewed NZ intermediate backend role explicitly requires Java/Spring; other hits include mobile, QA, overseas and lead roles. Westpac NZ lists **26 total**, three Java-search hits; its reviewed SSE is JavaScript/SQL/Windows with optional .NET, not a confirmed JVM lead. Thus **one confirmed Java NZ IC lead**, not eleven, in these reviewed results. | 2–4 days for one-employer bounded CXS listing/detail prototype, normalization and regressions; wider runtime integration/tenants adds effort. Medium/high maintenance because CXS is an observed frontend interface, not an established documented collector contract. |
| 5 | **Plata — promote an already configured custom source after validation** | Current [Greenhouse feed](https://boards-api.greenhouse.io/v1/boards/platacard/jobs?content=true): **165 records**, one explicit Senior Java/Kotlin IC title; other backend-keyword matches include QA/Go and management. Location says Worldwide, which is not proof of worldwide hiring rights. No NZ contribution established. | 0.5–1 day read-only validation/pilot using existing Greenhouse normalizer and canonical routing; check actual geography, language and role support before any config promotion. Low parser cost, small currently measured increment. |
| 6 | **Canva — defer broad activation pending better current IC stock** | Complete current public list: **28 postings**, one NZ metadata record. Its three engineering titles are Staff/Principal production/export roles, outside current level policy; zero explicit JVM title matches. A Java backend ad found in search indexes was checked and is **inactive**. Do not forecast yield from old indexed ads. | Existing SmartRecruiters discovery can be reused, but immediate preferred-IC value is currently weak. Future employer-specific pilot only; no Prepare work. |
| 7 | **Delivery Hero — targeted team/brand pilot before a broad feed** | API reports **810**, bounded sample collected **500 distinct records** and is explicitly incomplete. Eight sampled titles say backend, none explicitly JVM. Two reviewed active backend roles require Go or .NET/Go/C# rather than Java. Remaining 310 records and other teams are unreviewed; no confirmed incremental JVM count. | Reuse SmartRecruiters, but broad brand/location/commercial noise and detail budgets increase analysis/maintenance cost. First establish a higher-fit team/region subset, not an 810-ad LLM run. |

Datacom is the strongest newly measured **NZ Java-specific** source. Xero is the
cheapest **NZ/AI breadth** experiment and avoids building another ATS family.
Endava offers international JVM breadth but no established immigration support.
Workday is feasible and strategically useful, but today's observed increment
does not justify treating a broad Workday project as the next cheapest win.

## Concrete primary-source leads and limits

- Datacom [Senior Developer — Application Integration](https://apply.workable.com/datacom1/j/73C0CC240E/)
  is an Auckland-based 12-month Java/Spring/API-integration contract. The
  [Java/MuleSoft contract](https://apply.workable.com/datacom1/j/60D9E0EE22/)
  spans NZ locations and requires MuleSoft/CloudHub expertise. Contract location
  and integration specialization need review; neither ad establishes sponsorship.
  The [official board](https://apply.workable.com/datacom1/) shows 77 openings,
  consistent with unique API shortcodes rather than 188 location rows.
- Xero's [AI Harness](https://careers.xero.com/jobs/9f47da32-dd07-46ea-94ea-1e77d661af92/senior-full-stack-engineer-ai-harness/)
  describes C#/.NET modernization and verification tooling, with Wellington-based
  remote-first work. [Product & Tech Operations](https://careers.xero.com/jobs/0ae2cf75-3e58-44c7-9a08-e681cbb36cee/senior-engineer-product-tech-operations/)
  requires production AI agents/automations and permits Australia/NZ-based work.
  The generic remote flag does not mean worldwide. A Search Engineer has NZ
  remote scope in its description but Australian primary/secondary metadata;
  keep that distinction visible rather than silently changing resume geography.
- Endava's [Senior Java Developer](https://jobs.smartrecruiters.com/Endava/744000163477687)
  and [Senior Software Engineer, Java/Kotlin](https://jobs.smartrecruiters.com/Endava/744000162720359)
  have matching active public detail. Hybrid location is not international
  relocation permission. The list also has five JVM lead titles and two consultant
  titles; a large title-hit count is not the number of preferred senior IC roles.
- EROAD's [Intermediate Software Engineer, Java](https://eroadgroup.wd3.myworkdayjobs.com/EROAD/job/Auckland-Albany-HQ-NZ/Intermediate-Software-Engineer--Java-_JR101430)
  is genuine Java/Spring backend work in Auckland. AI coding-agent usage is
  not itself an LLM-integration job. Westpac's current
  [Senior Software Engineer](https://westpacnz.wd105.myworkdayjobs.com/Westpac_Careers/job/Westpac-on-Takutai-Square/Senior-Software-Engineer_JR108272)
  demonstrates why the upstream Java search needs whole-word/role review:
  its essential stack is JavaScript/web/SQL Server/Windows.

## Workday feasibility: discovery, not application automation

Normal unauthenticated requests succeeded against two independently configured
employer public job-search and detail interfaces:

```text
POST https://eroadgroup.wd3.myworkdayjobs.com/wday/cxs/eroadgroup/EROAD/jobs
POST https://westpacnz.wd105.myworkdayjobs.com/wday/cxs/westpacnz/Westpac_Careers/jobs
```

The read-only search payload used limit 20, offset 0, empty applied facets and
either empty or Java search text. Details were fetched only for two advertised
external paths. No candidate/account/application data was sent. Blank-query
results returned total counts but only their first 20 items; these are **not
complete 33/26-job detail scans**. Java-query totals (8/3) fit within those pages.

Workday's own [career-site documentation](https://doc.workday.com/workday-education/en-us/course-manuals/recruiting-for-administrators/career-sites.html)
separates external job advertising from application configuration. It does not
guarantee CXS schema stability or access for every tenant. A Seequent/Bentley
public search probe returned **429**; it was not retried or bypassed. A public
[Christchurch Software Engineer advertisement](https://bentleysystems.wd5.myworkdayjobs.com/en-US/seequent/job/Software-Engineer_R31)
mentions Python/Java, but live inventory/completeness there remains unverified.

A future discovery-only pilot needs explicit tenant/site/host configuration,
bounded pagination and detail budgets, stable employer/posting identities,
closed/reposted-job handling, full text/location normalization, duplicate and
schema/error tests, and conservative 403/429 handling. No browser or fingerprint
workaround should be a dependency. Production manual delivery would need a
trusted source/URL and callback mapping plus provider funnel counters; it does
**not** need a Workday Prepare adapter, login, profile import or Submit support.

## Other missing NZ employers and public-source options

- **BNZ:** absent from runtime configuration. Its current
  [official careers page](https://www.bnz.co.nz/about-us/careers) links to
  `nab.eightfold.ai/careers?microsite=bnz`. Older recruitment privacy wording
  mentions Workday, but that is not proof its current public listing is a
  simple Workday tenant. Treat Eightfold discovery as a separate unmeasured
  investigation, not a free addition to the EROAD CXS adapter.
- **Seequent, EROAD, Datacom and Xero** are missing from the loaded runtime
  sources and have concrete official public leads above. **ASB, Spark, Trade Me,
  Pushpay, Vista and Serko** are additional employer watchlist candidates, not
  measured yield claims or verified reusable ATS integrations in this report.
  Do not expand all of them on company reputation alone.
- **Revolut, N26 and Trade Republic** are already configured custom/manual
  international prospects, not functioning runtime collectors. Their current
  backend inventory/public data interfaces were not measured in this bounded
  refresh. They merit source validation, not an invented vacancy forecast or
  a claim that all configured employers are already covered.
- **Trade Me Jobs:** official [search API](https://developer.trademe.co.nz/api-reference/search-methods/jobs-search)
  is authenticated and rate-limited. Access/terms must be established before
  budgeting an integration; no credentials were sought or used.
- **User-configured job-alert emails** from NZ boards/employers could complement
  the existing IMAP collector without application automation. Each provider
  still needs representative sanitized emails, identity/dedup/parser tests and
  an explicit user setup action. No alerts were subscribed to, no emails read,
  and no achievable delivery-volume estimate is available.
- **Workable** is a more direct new family for this measured Datacom opportunity:
  its [documented public account endpoint](https://workable.readme.io/reference/jobs-1)
  returned jobs/descriptions/locations without credentials. Do not confuse it
  with the authenticated recruiter/candidate APIs. Preserve shortcode identity,
  multiple locations, publication state and trusted public job URLs.

## Roadmap recommendation and evidence retention

First address the confirmed, small explicit-Senior classification issue from
the [scoring audit](vacancy-scoring-audit-2026-11-26.md), without a global scoring
rewrite. Then propose a separate **Datacom read-only discovery pilot**, with
Xero/Ashby as the low-cost NZ AI breadth alternative; choose the source task
explicitly before any production integration. EROAD-first Workday follows once
the existing-adapter and documented-public-feed opportunities are measured.

Any future source integration should deliver trusted vacancy links for manual
applications even if Prepare is unsupported. Broader SmartRecruiters discovery
must not enable its Prepare flag or assume non-Wise forms are compatible.
The existing application's provider-specific delivery gates must be handled
explicitly; merely adding a collector can otherwise collect jobs that never
reach Telegram.

Public responses, statuses, bounds and CPU-only derivation are retained locally
under ignored `data/discovery-research-20261126/`. Full job descriptions and
raw historical exports are not added to Git. This report does not claim
legal eligibility, fresh production yield, universal source availability or
that source expansion recommendations have been implemented.
