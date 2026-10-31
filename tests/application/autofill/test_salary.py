from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.salary import (
    BENCHMARK_MAX_AGE_DAYS,
    SOURCE_BENCHMARK,
    SOURCE_CANDIDATE,
    SOURCE_PUBLISHED,
    numeric_readback_matches,
    resolve_desired_salary,
)
from app.application.candidate_profile import SalaryExpectations
from app.collectors.vacancy_collector import NormalizedVacancy

TODAY = date(2026, 10, 30)
LABEL = "Desired annual gross salary in EUR"
TITLE = "Senior Backend Engineer"
BENCH_URL = "https://benchmarks.example.test/reports/backend?utm=1#frag"


def _field(label: str = LABEL, **kwargs: object) -> DiscoveredField:
    return DiscoveredField(label=label, **kwargs)  # type: ignore[arg-type]


def _vacancy(description: str = "", location: str | None = "Germany") -> NormalizedVacancy:
    return NormalizedVacancy(
        source="target_company:greenhouse:example",
        external_id="1",
        title=TITLE,
        company="Example",
        location=location,
        employment=None,
        description=description,
        url="https://user:pw@boards.example.test:8443/example/jobs/1?token=abc#x",
        published_at="2026-10-01T10:00:00Z",
    )


def _exp(**kwargs: object) -> SalaryExpectations:
    kwargs.setdefault("fill_salary", True)
    return SalaryExpectations.model_validate(kwargs)


def _bench(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": TITLE,
        "market": "Germany",
        "seniority": "SENIOR",
        "amount": 90000,
        "currency": "EUR",
        "period": "annual",
        "basis": "gross",
        "source_url": BENCH_URL,
        "source_date": TODAY - timedelta(days=40),
        "reviewed_on": TODAY - timedelta(days=30),
        "reviewed": True,
    }
    base.update(overrides)
    return base


def _resolve(field: DiscoveredField, exp: SalaryExpectations | None, vacancy: NormalizedVacancy | None = None, **kw: object):
    return resolve_desired_salary(field, exp, title=TITLE, vacancy=vacancy, today=TODAY, **kw)  # type: ignore[arg-type]


PUBLISHED = "Salary range: EUR 80,000 - 100,000 per year gross."
CANDIDATE = {"amount": 120000, "currency": "EUR", "period": "annual", "basis": "gross"}


def test_candidate_overrides_published() -> None:
    decision = _resolve(_field(), _exp(**CANDIDATE), _vacancy(PUBLISHED))
    assert (decision.amount, decision.source_kind) == ("120000", SOURCE_CANDIDATE)
    assert (decision.currency, decision.period, decision.basis) == ("EUR", "annual", "gross")


def test_published_midpoint_overrides_benchmark_and_sanitizes_url() -> None:
    decision = _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(PUBLISHED))
    assert (decision.amount, decision.source_kind) == ("90000", SOURCE_PUBLISHED)
    assert decision.evidence_url == "https://boards.example.test/example/jobs/1"
    assert decision.evidence_date == "2026-10-01"
    assert "pw" not in decision.note() and "token" not in decision.note()


def test_published_midpoint_may_be_fractional_and_k_suffix_and_html() -> None:
    html = "<p>Base salary: <b>€75k - €80k</b> annually, gross</p>"
    assert _resolve(_field(), _exp(), _vacancy(html)).amount == "77500"
    odd = "Salary EUR 80,001 - 80,002 per year gross"
    assert _resolve(_field(), _exp(), _vacancy(odd)).amount == "80001.5"


def test_opt_out_and_missing_config_never_fill() -> None:
    assert not _resolve(_field(), _exp(fill_salary=False, **CANDIDATE), _vacancy(PUBLISHED)).fill
    assert not _resolve(_field(), _exp(fill_salary=False), _vacancy(PUBLISHED)).fill  # existing object = opt-out
    assert not _resolve(_field(), _exp(fill_salary=False, benchmarks=[_bench()]), _vacancy()).fill
    assert not _resolve(_field(), None, _vacancy("No pay information.")).fill
    assert not _resolve(_field(), None, None).fill


def test_absent_config_permits_published_fallback_only() -> None:
    decision = _resolve(_field(), None, _vacancy(PUBLISHED))
    assert (decision.amount, decision.source_kind) == ("90000", SOURCE_PUBLISHED)
    assert not _resolve(_field("Desired monthly gross salary in EUR"), None, _vacancy(PUBLISHED)).fill


def test_no_invented_facts_or_example_inheritance() -> None:
    assert not _resolve(_field(), _exp(), _vacancy("We offer great benefits.")).fill
    assert not _resolve(_field(), _exp(), None).fill
    assert SalaryExpectations().benchmarks == [] and SalaryExpectations().amount is None


@pytest.mark.parametrize(
    "label",
    [
        "Desired annual salary in EUR",  # missing gross/net
        "Desired gross salary in EUR",  # missing period
        "Desired annual gross salary",  # missing currency
        "Desired annual gross salary in $",  # ambiguous currency
        "Desired annual gross salary in local currency",
        "Desired annual gross salary in EUR or USD",
        "Desired annual or monthly gross salary in EUR",
        "Desired gross and net annual salary in EUR",
        "Annual gross salary in EUR",  # not provably desired
        "Current annual gross salary in EUR",
        "Last salary / desired annual gross salary in EUR",
        "Previous annual gross salary expectation in EUR",
        "Desired annual gross bonus in EUR",
        "Desired annual gross total package (OTE) in EUR",
        "Desired annual gross equity in EUR",
    ],
)
def test_ambiguous_or_unrelated_fields_stay_manual_even_with_pref_and_range(label: str) -> None:
    decision = _resolve(_field(label), _exp(**CANDIDATE), _vacancy(PUBLISHED))
    assert not decision.fill and decision.reason


def test_unit_mismatches_never_convert() -> None:
    for label in (
        "Desired monthly gross salary in EUR",
        "Desired hourly gross salary in EUR",
        "Desired annual net salary in EUR",
        "Desired annual gross salary in USD",
    ):
        assert not _resolve(_field(label), _exp(**CANDIDATE), _vacancy(PUBLISHED)).fill
        assert not _resolve(_field(label), _exp(), _vacancy(PUBLISHED)).fill


def test_unsupported_control_shapes_stay_manual() -> None:
    assert not _resolve(_field(field_type="select", options=["80000"]), _exp(**CANDIDATE)).fill
    assert not _resolve(_field(field_type="radio"), _exp(**CANDIDATE)).fill
    assert _resolve(_field(field_type="number"), _exp(**CANDIDATE)).fill


def test_sibling_context_is_not_borrowed() -> None:
    field = _field("Salary", context="Desired annual gross salary in EUR")
    assert not _resolve(field, _exp(**CANDIDATE)).fill
    assert not _resolve(_field("", context=LABEL), _exp(**CANDIDATE)).fill  # units need the field's own label
    assert not _resolve(_field("", context=LABEL), None, _vacancy(PUBLISHED)).fill
    # Only the field's own label counts: a sibling's prior-pay text cannot override it.
    assert _resolve(_field(LABEL, context="Previous salary"), _exp(**CANDIDATE)).fill
    assert not _resolve(_field("Current annual gross salary in EUR", context="Desired salary"), _exp(**CANDIDATE)).fill
    assert _resolve(_field(LABEL, context="Tell us about yourself"), _exp(**CANDIDATE)).fill


def test_incomplete_applicable_preference_stays_manual() -> None:
    for partial in (
        {"amount": 120000, "currency": "EUR", "period": "annual"},  # no basis
        {"amount": 120000, "currency": "EUR", "basis": "gross"},  # no period
        {"currency": "EUR", "period": "annual", "basis": "gross"},  # no amount
        {"amount": 120000, "currency": "EUR", "period": "annual", "basis": "gross", "roles": ["Frontend"]},
    ):
        decision = _resolve(_field(), _exp(**partial), _vacancy(PUBLISHED))
        if "roles" in partial:  # out-of-scope role: preference not applicable, published range applies
            assert decision.source_kind == SOURCE_PUBLISHED
        else:
            assert not decision.fill


def test_preference_without_currency_in_scope_is_incomplete() -> None:
    assert not _resolve(_field(), _exp(amount=1, period="annual", basis="gross"), _vacancy(PUBLISHED)).fill


def test_preference_scopes_and_other_currency_fallthrough() -> None:
    scoped = _exp(preferences=[{**CANDIDATE, "amount": 111000, "markets": ["DE"], "roles": ["backend engineer"]}])
    assert _resolve(_field(), scoped, _vacancy()).amount == "111000"
    assert not _resolve(_field(), scoped, _vacancy(location="Berlin, Germany or Warsaw, Poland")).fill
    assert not _resolve(_field(), scoped, _vacancy(location=None)).fill
    other_market = _exp(preferences=[{**CANDIDATE, "markets": ["Poland"]}])
    assert not _resolve(_field(), other_market, _vacancy()).fill  # out of scope, nothing else
    usd_pref = _exp(amount=5, currency="USD", period="annual", basis="gross")
    assert _resolve(_field(), usd_pref, _vacancy(PUBLISHED)).source_kind == SOURCE_PUBLISHED
    period_mismatch = _exp(amount=5000, currency="EUR", period="monthly", basis="gross")
    assert not _resolve(_field(), period_mismatch, _vacancy(PUBLISHED)).fill
    conflicting = _exp(preferences=[CANDIDATE, {**CANDIDATE, "amount": 130000}])
    assert not _resolve(_field(), conflicting).fill


@pytest.mark.parametrize(
    "description",
    [
        "Salary: EUR 80,000 - 100,000 per year gross. Senior track: EUR 110,000 - 130,000 per year gross.",
        "Salary: EUR 80,000 - 100,000 per year gross. Or USD 90,000 - 110,000 per year gross.",
        "Salary: EUR 80,000 - 100,000 per year gross plus bonus.",
        "Salary: EUR 80,000 - 100,000 per year.",  # no basis
        "Salary: EUR 80,000 - 100,000 gross.",  # no period
        "Salary: 80,000 - 100,000 per year gross.",  # no currency -> no range at all
        "Salary: $80,000 - $100,000 per year gross.",  # ambiguous $
        "Salary: EUR 80,000 - 100,000 per month gross.",  # field is annual
        "Salary: EUR 80,000 - 100,000 per year net.",
        "Salary: EUR 80.000 - 100.000 per year gross.",  # ambiguous separators
        "Salary: EUR 100,000 - 80,000 per year gross.",
        "Salary: EUR 0 - 100,000 per year gross.",  # non-positive endpoint
        "Salary: EUR 0 - 0 per year gross.",
    ],
)
def test_published_range_conflicts_and_unit_gaps_stay_manual(description: str) -> None:
    assert not _resolve(_field(), _exp(), _vacancy(description)).fill


def test_published_range_in_multi_country_posting_stays_manual() -> None:
    vacancy = _vacancy(PUBLISHED, location="Germany, Poland")
    assert not _resolve(_field(), _exp(), vacancy).fill


def test_lowercase_iso_published_range_is_recognized_over_benchmark() -> None:
    exp = _exp(benchmarks=[_bench(amount=150000)])
    decision = _resolve(_field(), exp, _vacancy("Salary range: eur 50,000 - 60,000 gross per year."))
    assert (decision.amount, decision.source_kind, decision.currency) == ("55000", SOURCE_PUBLISHED, "EUR")
    mixed = _resolve(_field(), exp, _vacancy("Salary range: Eur 50,000 - 60,000 gross per year."))
    assert mixed.amount == "55000"


@pytest.mark.parametrize(
    "description",
    [
        "Salary range: 50,000 - 60,000 gross per year.",
        "Salary range: £50,000 - £60,000 gross per year.",
        "Salary range: 50k - 60k gross per year.",
    ],
)
def test_published_pay_range_without_recognized_currency_blocks_benchmark(description: str) -> None:
    decision = _resolve(_field(), _exp(benchmarks=[_bench(amount=150000)]), _vacancy(description))
    assert not decision.fill and decision.amount is None


def test_explicit_iso_with_pound_symbol_may_use_that_iso_and_gbp_field_label() -> None:
    field = _field("Desired annual gross salary in GBP")
    decision = _resolve(field, _exp(), _vacancy("Salary range: £ GBP 50,000 - 60,000 gross per year."))
    assert (decision.amount, decision.currency) == ("55000", "GBP")
    assert not _resolve(field, _exp(), _vacancy("Salary range: £50,000 - £60,000 gross per year.")).fill


def test_field_iso_code_is_case_insensitive_and_candidate_still_applies() -> None:
    for label in ("Desired annual gross salary in eur", "Desired annual gross salary in Eur", "Desired salary (eUr, gross, annual)"):
        decision = _resolve(_field(label), _exp(**CANDIDATE))
        assert (decision.amount, decision.currency, decision.source_kind) == ("120000", "EUR", SOURCE_CANDIDATE)
    assert not _resolve(_field("Desired annual gross salary in £"), _exp(**CANDIDATE)).fill


def test_small_nonpay_ranges_are_ignored() -> None:
    description = "Salary review after 3-6 months. " + PUBLISHED
    assert _resolve(_field(), _exp(), _vacancy(description)).amount == "90000"


@pytest.mark.parametrize(
    "description",
    [
        "Salary range: 40 - 60 gross per hour.",
        "Monthly salary: 400 - 600 gross.",
    ],
)
def test_currencyless_small_pay_range_blocks_conflicting_benchmark(description: str) -> None:
    field = _field("Desired hourly gross salary in EUR")
    bench = _bench(amount=150, period="hourly")
    decision = _resolve(field, _exp(benchmarks=[bench]), _vacancy(description))
    assert not decision.fill and decision.amount is None


def test_repeated_identical_range_is_not_a_conflict() -> None:
    assert _resolve(_field(), _exp(), _vacancy(PUBLISHED + "\n" + PUBLISHED)).amount == "90000"


def test_benchmark_requires_exact_reviewed_scope() -> None:
    ok = _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy())
    assert (ok.amount, ok.source_kind, ok.evidence_url) == (
        "90000",
        SOURCE_BENCHMARK,
        "https://benchmarks.example.test/reports/backend",
    )
    assert ok.evidence_date == (TODAY - timedelta(days=40)).isoformat()  # the SOURCE date
    for override in (
        {"title": "Backend Engineer"},
        {"market": "Poland"},
        {"seniority": "JUNIOR"},
        {"currency": "USD"},
        {"period": "monthly"},
        {"basis": "net"},
        {"source_url": "ftp://x"},
    ):
        assert not _resolve(_field(), _exp(benchmarks=[_bench(**override)]), _vacancy()).fill
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(location="Germany and Poland")).fill
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(location=None)).fill


def test_benchmark_staleness_and_future_dates() -> None:
    edge = _bench(source_date=TODAY - timedelta(days=BENCHMARK_MAX_AGE_DAYS), reviewed_on=TODAY)
    assert _resolve(_field(), _exp(benchmarks=[edge]), _vacancy()).fill
    stale = _bench(source_date=TODAY - timedelta(days=BENCHMARK_MAX_AGE_DAYS + 1), reviewed_on=TODAY)
    assert not _resolve(_field(), _exp(benchmarks=[stale]), _vacancy()).fill
    future = _bench(source_date=TODAY + timedelta(days=1), reviewed_on=TODAY + timedelta(days=1))
    assert not _resolve(_field(), _exp(benchmarks=[future]), _vacancy()).fill
    future_review = _bench(reviewed_on=TODAY + timedelta(days=1))
    assert not _resolve(_field(), _exp(benchmarks=[future_review]), _vacancy()).fill
    conflict = [_bench(), _bench(amount=95000)]
    assert not _resolve(_field(), _exp(benchmarks=conflict), _vacancy()).fill


def test_old_source_reviewed_today_is_not_fresh() -> None:
    old = _bench(source_date=date(2020, 1, 1), reviewed_on=TODAY)
    assert not _resolve(_field(), _exp(benchmarks=[old]), _vacancy()).fill


@pytest.mark.parametrize(
    "override",
    [{"reviewed": False}, {"source_date": None}, {"amount": 0}, {"amount": -5}, {"source_url": ""}, {"source_url": "not a url"}],
)
def test_unreliable_benchmark_records_are_never_used(override: dict[str, object]) -> None:
    assert not _resolve(_field(), _exp(benchmarks=[_bench(**override)]), _vacancy()).fill


def test_benchmark_without_review_fields_defaults_to_unused() -> None:
    record = _bench()
    del record["reviewed"], record["source_date"]
    assert not _resolve(_field(), _exp(benchmarks=[record]), _vacancy()).fill


def test_benchmark_ambiguous_market_is_manual_with_reason() -> None:
    decision = _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(location=None))
    assert not decision.fill and "market" in (decision.reason or "")


MULTI_LOCATIONS = [
    "Germany and France",
    "France and Germany",
    "Germany, France",
    "France, Germany",
    "Germany / France",
    "France/Germany",
    "Berlin, Germany; Paris, France",
    "Berlin, Germany, Paris, France",
]


@pytest.mark.parametrize("location", MULTI_LOCATIONS)
def test_partial_country_lists_never_prove_a_single_market(location: str) -> None:
    scoped = _exp(preferences=[{**CANDIDATE, "markets": ["Germany"]}])
    assert not _resolve(_field(), scoped, _vacancy(location=location)).fill
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(location=location)).fill
    assert not _resolve(_field(), _exp(), _vacancy(PUBLISHED, location=location)).fill


def test_scope_matches_exact_location_or_whole_country_alias_only() -> None:
    scoped = _exp(preferences=[{**CANDIDATE, "markets": ["Germany"]}])
    for location in ("Germany", "DE", "  germany "):
        assert _resolve(_field(), scoped, _vacancy(location=location)).fill
    exact = _exp(preferences=[{**CANDIDATE, "markets": ["Berlin, Germany"]}])
    assert _resolve(_field(), exact, _vacancy(location="berlin,  Germany")).fill
    # `Something, Germany` is never Germany; neither is a partial list with an unknown country.
    for location in ("Berlin, Germany", "Cuba, Germany", "Germany, Cuba", "Berlin, Germany (Remote)"):
        assert not _resolve(_field(), scoped, _vacancy(location=location)).fill
        assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(location=location)).fill
    cuba = _exp(preferences=[{**CANDIDATE, "amount": 100000, "markets": ["Germany"]}])
    assert not _resolve(_field(), cuba, _vacancy(location="Cuba, Germany")).fill
    exact_bench = _exp(benchmarks=[_bench(market="Berlin, Germany")])
    assert _resolve(_field(), exact_bench, _vacancy(location="Berlin, Germany")).fill


def test_published_cues_bind_to_the_same_clause() -> None:
    conferences = "We have annual conferences. Salary EUR 80000 - 100000 gross."
    assert not _resolve(_field(), _exp(), _vacancy(conferences)).fill
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(conferences)).fill
    subscription = "Subscription costs EUR 100 - 200 annual. Salary negotiable."
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(subscription)).fill
    two_ranges = "Salary EUR 80,000 - 100,000 per year gross.\nTeam budget EUR 5,000 - 9,000 per year gross."
    assert not _resolve(_field(), _exp(benchmarks=[_bench()]), _vacancy(two_ranges)).fill
    split_cue = "Salary\nEUR 80,000 - 100,000 per year gross."
    assert not _resolve(_field(), _exp(), _vacancy(split_cue)).fill


def test_valid_salary_clause_survives_separate_bonus_and_benefits_sentences() -> None:
    text = "Salary range: EUR 80,000 - 100,000 per year gross. Bonus: EUR 5,000 - 10,000. Benefits include annual leave."
    assert _resolve(_field(), _exp(), _vacancy(text)).amount == "90000"
    row = "<table><tr><td>Salary</td><td>EUR 80,000 - 100,000 per year gross</td></tr><tr><td>Bonus</td><td>up to 10%</td></tr></table>"
    assert _resolve(_field(), _exp(), _vacancy(row)).amount == "90000"


def test_lower_sources_are_not_parsed_after_a_candidate_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.application.autofill.salary as salary

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("lower source consulted")

    monkeypatch.setattr(salary, "_from_published", boom)
    monkeypatch.setattr(salary, "_from_benchmark", boom)
    assert _resolve(_field(), _exp(**CANDIDATE), _vacancy(PUBLISHED)).source_kind == SOURCE_CANDIDATE


def test_benchmark_diagnostics_carry_units_source_and_date_only() -> None:
    note = _resolve(_field(), _exp(benchmarks=[_bench()], notes="private note"), _vacancy()).note()
    assert "90000 EUR annual gross" in note and "source=reviewed_benchmark" in note
    assert f"evidence_date={TODAY - timedelta(days=40)}" in note and "private note" not in note


def test_unknown_seniority_title_never_uses_benchmark() -> None:
    decision = resolve_desired_salary(
        _field(), _exp(benchmarks=[_bench(title="Backend Engineer", seniority="UNKNOWN")]),
        title="Backend Engineer", vacancy=_vacancy(), today=TODAY,
    )
    assert not decision.fill


def test_note_has_safe_diagnostics_only() -> None:
    decision = _resolve(_field(), _exp(**CANDIDATE, notes="private note"), _vacancy())
    note = decision.note()
    assert "120000 EUR annual gross" in note and "candidate_preference" in note
    assert "private note" not in note


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ("80000", "80000", True),
        ("80000", "80,000", True),
        ("80000", "180000", False),
        ("80000", "abc80000", False),
        ("80000", "80000 EUR", False),
        ("80000", "80000.0", True),
        ("80000.5", "80000", False),
        ("80000", "", False),
    ],
)
def test_numeric_readback_is_strict(expected: str, actual: str, ok: bool) -> None:
    assert numeric_readback_matches(expected, actual) is ok


@pytest.mark.parametrize(
    "label",
    [
        "Desired annual gross salary in EUR (in thousands)",
        "Desired annual gross salary in EUR (in k)",
        "Desired annual gross salary (EUR k)",
        "Desired annual gross salary in EUR, in millions",
        "Desired annual gross salary in INR (lakh)",
        "Desired annual gross salary in INR (LPA)",
        "Desired annual gross salary in EUR k",
        "Desired annual gross CTC in EUR",
        "Desired annual gross salary (total cost to company) in EUR",
    ],
)
def test_scaled_or_ctc_fields_stay_manual_despite_preference_and_benchmark(label: str) -> None:
    exp = _exp(benchmarks=[_bench()], **CANDIDATE)
    decision = _resolve(_field(label), exp, _vacancy(PUBLISHED))
    assert not decision.fill and decision.amount is None


def test_thousands_field_with_explicit_preference_never_gets_whole_units() -> None:
    label = "Desired annual gross salary in EUR (in thousands)"
    exp = _exp(amount=90000, currency="EUR", period="annual", basis="gross")
    decision = _resolve(_field(label), exp, _vacancy())
    assert not decision.fill and "scale" in decision.note()


@pytest.mark.parametrize(
    "description",
    [
        "Salary: INR 20 - 30 LPA gross per year.",
        "Salary: INR 20 to 30 lakh gross per year.",
        "Salary: INR 2 crore gross per year.",
        "Salary: EUR 1 - 2 million gross per year.",
        "Salary: EUR 1.2 million to 1.5 million per year gross.",
        "Salary: EUR 1.2m - 1.5m per year gross.",
        "Salary (in thousands): EUR 80 - 100 per year gross.",
        "Salary: EUR 80 - 100 (in k) per year gross.",
        "Salary: 80 - 100 thousand EUR per year gross.",
    ],
)
def test_unsupported_published_scale_stays_manual_and_blocks_benchmark(description: str) -> None:
    inr = "INR" in description
    field = _field("Desired annual gross salary in INR") if inr else _field()
    bench = _bench(currency="INR", amount=2500000) if inr else _bench()
    decision = _resolve(field, _exp(benchmarks=[bench]), _vacancy(description))
    assert not decision.fill and "scale" in decision.note()


def test_supported_k_suffix_range_is_unchanged_with_scale_guard() -> None:
    decision = _resolve(_field(), _exp(benchmarks=[_bench(amount=1)]), _vacancy("Salary: EUR75k-80k per year gross."))
    assert (decision.amount, decision.source_kind) == ("77500", SOURCE_PUBLISHED)
