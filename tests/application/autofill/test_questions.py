from __future__ import annotations

import pytest

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.questions import QuestionKind, map_question
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
        "professional_links": {
            "linkedin": "https://www.linkedin.com/in/ada-example-test",
            "github": "https://github.com/ada-example-test",
        },
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        "work_eligibility": {
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "employment": {
            "salary_expectations": {
                "amount": 80000,
                "currency": "EUR",
                "fill_salary": False,
            }
        },
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


@pytest.mark.parametrize(
    ("label", "name", "expected_kind", "expected_value"),
    [
        ("First Name", "job_application[first_name]", QuestionKind.FIRST_NAME, "Ada"),
        ("Last Name", "last_name", QuestionKind.LAST_NAME, "Example"),
        ("Email", "email", QuestionKind.EMAIL, "ada.example@example.test"),
        ("Phone", "phone", QuestionKind.PHONE, "+15555550100"),
        (
            "LinkedIn Profile",
            "linkedin",
            QuestionKind.LINKEDIN,
            "https://www.linkedin.com/in/ada-example-test",
        ),
        (
            "Will you now or in the future require visa sponsorship?",
            "sponsorship",
            QuestionKind.VISA_SPONSORSHIP,
            False,
        ),
        (
            "Are you legally authorized to work in Germany?",
            "work_auth",
            QuestionKind.WORK_AUTHORIZATION,
            True,
        ),
        (
            "Country*",
            "country",
            QuestionKind.COUNTRY,
            "Germany",
        ),
    ],
)
def test_known_questions_map_from_explicit_profile(
    label: str,
    name: str,
    expected_kind: QuestionKind,
    expected_value: object,
) -> None:
    mapped = map_question(DiscoveredField(label=label, name=name), _profile())
    assert mapped.kind is expected_kind
    assert mapped.value == expected_value
    assert mapped.fillable is True


def test_work_authorization_in_other_country_is_not_guessed() -> None:
    mapped = map_question(
        DiscoveredField(label="Are you legally authorized to work in the United States?"),
        _profile(),
    )
    assert mapped.kind is QuestionKind.WORK_AUTHORIZATION
    assert mapped.fillable is False
    assert mapped.value is None


def test_salary_and_why_company_stay_unknown() -> None:
    profile = _profile()
    salary = map_question(DiscoveredField(label="Expected salary", required=True), profile)
    why = map_question(DiscoveredField(label="Why do you want to work here?"), profile)
    assert salary.kind is QuestionKind.SALARY
    assert salary.fillable is False
    assert why.kind is QuestionKind.WHY_COMPANY
    assert why.fillable is False


def test_sponsorship_unset_is_not_fillable() -> None:
    profile = _profile(work_eligibility={"requires_visa_sponsorship": None, "work_authorizations": []})
    mapped = map_question(
        DiscoveredField(label="Will you require visa sponsorship?"),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is False
    assert mapped.value is None


def test_resume_id_is_mapped_even_when_label_is_attach() -> None:
    mapped = map_question(
        DiscoveredField(label="Attach", name=None, field_type="file", element_id="resume"),
        _profile(),
    )
    assert mapped.kind is QuestionKind.RESUME
    assert mapped.fillable is True


def test_cover_letter_attach_is_not_resume() -> None:
    mapped = map_question(
        DiscoveredField(label="Attach", field_type="file", element_id="cover_letter"),
        _profile(),
    )
    assert mapped.kind is not QuestionKind.RESUME


def test_newsletter_email_me_about_defaults_to_no() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Email me about other job openings within the Booking Holding’s entities and recruitment-related newsletters*",
            element_id="question_59548305",
            required=True,
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.NEWSLETTER
    assert mapped.fillable is True
    assert mapped.value == "No"


def test_plain_email_label_still_maps() -> None:
    mapped = map_question(DiscoveredField(label="Email", element_id="email"), _profile())
    assert mapped.kind is QuestionKind.EMAIL
    assert mapped.value == "ada.example@example.test"


def test_country_is_not_treated_as_work_authorization() -> None:
    mapped = map_question(
        DiscoveredField(label="Country*", element_id="country", required=True),
        _profile(),
    )
    assert mapped.kind is QuestionKind.COUNTRY
    assert mapped.value == "Germany"
    assert mapped.fillable is True


def test_custom_currently_based_country_question_uses_profile_country() -> None:
    mapped = map_question(
        DiscoveredField(
            label="In which country/region are you currently based?*",
            element_id="question_58288493",
            required=True,
            field_type="select",
            options=["Germany", "Thailand", "Uzbekistan"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.COUNTRY
    assert mapped.fillable is True
    assert mapped.value == "Germany"


def test_website_blog_other_does_not_use_github_fallback() -> None:
    mapped = map_question(
        DiscoveredField(label="Website / Blog / Other", required=True),
        _profile(),
    )
    assert mapped.kind is QuestionKind.WEBSITE
    assert mapped.fillable is False
    assert mapped.value is None


def test_website_uses_explicit_website_not_github() -> None:
    profile = _profile(
        professional_links={
            "linkedin": "https://www.linkedin.com/in/ada-example-test",
            "github": "https://github.com/ada-example-test",
            "website": "https://ada.example.test",
        }
    )
    mapped = map_question(DiscoveredField(label="Website / Blog / Other", required=True), profile)
    assert mapped.fillable is True
    assert mapped.value == "https://ada.example.test"


def test_github_url_stored_as_website_is_not_used_for_website_field() -> None:
    profile = _profile(
        professional_links={
            "github": "https://github.com/ada-example-test",
            "website": "https://github.com/ada-example-test",
        }
    )
    mapped = map_question(DiscoveredField(label="Website / Blog / Other"), profile)
    assert mapped.kind is QuestionKind.WEBSITE
    assert mapped.fillable is False
    assert mapped.value is None


def test_github_specific_field_still_uses_github() -> None:
    mapped = map_question(
        DiscoveredField(label="Github Profile? (Please paste link or answer 'No')"),
        _profile(),
    )
    assert mapped.kind is QuestionKind.GITHUB
    assert mapped.value == "https://github.com/ada-example-test"
    assert mapped.fillable is True


def test_years_of_experience_maps_to_select_range() -> None:
    profile = _profile(employment={"years_of_experience": 7})
    mapped = map_question(
        DiscoveredField(
            label="What is your overall years of relevant experience?*",
            required=True,
            field_type="select",
            options=["0-2 years", "3-5 years", "6-8 years", "9+ years"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.value == "6-8 years"
    assert mapped.fillable is True


def test_academic_level_and_tech_stack_and_interest() -> None:
    profile = _profile(
        employment={
            "highest_academic_level": "Master's",
            "professional_tech_stack": ["Java", "Kotlin", "Spring Boot", "PostgreSQL"],
            "preferred_fields_of_interest": ["Backend", "Platform"],
        }
    )
    academic = map_question(
        DiscoveredField(
            label="Highest academic level*",
            field_type="select",
            options=["High school", "Bachelor's", "Master's", "PhD"],
        ),
        profile,
    )
    stack = map_question(
        DiscoveredField(
            label="Which of the following technologies have you had professional experience with? (select top 3)*",
            field_type="multiselect",
            options=["Javascript", "Java", "Python", "Go", "Kotlin", "Ruby"],
        ),
        profile,
    )
    interest = map_question(
        DiscoveredField(
            label="Preferred field of interest*",
            field_type="select",
            options=["Frontend", "Backend", "Mobile"],
        ),
        profile,
    )
    assert academic.value == "MASTERS"
    assert stack.value == ["Java", "Kotlin", "Spring Boot", "PostgreSQL"]
    assert stack.max_choices == 3
    assert "Javascript" not in stack.value
    assert interest.value == "Backend"


def test_specialist_and_postgraduate_map_to_masters_not_doctorate() -> None:
    profile = _profile(
        employment={
            "highest_academic_level": "Russian specialist degree, completed aspirantura",
            "postgraduate_studies_completed": True,
            "doctorate_awarded": False,
        }
    )
    academic = map_question(
        DiscoveredField(
            label="What is your highest academic level?",
            field_type="select",
            options=[
                "Highschool",
                "Diploma",
                "Bachelor's Degree",
                "Master's Degree",
                "Doctorate Degree",
            ],
        ),
        profile,
    )
    assert academic.fillable is True
    assert academic.value == "MASTERS"


def test_awarded_doctorate_maps_to_doctorate_token() -> None:
    profile = _profile(
        employment={
            "highest_academic_level": "PhD",
            "postgraduate_studies_completed": True,
            "doctorate_awarded": True,
        }
    )
    academic = map_question(
        DiscoveredField(
            label="Highest academic level*",
            field_type="select",
            options=["Bachelor's Degree", "Master's Degree", "Doctorate Degree"],
        ),
        profile,
    )
    assert academic.value == "DOCTORATE"


def test_relocation_to_explicit_destination() -> None:
    profile = _profile(
        application_policy={"relocation": {"willing": True}}
    )
    mapped = map_question(
        DiscoveredField(
            label="Are you currently based in Bangkok or open to relocate to Bangkok?*",
            field_type="radio",
            options=["Yes", "No"],
            required=True,
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.RELOCATION
    assert mapped.value == "Yes"
    assert mapped.fillable is True


def test_open_to_relocation_answers_unlisted_city() -> None:
    profile = _profile(application_policy={"relocation": {"willing": True}})
    mapped = map_question(
        DiscoveredField(label="Are you willing to relocate to Singapore?"),
        profile,
    )
    assert mapped.kind is QuestionKind.RELOCATION
    assert mapped.fillable is True
    assert mapped.value in {"Yes", True}


def test_relocation_or_based_in_question_is_yes() -> None:
    profile = _profile(employment={"open_to_relocation": True})
    mapped = map_question(
        DiscoveredField(
            label="Would you be open to relocation?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert mapped.value == "Yes"
    assert mapped.fillable is True


def test_employee_relationship_no_leaves_conditionals_empty() -> None:
    profile = _profile(
        employee_relationship={
            "has_relationship": False,
            "how_known": "should not be used",
            "employee_name": "should not be used",
        }
    )
    relationship = map_question(
        DiscoveredField(
            label="Do you have a personal relationship with a current Agoda employee?*",
            field_type="radio",
            options=["Yes", "No"],
        ),
        profile,
    )
    how = map_question(DiscoveredField(label="If yes, how do you know the employee?"), profile)
    name = map_question(DiscoveredField(label="What is the employee's name?"), profile)
    assert relationship.value == "No"
    assert relationship.fillable is True
    assert how.fillable is False
    assert how.value is None
    assert name.fillable is False
    assert name.value is None
    assert name.inactive_conditional is True


def test_unset_employee_relationship_defaults_to_no() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Do you have a personal relationship with a current employee?*",
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.EMPLOYEE_RELATIONSHIP
    assert mapped.value == "No"
    assert mapped.fillable is True


def test_deloitte_affiliation_maps_to_explicit_no() -> None:
    profile = _profile(
        application_policy={
            "prior_affiliations": [{"organization": "Deloitte", "associated": False}],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Please confirm if you are currently or formerly associated with Deloitte or any of its subsidiary entities",
            field_type="select",
            options=[
                "Yes, I am a current/an ex-employee of Deloitte or its subsidiaries",
                "No, I am not a current/an ex-employee of Deloitte or any of its subsidiary entities",
            ],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.PRIOR_AFFILIATION
    assert mapped.fillable is True
    assert str(mapped.value).startswith("No")


def test_how_did_you_hear_prefers_company_website() -> None:
    mapped = map_question(
        DiscoveredField(
            label="How did you hear about this job?",
            field_type="select",
            options=["Employee Referral", "Company Website", "LinkedIn", "Recruiter", "Other"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.APPLICATION_SOURCE
    assert mapped.value == "Company Website"
    assert mapped.fillable is True


def test_how_did_you_hear_never_selects_referral() -> None:
    mapped = map_question(
        DiscoveredField(
            label="How did you hear about this opportunity?",
            field_type="select",
            options=["Employee Referral", "Recruiter", "University"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.APPLICATION_SOURCE
    assert mapped.fillable is False
    assert mapped.value is None


def test_privacy_consent_fills_only_when_explicitly_true() -> None:
    unset = map_question(
        DiscoveredField(label="I consent to the processing of my personal data for recruiting"),
        _profile(),
    )
    assert unset.kind is QuestionKind.PRIVACY_CONSENT
    assert unset.fillable is False
    allowed = map_question(
        DiscoveredField(label="I consent to the processing of my personal data for recruiting"),
        _profile(application_consent={"privacy_data_processing": True}),
    )
    assert allowed.fillable is True


def test_cover_letter_is_its_own_kind() -> None:
    mapped = map_question(
        DiscoveredField(label="Cover Letter", field_type="cover_letter", element_id="cover_letter"),
        _profile(),
    )
    assert mapped.kind is QuestionKind.COVER_LETTER
    assert mapped.fillable is False


def test_javascript_is_not_selected_when_absent_from_skills() -> None:
    profile = _profile(employment={"professional_tech_stack": ["Java", "Kotlin"]})
    mapped = map_question(
        DiscoveredField(
            label="Which of the following technologies have you had professional experience with? (select top 3)*",
            field_type="multiselect",
            options=["Javascript", "Java", "Kotlin", "Python"],
        ),
        profile,
    )
    assert mapped.value == ["Java", "Kotlin"]
    assert "Javascript" not in mapped.value


def test_gender_maps_prefer_not_to_disclose_even_if_profile_has_female() -> None:
    profile = _profile(sensitive={"gender": "Female"})
    mapped = map_question(
        DiscoveredField(
            label="Gender*",
            required=True,
            field_type="select",
            options=["Prefer not to disclose", "Female", "Genderqueer", "Male"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.GENDER
    assert mapped.fillable is True
    assert mapped.value == "Prefer not to disclose"


def test_unset_sensitive_gender_still_fills_prefer_not_to_disclose() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Gender*",
            required=True,
            field_type="select",
            options=["Prefer not to disclose", "Female", "Male"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.GENDER
    assert mapped.fillable is True
    assert mapped.value == "Prefer not to disclose"


def test_booking_holdings_employment_defaults_to_no() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Are you presently employed by any company within the Booking Holdings group of companies?",
            field_type="select",
            options=["Yes", "No"],
            required=True,
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.PRIOR_AFFILIATION
    assert mapped.fillable is True
    assert mapped.value == "No"


def test_sms_updates_default_to_no() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Do you allow us to provide you TEXT/SMS updates of your interview process?",
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.SMS_UPDATES
    assert mapped.value == "No"
    assert mapped.fillable is True


def test_question_override_engineering_blog_yes() -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [{"question_contains": "engineering blog", "answer": True}],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Have you read our engineering blog?",
            field_type="select",
            options=["Yes", "No"],
            required=True,
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.QUESTION_OVERRIDE
    assert mapped.value == "Yes"
    assert mapped.fillable is True


def test_question_override_recent_application_no() -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["applied", "past 6 months"], "answer": False},
            ],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Have you applied to this company in the past 6 months?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.QUESTION_OVERRIDE
    assert mapped.value == "No"
