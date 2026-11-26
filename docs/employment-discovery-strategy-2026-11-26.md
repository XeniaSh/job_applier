# Employment discovery strategy — Russian-speaking/CIS first, NZ protected

Date: 2026-11-26. Supersedes the implementation ranking in the earlier
[discovery analysis](discovery-expansion-analysis-2026-11-26.md), retaining its
measured source facts and the [TASK-093 audit](vacancy-scoring-audit-2026-11-26.md).
**Strategy/research only. No runtime configuration, filters, collectors, caches,
candidate profiles, application history or production data changed.** No Claude,
LLM reanalysis, authenticated sessions, applications or messages were used.

## Objective and evidence limits

Optimize for a viable employment offer and interviews, not advertisements,
prestige, Prepare coverage or application volume. The user now prioritizes:

- Senior/Middle+ Java/Kotlin backend, approximately 7+ years of backend experience;
  substantive production AI/LLM integration is an additional relevant lane.
- Russian-speaking teams and CIS employers with legitimate cross-border remote
  arrangements; English-speaking opportunities remain welcome.
- Remote work from abroad, NZ roles with realistic visa support, and explicitly
  supported international relocation to a candidate-accepted destination.
- Exclude gambling, defense, adult entertainment and speculative-trading
  businesses/projects. Ordinary games, payments, insurance and general fintech
  are not automatically these categories.

The user reports 348 found, 199 delivered and 41 applied in the last 30 days.
Period ratios are 57.2% delivered/found, 20.6% applied/delivered and 11.8%
applied/found. These are **not cohort conversion probabilities**: events can
belong to different discovery periods. No employer-response/interview counts or
company-by-delivery breakdown were supplied. Do not label any employer a proven
low-response source. The arithmetic difference of 158 is not a confirmed count
of unapplied delivered jobs or immigration-related rejections.

Existing available history remains ten sanitized NZ categorical decisions and
49 manual Wise pilot reviews. Do not repeat the completed 20-case audit or
pretend these describe the full production funnel. No production DB, private
candidate configuration, mailbox or browser data was read for this phase.
Fresh source evidence below is public stock/sample evidence, not daily yield,
distinct hiring headcount or confirmed personal legal eligibility.

## Current coverage and why company counts overstate useful coverage

The existing merged catalog has 84 companies, 67 supported target-company
sources: 43 Greenhouse, four Lever, 19 Ashby and Wise SmartRecruiters. Regular
email discovery is a separate existing route; generic configured Greenhouse
boards can also run. HH is **CLI-only**, not part of regular `run` discovery.
Habr, Geekjob and getmatch have no collector in this checkout.

All 84 catalog entries currently have priority A. Only 11 have an explicit
Russian-speaking signal; only **Finom, JetBrains and PandaDoc** among those 11
have a supported runtime watcher. Revolut, Tabby, Exness, Salmon, Plata,
TradingView, Miro and inDrive are custom/manual entries. A configured employer
is not necessarily being collected. The priority and Russian-signal fields have
no ranking use in the reviewed application code; changing their YAML labels
alone would not implement the revised delivery strategy. They are also employer
research hints, not proof of a team's working language or employment rights.

The previous six-board NZ refresh and Datacom/Xero research remain valid as
dated observations; they were not repeated. Protect NZ investigation capacity.
The small NZ supply does not justify replacing it with hundreds of country-bound
global advertisements.

### Existing analysis gaps relevant to the new objective

Code reviewed: `app/company_watch/{feasibility,application_recommendation,
candidate_constraints,prefilter}.py`, `app/cli.py`, `app/requirement_matcher.py`,
`prompts/analyze_vacancy.md` and `app/collectors/{hh_client,hh_collector,models}.py`.

- APPLY_NOW and CHECK_MANUALLY both enter ordinary Telegram delivery. Unknown
  eligibility therefore competes for attention with supported opportunities.
- Feasibility stores restriction text but recommendation does not compare a
  structured allowed-country set with country-specific candidate facts.
  Discovery constraints have global sponsorship/relocation flags, not the
  distinct factual model already used by application preparation.
- Existing generic markers predominantly recognize English. Russian wording
  needs captured examples, negation and scope handling; adding a loose
  `удаленно` keyword is insufficient.
- The extraction prompt can turn “cannot sponsor” into “existing authorization
  required.” Those are different facts: a contractor route may not require
  employer visa sponsorship, while local employment may require existing rights.
- No explicit excluded-industry policy was found in the reviewed discovery
  constraints/prompt/recommendation path. Company category notes are not a
  demonstrated domain-exclusion gate.

Two **synthetic, CPU-only replays of current shared functions** establish
specific failure modes, not their historical production frequency:

| Input, with synthetic Senior Java/STRONG decision and sponsorship-needed constraints | Current result | Proposed treatment |
| --- | --- | --- |
| Text says work from anywhere, followed by US-only base; extracted remote is country_limited and restrictions include US only | `remote_type=worldwide`, LIKELY, APPLY_NOW | Specific restriction must override generic marketing; manual until country facts establish compatibility |
| Relocation assistance + explicit no sponsorship + existing UK work rights required | Feasibility UNLIKELY, but recommendation APPLY_NOW because relocation is positive | Moving money cannot satisfy a visa requirement; never promote this conflict. Manual/blocked according to explicit candidate-country facts |

The current generic local-work-rights case also returns SKIP without an ability
to represent country-specific candidate rights. This is a modeling limitation,
not evidence the real candidate has those rights or a particular rejection was
wrong. Wise already has a separate refusal/local-transfer correction; do not
remove it while addressing shared semantics. The confirmed Senior/Engineer I
precedence defect remains a small, high-priority independent fix.

## Reduce low-value international delivery without banning fintech

The lists below are **recommendations, not applied configuration**. Suppression
must preserve history and the distinction between policy exclusion, explicit
ineligibility and insufficient information.

| Employer/category | Recommended action | Reason and exceptions |
| --- | --- | --- |
| Exness; Binance; Coinbase | Propose company-level policy exclusions | Their own descriptions identify CFD/brokerage or crypto-exchange/trading businesses, matching the revised speculative-trading avoidance. Exness is manual already; Binance/Coinbase are runtime sources. This is a domain decision, not evidence of poor response rates. [Exness](https://www.exness.com/about-us/), [Binance](https://www.binance.com/en/about), [Coinbase](https://www.coinbase.com/about) |
| Robinhood, Trade Republic, TradingView; new Devexperts/DX trading-platform leads | Deprioritize/hold for explicit domain classification before expanding | Brokerage, investment and trading infrastructure should not be admitted just because Java/remote is strong. Verify core product/project against the speculative-trading exclusion; do not silently classify all investing or analytics as gambling. TradingView describes trading charts/social tools; Devexperts serves brokerage/exchange systems. Trade Republic/TradingView are not current runtime collectors. [TradingView](https://www.tradingview.com/about/), [Devexperts](https://devexperts.com/about-devexperts/) |
| Wise, Stripe, Airwallex, Adyen, Qonto; existing English-language US/EU fintech such as Brex/Chime/Affirm | Keep collection; lower routine delivery priority for weakly evidenced international roles | Preserve explicitly supported NZ, viable cross-border remote and accepted relocation/sponsorship roles. Local-only, no-new-sponsorship and country-limited remote require role-level handling. Unknown is a bounded manual-review candidate, not an automatic rejection or a high-priority card. No employer-wide impossibility is established |
| Finom; relevant payment/product teams at Tabby, Plata, Salmon | Retain as role-verified Russian-speaking/cross-border prospects | Finom is already collected; the others need source validation, not an assumption of coverage. Finom publishes a geographically distributed workforce including CIS countries, but this does not prove new hiring from every listed country. [Finom careers](https://careers.finom.co/) |
| JetBrains and PandaDoc | Keep; prioritize only matching teams/stack and supported work arrangements | Language/team signal needs confirmation. JetBrains advertises hybrid work, limited work-abroad days and relocation benefits; that is not permanent work-from-anywhere permission. [JetBrains careers](https://www.jetbrains.com/careers/) |
| NZ employers; Datacom and Xero discovery proposals | Protect priority | Retain role-level sponsorship checks. Earlier Java-contract/AI-builder caveats remain; NZ metadata or relocation cash alone is insufficient |
| Explicit casino/iGaming/betting, adult-content, weapons/defense or speculative-trading projects found on any source | Policy-exclude confirmed cases; hold unclear client/domain cases | Applies equally to CIS recruiters and international brands. “Entertainment,” “gaming,” “risk,” or an undisclosed client alone is not proof. Do not admit an unknown client solely because remote employment is offered |

Vacancy-level rules proposed for approval:

1. Exact existing-local-rights/no-sponsorship requirement + explicit incompatible
   candidate facts => ineligible for that route. No candidate facts => manual,
   not a fabricated refusal or authorization. Sponsor-transfer-only is distinct
   from new sponsorship. Support limited to another location cannot rescue it.
2. Remote “US only,” “EU residents,” “RF territory only,” or a country whitelist
   remains limited. Being outside Russia does not prove EU eligibility. EMEA is
   a region, not a list of countries with lawful contracting/payroll support.
3. Office/hybrid in an unapproved destination, with no accepted relocation route,
   is low priority/manual; explicit unwillingness establishes a preference
   conflict. Do not invent an unwanted-destination blacklist from temporary
   residence. Ask for destination preferences only when evaluating that route.
4. A proven viable exception stays visible even at a generally deprioritized
   nonexcluded fintech. Confirmed excluded domains remain excluded regardless
   of geography. Never delete or resend old notifications automatically.

No company-specific delivery/application rates can currently be calculated from
the supplied totals. A future sanitized employer/source aggregation should join
delivered, applied and outcomes before any outcome-based company suppression.

## Russian-speaking/CIS sources — practical ranking

Effort estimates include a bounded pilot and focused tests, not production
activation. Eligible new-per-week yield is **unmeasured for every new source**;
do not multiply snapshot counts by polling frequency or add overlapping portals.

| Priority/source | Observed supply and expected relevance | Geography/employment and salary | Supported access, restrictions, effort/maintenance and dedup |
| --- | --- | --- | --- |
| 1. **Habr Career RSS** | Advertised filtered feed returned HTTP 200 and 50 unique items; 12 plausible JVM development titles after obvious QA/junior/ЦФТ exclusions. This is before grade, domain, freshness and eligibility review; feed also matches JavaScript/QA. Expect a useful shortlist to test, not 12 eligible hires | Russian advertisements and grade tags; remote badge does not establish abroad permission. Salary present in titles for 13/50, 3/12 development candidates. Similar-specialist pay estimates are not an employer's compensation offer | The search page advertises [RSS](https://career.habr.com/vacancies/rss?page=1&per_page=25&q=Java&remote=1). Requested per_page=25 actually returned 50; validate filters/window rather than assuming coverage. RSS descriptions are snippets; details may be text or images. Start RSS-only/manual links, **1–2 days**. Approved bounded detail enrichment/routing another **1–2 days**, medium maintenance. IDs are Habr vacancy IDs; updates/reposts and other-source copies need linking |
| 2. **HH Russia/Kazakhstan/Uzbekistan** | Highest potential broad regional reach, but qualified supply unmeasured. Public indexed remote searches show **284 Moscow**, **21 Kazakhstan**, **7 Uzbekistan** raw matches; these are different geographic scopes, not comparable whole-country counts or JVM-only jobs. The UZ seven include QA, DevOps, mobile and business roles | Remote is not cross-border. Salary fields support currency/gross/period but may be missing. Prefer explicit abroad permission/contract entity; reject office-only only with known destination conflict. Keep a no-area abroad lane so localized regional searches do not hide worldwide roles | Existing CLI reuses API, analyzer and seen storage, but always first page of 20 / last 7 days, no host/area/work-format options, no regular delivery. Current [API documentation](https://api.hh.ru/openapi/redoc) describes authentication, host versus area and work_format; unauthenticated search probe returned 403. **Stopped**; KZ/UZ API probes not attempted. Obtain approved registered API access, never scrape around denial. Extend existing client/normalizer **1–2 days** after access; regular-run integration/budgets/history/health **2–3 days** separately. Medium maintenance; same HH vacancy ID across regional hosts must deduplicate |
| 3. **getmatch** | Relevant Russian-language backend specialization and salary bands. Four sampled Java remote/abroad pages are **archived**; useful eligibility examples, not current volume. Listing HTML exposed filters but no usable vacancy list in this probe | Historical ads distinguish outside-RF allowed, outside-RF required, timezone and resident-status conditions. Salary often explicit, preserve net/gross/currency. “Весь мир” badge cannot override details | Public website and official [candidate bot](https://t.me/g_jobbot) support manual discovery. No supported bulk listing API/RSS verified; do not reverse-engineer a private endpoint. Manual saved search now; integration feasibility/permission **0.5–1 day**, implementation only after a supported input exists. Medium/high HTML maintenance. Dedup getmatch ID plus canonical employer job/recruiter identity |
| 4. **Geekjob** | Russian-speaking IT/remote ecosystem; current eligible Java stock not measured. Official channel directory confirms both JVM and remote channels | Salaries sometimes explicit, requirements vary per ad; an international company is not proof of hiring from the candidate's country | Public site HTTP 200. No advertised RSS on homepage or supported bulk API verified. Prefer manual listings/channel reading or provider-approved feed. **0.5–1 day** supported-input pilot design; collector estimate pending permission/schema. Medium/high HTML maintenance; repeated recruiter and cross-channel copies need dedup. [Official channel directory](https://geekjob.ru/content/channels) |
| 5. **Selected employer career pages** | Uzum supplies one strong current JVM remote lead; SENSE one current Java posting with contract/location questions. Others below are watchlist, not counted prospects | Favor explicit foreign-work permission and identified contract mechanism; Russian-language ad is a team-language signal, not a legal right. Salary often undisclosed | Reuse a verified existing ATS when linked by employer; otherwise manual links before custom parsing. **0.5–1 day per existing-ATS pilot**, **1–3 days per custom page**; no guaranteed public API. Stable employer vacancy ID and language-duplicate merging required |
| 6. **LinkedIn saved alerts for Russian-speaking teams** | Incremental yield unmeasured; existing email collector makes discovery cheaper than another scraper. Russian/Russian-speaking + Java/Kotlin/backend/LLM, named-employer alerts and geography-specific variants are possible search experiments | Job country, required language and cross-border terms need individual confirmation; Russian founders/surnames do not establish team language | Use manual [job alerts](https://www.linkedin.com/help/billing/answer/a511279) into the already supported email workflow. No subscription or mailbox access performed. LinkedIn [prohibits scraping/automated site activity](https://www.linkedin.com/help/linkedin/answer/a1341543); no site scraper/API entitlement assumed. User setup, little initial coding; sanitized parser regressions **0.5–1 day** if formats differ. Dedup LinkedIn ID/canonical ATS identity |
| 7. **Telegram JVM/remote channels** | Highly targeted discovery hints, unmeasured eligible weekly supply, many repeated/anonymous/RF-only posts. Do not treat subscriber totals as vacancy supply | Posts can state salary, timezone, ИП/contract, country restrictions; claims require employer/project verification | Manual channel reading first. Bot API cannot read arbitrary channel history; channel membership/admin cooperation is not interchangeable with a personal Telegram session. Telegram's [API terms](https://core.telegram.org/api/terms) and [content licensing](https://telegram.org/tos/content-licensing) restrict aggregation/AI use. Do not feed channel text into the current LLM pipeline without a compliant supported arrangement/consents. No userbot scraping project proposed. Dedup channel/message ID plus canonical source URL; manual link to employer-owned ad is preferable |

HH indexed sources: [Moscow](https://hh.ru/vacancies/java-developer/udalennaya_rabota),
[Kazakhstan](https://hh.kz/vacancies/java-developer/udalennaya_rabota),
[Uzbekistan](https://hh.uz/vacancies/java-developer/udalennaya_rabota).
These web-index observations do not establish working API access. API base stays
`api.hh.ru`; changing host to hh.kz/hh.uz is not country filtering or an access
workaround. Use explicit area configuration verified against its dictionaries.

The existing HH detail normalizer reads legacy `salary` only and discards
`salary_range` and `work_format`; expanding queries alone would lose evidence.
Keep publication state, salary basis/currency/period, structured work format,
experience and any explicit country/contract wording through normalization.

Historical eligibility controls below are **all archived**, not current leads:

| Archived getmatch role | Lesson for future fixtures |
| --- | --- |
| [CDEK Java](https://getmatch.ru/vacancies/13753-java-developer) | Abroad permitted, but overlap limited relative to Moscow; worldwide badge is incomplete evidence |
| [M.Video Java](https://getmatch.ru/vacancies/13941-java-developer) | Abroad permitted subject to retaining a resident status; the text does not establish which residence category or the candidate's status |
| [Gameram Java](https://getmatch.ru/vacancies/13967-java-developer) | Outside-RF remote permitted/required; Armenia relocation is a separate optional route, not an assumed preference |
| [PPR Java/Oracle](https://getmatch.ru/vacancies/11639-java-razrabotchik-oracle) | Explicit abroad permission is useful; contract/country compatibility still needs confirmation |

Habr [publishes API documentation navigation](https://career.habr.com/info/api),
but this inspection did not establish a usable public vacancy-search contract;
its visible navigation returned the same page. Prefer the advertised RSS, not
an undocumented internal API. [Habr terms](https://career.habr.com/info/legal/agreement)
and [getmatch terms](https://getmatch.ru/docs/terms-of-service) do not establish
a bulk redistribution license merely because a page returns 200. Verify the
intended integration scope before enrichment/republication. No account creation,
application API, subscription, CAPTCHA solution or access workaround was used.

### Employer shortlist and what is actually known

| Order | Employer/team | Evidence and next screening step |
| --- | --- | --- |
| 1 | **Uzum Market** | Current [Senior/Staff Java Engineer](https://people.uzum.com/career/ru/vacancies/1958), also present in current employer listing: six years Java/Spring, Kotlin/Kafka, explicit worldwide remote. Russian ad is directly relevant. Confirm Senior IC scope versus Staff, actual contracting/payroll entity, supported work country, payment terms and overlap. Salary amount absent. Tashkent metadata must not overwrite explicit global remote scope; neither proves personal work permission |
| 2 | **SENSE** | Current [Senior Java role](https://career.habr.com/vacancies/1000150058), Java 21/microservices, salary ceiling 450k RUB. [Employer profile](https://career.habr.com/companies/sense-group) describes abroad remote, but this particular ad says Russian employment contract and does not settle country eligibility. Manual confirmation of client industry, lawful abroad arrangement and gross/net basis; no automatic eligible label |
| 3 | **JetBrains; Finom** | Already runtime-supported and configured with Russian-language signal. Preserve matching JVM/product teams. Confirm actual team language and supported entity/country per role; no new ATS needed. JetBrains workation/hybrid is not permanent international remote; Finom's distributed staff list is not a hiring-country guarantee |
| 4 | **Wrike; DataArt** | Worth targeted team/source validation, not broad company ingestion. Wrike official site links to Greenhouse (`wrike`) and displays 69 roles/19 R&D, but only a partial list was inspected, no current matching IC count established. DataArt's [workplace policy](https://www.dataart.team/work-at-dataart/workplace-options) expressly requires legal rights in residence and client-specific countries. Its reviewed [Java AI-Native full-stack role](https://team.dataart.com/vacancies/jav00303) is USA-scoped and emphasizes AI coding tools, not proof of production LLM-integration duties. Do not promote it on company heritage or AI title |
| 5 | **inDrive, Miro, Tabby, Plata, Salmon; PandaDoc** | Existing configured research prospects; many are not runtime sources. Recheck JVM/backend/real AI duties and cross-border entity before building coverage. PandaDoc is already collected but Russian-speaking relevance alone cannot compensate for a stack mismatch. No new vacancy-volume forecast made |
| 6 | **Kazakhstan: Kolesa Group, selected product/payment employers; Uzbekistan: additional Uzum teams, Payme/Click, Itransition** | Company/role validation watchlist. Reviewed [Kolesa role](https://kolesa.group/career/job/middle-go-backend-razrabotcik-core-team-136951220) is Go/Almaty, not a confirmed Java-abroad opportunity. UZ HH results include Itransition AI-agent work but need backend/seniority/project screening. Do not replace Western office-only noise with CIS office-only, QA/mobile, trading or junior noise. Payme/Click cross-border inventory was not measured |
| 7 | **Aviasales/Travelpayouts; CDEK IT** | Historically promising abroad arrangements; current [Aviasales list](https://aviasales.ru/about/vacancies) did not establish an active Java opening. Archived [CDEK Java ad](https://getmatch.ru/vacancies/13753-java-developer) permits abroad work but limits timezone; current [IT list](https://rabota.cdek.ru/vacancies/it) shows other specialties. Watch rather than forecast immediate Java yield |

Telegram manual shortlist: **@alljvmjobs**, **@remotegeekjob** (verified in
Geekjob's directory), **@jvmjobs**, **@javadevjob** (public channel descriptions
show JVM vacancies). getmatch's **@g_jobbot** is a candidate service;
**@g_jobchannel** is a mixed career/content channel, not a pure vacancy feed.
No channels subscribed to, admins contacted or messages forwarded. No personal
recruiter contact information is copied into repository fixtures.

For a promising role, resolve five questions manually before calling it feasible:
which country may the person actually work from on an ongoing basis; which legal
entity and employee/contractor/EOR arrangement would engage them; what existing
rights or new visa support that route needs; required timezone overlap and team
language; and the actual product/client industry. An international bank account,
foreign office or advertised remote benefit alone answers none of these.
No recruiter question was sent during this research.

## Revised classification and delivery proposal

Keep technical suitability separate from employer opportunity, candidate facts
and delivery priority. Do not encode hiring feasibility as a bigger Java score.

| Proposed status | Evidence needed | Proposed delivery |
| --- | --- | --- |
| **Explicitly eligible** | Explicit employer route and compatible user-provided, country-specific facts/preferences; no conflicting restriction. A job may instead have only an explicit employer-side route while personal confirmation is pending | Priority card only after compatibility is established; label employer-side evidence precisely. Never a guarantee of a visa or hiring outcome |
| **Potentially eligible / manual confirmation** | Unknown sponsorship/country/payroll; worldwide claim with unresolved contract; conflicting remote text; foreign entity without country support; local/transfer-only with personal status unknown | Clearly labeled, bounded review queue/digest behind eligible leads. Preserve discoverability and reasons; do not silently cache as permanently rejected. Digest/caps are a proposed product change, not current behavior |
| **Explicitly ineligible for the route** | Specific employer restriction contradicts an explicit candidate fact, e.g. required local rights explicitly absent and no supported alternative; or confirmed unwanted office relocation | Suppress ordinary vacancy cards for that route with evidence/reason, retain identity/history. Another explicitly supported route can be assessed separately |

Excluded domain or a technical mismatch is a **separate policy/fit rejection**,
not a fabricated immigration judgment. This separation matters in audits.

Proposed evidence schema: allowed/excluded work countries; location/office and
timezone requirements; new sponsorship versus transfer; relocation destination
and support; contract/payroll entity and employee/contractor/EOR route; required
work/residence rights; team working language; explicit quotation or source span,
URL and checked time. Store candidate citizenship, current location, permanent
residence, tax residence, country work rights and location preferences separately
with unknown defaults. Do not derive any from phone prefix or employer office.
EOR/contractor wording is a route to verify, not a legal exemption.

Order proposed for ranking: domain/fit and explicit blockers; verified feasible
route; Senior/Middle+ JVM or substantive AI-backend scope; Russian/English team
fit; NZ supported hiring or accepted relocation; compensation/freshness and
reasonable application effort. Unknown compensation is not zero. Prestigious
employer/ATS support gets no bonus. Unknowns must not outweigh explicit denials.

Use source diversity and a per-employer attention budget for uncertain roles,
with protected opportunities for NZ/supported relocation. Select caps using a
short outcome trial rather than inventing a permanent blacklist or raw-score
cutoff now. Discovery retention, analysis budgeting and Telegram delivery are
separate controls; do not drop a proven feasible exception because a feed is busy.

Preserve `java_backend`, `ai_backend`, `java_backend_nz`, `ai_backend_nz` and all
selection/factual/salary rules. Do not switch to NZ variants because of a
candidate's residence, visa hopes or timezone. AI variants require substantial
AI-backend duties, not coding copilots. Consider an explicit backend-AI discovery
lane after fixtures prove it will not admit generic ML/QA/AI-branded full-stack
noise; do not broaden mandatory skill claims or alter CVs. Russian `ведущий` and
mixed Senior/Staff titles need scope review, not a blanket leadership override.

## Revised implementation roadmap — approval required

Efforts are engineering estimates, not promised job yield. All rows are proposals.

| Rank | Bounded task | Value, acceptance and dependencies | Effort |
| --- | --- | --- | --- |
| 1 | **Shared eligibility conflict handling and delivery evidence** | Regress the two synthetic contradictions above, sponsorship versus relocation, worldwide versus whitelist, Russian negation, local transfer and missing country facts. No positive route may override a specific blocker; do not convert unknowns into country denials. Preserve Wise safeguards and existing adapters. Decide small schema/compatibility scope before implementation | 1–2 days for scoped conflict correction; richer country/contract facts and delivery lanes another 2–3 days, separate approval |
| 2 | **Senior/Engineer I precedence** | Already confirmed 12/49 title replays, not 12 proven missed deliveries. Tiny independent fix and mixed-signal regressions, preserve Junior/Staff/manager policy, no cache replay | 0.5 day; can be implemented independently of rank 1 |
| 3 | **Explicit domain policy and uncertain-delivery priority** | Curated evidence-backed excluded employer/project identities, separate from immigration reasons; no “fintech” or “gaming” substring ban. Wire actual priority instead of inert all-A metadata. Preserve supported nonexcluded exceptions, history and manual discoverability; exact digest/caps need product approval | 0.5–1 day curated policy gate; priority/manual queue another 1–2 days, separate approval |
| 4 | **Habr RSS discovery-only pilot** | Advertised working feed, Russian JVM pool. Bound feed window, dedup ID, snippet completeness and Russian/English QA/mobile/lead exclusions; return manual source links. Only after review integrate with regular analysis/delivery; supported descriptions and no LLM reanalysis of old history | 1–2 days pilot; another 1–2 days approved detail/routing integration |
| 5 | **HH access/normalization, then regular discovery** | First establish registered supported API access; no secret recovery, captcha bypass or use of regional hosts to evade 403. Extend query scopes/metadata and normalize to shared vacancy pipeline. Existing collect-and-analyze marks seen immediately: do not simply call it from run and lose delivery/history semantics. Test host dedup, pagination fairness, errors, concurrent polling and budget | 0.5 day access feasibility; 1–2 days client/data; 2–3 days regular-run integration after access and pilot |
| 6 | **Outcome attribution and manual response/interview events** | Employer/source/route cohorts, explicit response/interview outcomes; preserve APPLIED/SKIPPED/history/undo semantics. Start sanitized read-only aggregation; persistence/migration/UI is separately approved | 0.5–1 day aggregate design/export; 1–2 days minimal additive tracking after schema approval |
| 7 | **Small employer pilots: Uzum first; Wrike if relevant stock confirmed** | Validate actual Senior IC/cross-border terms; existing ATS reuse where verified. Prefer a few qualified employers over a CIS catalog dump; no Prepare dependency | 0.5–1 day existing ATS pilot; 1–3 days custom source, per employer |
| Parallel NZ lane | **Protected NZ discovery: Datacom and Xero** | Retain previous evidence. Datacom Java contracts more stack-specific; Xero cheap Ashby reuse/AI breadth with stack/geography checks. Evaluate alongside, not after exhausting, all CIS sources | Datacom 1–2 days standalone pilot; Xero 0.5–1 day; runtime integration separate |
| Later | getmatch/Geekjob approved input; Endava/EROAD Workday and wider international coverage | Support manual searches now. Automation only after access/yield evidence; no generic Telegram/LinkedIn scraper, bulk fintech expansion or SmartRecruiters Prepare work | Input feasibility 0.5–1 day each; remaining old estimates retained as lower priority |

**Fastest employment action without software:** manually review the current
Uzum role and a small Habr/getmatch shortlist, confirming cross-border terms
before investing in an application. No outreach or application was performed.
**Smallest code improvement:** Senior/Engineer I, half a day.
**Highest immediate delivery-quality correction:** shared eligibility conflicts,
before adding more high-volume sources. **Fastest evidenced new discovery
integration:** Habr RSS; HH has reusable code but access is currently unverified.
These are different measures of “fastest”; none promises an interview.

## Measure employment outcomes and source value

Current `application_history` already retains employer, source/ID, selected
resume and found/sent/prepared/applied/skipped timestamps. It does **not** model
responses, interview stages or offers. `get_application_stats` counts each event
within its own time window; its top-company grouping counts first-seen jobs,
not delivery concentration or response yield. Do not present it as that report.

Propose an additive outcome-event log linked to the existing canonical job and
application attempt: human response, rejection, recruiter screen scheduled/held,
technical interview, final, offer, accepted offer, withdrawn/closed, and explicit
eligibility confirmation/refusal. Keep acknowledgement-only emails separate.
Distinguish pending/no known response from explicit rejection; never infer
failure solely from silence. Record user-reported outcomes first; email outcome
parsing would require separate opt-in, fixtures and privacy scope, not this task.

Review weekly using application cohorts aged at least 21 days (also show recent
pending cohorts), grouped by employer, source, actual team language, work route
and eligibility evidence at application time:

- Human-response and recruiter-screen rates per matured application; technical
  interviews/viable offers per application and per unique employer.
- Time to first human response/interview, application effort and proportion of
  interviews blocked later by country/payroll/visa restrictions.
- Delivered-to-applied by delivery cohort; skip/manual-confirmation reasons,
  duplicate/repost rate and attention spent on unworkable leads.
- Confirmed feasible leads and interviews per reviewed vacancy and per unit of
  source/LLM/review effort, not collector record counts. Show counts with rates;
  small samples and response delays do not justify automatic company bans.

Attribute a role to all discovery sources but count one canonical application;
preserve original source IDs and history. Match exact employer job IDs/URLs
first, then flag similar employer/title/location records for review rather than
merging distinct openings automatically. Four resume families can be reported
descriptively; small observational samples do not establish causal CV quality.
The primary outcome is a viable accepted employment offer, with interviews as
the nearer-term signal. No automatic recruiter messages, follow-ups or submission.

## Conditions and next approval

No production change is authorized by this strategy. Next proposed code scope
is the small shared eligibility-conflict regression/correction; Senior/Engineer I
remains an independent high-priority alternative. Habr RSS is the next source
pilot. HH needs supported access before an integration promise. The former
Datacom/Xero/Endava-first ordering is superseded, with Datacom/Xero preserved in
the NZ lane. No private candidate fact or relocation preference was changed.

Public research responses and derived RSS counts remain ignored under
`data/strategy-research-20261126/`; no raw job corpus, candidate information or
Telegram post corpus is added to Git. The two earlier reports keep their
historical observations and explicitly point here for the revised priorities.
