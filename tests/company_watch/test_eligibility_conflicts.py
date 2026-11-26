"""Synthetic evidence only: no country rights inferred from identity/location."""
import pytest

from app.company_watch.application_recommendation import recommend_application
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.feasibility import assess_application_feasibility
from app.company_watch.seniority import classify_seniority


def recommend(text="", *, needs_visa=True, **evidence):
    assessment = assess_application_feasibility(vacancy_text=text, **evidence)
    result = recommend_application(
        decision="STRONG_MATCH", feasibility=assessment,
        constraints=CandidateConstraints(
            known_languages=["english", "russian"], requires_visa_sponsorship=needs_visa,
            open_to_relocation=True, open_to_remote_worldwide=True,
            target_seniority=["MID", "SENIOR"],
        ), seniority=classify_seniority("Senior Java Engineer"),
    )
    return assessment, result


@pytest.mark.parametrize("restriction", [
    "Must be based in the US.", "Applicants must currently live in Germany.",
    "Remote work is limited to EU countries.", "US residents only.",
    "We are only hiring in Canada.", "Remote only from Kazakhstan.",
    "Allowed countries: Germany, Poland.", "Remote worldwide except Russia.",
    "Удаленная работа только из РФ.", "Удалённо только в России.",
    "Необходимо проживать в Казахстане.", "Не из любой страны: только из РФ.",
    "This role is not worldwide.",
])
def test_explicit_limit_defeats_worldwide_marketing(restriction):
    assessment, result = recommend(
        "Work from anywhere. " + restriction, remote_type="worldwide",
    )
    assert assessment.remote_type == "country_limited"
    assert assessment.label == "UNCLEAR"
    assert result.label == "CHECK_MANUALLY"
    assert "confirmation" in result.reasons[0]


@pytest.mark.parametrize("remote_type", ["country_limited", "hybrid", "onsite"])
def test_extracted_specific_work_mode_wins(remote_type):
    assessment, result = recommend("Work from anywhere.", remote_type=remote_type)
    assert assessment.remote_type == remote_type
    if remote_type == "country_limited":
        assert result.label == "CHECK_MANUALLY"
    assert assessment.remote_type != "worldwide"


def test_audit_case_extracted_limit_cannot_be_promoted():
    assessment, result = recommend(
        "Work from anywhere. Must be based in the US.", location="US",
        remote_type="country_limited", location_restrictions=["US only"],
    )
    assert assessment.location_restrictions == ["US only"]
    assert assessment.label == "UNCLEAR"
    assert result.label == "CHECK_MANUALLY"


@pytest.mark.parametrize("limits", [["Germany, Poland"], ["CET overlap required"]])
def test_structured_limits_are_not_lost_even_without_recognized_wording(limits):
    assessment, result = recommend("Work from anywhere.", location_restrictions=limits)
    assert assessment.label == "UNCLEAR"
    assert assessment.location_restrictions == limits
    assert result.label == "CHECK_MANUALLY"


def test_unrestricted_structured_location_is_not_a_blocker():
    _, result = recommend("Work from anywhere.", location_restrictions=["Worldwide"])
    assert result.label == "APPLY_NOW"


def test_moving_money_does_not_supply_required_visa():
    assessment, result = recommend(
        "Relocation support. No visa sponsorship. Must have the right to work in the UK.",
        location="UK",
    )
    assert assessment.relocation_support == "yes"
    assert assessment.visa_sponsorship == "no"
    assert assessment.label == "UNLIKELY"
    assert result.label == "SKIP"
    assert result.reasons == ["required visa sponsorship is explicitly not offered"]


def test_visa_unknown_is_not_an_explicit_denial():
    assessment, result = recommend("Candidates must have the right to work in Kazakhstan.")
    assert assessment.visa_sponsorship == "unknown"
    assert assessment.work_authorization_requirement == "required"
    assert assessment.label == "UNCLEAR"
    assert result.label == "CHECK_MANUALLY"


def test_no_visa_needed_does_not_invent_country_authorization():
    _, result = recommend(
        "No visa sponsorship. Must have the right to work in the UK.", needs_visa=False,
    )
    assert result.label == "CHECK_MANUALLY"


def test_unrestricted_worldwide_remote_is_a_supported_alternative():
    assessment, result = recommend("Remote worldwide. No visa sponsorship.")
    assert assessment.remote_type == "worldwide"
    assert result.label == "APPLY_NOW"


def test_multi_route_rights_question_is_retained_for_manual_review():
    _, result = recommend(
        "Remote contractors worldwide are welcome. Office employees must have the right to work in the UK. "
        "No visa sponsorship.",
    )
    assert result.label == "CHECK_MANUALLY"  # no rejection of the alternative route


def test_explicit_sponsored_relocation_remains_available():
    assessment, result = recommend("Visa sponsorship and relocation support are available.")
    assert assessment.label == "LIKELY"
    assert result.label == "APPLY_NOW"


def test_employer_address_does_not_restrict_worldwide_remote():
    assessment, result = recommend("Work from anywhere.", location="London, UK")
    assert assessment.remote_type == "worldwide"
    assert result.label == "APPLY_NOW"


def test_location_only_is_unknown_not_authorization():
    assessment, result = recommend("Java backend.", location="New Zealand")
    assert assessment.work_authorization_requirement == "unknown"
    assert assessment.label == "UNCLEAR"
    assert result.label == "CHECK_MANUALLY"
