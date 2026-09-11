from __future__ import annotations

from app.application.candidate_profile import CandidateProfile


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
            "country": "Germany",
        },
        "application_files": {"default_resume": "resumes/example.pdf"},
        "work_eligibility": {
            "citizenship": ["Germany"],
            "work_authorizations": [],
            "requires_visa_sponsorship": None,
        },
        "sensitive": {},
        "fill_sensitive_fields": False,
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def test_location_and_country_do_not_produce_work_authorization_answer() -> None:
    profile = _profile()

    assert profile.identity.country == "Germany"
    assert profile.identity.current_location is not None
    assert profile.work_authorization_for("Germany") is None
    assert profile.work_authorization_for("United States") is None


def test_requires_visa_sponsorship_used_only_when_explicitly_set() -> None:
    unset = _profile()
    assert unset.explicit_requires_visa_sponsorship() is None

    explicit_false = _profile(
        work_eligibility={
            "work_authorizations": [],
            "requires_visa_sponsorship": False,
        }
    )
    assert explicit_false.explicit_requires_visa_sponsorship() is False

    explicit_true = _profile(
        work_eligibility={
            "work_authorizations": [],
            "requires_visa_sponsorship": True,
        }
    )
    assert explicit_true.explicit_requires_visa_sponsorship() is True


def test_unset_sensitive_fields_are_not_answers() -> None:
    profile = _profile(
        sensitive={"gender": None, "ethnicity": None},
        fill_sensitive_fields=False,
    )
    assert profile.sensitive_answers_for_autofill() == {}
    assert profile.sensitive.is_unset()


def test_sensitive_values_stay_unused_without_fill_policy() -> None:
    profile = _profile(
        sensitive={"gender": "female", "veteran_status": "not_veteran"},
        fill_sensitive_fields=False,
    )
    assert profile.sensitive.gender == "female"
    assert profile.sensitive_answers_for_autofill() == {}
    assert profile.gender_for_autofill() == "prefer not to disclose"


def test_gender_disclosure_can_use_explicit_profile_value() -> None:
    profile = _profile(
        sensitive={"gender": "female"},
        application_policy={"prefer_not_to_disclose_gender": False},
    )
    assert profile.gender_for_autofill() == "female"
