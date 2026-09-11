from __future__ import annotations

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.candidate_profile import CandidateProfile


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "professional_links": {"linkedin": "https://www.linkedin.com/in/ada-example-test"},
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        "work_eligibility": {
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "sensitive": {"gender": "female"},
        "fill_sensitive_fields": False,
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def test_identity_fields_are_supported_deterministic() -> None:
    profile = _profile()
    classified = classify_field(DiscoveredField(label="First Name", required=True), profile)
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
    assert classified.fill is True
    assert classified.value == "Ada"


def test_required_unknown_question_is_unknown_required() -> None:
    classified = classify_field(
        DiscoveredField(label="What is your favorite IDE?", required=True),
        _profile(),
    )
    assert classified.classification is FieldClassification.UNKNOWN_REQUIRED
    assert classified.fill is False


def test_optional_unknown_question_is_unknown_optional() -> None:
    classified = classify_field(
        DiscoveredField(label="Anything else we should know?", required=False),
        _profile(),
    )
    assert classified.classification is FieldClassification.UNKNOWN_OPTIONAL
    assert classified.fill is False


def test_gender_policy_prefers_not_to_disclose() -> None:
    classified = classify_field(
        DiscoveredField(
            label="Gender",
            field_type="select",
            required=True,
            options=["Prefer not to disclose", "Female", "Male"],
        ),
        _profile(),
    )
    assert classified.kind.value == "gender"
    assert classified.fill is True
    assert classified.value == "Prefer not to disclose"
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_sensitive_optional_is_not_filled() -> None:
    classified = classify_field(
        DiscoveredField(label="Ethnicity", field_type="select", required=False),
        _profile(),
    )
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL
    assert classified.fill is False
    assert classified.value is None


def test_unrecognized_control_is_unsupported() -> None:
    classified = classify_field(
        DiscoveredField(label="Sign here", field_type="signature", required=True),
        _profile(),
    )
    assert classified.classification is FieldClassification.UNSUPPORTED
    assert classified.fill is False


def test_work_auth_without_country_authorization_is_not_deterministic() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
            "country": "Germany",
        },
        work_eligibility={"work_authorizations": [], "requires_visa_sponsorship": None},
    )
    classified = classify_field(
        DiscoveredField(
            label="Are you legally authorized to work in Germany?",
            required=True,
        ),
        profile,
    )
    assert classified.classification is FieldClassification.UNKNOWN_REQUIRED
    assert classified.fill is False
    assert classified.value is None


def test_inactive_relationship_details_are_not_required_unresolved() -> None:
    profile = _profile(employee_relationship={"has_relationship": False})
    classified = classify_field(
        DiscoveredField(label="If yes, what is the employee's name?", required=True),
        profile,
    )
    assert classified.inactive_conditional is True
    assert classified.fill is False
    assert classified.classification is not FieldClassification.UNKNOWN_REQUIRED
