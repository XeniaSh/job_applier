"""Deterministic desired-salary resolver (see docs/salary-autofill.md).

Precedence: (1) an explicit, applicable candidate preference, (2) the single
salary range published in this vacancy, (3) an explicitly configured, reviewed,
dated benchmark. Every source must exactly match the field's own currency,
period and gross/net basis; nothing is converted, inferred, or invented, and
anything ambiguous stays manual. No network, browser, or model access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urlsplit
import re

from app.application.autofill.fields import DiscoveredField
from app.application.candidate_profile import (
    SalaryBenchmark,
    SalaryExpectations,
    SalaryPreference,
    countries_mentioned,
    normalize_country_name,
)
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.seniority import SENIORITY_UNKNOWN, classify_seniority

BENCHMARK_MAX_AGE_DAYS = 365

SOURCE_CANDIDATE = "candidate_preference"
SOURCE_PUBLISHED = "published_range"
SOURCE_BENCHMARK = "reviewed_benchmark"

_ISO_CURRENCIES = frozenset(
    "EUR USD GBP CHF SEK NOK DKK PLN CZK HUF RON BGN CAD AUD NZD JPY CNY INR BRL MXN SGD HKD AED ILS ZAR THB".split()
)
_ISO_RE = re.compile(r"(?<![a-z])(" + "|".join(sorted(_ISO_CURRENCIES)) + r")(?![a-z])", re.IGNORECASE)
_SYMBOL_CURRENCIES = {"€": "EUR"}
# Several currencies share these symbols (dollars, yen/yuan, pounds): never inferred.
_AMBIGUOUS_SYMBOLS = ("$", "¥", "£")

_PERIOD_PATTERNS = {
    "annual": re.compile(r"\b(?:annual|annually|yearly|per\s+year|per\s+annum|a\s+year|p\.a)\b|/\s*(?:year|yr)\b"),
    "monthly": re.compile(r"\b(?:monthly|per\s+month|a\s+month)\b|/\s*(?:month|mo)\b"),
    "hourly": re.compile(r"\b(?:hourly|per\s+hour|an\s+hour)\b|/\s*(?:hour|hr)\b"),
    "daily": re.compile(r"\b(?:daily|per\s+day|a\s+day)\b|/\s*day\b"),
}
_PERIOD_SYNONYMS = {
    "annual": "annual", "annually": "annual", "yearly": "annual", "year": "annual",
    "per year": "annual", "per annum": "annual", "annum": "annual",
    "monthly": "monthly", "month": "monthly", "per month": "monthly",
    "hourly": "hourly", "hour": "hourly", "per hour": "hourly",
    "daily": "daily", "day": "daily", "per day": "daily",
}
_GROSS_RE = re.compile(r"\bgross\b|before\s+tax|\bpre-?tax\b")
_NET_RE = re.compile(r"\bnet\b|after\s+tax|\bpost-?tax\b|take[- ]home")
_LOCAL_CURRENCY = "local currency"

# Prior/historical pay: the desired-salary preference must never answer these.
PRIOR_PAY_RE = re.compile(
    r"\b(?:current|currently|present|last|previous|previously|prior|past|former|historical|existing|earlier|earn|earned|earning|earnings)\b"
)
_DESIRED_RE = re.compile(
    r"\b(?:desired|expected|expectation|expectations|expecting|target|preferred|requirement|requirements|minimum|asking|wanted|looking\s+for)\b"
)
# Controls whose amount is not a plain base salary figure.
_COMPONENT_RE = re.compile(
    r"bonus|equity|stock|\boptions?\b|\bote\b|on[- ]target|total\s+(?:package|compensation|comp|cash|reward)|\bpackage\b"
    r"|\bfees?\b|commission|variable|benefits?|sign[- ]?on|relocation|\bshares?\b|\brsu"
    r"|\bctc\b|cost\s+to\s+company"
)
# Amount scales are never converted: a scaled field or published clause stays manual.
# Lakh/crore/LPA are salary-specific, so they block without a separate pay cue.
_SCALE_STRONG_RE = re.compile(r"\b(?:lakhs?|lacs?|crores?|lpa)\b", re.IGNORECASE)
_SCALE_WORD_RE = re.compile(r"\b(?:thousands?|millions?|billions?|mm|bn)\b", re.IGNORECASE)
_CUR_PREFIX = r"(?:[€$£¥]|" + "|".join(sorted(_ISO_CURRENCIES)) + r")"
_SCALE_ANNOTATION_RE = re.compile(
    rf"\bin\s+{_CUR_PREFIX}?\s?[km]\b|\(\s*{_CUR_PREFIX}?\s?[km]\s*\)|(?<![a-z]){_CUR_PREFIX}\s?[km]\b|(?<![\w'])[km]\s?{_CUR_PREFIX}",
    re.IGNORECASE,
)
_DIGIT_M_RE = re.compile(r"\d\s?m\b", re.IGNORECASE)  # `1.2m`; digit-k is the supported suffix
# Only a real pay word counts; period words (annual/monthly/...) and "range" do not.
_PAY_CUE_RE = re.compile(r"salary|salaries|compensation|\bpay\b|\bbase\b|remuneration")
_DURATION_AFTER_RE = re.compile(r"\s?(?:calendar\s+)?(?:months?|years?|yrs?|weeks?|days?)\b")

_NUM = r"\d[\d,. ]*\d|\d"
_CUR = r"(?:" + "|".join(sorted(_ISO_CURRENCIES)) + r"|€|£|\$|¥)"
_RANGE_RE = re.compile(
    rf"(?P<c1>{_CUR})?\s?(?P<a>{_NUM})\s?(?P<ka>[kK])?\s?(?P<c2>{_CUR})?\s?(?:-|–|—|\bto\b)\s?"
    rf"(?P<c3>{_CUR})?\s?(?P<b>{_NUM})\s?(?P<kb>[kK])?\s?(?P<c4>{_CUR})?(?!\s?(?:million|billion|mm?\b|bn\b))",
    re.IGNORECASE,
)
_THOUSANDS_COMMA_RE = re.compile(r"^\d{1,3}(?:[, ]\d{3})+$")
_PLAIN_DECIMAL_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")


@dataclass(frozen=True)
class FieldUnits:
    currency: str | None
    period: str | None
    basis: str | None


@dataclass(frozen=True)
class SalaryDecision:
    amount: str | None = None
    currency: str | None = None
    period: str | None = None
    basis: str | None = None
    source_kind: str | None = None
    evidence_url: str | None = None
    evidence_date: str | None = None
    reason: str | None = None

    @property
    def fill(self) -> bool:
        return self.amount is not None

    def note(self) -> str:
        if not self.fill:
            return self.reason or "desired salary requires manual review"
        parts = [
            f"Desired salary {self.amount} {self.currency} {self.period} {self.basis}",
            f"source={self.source_kind}",
        ]
        if self.evidence_url:
            parts.append(f"evidence={self.evidence_url}")
        if self.evidence_date:
            parts.append(f"evidence_date={self.evidence_date}")
        return "; ".join(parts) + " -- verify before submitting."


def _manual(reason: str) -> SalaryDecision:
    return SalaryDecision(reason=reason)


def is_prior_pay_text(text: str) -> bool:
    return bool(PRIOR_PAY_RE.search(" ".join(text.lower().split())))


def normalize_period(value: str | None) -> str | None:
    return _PERIOD_SYNONYMS.get(" ".join((value or "").lower().replace("_", " ").split()))


def normalize_basis(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned if cleaned in {"gross", "net"} else None


def normalize_currency(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    return cleaned if cleaned in _ISO_CURRENCIES else None


def _currency_from_text(text: str) -> str | None:
    """One unambiguous ISO currency, or None (missing, `$`-only, local, conflicting)."""
    if _LOCAL_CURRENCY in text.lower():
        return None
    found = {code.upper() for code in _ISO_RE.findall(text)}
    found.update(code for symbol, code in _SYMBOL_CURRENCIES.items() if symbol in text)
    if re.search(r"\beuros?\b", text, re.IGNORECASE):
        found.add("EUR")
    if len(found) != 1:
        return None
    return next(iter(found))


def _single(patterns: dict[str, re.Pattern[str]], lowered: str) -> str | None:
    hits = [name for name, pattern in patterns.items() if pattern.search(lowered)]
    return hits[0] if len(hits) == 1 else None


def _period_from_text(lowered: str) -> str | None:
    return _single(_PERIOD_PATTERNS, lowered)


def _basis_from_text(lowered: str) -> str | None:
    gross, net = bool(_GROSS_RE.search(lowered)), bool(_NET_RE.search(lowered))
    if gross == net:
        return None
    return "gross" if gross else "net"


def parse_units(text: str) -> FieldUnits:
    lowered = " ".join(text.lower().split())
    return FieldUnits(_currency_from_text(text), _period_from_text(lowered), _basis_from_text(lowered))


def format_amount(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _parse_number(token: str) -> Decimal | None:
    token = token.strip()
    try:
        if "," in token and "." in token:
            return None
        if "," in token or " " in token:
            if not _THOUSANDS_COMMA_RE.match(token):
                return None
            return Decimal(re.sub(r"[, ]", "", token))
        if "." in token:
            # `80.000` could be a thousands separator: ambiguous, never guessed.
            return Decimal(token) if _PLAIN_DECIMAL_RE.match(token) and not re.search(r"\.\d{3}$", token) else None
        return Decimal(token)
    except InvalidOperation:
        return None


def public_url(url: str | None) -> str | None:
    """http(s) scheme + host + path only; no userinfo, port, query, or fragment."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host:
        return None
    return f"{parts.scheme}://{host}{parts.path}"[:200]


# `countries_mentioned` only knows a handful of countries, so a partial list
# must never prove a single market. These names exist solely to detect that a
# location lists another country; they are not a geocoder.
_MULTI_LOCATION_RE = re.compile(r"[;/|&+]|\b(?:and|or|und|et)\b", re.IGNORECASE)
_OTHER_COUNTRY_NAMES = frozenset(
    {
        "france", "germany", "deutschland", "spain", "italy", "portugal", "netherlands", "belgium", "austria",
        "switzerland", "poland", "ireland", "sweden", "norway", "denmark", "finland", "iceland", "czechia",
        "czech republic", "romania", "hungary", "greece", "bulgaria", "croatia", "serbia", "slovakia", "slovenia",
        "lithuania", "latvia", "estonia", "luxembourg", "malta", "cyprus", "uk", "united kingdom", "england",
        "scotland", "wales", "usa", "us", "united states", "canada", "australia", "india", "brazil", "mexico",
        "argentina", "chile", "colombia", "singapore", "japan", "china", "israel", "ukraine", "turkey", "uae",
        "south africa", "new zealand", "egypt", "nigeria", "kenya", "philippines", "indonesia",
    }
)


def _is_country_name(part: str) -> bool:
    return _normalized(part) in _OTHER_COUNTRY_NAMES or bool(countries_mentioned(part))


def _multiple_locations(location: str) -> bool:
    if _MULTI_LOCATION_RE.search(location) or len(countries_mentioned(location)) > 1:
        return True
    return sum(_is_country_name(part) for part in location.split(",")) > 1


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _vacancy_market(vacancy: NormalizedVacancy | None) -> str | None:
    """The exact normalized observed location; a country alias (`DE`) resolves
    only when the WHOLE location is that single country. Nothing is inferred
    from parts of a longer location."""
    if vacancy is None or not (vacancy.location or "").strip():
        return None
    return normalize_country_name(vacancy.location or "")


def _scope_key(configured: str) -> str:
    return normalize_country_name(configured)


def _units_label(units: FieldUnits) -> str:
    return f"{units.currency}/{units.period}/{units.basis}"


def _has_scale_marker(lowered: str, *, field_label: bool = False) -> bool:
    """Scale words/notations (thousands, million, lakh, LPA, `in k`) in a label or clause.

    A field label with any such marker, or a standalone `k`/`m`, is scaled. In a
    published clause a pay cue is required for the generic words; digit-k stays
    the supported range suffix.
    """
    if _SCALE_STRONG_RE.search(lowered) or _SCALE_ANNOTATION_RE.search(lowered):
        return True
    if field_label and re.search(r"(?<![\w-])[km](?![\w-])", lowered):
        return True
    if _SCALE_WORD_RE.search(lowered) or _DIGIT_M_RE.search(lowered):
        return field_label or bool(_PAY_CUE_RE.search(lowered))
    return False


def _check_field(field: DiscoveredField) -> tuple[FieldUnits | None, str | None]:
    # Units and prior/current-pay checks use the field's OWN label only;
    # context may hold siblings and is never consulted.
    text = field.label
    lowered = _normalized(text)
    if field.field_type not in {"text", "number"} or field.options:
        return None, "desired salary control is not a plain numeric/text amount input"
    if not lowered:
        return None, "salary control has no label of its own to establish units"
    if PRIOR_PAY_RE.search(lowered):
        return None, "question concerns current/prior pay, never answered from desired salary"
    if _COMPONENT_RE.search(lowered):
        return None, "salary control is a bonus/equity/fee/total-package style component, not base desired salary"
    if _has_scale_marker(lowered, field_label=True):
        return None, "salary control asks for a scaled amount (thousands/millions/lakh/LPA/k); no scale conversion is made"
    if not _DESIRED_RE.search(lowered):
        return None, "salary control is not provably a desired/expected salary question"
    units = parse_units(text)
    if units.currency is None:
        return None, "salary control has no unambiguous currency in its own label"
    if units.period is None:
        return None, "salary control has no unambiguous period in its own label"
    if units.basis is None:
        return None, "salary control has no explicit gross/net basis in its own label"
    return units, None


def _decision(units: FieldUnits, amount: Decimal, source: str, url: str | None = None, when: str | None = None) -> SalaryDecision:
    return SalaryDecision(
        amount=format_amount(amount),
        currency=units.currency,
        period=units.period,
        basis=units.basis,
        source_kind=source,
        evidence_url=url,
        evidence_date=when,
    )


def _candidate_preferences(expectations: SalaryExpectations) -> list[SalaryPreference]:
    prefs: list[SalaryPreference] = []
    if any(v is not None for v in (expectations.amount, expectations.currency, expectations.period, expectations.basis)):
        prefs.append(
            SalaryPreference(
                amount=expectations.amount,
                currency=expectations.currency,
                period=expectations.period,
                basis=expectations.basis,
                markets=expectations.markets,
                roles=expectations.roles,
            )
        )
    prefs.extend(expectations.preferences)
    return prefs


def _scope_applies(pref: SalaryPreference, market: str | None, title: str) -> bool | None:
    """True/False, or None when a configured market scope cannot be decided."""
    if pref.markets:
        if market is None:
            return None
        if market not in {_scope_key(m) for m in pref.markets}:
            return False
    if pref.roles:
        wanted = [_normalized(r) for r in pref.roles if r.strip()]
        lowered = _normalized(title)
        if not any(re.search(rf"\b{re.escape(role)}\b", lowered) for role in wanted):
            return False
    return True


def _from_candidate(
    expectations: SalaryExpectations, units: FieldUnits, market: str | None, title: str
) -> SalaryDecision | None:
    amounts: set[int] = set()
    for pref in _candidate_preferences(expectations):
        pref_currency = (pref.currency or "").strip()
        if pref_currency and normalize_currency(pref_currency) != units.currency:
            continue  # a different-currency preference is a different scope
        scope = _scope_applies(pref, market, title)
        if scope is False:
            continue
        if scope is None:
            return _manual("candidate salary preference is market-scoped but the vacancy market is ambiguous")
        if not (pref.amount and pref.amount > 0 and normalize_currency(pref_currency)
                and normalize_period(pref.period) and normalize_basis(pref.basis)):
            return _manual("applicable candidate salary preference is incomplete (needs amount, currency, period, gross/net basis)")
        if normalize_period(pref.period) != units.period or normalize_basis(pref.basis) != units.basis:
            return _manual("candidate salary preference period/basis does not match the field; no conversion is made")
        amounts.add(pref.amount)
    if len(amounts) > 1:
        return _manual("multiple conflicting applicable candidate salary preferences")
    if amounts:
        return _decision(units, Decimal(next(iter(amounts))), SOURCE_CANDIDATE)
    return None


def _strip_markup(text: str) -> str:
    text = re.sub(r"(?i)</(?:p|li|div|h\d|tr)>|<br\s*/?>", "\n", text)
    return unescape(re.sub(r"<[^>]+>", " ", text))


def _published_date(vacancy: NormalizedVacancy) -> str | None:
    match = re.match(r"\d{4}-\d{2}-\d{2}", vacancy.published_at or "")
    return match.group(0) if match else None


def _clauses(text: str) -> list[str]:
    """Lines / sentences / `;` parts."""
    text = re.sub(r"(?i)\bp\.\s?a\b\.?", "per annum", text)
    return [clause for line in text.split("\n") for clause in re.split(r"(?<=[.!?;])\s+", line)]


def _clause_ranges(text: str) -> list[tuple[str, re.Match[str]]]:
    """Every numeric range paired with its own clause."""
    return [(clause, match) for clause in _clauses(text) for match in _RANGE_RE.finditer(clause)]


def _looks_like_unlabelled_pay(window: str, match: re.Match[str]) -> bool:
    """A currency-less range in a pay clause, whatever its amount.

    Such evidence is published pay with a missing currency: it must block lower
    sources and stay manual. Only ranges directly followed by a duration unit
    (`3-6 months`, `3-5 years`) are ignored as non-pay.
    """
    lowered = window.lower()
    if not _PAY_CUE_RE.search(lowered) or _COMPONENT_RE.search(lowered) or is_prior_pay_text(window):
        return False
    if _DURATION_AFTER_RE.match(lowered, match.end()):
        return False
    return True


@dataclass(frozen=True)
class PublishedSalaryBand:
    low: Decimal
    high: Decimal
    units: FieldUnits


def published_salary_band(vacancy: NormalizedVacancy | None) -> PublishedSalaryBand | SalaryDecision | None:
    """The single explicit published band, or the existing manual decision.

    Shared by numeric salary resolution and band-acceptance questions. Parsing
    establishes evidence only; it never establishes a candidate's consent.
    """
    if vacancy is None:
        return None
    ranges: set[tuple[Decimal, Decimal, FieldUnits]] = set()
    ambiguous = False
    description = _strip_markup(vacancy.description or "")
    # Before any range parsing: a scaled clause must not be read as whole units
    # (`INR 20 - 30 LPA`) nor silently vanish and let a lower source decide.
    if any(_has_scale_marker(_normalized(clause)) for clause in _clauses(description)):
        return _manual("published salary uses an unsupported amount scale (thousands/million/lakh/LPA); no scale conversion is made")
    for window, match in _clause_ranges(description):
        tokens = [match.group(g).upper() for g in ("c1", "c2", "c3", "c4") if match.group(g)]
        if not tokens and not _looks_like_unlabelled_pay(window, match):
            continue
        # The pay cue, currency, gross/net and period must all sit in this
        # one clause/row; nothing is borrowed from adjacent prose.
        if not _PAY_CUE_RE.search(window.lower()):
            if not (_COMPONENT_RE.search(window.lower()) or is_prior_pay_text(window)):
                ambiguous = True  # a money range that is not provably pay
            continue
        low, high = _parse_number(match.group("a")), _parse_number(match.group("b"))
        if low is None or high is None:
            return _manual("published salary range uses an ambiguous number format")
        if match.group("kb") and not match.group("ka"):
            if low >= 1000:
                return _manual("published salary range has inconsistent k-suffix notation")
            low *= 1000
        elif match.group("ka") and not match.group("kb"):
            return _manual("published salary range has inconsistent k-suffix notation")
        elif match.group("ka"):
            low *= 1000
        if match.group("kb"):
            high *= 1000
        if low <= 0 or high <= 0:
            return _manual("published salary range has a non-positive endpoint")
        if low > high:
            return _manual("published salary range is inverted")
        if _COMPONENT_RE.search(window.lower()) or is_prior_pay_text(window):
            return _manual("published pay figures include bonus/equity/prior-pay components")
        currencies = {_SYMBOL_CURRENCIES.get(t, t) for t in tokens} - set(_AMBIGUOUS_SYMBOLS)
        if len(currencies) > 1:
            return _manual("published salary ranges use conflicting currencies")
        wdw = _normalized(window)
        range_units = FieldUnits(next(iter(currencies)) if currencies else None, _period_from_text(wdw), _basis_from_text(wdw))
        ranges.add((low, high, range_units))
    if ambiguous:
        return _manual("vacancy has a money range outside a salary/pay clause; published pay is ambiguous")
    if not ranges:
        return None
    if len(ranges) > 1:
        return _manual("vacancy publishes multiple or conflicting salary ranges")
    low, high, range_units = next(iter(ranges))
    if None in (range_units.currency, range_units.period, range_units.basis):
        return _manual("published salary range lacks explicit currency, period, or gross/net basis")
    if _multiple_locations(vacancy.location or ""):
        return _manual("published salary range cannot be tied to one market (multiple vacancy countries)")
    return PublishedSalaryBand(low, high, range_units)


def _from_published(units: FieldUnits, vacancy: NormalizedVacancy | None) -> SalaryDecision | None:
    band = published_salary_band(vacancy)
    if not isinstance(band, PublishedSalaryBand):
        return band
    range_units = band.units
    if range_units != units:
        return _manual(
            f"published range units ({_units_label(range_units)}) do not match field units ({_units_label(units)}); no conversion is made"
        )
    return _decision(units, (band.low + band.high) / 2, SOURCE_PUBLISHED, public_url(vacancy.url), _published_date(vacancy))


def resolve_salary_band_acceptance(
    expectations: SalaryExpectations | None, *, title: str, vacancy: NormalizedVacancy | None,
) -> tuple[bool | None, str]:
    """Conservative judgement from explicit preference AND published evidence.

    Inside the inclusive band means Yes; above it means No. A preference below
    the band does not establish that this different band meets expectations.
    Benchmarks and published midpoints are never candidate preferences.
    """
    if expectations is None or not expectations.fill_salary:
        return None, "salary-band judgement requires enabled explicit candidate preferences"
    band = published_salary_band(vacancy)
    if not isinstance(band, PublishedSalaryBand):
        return None, band.reason if band is not None else "no unambiguous published salary band"
    candidate = _from_candidate(expectations, band.units, _vacancy_market(vacancy), title)
    if candidate is None or not candidate.fill:
        return None, candidate.reason if candidate is not None else "no applicable explicit candidate salary preference"
    amount = Decimal(candidate.amount)
    if amount < band.low:
        return None, "candidate preference is below the band; acceptance requires manual judgement"
    return amount <= band.high, "explicit candidate preference compared with one published band; no conversion"


def _benchmark_is_valid(b: SalaryBenchmark, today: date) -> bool:
    """Reviewed, positive, publicly sourced, and fresh by its SOURCE date."""
    if not b.reviewed or b.amount <= 0 or not public_url(b.source_url) or b.source_date is None:
        return False
    if b.source_date > today or b.reviewed_on > today or b.reviewed_on < b.source_date:
        return False
    return (today - b.source_date).days <= BENCHMARK_MAX_AGE_DAYS


def _from_benchmark(
    expectations: SalaryExpectations, units: FieldUnits, market: str | None, title: str, today: date
) -> SalaryDecision | None:
    if not expectations.benchmarks:
        return None
    seniority = classify_seniority(title).label
    if seniority == SENIORITY_UNKNOWN:
        return None
    if market is None:
        return _manual("salary benchmark depends on the market but the vacancy location is missing or ambiguous")
    in_scope = [
        b
        for b in expectations.benchmarks
        if _normalized(b.title) == _normalized(title)
        and _scope_key(b.market) == market
        and b.seniority.strip().upper() == seniority
    ]
    if not in_scope:
        return None
    valid = [b for b in in_scope if _benchmark_is_valid(b, today)]
    if not valid:
        return _manual(
            f"matching salary benchmark is unreviewed, stale (source >{BENCHMARK_MAX_AGE_DAYS} days old), "
            "future-dated, non-positive, or lacks a source date or public source URL"
        )
    matching = [
        b for b in valid
        if (normalize_currency(b.currency), normalize_period(b.period), normalize_basis(b.basis))
        == (units.currency, units.period, units.basis)
    ]
    if not matching:
        return _manual("reviewed benchmark units do not match field units; no conversion is made")
    if len({b.amount for b in matching}) > 1:
        return _manual("multiple conflicting reviewed salary benchmarks")
    chosen = max(matching, key=lambda b: b.source_date or date.min)
    when = chosen.source_date.isoformat() if chosen.source_date else None
    return _decision(units, Decimal(chosen.amount), SOURCE_BENCHMARK, public_url(chosen.source_url), when)


def resolve_desired_salary(
    field: DiscoveredField,
    expectations: SalaryExpectations | None,
    *,
    title: str,
    vacancy: NormalizedVacancy | None,
    today: date | None = None,
) -> SalaryDecision:
    """Resolve a desired-salary answer for one field, or return a manual reason."""
    # Absent salary_expectations permits the published-range fallback; an
    # existing object with fill_salary false is an explicit opt-out.
    if expectations is not None and not expectations.fill_salary:
        return _manual("salary autofill is disabled (salary_expectations.fill_salary is false)")
    expectations = expectations or SalaryExpectations()
    units, reason = _check_field(field)
    if units is None:
        return _manual(reason or "salary control units are ambiguous")
    market = _vacancy_market(vacancy)
    # Lazy, by precedence: a lower source is never parsed once a higher one decides.
    sources = (
        lambda: _from_candidate(expectations, units, market, title),
        lambda: _from_published(units, vacancy),
        lambda: _from_benchmark(expectations, units, market, title, today or date.today()),
    )
    for source in sources:
        decision = source()
        if decision is not None:
            return decision
    return _manual("no applicable candidate preference, published range, or reviewed benchmark for this desired-salary field")


def numeric_readback_matches(expected: str, actual: str) -> bool:
    """Strict Decimal equality of two plain amounts (thousands commas allowed in `actual`)."""
    try:
        wanted = Decimal(expected.strip())
    except InvalidOperation:
        return False
    cleaned = actual.strip()
    if _THOUSANDS_COMMA_RE.match(cleaned):
        cleaned = re.sub(r"[, ]", "", cleaned)
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return False
    return Decimal(cleaned) == wanted
