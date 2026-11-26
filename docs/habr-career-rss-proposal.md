# Bounded proposal: Habr Career RSS discovery

Historical proposal; superseded by the approved
[Phase A implementation and measured report](habr-career-rss-pilot-2026-11-26.md).
Phase B subsequently received revised approval and is
[implemented with default-off activation](habr-career-phaseb.md).

Original proposal status: **no collector implemented or activated**. Uses the
2026-11-26 research already recorded in the employment strategy; no new live
requests, accounts, subscriptions or application sessions are required by this
proposal. HH, Telegram scraping and application autofill are outside scope.

## Evidence and first checkpoint

The public Habr search page advertised this RSS link, which returned HTTP 200:

`https://career.habr.com/vacancies/rss?page=1&per_page=25&q=Java&remote=1`

It returned 50 distinct items despite `per_page=25`; 12 plausible JVM development
titles survived obvious QA/junior/specialist exclusions, before full qualification.
Descriptions were snippets, not full requirements. Salary appeared in 13 titles,
three of the 12 development candidates. These are sample observations, not new
eligible vacancies/week. API documentation navigation exists but a usable public
vacancy-search API contract was not verified. Use advertised RSS, not an internal
JSON endpoint. Review feed/enrichment scope under Habr's published terms before
runtime integration; public access is not a bulk republication license.

Checkpoint A: a standalone, read-only RSS pilot producing sanitized normalized
vacancies and a funnel report, with **no LLM calls, Telegram delivery or DB writes**.
Start with the advertised Java/remote feed; add a Kotlin feed only after its
advertised query/filter behavior is verified. This is the first approval scope,
estimated 1–2 engineering days including fixture tests.

## Supported access and bounded pagination

- Ordinary public HTTPS only, trusted `career.habr.com` host and advertised feed
  links. No login, browser, CAPTCHA solution, private API, proxy or fingerprint work.
- One bounded request per configured feed per cycle, explicit connection/read
  deadlines, response-byte/item caps and conditional requests if advertised
  ETag/Last-Modified support is verified. Do not promise those headers exist yet.
- Treat RSS as a rolling window initially. `page`/`per_page` behavior, date order
  and completeness are **not verified**. Do not increment pages blindly or claim
  an exhaustive crawl. A later normal public pagination check must prove distinct
  pages; repeated IDs/content stop traversal. No backfill of old history.
- Report timeout, malformed feed, empty feed, 403/429/5xx and truncated-window
  outcomes distinctly. Respect rate-limit information; no repeated access-denial
  probes. Discovery error handling must leave Telegram polling operational.

These are request/resource bounds, not new delivery quotas or geographic filters.

## Parsing and identity

Use a safe XML parser with response limits and DTD/entity rejection. Extract
title, link/guid, timezone-aware pubDate, employer and description snippet when
present. Normalize HTML without copying recruiter contacts. Validate a canonical
numeric posting ID from `https://career.habr.com/vacancies/<id>`; reject other
hosts/schemes and malformed identities. Use canonical source `habr` plus that ID,
not a title or query as identity. Merge repeat observations across query feeds.

Keep salary currency, range, period and gross/net basis only when explicitly
published. “Similar specialists earn” estimates are not the employer's salary.
Grade tags are evidence; a low salary or `ведущий` alone does not prove junior
or management scope. Avoid JavaScript/Java, JVM/QA and Kotlin/mobile confusion.

RSS-only records must be PARTIAL, with missing employer/location/requirements
explicit. The current generic pipeline passes FULL to its analyzer, so runtime
integration must first add a focused completeness-preserving path and tests;
do not plug snippets into it unchanged. Missing/image-only job descriptions stay
manual; no OCR or invented full description. Existing four resume variants and
salary/factual safety mechanisms remain unchanged.

Optional bounded detail enrichment is a **separate checkpoint**: only trusted
public Habr posting pages, text completeness verified, per-cycle request budget,
no automatic following of external Apply destinations. Stop on access restrictions.
The pilot can be valuable as a manually reviewed shortlist without enrichment.

## Deduplication, policy and eligibility

Primary dedup is `(habr, numeric_id)` across queries and cycles. Publication
bumps or a changed RSS pubDate do not create a new application or resend an old
notification. Keep a content hash separate from identity; reuse technical
analysis when unchanged and reevaluate current deterministic policy without LLM.
Preserve APPLIED/SKIPPED/undo history. No seen/cache reset or bulk replay.

For HH/Telegram/getmatch/employer copies, associate exact canonical employer/ATS
posting IDs or verified links when available. Otherwise flag likely duplicates
for review; similar titles alone must not merge distinct openings. Preserve all
source identities while counting one actual application attempt.

Apply the configured domain policy before analysis and delivery. Do not infer
Russian team language, cross-border payroll or citizenship from a Russian ad.
Unknown client/entertainment projects need domain confirmation. No ordinary
fintech, games or payment-industry keyword ban.

Classify vacancy-side route evidence separately from candidate facts:

- Explicit worldwide/abroad work: verify actual supported countries, legal entity,
  contract/EOR/employee route, timezone and any required existing rights.
- RF-only, outside-RF-only, country whitelist, resident-status wording and office
  requirements: preserve the exact constraint; missing candidate compatibility
  is manual, not a nationality/location-based decision.
- No sponsorship plus moving assistance cannot become visa support. A limited
  remote alternative remains manual when compatibility is unknown, not rejected
  as though every route required an employer relocation visa.
- Real production LLM integration differs from coding tools/AI marketing; missing
  duties cannot select an AI profile by guesswork. NZ variants stay tied to the
  structured vacancy location, not candidate location or intended immigration.

## Telegram integration checkpoint B — separate approval

Estimated another 1–2 days **after** checkpoint A is reviewed; larger interface
work must be split and proposed, not silently expanded.

Reuse regular discovery/analysis/history/manual application delivery and batch
prioritization. Add an explicit manual-source registration with trusted Habr
posting URLs and a short callback code (proposed `hb`). Existing source-code
mapping currently rejects unknown sources; do not masquerade as Greenhouse or
SmartRecruiters. The target-company form gate also assumes ATS-hosted forms;
use a reviewed manual-discovery route rather than weakening its URL protections.

Cards: canonical public posting link, fit/completeness, salary evidence and
manual eligibility notice; existing Applied/Skip/Undo workflow where supported.
No Prepare button, artifact-generation button, application resolver/adapter,
CV upload or Submit path. Forged/stale Prepare callbacks must remain unavailable.
First integration is default-off pending explicit activation approval; never
resend historical notifications automatically. No unsolicited pilot messages.

Collection runs in the existing discovery worker, not the Telegram poller or
Prepare worker. Keep per-source raw/unique/prefilter/new/analyzed/manual/eligible/
sent/error counters without candidate data, private contacts or raw descriptions
in logs. Do not equate an employer-side route with personal legal eligibility.

## Acceptance and stop conditions

Offline fixtures must cover RSS escaping/HTML, malformed XML/DTD, missing fields,
Unicode/Russian titles, QA/mobile/JavaScript negatives, grade ambiguity, real AI
duties, salary estimates versus offers, dates/timezones, repeat/bumped posts,
query overlap, invalid URLs, incomplete/image-only text, all five domain policies,
country/contract/visa alternatives, cache-policy refresh and dedup/history.

Checkpoint B also needs provider/source mapping, callbacks/Undo, completeness
propagation, manual eligibility labels, polling isolation, default-off rollout,
no Prepare/Submit, existing ATS regressions and all four resume families.

Stop and propose a separate scope if supported access needs authentication or
provider permission, RSS is unavailable/incorrectly filtered, full text cannot be
verified, source routing needs a larger shared architecture change, or observed
cross-border relevance is weak. Review a small manually labeled sample over a
7–14 day observation window before broad integration. Measure viable leads and
eventual interviews per review effort; do not promise numeric weekly yield.
