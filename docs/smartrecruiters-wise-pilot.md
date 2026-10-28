# SmartRecruiters Wise discovery pilot — 2026-10-28

Status: read-only pilot complete; local implementation review and verification passed. Production integration remains disabled and requires a separate decision.

## Observed funnel

One employer, Wise, using the public Posting API. Live snapshot: 12:36:55–12:37:18 UTC (19:36–19:37 Asia/Bangkok). Bounds: 100 rows/page, 10 list pages, 100 matching-posting details. Actual requests: 54 read-only GETs. No candidate profile, private runtime data, scoring, production cache, Telegram, Prepare or submission was used.

| Stage | Observed count |
| --- | ---: |
| List pages | 5 |
| Rows by offset 0/100/200/300/400 | 100 / 100 / 100 / 100 / 17 |
| Raw rows / unique posting identities | 417 / 417 |
| Duplicate identities | 0 |
| Title-prefilter matches | 49 |
| Successful full-detail requests / normalized postings | 49 / 49 |
| Company or detail errors | 0 |
| Request-bound truncation | None |
| Explicit Java/JVM backend postings after manual description review | 34 |
| Other technical groups, separated below | 15 |

The pilot uses the existing title prefilter with include phrases `java`, `kotlin`, `backend`, `back-end`, `software engineer`, `platform engineer` and its existing exclusions. Generic titles only qualify for detail retrieval, not a backend recommendation. The first 100 snapshot rows contained 18 passes; pagination added 31 beyond those. No global filter was loosened.

## Technical and eligibility review

The 34 backend postings explicitly use, require or accept Java/JVM languages for substantive backend work. This is a technical source-yield measure, not a claim that the candidate meets every requirement. It includes two substantial Java + AI-integration roles. Of these 34: **1 explicitly offers relocation/visa support, 5 have local/relocation/sponsorship restrictions, and 28 leave the policy unknown**. Their locations are London 20, Tallinn 9, Budapest 4 and Hyderabad 1.

Across all 49 title matches, 42 have unknown relevant relocation/sponsorship policies; 3 consider only local candidates and conditionally support visa transfer; 1 excludes relocation and requires UK presence/ability to work there while leaving visa policy unknown; 2 explicitly exclude both relocation and visa sponsorship; and 1 offers both. Considering sponsorship alone, the 49 postings contain 1 offer, 3 conditional local-transfer policies, 2 refusals and 43 unknowns. Existing residence, nationality and work authorization were not inferred. A positive posting policy does not establish personal eligibility or visa approval. Hybrid/on-site location is not a sponsorship refusal. Payment authorization and customers who relocate were ignored as unrelated keyword hits.

The other 15 matches comprise 6 backend roles with an unstated Java stack, 1 with Java only optional, 3 mixed full-stack roles, 1 infrastructure role, 1 specialist data-engineering role, 1 ERP specialist, 1 AI developer-platform role and 1 ML-platform role accepting Java but preferring Python. These are not silently promoted into the 34.

### Priority examples

- [Rules Platform, Tallinn](https://jobs.smartrecruiters.com/Wise/744000156787429-senior-software-engineer-rules-platform): Java/Kotlin backend, Kafka/SQL, Spring useful; explicitly offers hybrid Tallinn work with relocation and visa sponsorship if needed. The strongest employer-supported international lead observed.
- [Java AML Handling, Tallinn](https://jobs.smartrecruiters.com/Wise/744000158102439): Java/Spring Boot/Kafka/SQL, 5+ years; responsibilities include bringing AI automation into production with evaluation and human oversight. Prior production ML/LLM experience is preferred rather than mandatory. Sponsorship remains unknown.
- [Contact Automation AI, Budapest](https://jobs.smartrecruiters.com/Wise/744000156244029): Java/Kafka plus real RAG/LLM workflows, Bedrock and AI-vendor integration. TypeScript/Python and AI-delivery scope need review against actual experience. Sponsorship remains unknown.
- [Business Onboarding, London](https://jobs.smartrecruiters.com/Wise/744000152127649): relevant Java/JVM backend requirements, but explicitly no relocation or visa sponsorship. Do not recommend as a sponsored-relocation lead.
- [Product Eligibility, London](https://jobs.smartrecruiters.com/Wise/744000157420370): Java backend, no relocation and a UK-based condition. Its visa policy is not stated; this must not be rewritten as an explicit sponsorship refusal.
- [Engineering Experience, London](https://jobs.smartrecruiters.com/Wise/744000151565965): Java accepted for a hands-on developer/AI platform, including MCP and agent-tooling services. Kept adjacent because of broad cross-team enablement scope. Unknown sponsorship.
- [ML Platform, Tallinn](https://jobs.smartrecruiters.com/Wise/744000145759379): accepts production backend Java, prefers Python and requires Kubernetes/platform experience; prior ML experience is explicitly not required. Kept adjacent to ordinary Java backend roles. Unknown sponsorship.

### All reviewed title matches

Counts below are posting identities, not proven unique requisitions or newly discovered opportunities for this candidate. Two Risk Control & Governance IDs have identical description bodies; distinct posting IDs are retained, not merged by title or body.

| Posting | Location | Technical group | Eligibility evidence | Review note |
| --- | --- | --- | --- | --- |
| [Senior Software Engineer I - Pricing Core](https://jobs.smartrecruiters.com/Wise/744000158265079-senior-software-engineer-i-pricing-core) | London, United Kingdom | Java/JVM backend | Unknown | Java/Spring Boot backend on AWS. |
| [Senior Backend Software Engineer](https://jobs.smartrecruiters.com/Wise/744000158264979-senior-backend-software-engineer) | London, United Kingdom | Java/JVM backend | Local only; visa transfer conditional | Java or OOP; production backend; local candidates only, visa transfer conditional. |
| [Senior Software Engineer II - Wise for Enterprise](https://jobs.smartrecruiters.com/Wise/744000158264339-senior-software-engineer-ii-wise-for-enterprise) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java/JVM, Spring, Kafka and microservices. |
| [Senior Software Engineer II - Data Onboarding & Reporting](https://jobs.smartrecruiters.com/Wise/744000158242549-senior-software-engineer-ii-data-onboarding-reporting-) | London, England, United Kingdom (Hybrid) | Specialist data engineering | Unknown | Trino/Iceberg, medallion data modeling and dbt/Airflow are mandatory; Java incidental. |
| [Senior Software Engineer - Global KYC and Onboarding](https://jobs.smartrecruiters.com/Wise/744000158109100-senior-software-engineer-global-kyc-and-onboarding) | London, United Kingdom | Backend; stack unstated | Local only; visa transfer conditional | Any-language backend and KYC automation; Java not stated; local-only conditional visa transfer. |
| [Senior Software Engineer I - Risk Control & Governance](https://jobs.smartrecruiters.com/Wise/744000158109379-senior-software-engineer-i-risk-control-governance) | London, United Kingdom | Backend; stack unstated | Unknown | Any-language backend/data-governance systems; Java stack not stated. |
| [Senior Backend Engineer - Risk Domain](https://jobs.smartrecruiters.com/Wise/744000158104619-senior-backend-engineer-risk-domain) | London, United Kingdom | Java/JVM backend | Unknown | Backend languages explicitly include Java; risk APIs and distributed services. |
| [Senior Software Engineer I - Account Integrity](https://jobs.smartrecruiters.com/Wise/744000158101614-senior-software-engineer-i-account-integrity-) | London, England, United Kingdom (Hybrid) | Mixed full-stack | Unknown | Explicit 80% backend/20% frontend; JVM/Spring plus React/web skills. |
| [Senior Software Engineer - Java - AML Handling](https://jobs.smartrecruiters.com/Wise/744000158102439-senior-software-engineer-java-aml-handling) | Tallinn, Estonia | Java/JVM backend | Unknown | Java/Spring/Kafka core; build production AI automation for AML workflows; 5+ years. |
| [Senior Software Engineer I - Product Eligibility](https://jobs.smartrecruiters.com/Wise/744000157420370-senior-software-engineer-i-product-eligibility) | London, England, United Kingdom (Hybrid) | Java/JVM backend | UK-based/no relocation; visa unknown | Java8+ backend/APIs; relocation unavailable and UK-based condition; visa policy not stated. |
| [Senior Software Engineer I - Wise for Enterprise - Banks](https://jobs.smartrecruiters.com/Wise/744000157078266-senior-software-engineer-i-wise-for-enterprise-banks) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java/Spring Boot/Kafka/PostgreSQL backend stack. |
| [Backend Engineer - Send Experience](https://jobs.smartrecruiters.com/Wise/744000156862144-backend-engineer-send-experience) | London, United Kingdom | Backend; stack unstated | Unknown | Backend recipient services; language and detailed requirements not stated. |
| [Senior Software Engineer - Rules Platform](https://jobs.smartrecruiters.com/Wise/744000156787429-senior-software-engineer-rules-platform) | Tallinn, Estonia | Java/JVM backend | Relocation + visa support offered | Java/Kotlin backend, Kafka/SQL; explicitly offers Tallinn relocation and visa support. |
| [Senior Software Engineer I - Risk Control & Governance](https://jobs.smartrecruiters.com/Wise/744000156752984-senior-software-engineer-i-risk-control-governance) | London, United Kingdom | Backend; stack unstated | Unknown | Any-language backend/data governance; Java stack not stated. |
| [Senior Software Engineer II - Account Integrity](https://jobs.smartrecruiters.com/Wise/744000156563169-senior-software-engineer-ii-account-integrity) | London, United Kingdom | Mixed full-stack | Unknown | 80% backend/20% frontend; JVM/Spring plus cross-stack duties. |
| [Platform Engineer - Compute](https://jobs.smartrecruiters.com/Wise/744000156559959-platform-engineer-compute-) | London, United Kingdom | Infrastructure | Unknown | AWS/Kubernetes compute, operators and service mesh; Java/backend application stack not established. |
| [Senior Full-Stack Software Engineer - Account Integrity - Java, React](https://jobs.smartrecruiters.com/Wise/744000156558844-senior-full-stack-software-engineer-account-integrity-java-react) | London, United Kingdom | Mixed full-stack | Unknown | Full-stack Java/Spring plus React/web-app expectations. |
| [Senior Software Engineer - AML - Java](https://jobs.smartrecruiters.com/Wise/744000156558804-senior-software-engineer-aml-java) | London, United Kingdom | Java/JVM backend | Unknown | Java/Spring/Kafka AML backend; 5+ years; ML/LLM production only preferred. |
| [Senior Backend Engineer – Contacts](https://jobs.smartrecruiters.com/Wise/744000156524929-senior-backend-engineer-contacts) | Tallinn, Estonia (Hybrid) | Java/JVM backend | Unknown | Java/Spring backend recipient APIs and distributed systems. |
| [Backend Engineer - North America Risk Prevention](https://jobs.smartrecruiters.com/Wise/744000156324639-backend-engineer-north-america-risk-prevention) | Budapest, Hungary (Hybrid) | Java/JVM backend | Unknown | Java/Kotlin/JVM+Spring backend; 4+ years and ML-fraud model integration basics. |
| [Senior Software Engineer - Contact Automation - AI](https://jobs.smartrecruiters.com/Wise/744000156244029-senior-software-engineer-contact-automation-ai-) | Budapest, Hungary | Java/JVM backend | Unknown | Java/Kafka core plus RAG, LLM workflows, Bedrock and AI vendor integration; TypeScript/Python scope. |
| [Senior Software Engineer II - Contacts Team - Java](https://jobs.smartrecruiters.com/Wise/744000155997469-senior-software-engineer-ii-contacts-team-java) | London, England, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java8+ backend recipient APIs, Spring. |
| [Senior Software Engineer - Send High Volume](https://jobs.smartrecruiters.com/Wise/744000155988185-senior-software-engineer-send-high-volume) | Tallinn, Estonia | Java/JVM backend | Unknown | Java/Spring/Kafka API-first backend; AI-assisted development language does not establish required LLM integration duties. |
| [Senior Software Engineer - Recommend Team (Java)](https://jobs.smartrecruiters.com/Wise/744000155933663-senior-software-engineer-recommend-team-java-) | London, United Kingdom | Java/JVM backend | Unknown | 5+ Java/Kotlin/JVM, Spring/Kafka/backend systems. |
| [Senior Software Engineer (IC3 - Backend)](https://jobs.smartrecruiters.com/Wise/744000155771274-senior-software-engineer-ic3-backend-) | Hyderabad, India (Hybrid) | Backend; stack unstated | Unknown | Identity/risk backend APIs in Hyderabad; language not stated. |
| [Senior Software Engineer II - FX Markets](https://jobs.smartrecruiters.com/Wise/744000155375829-senior-software-engineer-ii-fx-markets) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java17+/Spring/Kafka/streaming; explicitly 8+ years, above approximate seven-year profile. |
| [Europe Risk And Controls Senior Software Engineer II](https://jobs.smartrecruiters.com/Wise/744000155375749-europe-risk-and-controls-senior-software-engineer-ii) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | 7+ Java/Spring/Kafka distributed backend; aligns at stated experience threshold. |
| [Senior Backend Engineer - Payin Platform](https://jobs.smartrecruiters.com/Wise/744000154612149-senior-backend-engineer-payin-platform) | London, United Kingdom | Backend; stack unstated | Unknown | Any-language payment backend/APIs, PostgreSQL/Kafka; Java stack not stated. |
| [Software Engineer - AML Risk](https://jobs.smartrecruiters.com/Wise/744000153669609-software-engineer-aml-risk) | Tallinn, Estonia | Java/JVM backend | Unknown | Java/Spring/Kafka AML backend; stated 2-5 years may be below preferred level. |
| [Software Engineer - Recurring Payin](https://jobs.smartrecruiters.com/Wise/744000153155714-software-engineer-recurring-payin) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java/Kotlin/Spring Boot recurring-payments backend. |
| [Senior Software Engineer - Wise Platform - API Experience](https://jobs.smartrecruiters.com/Wise/744000152862917-senior-software-engineer-wise-platform-api-experience) | London, United Kingdom (Hybrid) | Java/JVM backend | Local only; visa transfer conditional | Java/Spring/Kafka/PostgreSQL APIs; local-only conditional sponsorship transfer. |
| [Senior Software Engineer I - Business Onboarding and Verification](https://jobs.smartrecruiters.com/Wise/744000152127649-senior-software-engineer-i-business-onboarding-and-verification) | London, England, United Kingdom (Hybrid) | Java/JVM backend | No relocation or visa sponsorship | Java/JVM+Spring backend; explicitly no relocation or visa sponsorship. |
| [Senior Software Engineer II- Regional Expansion](https://jobs.smartrecruiters.com/Wise/744000151810217-senior-software-engineer-ii-regional-expansion-) | London, United Kingdom (Hybrid) | Backend; Java optional | Unknown | Backend/microservices; Java only nice-to-have and LLM coding tools are not product AI integration. |
| [Backend Software Engineer - Product Eligibility](https://jobs.smartrecruiters.com/Wise/744000151610329-backend-software-engineer-product-eligibility) | London, England, United Kingdom (Hybrid) | Java/JVM backend | No relocation or visa sponsorship | Java8+/Spring/Kafka backend; explicitly no relocation or visa sponsorship. |
| [Senior Software Engineer II - Engineering Experience](https://jobs.smartrecruiters.com/Wise/744000151565965-senior-software-engineer-ii-engineering-experience) | London, United Kingdom | AI developer platform | Unknown | Hands-on AI/MCP developer platform; Java accepted among polyglot options, broad org-wide influence. |
| [Senior Software Engineer II - Backend (Java) - Account Sharing](https://jobs.smartrecruiters.com/Wise/744000151430506-senior-software-engineer-ii-backend-java-account-sharing) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Backend distributed account-sharing services; Java/Kotlin among accepted languages. |
| [Senior Software Engineer - Risk Intelligence Platform](https://jobs.smartrecruiters.com/Wise/744000150313159-senior-software-engineer-risk-intelligence-platform) | Tallinn, Estonia (Hybrid) | Java/JVM backend | Unknown | Java backend at ML/data boundary; batch/stream-processing experience required (Spark/Flink/Kafka Streams). |
| [Senior Backend Software Engineer I - Personal & Business Pricing Team](https://jobs.smartrecruiters.com/Wise/744000150224729-senior-backend-software-engineer-i-personal-business-pricing-team-) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Java or OOP backend pricing systems. |
| [Senior Backend Engineer](https://jobs.smartrecruiters.com/Wise/744000148210500-senior-backend-engineer) | Budapest, Hungary (Hybrid) | Java/JVM backend | Unknown | Java/JVM/Spring distributed backend, Budapest. |
| [Senior Software Engineer (Backend)](https://jobs.smartrecruiters.com/Wise/744000147911819-senior-software-engineer-backend-) | Hyderabad, India | Java/JVM backend | Unknown | 6+ Java8+/Spring/queues/microservices; Hyderabad. |
| [Senior II Software Engineer - Machine Learning Platform](https://jobs.smartrecruiters.com/Wise/744000145759379-senior-ii-software-engineer-machine-learning-platform) | Tallinn, Estonia | ML platform; Java accepted | Unknown | Production backend and ML platform; Java accepted, Python preferred, Kubernetes required; prior ML not required. |
| [Software Engineer - ERP Developer (Microsoft BC)](https://jobs.smartrecruiters.com/Wise/744000145736524-software-engineer-erp-developer-microsoft-bc-) | Tallinn, Estonia (Hybrid) | ERP specialist | Unknown | Microsoft Business Central AL/C/AL and ERP expertise required; not a Java backend match. |
| [Backend Engineer - Feature Platform](https://jobs.smartrecruiters.com/Wise/744000143268808-backend-engineer-feature-platform) | Budapest, Hungary (Hybrid) | Java/JVM backend | Unknown | Java11/21 backend feature platform; batch/stream processing required, Python willingness accepted. |
| [Senior Software Engineer I - Fincrime Requests](https://jobs.smartrecruiters.com/Wise/744000141541057-senior-software-engineer-i-fincrime-requests) | Tallinn, Estonia | Java/JVM backend | Unknown | Java/Spring/messaging backend case-management and enrichment. |
| [Senior Software Engineer I - Servicing Platform](https://jobs.smartrecruiters.com/Wise/744000139108668-senior-software-engineer-i-servicing-platform) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Backend languages explicitly accept Java/Python/Go; service platform. |
| [Senior Software Engineer II - Payin Platform - Backend (Java)](https://jobs.smartrecruiters.com/Wise/744000137863319-senior-software-engineer-ii-payin-platform-backend-java-) | London, United Kingdom | Java/JVM backend | Unknown | Java/Kotlin among accepted backend languages; Spring/Kafka/payment APIs. |
| [Senior Software Engineer I - Receive](https://jobs.smartrecruiters.com/Wise/744000127948469-senior-software-engineer-i-receive) | London, United Kingdom (Hybrid) | Java/JVM backend | Unknown | Backend fraud engine with Java preferred and data/ML basics. |
| [Sr Software Engineer - Screening Engine](https://jobs.smartrecruiters.com/Wise/744000123620242--sr-software-engineer-screening-engine) | Tallinn, Estonia (Hybrid) | Java/JVM backend | Unknown | Java/Spring/messaging/REST backend screening services. |
| [Senior Software Engineer I - Payment Processing Platform](https://jobs.smartrecruiters.com/Wise/744000108484376-senior-software-engineer-i-payment-processing-platform) | Tallinn, Estonia (Hybrid) | Java/JVM backend | Unknown | Java11+/Spring microservices/payment processing. |

## Limitations and recommendation

- No NZ posting was present among the 417 raw rows. The 49 title matches are located in London (32), Tallinn (11), Budapest (4), and Hyderabad (2). This one-employer pilot does not establish yield for other SmartRecruiters employers or improve NZ coverage.
- No production-history comparison was performed. These postings may already have appeared through LinkedIn or other routes; new-to-user counts are unmeasured. The default seniority/title exclusions also limit the reviewed population.
- FX Markets explicitly asks for 8+ years, above the approximate seven-year profile. AML Risk states 2–5 years and may be below the preferred level. Some Java risk/feature platforms require additional batch/stream-processing skills. Mixed full-stack roles have genuine frontend responsibilities. These remain explicit review caveats.
- One pair of identical descriptions has distinct IDs: `744000158109379` and `744000156752984`. Stable posting-level dedup does not establish separate hiring headcount.
- Source results are a point-in-time API snapshot, not a reservation of a role. Unknown sponsorship requires confirmation before treating it as employer-supported immigration.

**Recommendation:** the observed 34 technically relevant postings, including one explicit relocation/sponsorship lead and 28 unknown-policy backend leads, justify considering a separate **Wise-only production discovery integration**. First review the measured yield and how notification-only/manual application should work while SmartRecruiters Prepare remains unsupported. Preserve explicit restrictions and unknown states, and verify cross-source delivery dedup before scheduling. No broader SmartRecruiters rollout, Prepare integration or source/eligibility loosening is justified by this single pilot.

## Implementation, verification and reproducibility

The existing standalone watcher now performs bounded pagination, fetches details only for unique title matches, cleans all description sections, emits canonical public posting URLs and stable posting IDs, and deduplicates before detail fetches. Repeated/failing/malformed pages retain successful data with explicit errors or truncation; empty role content cannot be labeled FULL. The script validates request bounds, loads this checkout explicitly, restricts new output to this checkout's ignored pilot directory and excludes raw API error/creator metadata. Production company configuration, runtime scheduling, Telegram, Prepare and other ATS behavior are unchanged.

Codex independently ran **63 SmartRecruiters/script regressions** covering pagination, effective page sizes, missing metadata, partial failures, bounds, dedup, full detail validation, public URLs, stable identities, output isolation and privacy. **61 existing Greenhouse/Lever/Ashby/shared-prefilter tests** also passed. Ruff and `git diff --check` passed. Intermediate test-assertion failures and Ruff findings were corrected; no failures remain in the executed sets. No full test suite was run. Claude implemented the changes but could not execute Python tests in its session; the results here are Codex's actual runs.

The final watcher and actual pilot CLI replayed all 54 sanitized saved responses through `httpx.MockTransport`, making zero new live requests. They reproduced 417 raw/unique rows, 49 title matches/details/vacancies, zero errors and no truncation. All 49 source identities, titles, public URLs, locations and description texts matched the live capture exactly. `complete: true` describes bounded collection/detail completion under the stated title rules, not verified candidate eligibility or a claim to have fetched all 417 detailed ads.

An isolated-interpreter `--help` smoke check also passed. To run a fresh read-only pilot after reviewing this snapshot:

```sh
.venv/bin/python scripts/pilot_smartrecruiters.py --page-limit 100 --max-pages 10 --max-detail 100
```

Run from the checkout containing the accepted code. The script prints the timestamped output filename under `data/smartrecruiters-pilot/`, never overwrites an existing file, and does not schedule itself. This command is provided for reproducibility; no second live board scan was performed in this task.

Live public responses were captured with creator/contact metadata excluded and retained only under ignored `data/smartrecruiters-pilot/`. Snapshot SHA-256: `e759cdf8e1b8b596b887d6547a742f493281950d10649cb5cb9153875d8519e9`. Final accepted code was replayed against this snapshot, avoiding another board scan. The verified isolated result is `data/smartrecruiters-pilot/wise-final-report.json`. Manual annotations are in `wise-reviewed.json`; they are not runtime LLM scores.

The public interface is documented by [SmartRecruiters endpoints](https://developers.smartrecruiters.com/docs/endpoints), [list parameters](https://developers.smartrecruiters.com/reference/v1listpostings), [posting details](https://developers.smartrecruiters.com/reference/v1getposting), and [object definitions](https://developers.smartrecruiters.com/docs/objects). The public employer board is [Wise on SmartRecruiters](https://careers.smartrecruiters.com/Wise).
