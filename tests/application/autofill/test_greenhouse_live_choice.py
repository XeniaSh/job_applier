from app.application.autofill.greenhouse import _live_choice_match
from app.application.autofill.questions import QuestionKind


def test_live_gender_match_uses_decline_semantics_not_exact_mapper_string() -> None:
    live = ["Female", "Male", "I prefer not to say"]
    assert (
        _live_choice_match("Prefer not to disclose", live, QuestionKind.GENDER)
        == "I prefer not to say"
    )
    assert (
        _live_choice_match("prefer not to disclose", ["Decline To Self Identify", "Female"], QuestionKind.GENDER)
        == "Decline To Self Identify"
    )


def test_live_academic_match_maps_masters_without_selecting_diploma() -> None:
    live = ["High School", "Diploma", "Bachelor's Degree", "Master's Degree", "Doctorate Degree"]
    assert _live_choice_match("MASTERS", live, QuestionKind.ACADEMIC_LEVEL) == "Master's Degree"


def test_live_application_source_still_prefers_company_website() -> None:
    live = ["Employee Referral", "Company Website", "LinkedIn", "University"]
    assert (
        _live_choice_match("Company Website", live, QuestionKind.APPLICATION_SOURCE)
        == "Company Website"
    )


def test_live_sensitive_match_uses_decline_semantics_regardless_of_wanted() -> None:
    """SENSITIVE (race/ethnicity, veteran/military, disability, sexual
    orientation, ...) only ever carries a decline intent, never real option
    text (see `_sensitive_decline_value`), so the live menu is always
    searched for the explicit decline option, exactly like AGE.
    """
    live = ["Hispanic or Latino", "Not Hispanic or Latino", "I don't wish to answer"]
    assert (
        _live_choice_match("decline_to_answer", live, QuestionKind.SENSITIVE)
        == "I don't wish to answer"
    )


def test_live_sensitive_match_returns_none_when_no_decline_option_exists() -> None:
    live = ["Hispanic or Latino", "Not Hispanic or Latino"]
    assert _live_choice_match("decline_to_answer", live, QuestionKind.SENSITIVE) is None


def test_live_school_matches_only_exact_normalized_equivalent() -> None:
    live = ["Aalto University", "University of Helsinki"]
    assert _live_choice_match("Example University", live, QuestionKind.SCHOOL) is None
    assert _live_choice_match("aalto, university.", live, QuestionKind.SCHOOL) == "Aalto University"


def test_live_school_never_selects_partial_or_prefix_match() -> None:
    live = ["Aalto University of Applied Sciences"]
    assert _live_choice_match("Aalto University", live, QuestionKind.SCHOOL) is None
    assert _live_choice_match("Aalto", live, QuestionKind.SCHOOL) is None


def test_live_sponsorship_does_not_select_unrelated_netherlands_hsm() -> None:
    live = ["Yes, Netherlands Highly Skilled Migrant Visa", "No"]
    assert _live_choice_match("Yes", live, QuestionKind.VISA_SPONSORSHIP, "uzbekistan") is None
    assert _live_choice_match("Yes", live, QuestionKind.VISA_SPONSORSHIP) is None
    assert (
        _live_choice_match("Yes", live, QuestionKind.VISA_SPONSORSHIP, "netherlands")
        == "Yes, Netherlands Highly Skilled Migrant Visa"
    )
