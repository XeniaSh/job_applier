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


def test_resides_in_any_uses_current_residence_only() -> None:
    germany = _profile(application_policy={"relocation": {"willing": True}})
    assert germany.resides_in_any(["UK", "Poland"]) is False
    assert germany.resides_in_any(["Germany"]) is True
    uk = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "London",
            "country": "UK",
        }
    )
    assert uk.resides_in_any(["the UK", "Poland"]) is True
    empty = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": None,
            "country": None,
        }
    )
    assert empty.resides_in_any(["UK", "Poland"]) is None


def test_employment_restrictions_answer_is_explicit_only() -> None:
    unset = _profile()
    assert unset.employment_restrictions_answer() is None
    denied = _profile(
        application_policy={"has_employment_or_post_employment_restrictions": False}
    )
    assert denied.employment_restrictions_answer() is False
    restricted = _profile(
        application_policy={"has_employment_or_post_employment_restrictions": True}
    )
    assert restricted.employment_restrictions_answer() is True


def test_sponsorship_required_for_is_country_specific() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Tashkent, Uzbekistan",
            "country": "Uzbekistan",
        },
        work_eligibility={
            "citizenship": ["Germany"],
            "requires_visa_sponsorship": True,
            "work_authorizations": [
                {"country": "Netherlands", "authorized": False, "requires_sponsorship": True},
            ],
        },
        application_policy={"relocation": {"willing": True}},
    )
    assert profile.identity.country == "Uzbekistan"
    assert profile.resides_in_any(["Uzbekistan"]) is True
    assert profile.resides_in_any(["Netherlands"]) is False
    assert profile.sponsorship_required_for("Uzbekistan") is None
    assert profile.sponsorship_required_for("Netherlands") is True
    assert profile.sponsorship_answer_for_scope("current") == (None, "Uzbekistan")
    assert profile.sponsorship_answer_for_scope("generic") == (True, None)
    assert profile.work_authorization_for("Uzbekistan") is None
    assert profile.relocation_answer_for("Netherlands") is True


def test_current_location_sponsorship_falls_back_to_location_country_without_identity_country() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Amsterdam, Netherlands",
            "country": None,
        },
        work_eligibility={
            "work_authorizations": [
                {"country": "Netherlands", "authorized": False, "requires_sponsorship": True},
            ],
        },
    )
    assert profile.identity.country is None
    assert profile.sponsorship_answer_for_scope("current") == (True, "Netherlands")


def test_current_location_sponsorship_fallback_is_fail_closed_without_matching_fact() -> None:
    no_facts = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Amsterdam, Netherlands",
            "country": None,
        },
        work_eligibility={"work_authorizations": []},
    )
    assert no_facts.sponsorship_answer_for_scope("current") == (None, None)

    mismatched = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Amsterdam, Netherlands",
            "country": None,
        },
        work_eligibility={
            "work_authorizations": [
                {"country": "Germany", "authorized": True, "requires_sponsorship": False},
            ],
        },
    )
    assert mismatched.sponsorship_answer_for_scope("current") == (None, None)


def test_current_location_sponsorship_fallback_does_not_use_residence_or_citizenship() -> None:
    """Being physically located somewhere is not a citizenship/residence inference shortcut.

    The fallback only matches an explicit work_authorizations fact; a
    generic requires_visa_sponsorship flag or citizenship must not leak in.
    """
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Amsterdam, Netherlands",
            "country": None,
        },
        work_eligibility={
            "citizenship": ["Netherlands"],
            "requires_visa_sponsorship": True,
            "work_authorizations": [],
        },
    )
    assert profile.sponsorship_answer_for_scope("current") == (None, None)
