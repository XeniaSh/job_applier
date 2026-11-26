# TASK-093 — Provisional vacancy scoring quality audit

Date: 2026-11-26. **Research only; no scoring/filter/policy changes.**
Task A's independent lifecycle-fixture repair is `f4a17af93e280803adf4c7452a1c0eb87c9239f3`
(76 focused regressions passed). No Claude, LLM reanalysis, production DB/cache
access, candidate-profile access, application preparation or submission was used
for this audit.

## Evidence and scope

The user explicitly approved using the available data with these limits:

- **10 persisted NZ decisions:** the existing sanitized export
  `data/nz-funnel-export.json`, generated 2026-10-26 14:10:01 UTC. It contains
  categorical decisions/recommendations, reason codes, seniority and eligibility
  flags. Numeric match scores, extraction/skill details and historical resume
  choices are **not present**. Individual application/delivery dispositions are
  not reproduced in this report.
- **10 prior manual Wise pilot reviews:** selected from 49 reviewed postings in
  `data/smartrecruiters-pilot/wise-reviewed.json`, joined by posting ID to public
  descriptions in `wise-final-report.json` (2026-10-28). These are previously
  processed discovery/review cases, **not production LLM-scored records**. No
  production decisions are invented for them.
- Current public snapshots of six previously NZ-relevant feeds, plus two fresh
  remote-role controls below, supply comparison evidence only. A changed live
  description cannot explain an older cached decision without the old text.
  Seven of the ten historical NZ IDs remain in today's sampled feeds; only four
  descriptions match their recorded SHA-256 hashes. Current resume replays are
  explicitly separate from historical outcomes.

Selection is deliberate, not random: Java/Kotlin, real AI integration,
incidental Java, NZ relocation, unclear/negative sponsorship, mixed seniority
and accepted/rejected boundaries. The 20-case core has insufficient historical
remote coverage; fresh remote controls are kept outside it. This is **not** a
20-record production-score calibration study, nor a precision/recall estimate.
All manual fit assessments below are analyst judgments, not user-validated gold
labels or findings about personal immigration eligibility.

## Ten persisted NZ cases

P = POTENTIAL_MATCH; I = IGNORE; CHECK = CHECK_MANUALLY. These are separate
technical and application-recommendation stages.

| Case / public historical role | Persisted decision / recommendation | Assessment and evidence limits |
| --- | --- | --- |
| NZ01 [Halter — People Ecosystem](https://jobs.ashbyhq.com/halter/407391fe-3484-4396-84e2-949987ff3025) | I / SKIP; technology mismatch | Previous review found Python/TypeScript/full-stack emphasis and optional AI workflows. A possible integration stretch, not a proven erroneous rejection. Today's text changed and selects AI-NZ in a raw-text resume replay; that does not establish the old resume or score was wrong. |
| NZ02 [Halter — Senior Platform](https://jobs.ashbyhq.com/halter/8eafdca9-3003-4730-b2ae-d1ddf9b7b7ce) | P / CHECK | Infrastructure/AWS/IaC focus, backend product work optional. A manual-review outcome is defensible; no evidence warrants STRONG. Current text differs from the export. |
| NZ03 [Halter — Intermediate Field Operations](https://jobs.ashbyhq.com/halter/e1fe638b-8fde-4071-86e6-6b47f5747f61) | I / SKIP; technology mismatch | Backend APIs and optional Kotlin coexist with investigations, operations and on-call. Boundary case worth retaining for later reviewed labels. Posting absent from today's feed; historical rejection cannot be conclusively overturned. |
| NZ04 [Halter — Technical Specialist / Network](https://jobs.ashbyhq.com/halter/e49ed481-13f2-4b26-b195-69a93aba3840) | P / CHECK | The previous audit observed a later customer-experience title under the same ID. Absent today. Identity/title drift makes a current score judgment unsafe. |
| NZ05 [Halter — Production Test Systems](https://jobs.ashbyhq.com/halter/e6c21fb9-7257-466b-8c03-fba7938a6dd1) | I / SKIP; technology mismatch | Historical Go/Python/factory systems scope supports caution. Now titled Senior Backend Engineer, Manufacturing Systems with changed text. Do not apply the old judgment to the new text or reset caches; the ordinary description-hash mechanism is the relevant boundary. |
| NZ06 [Halter — Lead Platform](https://jobs.ashbyhq.com/halter/f8b4cf6e-78d0-46ea-af8a-01dd9a8d9893) | P / SKIP; excluded LEAD_MANAGER | Exact text hash still matches. A technically plausible role may intentionally be excluded by level preference: P followed by SKIP is not scoring inconsistency. Lead does not independently prove people management. |
| NZ07 [Partly — Software Engineer, NZ](https://jobs.ashbyhq.com/partly.com/3bf14081-16d2-4ecf-a2d6-05ac2be81d75) | P / CHECK | Exact hash matches. Broad SaaS/API/generalist invitation; specific level/opening unclear. Relocation assistance is positive but visa support remains unknown. A lead warning incorrectly cites office architecture; see finding F2. |
| NZ08 [Partly — Senior Software Engineer, Christchurch](https://jobs.ashbyhq.com/partly.com/843134db-a865-4c36-8482-eb9d5b4b1b9e) | P / CHECK | Exact hash matches. Distributed systems/APIs/databases; 5+ years and optional Rust. Reasonable manual-review lead; no proof of technical rejection. Architecture fundamentals produce a misleading lead warning. |
| NZ09 [Partly — Senior Software Engineer, Auckland](https://jobs.ashbyhq.com/partly.com/ba7f90a3-8cb9-466e-a609-08fee6748e50) | P / CHECK | Exact hash matches; similar scope to NZ08. Separate posting ID does not prove separate hiring headcount. Same diagnostic warning defect; relocation is not work authorization. |
| NZ10 [Everbridge — Senior Software Engineer, Auckland](https://jobs.lever.co/everbridge/0e7dd2e6-aeea-426c-b46c-f1293da08bd0) | P / CHECK | Previous review found technology-agnostic SaaS/backend work including Java; sponsorship unknown. Absent from today's feed. No basis to relabel it as a missed/rejected role or fabricate current availability. |

Totals: **7 P, 3 I; 6 CHECK, 4 SKIP; zero persisted STRONG/APPLY_NOW**.
Three SKIPs follow IGNORE; the fourth follows excluded seniority. All ten have
unknown sponsorship/work-authorization flags. The three Partly cases record
relocation support. Unknown is not refusal, nor evidence of eligibility. These
selected NZ outcomes do not establish that the whole system is too conservative.

## Ten historical Wise review cases and deterministic replay

Replayed existing `classify_seniority(title)` and
`decide_resume_variant(ResumePolicyInput(title, description, location))` against
saved public text, with no LLM and no profile/resume-file loading. The resume
column is today's raw-text policy replay, **not a saved production choice**.

| Case / historical posting | Replay | Review assessment |
| --- | --- | --- |
| WI01 [Pricing Core — Senior Software Engineer I](https://jobs.smartrecruiters.com/Wise/744000158265079) | JUNIOR; java_backend | Java/Spring backend. Explicit Senior conflicts with the generic Engineer I rule; confirmed title-rule defect F1. Personal qualification and prior production disposition are unknown. |
| WI02 [Senior Backend Software Engineer](https://jobs.smartrecruiters.com/Wise/744000158264979) | SENIOR; java_backend | Java/OOP backend, but local-candidate-only and conditional sponsorship transfer. Transfer support must not become new visa sponsorship or international relocation eligibility. |
| WI03 [Data Onboarding & Reporting](https://jobs.smartrecruiters.com/Wise/744000158242549) | SENIOR; java_backend | Data engineering requires Trino/Iceberg/dbt/Airflow; Java mention is insufficient for a strong JVM-backend fit. Resume family does not mean technical match. |
| WI04 [Java — AML Handling](https://jobs.smartrecruiters.com/Wise/744000158102439) | SENIOR; java_backend | Mandatory work includes bringing AI capabilities into production, evaluation and human oversight. Prior manual review marked substantive AI work. Raw-text resume policy misses this phrasing: F3. Do not confuse it with the separate optional ML/LLM experience bullet. |
| WI05 [Rules Platform](https://jobs.smartrecruiters.com/Wise/744000156787429) | SENIOR; java_backend | Java/Kotlin, Kafka/SQL; saved text explicitly offers Tallinn relocation and visa support. Strong employer-side signal, not a personal visa decision. This is a historical ID; current acceptance used a newer canonical posting. |
| WI06 [Contact Automation — AI](https://jobs.smartrecruiters.com/Wise/744000156244029) | SENIOR; ai_backend | Substantial RAG/LLM/vendor integration and production support automation. Positive AI control; Java remains part of the core stack. Python/TypeScript breadth needs factual review. |
| WI07 [Send High Volume](https://jobs.smartrecruiters.com/Wise/744000155988185) | SENIOR; ai_backend | Java/Spring/Kafka backend. Coding-tool use and AI-first wording are not enough; shipping LLM features appears under an explicitly nonessential nice-to-have header. Optional-section leakage is reproducible: F4. |
| WI08 [Senior Software Engineer II — FX Markets](https://jobs.smartrecruiters.com/Wise/744000155375829) | SENIOR; java_backend | Saved review requires 8+ years. This is a role-requirement boundary, not proof of a candidate shortfall or grounds to invent a score. |
| WI09 [AML Risk](https://jobs.smartrecruiters.com/Wise/744000153669609) | UNKNOWN; java_backend | Saved description states 2–5 years. Generic title lacks an explicit level, so UNKNOWN is a factual title result; description-level fit requires review. Do not automatically call it senior or reject solely on the title. |
| WI10 [Business Onboarding and Verification — Senior Software Engineer I](https://jobs.smartrecruiters.com/Wise/744000152127649) | JUNIOR; java_backend | Explicit refusal of relocation/sponsorship is distinct from unclear policy. F1 also affects its title, but fixing that alone cannot remove its independent geographic/immigration constraint. |

Wise links identify the historical pilot records, not currently open
recommendations.

## Confirmed findings and recommendations — not implemented

**F1 — Explicit Senior is overridden by Engineer I (high priority).**
The generic I rule precedes the Senior rule in
`app/company_watch/seniority.py`. Across the complete 49-title pilot, **12 titles
containing explicit Senior replay as JUNIOR**, including WI01/WI10. This is a
deterministic classification count, not twelve proven lost deliveries. Where
JUNIOR is excluded, the recommendation layer can reject an otherwise suitable
role. Recommend a narrow precedence correction: preserve management/staff
guards and unqualified Engineer I => JUNIOR, but honor explicit Senior before
the unqualified numeral rule. Add mixed-signal regression cases. Do not change
global thresholds, remove junior guards or replay production caches.

**F2 — Lead diagnostics misread ordinary architecture (confirmed).**
On the unchanged NZ07/08/09 texts, `_build_lead_warning(v.to_analysis_text())`
flags office design or engineering fundamentals. It searches the substring
`architect` and fails to reset its source label at `Description:`, reporting
description evidence as title evidence. Lead Platform is a valid positive
control. Recommend a separate narrow warning/sourcing fix with these three
counterexamples. No evidence shows this warning caused their P rather than
STRONG decisions; it does not justify promoting those records.

**F3/F4 — AI resume fallback needs section-aware evidence review.**
WI04's mandatory production-AI work is missed, while WI07's optional LLM
experience selects AI. The latter nice-to-have header is separated from the
bullet by other items; sentence-local optional checks lose its scope. Recommend
captured regression fixtures and bounded raw-text recognition/section handling,
while preserving genuine mandatory AI duties, all four variants and the rule
that coding tools/branding alone are insufficient. The LLM-extraction path was
not replayed; actual historical resume selections and live attachment behavior
are unknown. No private CV content should be changed based on this audit.

**No supported case for broad scoring/filter relaxation.**
Java acceptance, incidental Java, JS, specialist data/ERP work, lead scope and
eligibility are different dimensions. The available cache flags cannot validate
point calibration, extracted mandatory-vs-optional skills or every rejected
NZ role. Potential false negatives NZ01/NZ03 remain uncertain. No confirmed
incorrect numeric score or erroneous production resume file is asserted.

## Remote, geography and technology controls outside the historical 20

Two fresh Xero postings illustrate the missing remote dimension without being
misrepresented as previously scored cases:

- [AI Harness](https://careers.xero.com/jobs/9f47da32-dd07-46ea-94ea-1e77d661af92/senior-full-stack-engineer-ai-harness/)
  is remote-first but its description requires being based in Wellington.
  It involves .NET/C# modernization and agent verification tooling, not an
  automatically suitable Java vacancy or worldwide-remote opportunity.
- [Product & Tech Operations](https://careers.xero.com/jobs/0ae2cf75-3e58-44c7-9a08-e681cbb36cee/senior-engineer-product-tech-operations/)
  requires actual production AI workflows and permits Australia/NZ-based work.
  Python/TypeScript/internal-tool ownership are explicit. This is an AI
  integration stretch to assess, not a claim of candidate eligibility.

A current Workday search for Java also returned a Westpac role whose essential
stack is JavaScript/web/SQL Server with optional C#/.NET. Upstream keyword hits
must not be treated as whole-word Java evidence. This is a source-search false
positive control, not a demonstrated defect in this project's tech matcher.

The NZ resume dimension remains tied to structured job location; relocation,
citizenship, residence, sponsorship and remote permission must not be conflated.
Every current replay with an unambiguous NZ job location selected an NZ variant.
Historical actual choices remain absent, and multi-country/description-only
remote scope is not permission to invent a location or work authorization.

## Reproducibility and next implementation proposal

Core cohort: all ten `nz_jobs` in the sanitized export; Wise review indices
`0,1,3,8,12,20,22,25,28,31`, joined by `external_id`, never list position in a
different snapshot. Current-source comparison uses the same existing normalizers
and `vacancy_description_hash`, with no cache writes. Local public snapshots and
CPU-only derivation are under ignored `data/discovery-research-20261126/`.
The archived full NZ public snapshot from October is no longer available; the
prior [NZ audit](nz-discovery-audit.md) remains secondary historical description
evidence for texts that changed or disappeared.

**Recommended next implementation: F1, explicit-Senior precedence**, approximately
half an engineering day including focused tests and review. It is smaller and
better evidenced than a score/model rewrite. F2 and F3/F4 are separate proposed
follow-ups. None is implemented or authorized by this research report.
