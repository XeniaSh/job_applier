from __future__ import annotations

import pytest

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.wise_screening import (
    CUSTOMER, ELIGIBILITY, ELIGIBILITY_OPTIONS, NOTICE, OFFICE, OFFICE_OPTIONS,
    PRIVACY, PWC, SALARY, map_wise_screening, wise_work_country,
)
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy


def profile(**parts):
    return CandidateProfile.model_validate({
        "identity": {"first_name": "Ada", "last_name": "Example", "email": "ada@example.invalid",
                     "phone": "+37255501234", "current_location": "Tallinn, Estonia", "country": "Estonia"},
        "application_files": {"default_resume": "unused.pdf"}, **parts,
    })


def vacancy(location="Tallinn, Estonia", description="Salary EUR 5000 - 7000 monthly gross."):
    normalized = NormalizedVacancy(
        source="target_company:smartrecruiters:wise", external_id="1", title="Backend Engineer",
        company="Wise", location=location, description=description,
        url="https://jobs.smartrecruiters.com/Wise/744000123456789-engineer", employment=None, published_at=None,
    )
    return ResolvedVacancy(source=normalized.source, external_id="1", title=normalized.title,
                           company="Wise", url=normalized.url, application_url=normalized.url, vacancy=normalized)


def mapped(label, *, p=None, v=None, kind="combobox", required=True):
    p = p or profile()
    field = DiscoveredField(label=label, name="sr:question-test", field_type=kind, required=required)
    return map_wise_screening(classify_field(field, p), profile=p, vacancy=v or vacancy())


def expectations(amount=6000, **overrides):
    return {"amount": amount, "currency": "EUR", "period": "monthly", "basis": "gross",
            "fill_salary": True, **overrides}


@pytest.mark.parametrize("amount,answer", [(5000, True), (6000, True), (7000, True), (7001, False), (4999, None)])
def test_salary_band_decisions(amount, answer):
    item = mapped(SALARY, kind="radio", p=profile(employment={"salary_expectations": expectations(amount)}))
    assert item.fill is (answer is not None) and item.value is answer
    assert item.kind is not QuestionKind.SALARY  # numeric enrichment cannot replace Yes/No


@pytest.mark.parametrize("config", [None, {}, {"fill_salary": True}, expectations(fill_salary=False), expectations(basis=None),
    expectations(currency="GBP"), expectations(period="annual"), expectations(basis="net"),
    expectations(markets=["London, United Kingdom"]), expectations(roles=["Accountant"]),
    expectations(preferences=[{"amount": 6500, "currency": "EUR", "period": "monthly", "basis": "gross"}]),
])
def test_salary_requires_matching_explicit_preference(config):
    assert not mapped(SALARY, kind="radio", p=profile(employment={"salary_expectations": config})).fill


@pytest.mark.parametrize("description", [
    "Salary EUR 5000 - 7000 monthly.", "Salary 5000 - 7000 monthly gross.",
    "Salary EUR 5000 - 7000 gross.", "Salary EUR 7000 - 5000 monthly gross.",
    "Salary EUR 5000 - 7000 monthly gross. Salary EUR 6000 - 8000 monthly gross.",
    "Salary EUR 5000 - 7000 monthly gross including bonus.",
    "Salary EUR 5 - 7 million monthly gross.", "Salary EUR 5.000 - 7.000 monthly gross.",
    "Salary GBP 5000 - 7000 monthly gross.", "No published compensation.",
])
def test_ambiguous_published_salary_is_manual(description):
    p = profile(employment={"salary_expectations": expectations()})
    assert not mapped(SALARY, kind="radio", p=p, v=vacancy(description=description)).fill


def test_uk_salary_needs_explicit_basis_and_never_borrows_from_candidate():
    p = profile(employment={"salary_expectations": expectations(80000, currency="GBP", period="annual")})
    assert not mapped(SALARY, kind="radio", p=p, v=vacancy("London, United Kingdom", "Salary GBP 68000 - 87500 yearly.")).fill
    assert mapped(SALARY, kind="radio", p=p, v=vacancy("London, United Kingdom", "Salary GBP 68000 - 87500 yearly gross.")).value is True


@pytest.mark.parametrize("location", ["London, United Kingdom / Tallinn, Estonia", "London, France, UK", "Remote", "London"])
def test_ambiguous_geography_does_not_resolve_work_eligibility_or_band(location):
    assert wise_work_country(vacancy(location)) is None
    assert not mapped(ELIGIBILITY, v=vacancy(location), p=profile(
        work_eligibility={"work_authorizations": [{"country": "UK", "authorized": False, "requires_sponsorship": True}]},
    )).fill


@pytest.mark.parametrize("facts", [
    {"citizenship": ["Estonia"]},
    {"requires_visa_sponsorship": True},
    {"work_authorizations": [{"country": "Estonia", "authorized": True, "requires_sponsorship": False}]},
    {"work_authorizations": [{"country": "UK", "authorized": False, "requires_sponsorship": True}]},
])
def test_residence_citizenship_global_sponsorship_and_authorized_do_not_prove_visa_subtype(facts):
    assert not mapped(ELIGIBILITY, p=profile(work_eligibility=facts)).fill


def test_country_scoped_no_authorization_and_sponsorship_is_distinct():
    p = profile(work_eligibility={"work_authorizations": [
        {"country": "Estonia", "authorized": False, "requires_sponsorship": True},
    ]})
    assert mapped(ELIGIBILITY, p=p).value == ELIGIBILITY_OPTIONS[3]
    assert not mapped(ELIGIBILITY, p=p, v=vacancy("London, United Kingdom")).fill


def test_citizen_option_requires_full_conjunction_and_conflicts_fail_closed():
    facts = {"citizenship": ["Estonia"], "work_authorizations": [
        {"country": "Estonia", "authorized": True, "requires_sponsorship": False},
    ]}
    assert mapped(ELIGIBILITY, p=profile(work_eligibility=facts)).value == ELIGIBILITY_OPTIONS[0]
    facts["work_authorizations"][0]["authorized"] = False
    assert not mapped(ELIGIBILITY, p=profile(work_eligibility=facts)).fill
    facts["work_authorizations"].append({"country": "Estonia", "authorized": True})
    assert not mapped(ELIGIBILITY, p=profile(work_eligibility=facts)).fill


def test_explicit_subtype_override_is_scoped_and_cannot_override_contradiction():
    policy = {"question_overrides": [{"question_contains": [ELIGIBILITY], "answer": ELIGIBILITY_OPTIONS[1]}]}
    assert mapped(ELIGIBILITY, p=profile(application_policy=policy)).value == ELIGIBILITY_OPTIONS[1]
    p = profile(application_policy=policy, work_eligibility={"work_authorizations": [{"country": "Estonia", "authorized": False}]})
    assert not mapped(ELIGIBILITY, p=p).fill


def test_foreign_citizenship_does_not_negate_an_explicit_permanent_residency_choice():
    policy = {"question_overrides": [{"question_contains": [ELIGIBILITY], "answer": ELIGIBILITY_OPTIONS[0]}]}
    p = profile(application_policy=policy, work_eligibility={"citizenship": ["Poland"], "work_authorizations": [
        {"country": "Estonia", "authorized": True, "requires_sponsorship": False},
    ]})
    assert mapped(ELIGIBILITY, p=p).value == ELIGIBILITY_OPTIONS[0]
    p.application_policy.question_overrides = []
    assert not mapped(ELIGIBILITY, p=p).fill  # no automatic PR inference


def test_office_willingness_and_same_city_do_not_assert_commutable_distance():
    assert not mapped(OFFICE).fill
    p = profile(application_policy={"office_work": {"willing": False}})
    assert mapped(OFFICE, p=p).value == OFFICE_OPTIONS[2]
    p = profile(employment={"open_to_relocation": True}, application_policy={"office_work": {"willing": True}})
    assert not mapped(OFFICE, p=p).fill
    p.employment.relocation_destinations = ["Estonia"]
    assert mapped(OFFICE, p=p).value == OFFICE_OPTIONS[1]
    assert not mapped(ELIGIBILITY, p=p).fill


@pytest.mark.parametrize("label,kind", [(CUSTOMER, "radio"), (PWC, "combobox"),
    ("Were you involved, in any capacity, in the audit of Wise’s financial statements, including partner reviews in the last 12 months?", "radio")])
def test_affiliation_default_never_answers_customer_audit_or_pwc(label, kind):
    assert not mapped(label, kind=kind).fill
    p = profile(application_policy={"question_overrides": [{"question_contains": [label], "answer": False}]})
    assert mapped(label, kind=kind, p=p).value is False


def test_unknown_audit_timeframe_does_not_inherit_a_broad_override():
    label = "Were you involved, in any capacity, in the audit of Wise’s financial statements, in the last 24 months?"
    p = profile(application_policy={"question_overrides": [{"question_contains": ["audit of Wise"], "answer": False}]})
    assert not mapped(label, kind="radio", p=p).fill


def test_conflicting_published_vacancy_context_is_manual():
    from dataclasses import replace
    v = vacancy()
    p = profile(employment={"salary_expectations": expectations()})
    assert not mapped(SALARY, kind="radio", p=p, v=replace(v, title="Accountant")).fill
    other = replace(v.vacancy, company="Other employer")
    assert not mapped(SALARY, kind="radio", p=p, v=replace(v, vacancy=other)).fill
    assert wise_work_country(replace(v, vacancy=other)) is None


def test_unknown_question_override_does_not_enable_generation_or_fill():
    label = "Do you accept a binding unrelated declaration?"
    p = profile(application_policy={"question_overrides": [{"question_contains": [label], "answer": True}]})
    assert not mapped(label, p=p, kind="checkbox").fill


def test_notice_privacy_and_optional_consent_policies():
    p = profile(employment={"notice_period": "One month"})
    assert mapped(NOTICE, p=p, kind="text").value == "One month"
    assert mapped(PRIVACY, kind="checkbox").value is True
    for p in (profile(application_consent={"privacy_data_processing": False}),
              profile(application_policy={"privacy_acknowledgement": {"auto_acknowledge_required": False}})):
        assert not mapped(PRIVACY, p=p, kind="checkbox").fill
    p = profile(application_consent={"privacy_data_processing": True, "recruiting_contact": True},
                application_policy={"newsletter_opt_in": True})
    for label in (PRIVACY, "Receive Wise marketing updates", "Join our talent pool"):
        assert not mapped(label, p=p, kind="checkbox", required=False).fill


def test_required_survey_declines_and_unknown_self_description_is_manual():
    for label, kind in [("What's your age?", "combobox"),
                        ("I identify my sexual orientation as (mark all that apply)", "multiselect"),
                        ("Are you part of the first generation of your family to study further education? e.g. college or university?", "combobox")]:
        assert mapped(label, kind=kind).value == "decline_to_answer"
        assert not mapped(label, kind=kind, required=False).fill
    assert not mapped("If you have chosen to self describe, please specify below", kind="text", required=False).fill
