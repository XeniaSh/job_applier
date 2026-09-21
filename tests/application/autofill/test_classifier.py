from __future__ import annotations

import pytest

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import SENSITIVE_DECLINE_INTENT
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


def test_optional_gender_is_sensitive_and_unfilled() -> None:
    classified = classify_field(
        DiscoveredField(
            label="Gender",
            field_type="select",
            required=False,
            options=["Decline To Self Identify", "Female", "Male"],
        ),
        _profile(),
    )
    assert classified.kind.value == "gender"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL


def test_age_policy_selects_decline_option_when_required() -> None:
    classified = classify_field(
        DiscoveredField(
            label="What's your age?",
            field_type="select",
            required=True,
            options=["I don't wish to answer", "18-24", "25-34"],
        ),
        _profile(),
    )
    assert classified.kind.value == "age"
    assert classified.fill is True
    assert classified.value == "I don't wish to answer"
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_required_age_without_decline_option_is_unknown_required() -> None:
    classified = classify_field(
        DiscoveredField(
            label="What's your age?",
            field_type="select",
            required=True,
            options=["18-24", "25-34"],
        ),
        _profile(),
    )
    assert classified.kind.value == "age"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.UNKNOWN_REQUIRED


def test_optional_age_is_sensitive_and_unfilled() -> None:
    classified = classify_field(
        DiscoveredField(
            label="What's your age?",
            field_type="select",
            required=False,
            options=["18-24", "25-34"],
        ),
        _profile(),
    )
    assert classified.kind.value == "age"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL


def test_sensitive_optional_is_not_filled() -> None:
    classified = classify_field(
        DiscoveredField(label="Ethnicity", field_type="select", required=False),
        _profile(),
    )
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL
    assert classified.fill is False
    assert classified.value is None


@pytest.mark.parametrize(
    "label",
    [
        "Race/Ethnicity",
        "Veteran Status",
        "Protected Veteran/Military Status",
        "Sexual Orientation",
        "Disability Status",
    ],
)
def test_required_demographic_field_selects_explicit_decline_option(label: str) -> None:
    """Required voluntary self-identification with an unambiguous decline
    option is filled deterministically with that option -- never an inferred
    demographic value. Distinct from gender/age, which already have their
    own dedicated policy above.
    """
    classified = classify_field(
        DiscoveredField(
            label=label,
            field_type="select",
            required=True,
            options=["Option A", "Option B", "I don't wish to answer"],
        ),
        _profile(),
    )
    assert classified.kind.value == "sensitive"
    assert classified.fill is True
    assert classified.value == "I don't wish to answer"
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_required_demographic_field_without_decline_option_stays_sensitive_and_unresolved_required() -> None:
    """No unambiguous decline option in the discovered options -- preserve the
    prior fail-closed Sensitive/Manual classification (never guess a
    demographic value) rather than reclassifying as plain UNKNOWN_REQUIRED.
    The service layer surfaces a required-but-unresolved SENSITIVE_OPTIONAL
    field in both `sensitive_fields` and `unresolved_required_fields`.
    """
    classified = classify_field(
        DiscoveredField(
            label="Race/Ethnicity",
            field_type="select",
            required=True,
            options=["Option A", "Option B"],
        ),
        _profile(),
    )
    assert classified.kind.value == "sensitive"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL


def test_required_demographic_plain_select_with_no_options_stays_sensitive_and_unresolved() -> None:
    """Distinct from the custom-select case below: a plain "select" with no
    discovered options is not a live React combobox, so it must not be
    guessed as a resolved decline -- it stays SENSITIVE_OPTIONAL/unfilled,
    matching `test_required_demographic_field_without_decline_option_...`.
    """
    classified = classify_field(
        DiscoveredField(label="Race/Ethnicity", field_type="select", required=True, options=[]),
        _profile(),
    )
    assert classified.kind.value == "sensitive"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL


def test_required_demographic_custom_select_with_no_discovery_options_carries_decline_intent() -> None:
    """A custom React-select (e.g. Greenhouse) has no readable `options` at
    discovery time. The semantic decline intent is carried through -- never a
    guessed literal label -- for `greenhouse._live_choice_match` to resolve
    against the live menu, exactly like AGE.
    """
    classified = classify_field(
        DiscoveredField(label="Veteran Status", field_type="combobox", required=True, options=[]),
        _profile(),
    )
    assert classified.kind.value == "sensitive"
    assert classified.fill is True
    assert classified.value == SENSITIVE_DECLINE_INTENT


def test_optional_demographic_field_stays_sensitive_regardless_of_decline_option() -> None:
    """Optional demographic fields remain untouched even when an unambiguous
    decline option is present -- decline-selection only ever applies to
    required fields.
    """
    classified = classify_field(
        DiscoveredField(
            label="Sexual Orientation",
            field_type="select",
            required=False,
            options=["Option A", "Option B", "I don't wish to answer"],
        ),
        _profile(),
    )
    assert classified.kind.value == "sensitive"
    assert classified.fill is False
    assert classified.value is None
    assert classified.classification is FieldClassification.SENSITIVE_OPTIONAL


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
