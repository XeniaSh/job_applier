# Desired-salary autofill

Applies to Greenhouse, Lever and Ashby through the shared autofill service
(`app/application/autofill/salary.py`, wired in `service._decide_salary`).
It never submits; `submit_performed` stays `false`. The filled field's note
records the chosen amount, currency, period, gross/net basis, source kind and
any public evidence URL/date so it can be verified during manual review.

## Configuration and opt-out

If `employment.salary_expectations` is **absent**, the published-range fallback
still applies (explicitly authorized); with no config there is no candidate
preference and no benchmark, and nothing is invented. If the object **exists**
with `fill_salary: false` (the default for an existing object) that is a full
opt-out, including published and benchmark sources, and any pre-existing
adapter fill of the salary field is discarded. Salary facts are never inherited
from the example profile.

## Source precedence

1. **Candidate preference** applicable to the vacancy.
2. **The one salary range published in this vacancy's description**, midpoint
   (exact `Decimal`). The pay word (salary/pay/compensation/base/remuneration),
   currency, gross/net and period must all sit in the same clause, sentence or
   table row; nothing is borrowed from adjacent prose (`annual conferences`
   never supplies a period). Any other money range outside a pay clause (e.g.
   a subscription price) makes the posting ambiguous, so it stays manual.
3. **A reviewed, dated benchmark** you configured.

Sources are resolved lazily in this order; a lower source is not parsed once a
higher one decides. Every source must match the field's own currency, period and gross/net basis
exactly. There is no FX, annual/monthly/hourly, or gross/net conversion.

## Optional config (`employment.salary_expectations`)

The numbers below illustrate the schema; they are not market benchmarks or
salary recommendations. Replace preferences with your own facts. Benchmark
records require a real source and an explicit review before they can be used.

```yaml
salary_expectations:
  fill_salary: true
  # Top-level preference (optional); needs ALL of amount/currency/period/basis.
  amount: 90000
  currency: EUR          # ISO code
  period: annual         # annual | monthly | hourly | daily
  basis: gross           # gross | net
  markets: [Germany]     # optional scope; exact vacancy location (or whole-country alias); empty = any
  roles: [backend engineer]   # optional whole-word match on the vacancy title
  preferences:           # additional scoped preferences, same fields as above
    - {amount: 6000, currency: EUR, period: monthly, basis: net, markets: [Poland]}
  benchmarks: []         # empty by default; the app ships and fetches none
  # - title: Senior Backend Engineer   # exact (case/space-insensitive) vacancy title
  #   market: Germany                  # must equal the vacancy's exact location (or whole-country alias)
  #   seniority: SENIOR                # must equal the title's classified seniority
  #   amount: 95000
  #   currency: EUR
  #   period: annual
  #   basis: gross
  #   source_url: https://example.org/report   # public http(s) URL
  #   source_date: null          # supply the SOURCE data date after checking the real report
  #   reviewed_on: 2026-09-01    # date you reviewed it; not future, not before source_date
  #   reviewed: false            # set true only after verifying the real source and all matching units/scopes
```

A benchmark with a missing `source_date`, `reviewed` not true, a non-positive
`amount`, or a non-public `source_url` is never used. Old data reviewed today
is not fresh. Market is matched only by exact location (see below).

A preference applies when its market/role scope matches and its currency
equals the field's currency. An applicable preference that is incomplete, or
whose period/basis differs from the field, keeps the field manual rather than
falling through to another source.

## What stays manual

- Current/present/last/previous/prior/historical pay, or a question mixing it
  with desired pay; bonus, equity, fee, OTE, commission and total-package controls.
- Labels that are not provably desired/expected salary.
- Fields whose own label lacks an unambiguous currency, period or gross/net
  basis (`$`, `¥`, `£`, "local currency", several currencies/periods, ...). ISO
  codes are case-insensitive. Units
  must come from the field's own non-empty label; an empty label with a
  context-only question stays manual.
- Own label only: prior/current-pay wording and units are read from the
  field's own label. Sibling `context` is never consulted, so it can neither
  block nor establish anything.
- Market scope: a configured `markets` / benchmark `market` entry must equal
  the vacancy's EXACT observed location after case/whitespace normalization
  (`Berlin, Germany` matches only a `Berlin, Germany` entry). A country alias
  (`DE`, `Germany`) applies only when the whole vacancy location is that one
  country. Nothing is inferred from part of a longer location (`Cuba, Germany`,
  `Berlin, Germany` do not match `Germany`); such fields stay manual.
- Published ranges with a non-positive endpoint (`EUR 0 - 100000`) are rejected.
- Scaled amounts, never converted: a field label asking for thousands/millions/
  lakh/crore/LPA or an `in k`/`in m` annotation, or a CTC / total-cost-to-company
  component, stays manual. A published clause using such a scale (`INR 20 - 30
  LPA`, `EUR 1 - 2 million`) makes published pay manual and blocks the benchmark.
  Plain suffix-k source ranges (`EUR 75k-80k`) remain supported.
- Select/radio/option controls and anything but plain text/number inputs.
- Several or conflicting published ranges, currencies or components, ambiguous
  number formats (`80.000`), or a posting naming more than one country.
- Ambiguous market (several/unknown countries) for market-scoped sources;
  unknown seniority; stale (>365 days), future-dated, unsourced or conflicting
  benchmarks.

A filled value is the plain numeric string (e.g. `90000`). Readback must be
numerically equal (`80000` never confirms `180000` or `abc80000`).

## Verification and supervised acceptance

Local coverage includes source precedence, explicit opt-out, absent-config
published fallback, exact scopes, benchmark review/source dates, ambiguous and
scaled units, strict read-back, and the shared service path for all three ATS.
The real Ashby adjustment hook participates in offline service tests; its
local-currency browser fixture passes in an isolated Chromium process. Broader
checks and the separate pre-existing Ashby location-fixture failure are recorded
in `PROGRESS.md`.

The `salary_autofill` log describes the chosen amount and evidence; it does not
claim that a write succeeded. A field is listed as filled only after read-back.
No live candidate form acceptance is claimed. After integration, supervise one
existing Greenhouse/Lever/Ashby Prepare with a fully specified salary label and
valid evidence. Review the amount, currency, gross/net basis, period and source,
correct it manually if needed, and finish Done reviewing without submitting.
