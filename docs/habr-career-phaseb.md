# Habr Career Phase B

The user's revised acceptance supersedes the Phase A stop: unknown country or
contract eligibility is CHECK_MANUALLY, not a reason to block source integration.
No production activation, database modification, Telegram send or LLM call is
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
401/403/429 stops additional detail requests in that source cycle. Cache reads
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
cache, throttling, redirects/access failures and URL rejection. Runtime integration
and final regression results are recorded below when complete.
