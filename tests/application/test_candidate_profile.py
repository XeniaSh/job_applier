from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.application.candidate_profile import CandidateProfile


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
            "country": "Germany",
        },
        "professional_links": {
            "linkedin": "https://www.linkedin.com/in/ada-example-test",
            "github": "https://github.com/ada-example-test",
            "website": "https://ada.example.test",
        },
        "employment": {
            "current_title": "Backend Engineer",
            "years_of_experience": 7,
            "notice_period": "30 days",
            "open_to_relocation": True,
        },
        "work_eligibility": {
            "citizenship": ["Germany"],
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "application_files": {
            "default_resume": "resumes/example_java_backend.pdf",
        },
        "sensitive": {},
    }
    payload.update(overrides)
    return payload


def test_valid_profile_payload() -> None:
    profile = CandidateProfile.model_validate(_valid_payload())

    assert profile.identity.first_name == "Ada"
    assert profile.identity.email == "ada.example@example.test"
    assert profile.work_eligibility.requires_visa_sponsorship is False
    assert profile.work_authorization_for("Germany") is True
    assert profile.work_authorization_for("DE") is True
    assert profile.fill_sensitive_fields is False
    assert profile.sensitive.is_unset()


def test_website_helper_rejects_github_and_linkedin_urls() -> None:
    profile = CandidateProfile.model_validate(
        _valid_payload(
            professional_links={
                "github": "https://github.com/ada-example-test",
                "website": "https://github.com/ada-example-test",
            }
        )
    )
    assert profile.website_for_autofill() is None
    profile = CandidateProfile.model_validate(
        _valid_payload(
            professional_links={"website": "https://ada.example.test", "github": "https://github.com/ada"}
        )
    )
    assert profile.website_for_autofill() == "https://ada.example.test"


def test_relocation_willingness_answers_any_destination() -> None:
    profile = CandidateProfile.model_validate(
        _valid_payload(
            employment={"open_to_relocation": True},
            application_policy={"relocation": {"willing": True}},
        )
    )
    assert profile.relocation_answer_for("Bangkok") is True
    assert profile.relocation_answer_for("Singapore") is True
    assert profile.relocation_willingness() is True


def test_relocation_does_not_imply_work_authorization() -> None:
    profile = CandidateProfile.model_validate(
        _valid_payload(
            application_policy={"relocation": {"willing": True}},
            work_eligibility={
                "citizenship": [],
                "work_authorizations": [],
                "requires_visa_sponsorship": None,
            },
        )
    )
    assert profile.relocation_answer_for("Thailand") is True
    assert profile.work_authorization_for("Thailand") is None


def test_office_work_willingness_does_not_imply_location_or_authorization() -> None:
    profile = CandidateProfile.model_validate(
        _valid_payload(
            application_policy={"office_work": {"willing": True}},
            work_eligibility={
                "citizenship": [],
                "work_authorizations": [],
                "requires_visa_sponsorship": None,
            },
        )
    )
    assert profile.office_work_answer() is True
    assert profile.identity.current_location == "Berlin, Germany"
    assert profile.work_authorization_for("Netherlands") is None
    assert profile.work_authorization_for("Amsterdam") is None


def test_missing_required_identity_fields() -> None:
    payload = _valid_payload()
    identity = dict(payload["identity"])  # type: ignore[arg-type]
    del identity["email"]
    payload["identity"] = identity

    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(payload)


def test_empty_identity_fields_fail() -> None:
    payload = _valid_payload()
    identity = dict(payload["identity"])  # type: ignore[arg-type]
    identity["first_name"] = "  "
    payload["identity"] = identity

    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(payload)


def test_location_does_not_imply_work_authorization() -> None:
    profile = CandidateProfile.model_validate(
        _valid_payload(
            work_eligibility={
                "citizenship": [],
                "work_authorizations": [],
                "requires_visa_sponsorship": None,
            }
        )
    )

    assert profile.identity.current_location == "Berlin, Germany"
    assert profile.identity.country == "Germany"
    assert profile.work_authorization_for("Germany") is None
    assert profile.work_authorization_for("United States") is None
    assert profile.explicit_requires_visa_sponsorship() is None


def test_unknown_yaml_keys_are_rejected() -> None:
    payload = _valid_payload()
    payload["unexpected_policy"] = "guess-authorization"

    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(payload)


def test_specialist_is_masters_and_unawarded_doctorate_is_not() -> None:
    specialist = CandidateProfile.model_validate(
        _valid_payload(
            employment={
                "highest_academic_level": "specialist",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": False,
            }
        )
    )
    assert specialist.awarded_academic_level() == "MASTERS"

    postgraduate = CandidateProfile.model_validate(
        _valid_payload(
            employment={
                "highest_academic_level": "completed aspirantura, no PhD",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": False,
            }
        )
    )
    assert postgraduate.awarded_academic_level() == "MASTERS"
    assert postgraduate.awarded_academic_level() != "DOCTORATE"

    doctorate = CandidateProfile.model_validate(
        _valid_payload(
            employment={
                "highest_academic_level": "PhD",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": True,
            }
        )
    )
    assert doctorate.awarded_academic_level() == "DOCTORATE"

    bachelor = CandidateProfile.model_validate(
        _valid_payload(employment={"highest_academic_level": "Bachelor's"})
    )
    assert bachelor.awarded_academic_level() == "BACHELOR"
