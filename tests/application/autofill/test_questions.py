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


def test_required_linkedin_url_field_resolves_from_professional_links() -> None:
    """A required Lever-style 'LinkedIn URL' field must resolve deterministically
    from `professional_links.linkedin` -- the single source of truth -- and not
    be left unfillable just because the field is marked required.
    """
    profile = _profile()
    field = DiscoveredField(label="LinkedIn URL", name="urls[LinkedIn]", field_type="text", required=True)
    mapped = map_question(field, profile)
    assert mapped.kind is QuestionKind.LINKEDIN
    assert mapped.value == "https://www.linkedin.com/in/ada-example-test"
    assert mapped.value == profile.professional_links.linkedin
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


def test_technology_specific_years_question_stays_unresolved_without_explicit_value() -> None:
    profile = _profile(employment={"years_of_experience": 7, "years_of_relevant_experience": 7})
    mapped = map_question(
        DiscoveredField(
            label="Minimum Years of experience in Kotlin *",
            required=True,
            field_type="text",
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason is not None
    assert "Kotlin" in mapped.unresolved_reason
    # Never silently inherits the overall/relevant experience value.
    assert mapped.value != profile.relevant_experience_years()


def test_technology_specific_years_question_fills_from_explicit_configured_value() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "years_of_relevant_experience": 7,
            "technology_years": [{"technology": "Kotlin", "years": 2}],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Minimum Years of experience in Kotlin *",
            required=True,
            field_type="select",
            options=["0-1 years", "1-3 years", "3-5 years", "5+ years"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "1-3 years"


def test_technology_specific_years_question_generalizes_beyond_kotlin() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "technology_years": [{"technology": "Golang", "years": 4}],
        }
    )
    mapped = map_question(
        DiscoveredField(label="Years of experience with Golang", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "4"


def test_generic_years_experience_question_is_unchanged() -> None:
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
    assert mapped.fillable is True
    assert mapped.value == "6-8 years"


def test_technology_years_question_is_never_llm_eligible() -> None:
    from app.application.autofill.fields import DiscoveredField as _Field
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile(employment={"years_of_experience": 7})
    field = _Field(label="Minimum Years of experience in Kotlin *", required=True, field_type="text")
    mapped = map_question(field, profile)
    assert mapped.fillable is False
    assert is_llm_eligible_question(field, mapped) is False


@pytest.mark.parametrize(
    "label",
    [
        "How many years of Kotlin experience?",
        "Years of experience using Spring Boot development",
    ],
)
def test_technology_specific_years_alternate_phrasings_stay_unresolved_without_value(label: str) -> None:
    profile = _profile(employment={"years_of_experience": 7, "years_of_relevant_experience": 7})
    mapped = map_question(DiscoveredField(label=label, required=True, field_type="text"), profile)
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason is not None
    assert mapped.value != profile.relevant_experience_years()


def test_technology_specific_years_alternate_phrasing_fills_from_explicit_value() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "technology_years": [{"technology": "Kotlin", "years": 2}],
        }
    )
    mapped = map_question(
        DiscoveredField(label="How many years of Kotlin experience?", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "2"


def test_technology_using_trailing_filler_word_still_resolves_to_bare_technology() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "technology_years": [{"technology": "Spring Boot", "years": 4}],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Years of experience using Spring Boot development",
            required=True,
            field_type="text",
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "4"


def test_configured_technology_matches_when_question_has_extra_words() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "technology_years": [{"technology": "Kotlin", "years": 2}],
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Minimum years of experience in Kotlin required",
            required=True,
            field_type="text",
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.value == "2"
    assert mapped.fillable is True


def test_technology_named_only_in_context_without_recognized_phrasing_is_scoped() -> None:
    """A technology named via the profile's tech stack/context (rather than an
    "experience in/with/using <X>" phrasing) must still be treated as scoped:
    never silently answered with overall/relevant years.
    """
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "years_of_relevant_experience": 7,
            "professional_tech_stack": ["Kotlin"],
        }
    )
    mapped = map_question(
        DiscoveredField(label="Kotlin experience (years)", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.unresolved_reason is not None
    assert "Kotlin" in mapped.unresolved_reason
    assert mapped.value != profile.relevant_experience_years()


def test_unlisted_technology_experience_years_stays_unresolved() -> None:
    profile = _profile(employment={"years_of_experience": 7, "professional_tech_stack": ["Java"]})
    mapped = map_question(
        DiscoveredField(label="Rust experience (years)", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.value is None
    assert mapped.fillable is False
    assert mapped.unresolved_reason is not None
    assert "Rust" in mapped.unresolved_reason


def test_technology_named_only_in_context_fills_from_explicit_value() -> None:
    profile = _profile(
        employment={
            "years_of_experience": 7,
            "professional_tech_stack": ["Kotlin"],
            "technology_years": [{"technology": "Kotlin", "years": 3}],
        }
    )
    mapped = map_question(
        DiscoveredField(label="Kotlin experience (years)", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "3"


def test_combined_multi_technology_years_question_stays_unresolved() -> None:
    """A question naming two known technologies together cannot be answered with
    either technology's own configured value: that would silently drop or
    misattribute half the question.
    """
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile(
        employment={
            "professional_tech_stack": ["Java", "Kotlin"],
            "technology_years": [
                {"technology": "Java", "years": 7},
                {"technology": "Kotlin", "years": 2},
            ],
        }
    )
    field = DiscoveredField(
        label="Years of experience with Java and Kotlin",
        required=True,
        field_type="text",
    )
    mapped = map_question(field, profile)
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason is not None
    assert "Java" in mapped.unresolved_reason
    assert "Kotlin" in mapped.unresolved_reason
    assert is_llm_eligible_question(field, mapped) is False


def test_combined_technology_years_question_stays_unresolved_when_only_one_is_known() -> None:
    """A combined question ("X and Y") must stay unresolved even when only one
    of the named technologies is configured: the configured value belongs to
    just one of the two targets, so filling it would misattribute the answer
    to the unconfigured technology too.
    """
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile(
        employment={
            "years_of_experience": 7,
            "professional_tech_stack": ["Java"],
            "technology_years": [{"technology": "Java", "years": 7}],
        }
    )
    field = DiscoveredField(
        label="Years of experience with Java and Kotlin",
        required=True,
        field_type="text",
    )
    mapped = map_question(field, profile)
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason is not None
    assert "Java" in mapped.unresolved_reason
    assert "Kotlin" in mapped.unresolved_reason
    assert is_llm_eligible_question(field, mapped) is False


def test_comma_separated_technology_years_question_stays_unresolved_when_only_one_is_known() -> None:
    """A comma-separated combined question ("X, Y") must fail closed exactly
    like "X and Y", even when only one of the named technologies is configured.
    """
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile(
        employment={
            "years_of_experience": 7,
            "professional_tech_stack": ["Java"],
            "technology_years": [{"technology": "Java", "years": 7}],
        }
    )
    field = DiscoveredField(
        label="Years of experience with Java, Kotlin",
        required=True,
        field_type="text",
    )
    mapped = map_question(field, profile)
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason is not None
    assert "Java" in mapped.unresolved_reason
    assert "Kotlin" in mapped.unresolved_reason
    assert is_llm_eligible_question(field, mapped) is False


def test_single_technology_still_fills_its_own_value_when_other_technologies_are_configured() -> None:
    profile = _profile(
        employment={
            "professional_tech_stack": ["Java", "Kotlin"],
            "technology_years": [
                {"technology": "Java", "years": 7},
                {"technology": "Kotlin", "years": 2},
            ],
        }
    )
    mapped = map_question(
        DiscoveredField(label="Kotlin experience (years)", required=True, field_type="text"),
        profile,
    )
    assert mapped.kind is QuestionKind.YEARS_EXPERIENCE
    assert mapped.fillable is True
    assert mapped.value == "2"


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


def test_privacy_consent_optional_fills_only_when_explicitly_true() -> None:
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


def test_required_privacy_acknowledgement_fills_from_policy() -> None:
    mapped = map_question(
        DiscoveredField(
            label="I have read and acknowledge the privacy policy",
            field_type="checkbox",
            required=True,
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.PRIVACY_CONSENT
    assert mapped.fillable is True
    assert mapped.value is True


def test_point_of_data_transfer_checkbox_maps_to_privacy_yes() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Point of Data Transfer — Acknowledge/Confirm",
            field_type="checkbox",
            required=True,
            context=(
                "Point of Data Transfer Information submitted during the application "
                "will be held and used for considering the application and handled "
                "according to the Applicant Privacy Notice."
            ),
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.PRIVACY_CONSENT
    assert mapped.fillable is True
    assert mapped.value is True


def test_required_privacy_not_confused_with_newsletter() -> None:
    newsletter = map_question(
        DiscoveredField(
            label="Subscribe to newsletter and other job openings",
            field_type="checkbox",
            required=False,
        ),
        _profile(),
    )
    privacy = map_question(
        DiscoveredField(
            label="I consent to processing my application data for recruitment purposes",
            field_type="checkbox",
            required=True,
        ),
        _profile(),
    )
    assert newsletter.kind is QuestionKind.NEWSLETTER
    assert newsletter.value in {False, "No"}
    assert privacy.kind is QuestionKind.PRIVACY_CONSENT
    assert privacy.value is True


def test_unknown_legal_checkbox_is_not_auto_checked() -> None:
    mapped = map_question(
        DiscoveredField(
            label="I certify that the information provided is true and complete",
            field_type="checkbox",
            required=True,
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.UNKNOWN
    assert mapped.fillable is False


def test_office_days_question_maps_to_yes() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Are you willing to work 3 or more days per week in the office in Amsterdam?",
            field_type="select",
            options=["Yes", "No"],
            required=True,
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.OFFICE_WORK
    assert mapped.fillable is True
    assert mapped.value == "Yes"


def test_hybrid_schedule_question_maps_to_yes() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Are you comfortable with a hybrid schedule?",
            field_type="select",
            options=["Yes", "No"],
            required=True,
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.OFFICE_WORK
    assert mapped.value == "Yes"
    assert mapped.fillable is True


def test_office_policy_does_not_map_current_location_or_work_auth() -> None:
    profile = _profile()
    location = map_question(DiscoveredField(label="Current location"), profile)
    work_auth = map_question(
        DiscoveredField(label="Are you legally authorized to work in the Netherlands?"),
        profile,
    )
    office = map_question(
        DiscoveredField(
            label="Are you willing to work 3 or more days per week in the office in Amsterdam?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert location.kind is QuestionKind.LOCATION
    assert location.value == "Berlin, Germany"
    assert work_auth.kind is QuestionKind.WORK_AUTHORIZATION
    assert work_auth.fillable is False
    assert work_auth.value is None
    assert office.kind is QuestionKind.OFFICE_WORK
    assert office.value == "Yes"
    assert profile.identity.current_location == "Berlin, Germany"
    assert profile.work_authorization_for("Netherlands") is None
    assert profile.work_authorization_for("Amsterdam") is None


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


def test_optional_gender_is_left_untouched() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Gender",
            required=False,
            field_type="select",
            options=["Decline To Self Identify", "Female", "Male"],
        ),
        _profile(sensitive={"gender": "Female"}),
    )
    assert mapped.kind is QuestionKind.GENDER
    assert mapped.fillable is False
    assert mapped.value is None

    starred_optional = map_question(
        DiscoveredField(
            label="Gender*",
            required=False,
            field_type="select",
            options=["Prefer not to disclose", "Female", "Male"],
        ),
        _profile(sensitive={"gender": "Female"}),
    )
    assert starred_optional.fillable is False
    assert starred_optional.value is None


def test_required_gender_selects_decline_to_self_identify() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Gender",
            required=True,
            field_type="select",
            options=["Female", "Male", "Decline To Self Identify"],
        ),
        _profile(sensitive={"gender": "Female"}),
    )
    assert mapped.kind is QuestionKind.GENDER
    assert mapped.fillable is True
    assert mapped.value == "Decline To Self Identify"
    assert mapped.value != "Female"


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


def test_current_country_of_residence_uses_profile_country() -> None:
    mapped = map_question(
        DiscoveredField(
            label="What is your current country of residence?",
            required=True,
            field_type="select",
            options=["Germany", "Poland", "United Kingdom", "United States"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.COUNTRY
    assert mapped.fillable is True
    assert mapped.value == "Germany"


def test_located_in_uk_or_poland_is_no_when_residence_is_elsewhere() -> None:
    profile = _profile(application_policy={"relocation": {"willing": True}})
    mapped = map_question(
        DiscoveredField(
            label="Are you located in the UK or Poland?",
            required=True,
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.LOCATED_IN
    assert mapped.fillable is True
    assert mapped.value == "No"


def test_located_in_uk_or_poland_is_yes_when_residence_is_uk() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Are you located in the UK or Poland?",
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(
            identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "London, United Kingdom",
                "country": "United Kingdom",
            }
        ),
    )
    assert mapped.kind is QuestionKind.LOCATED_IN
    assert mapped.value == "Yes"


def test_located_in_does_not_use_relocation_or_work_auth() -> None:
    profile = _profile(
        application_policy={"relocation": {"willing": True}},
        work_eligibility={
            "citizenship": ["United Kingdom"],
            "work_authorizations": [{"country": "United Kingdom", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
    )
    located = map_question(
        DiscoveredField(
            label="Are you located in the UK or Poland?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    relocate = map_question(
        DiscoveredField(
            label="Would you be willing to relocate to the UK or Poland?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    work_auth = map_question(
        DiscoveredField(label="Are you legally authorized to work in the United Kingdom?"),
        profile,
    )
    assert located.kind is QuestionKind.LOCATED_IN
    assert located.value == "No"
    assert relocate.kind is QuestionKind.RELOCATION
    assert relocate.value == "Yes"
    assert work_auth.kind is QuestionKind.WORK_AUTHORIZATION
    assert work_auth.value is True


def test_employment_restrictions_fill_no_from_explicit_profile_fact() -> None:
    mapped = map_question(
        DiscoveredField(
            label=(
                "Are you subject to any employment agreements and/or post-employment "
                "restrictions with your current employer or a past employer?"
            ),
            required=True,
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(application_policy={"has_employment_or_post_employment_restrictions": False}),
    )
    assert mapped.kind is QuestionKind.EMPLOYMENT_RESTRICTIONS
    assert mapped.fillable is True
    assert mapped.value == "No"


def test_unset_employment_restrictions_are_not_invented() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Are you subject to any post-employment restrictions?",
            required=True,
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.EMPLOYMENT_RESTRICTIONS
    assert mapped.fillable is False
    assert mapped.value is None


def test_employment_restrictions_do_not_answer_unrelated_legal_questions() -> None:
    profile = _profile(application_policy={"has_employment_or_post_employment_restrictions": False})
    criminal = map_question(
        DiscoveredField(
            label="Have you been convicted of a criminal offense?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    certify = map_question(
        DiscoveredField(
            label="I certify that the information provided is true and complete.",
            field_type="checkbox",
            required=True,
        ),
        profile,
    )
    export_control = map_question(
        DiscoveredField(
            label="Are you subject to export control restrictions?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert criminal.kind is not QuestionKind.EMPLOYMENT_RESTRICTIONS
    assert criminal.fillable is False
    assert certify.fillable is False
    assert export_control.kind is not QuestionKind.EMPLOYMENT_RESTRICTIONS
    assert export_control.fillable is False


def test_previously_worked_or_consulted_defaults_to_no() -> None:
    mapped = map_question(
        DiscoveredField(
            label="Have you previously worked at or consulted for GitLab?",
            required=True,
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.PRIOR_AFFILIATION
    assert mapped.fillable is True
    assert mapped.value == "No"
    generic = map_question(
        DiscoveredField(
            label="Have you previously worked at or consulted for Acme?",
            field_type="select",
            options=["Yes", "No"],
        ),
        _profile(),
    )
    assert generic.kind is QuestionKind.PRIOR_AFFILIATION
    assert generic.value == "No"


def test_primary_programming_language_uses_java_not_full_stack() -> None:
    mapped = map_question(
        DiscoveredField(
            label="What is your primary programming language and/or framework?",
            required=True,
            field_type="text",
        ),
        _profile(
            employment={
                "professional_tech_stack": ["Java", "Kotlin", "Spring Boot", "PostgreSQL"],
                "primary_programming_language": "Java",
            }
        ),
    )
    assert mapped.kind is QuestionKind.PRIMARY_LANGUAGE
    assert mapped.fillable is True
    assert mapped.value == "Java"
    assert "Kotlin" not in str(mapped.value)
    assert "Spring" not in str(mapped.value)
    fallback = map_question(
        DiscoveredField(label="What is your primary programming language and/or framework?"),
        _profile(employment={"professional_tech_stack": ["Java", "Kotlin"]}),
    )
    assert fallback.value == "Java"

    slash = map_question(
        DiscoveredField(
            label="What is your primary programming language and / or framework?",
            required=True,
            field_type="textarea",
        ),
        _profile(employment={"primary_programming_language": "Java"}),
    )
    assert slash.kind is QuestionKind.PRIMARY_LANGUAGE
    assert slash.value == "Java"


def test_open_source_links_use_only_explicit_urls() -> None:
    empty = map_question(
        DiscoveredField(
            label="Please share links of any open source projects you own or have made contributions to",
            required=True,
            field_type="textarea",
        ),
        _profile(),
    )
    assert empty.kind is QuestionKind.OPEN_SOURCE_LINKS
    assert empty.fillable is False
    assert empty.value is None

    filled = map_question(
        DiscoveredField(
            label="Please share links of any open source projects you own or have made contributions to",
            required=True,
            field_type="textarea",
        ),
        _profile(
            professional_links={
                "github": "https://github.com/ada-example-test",
                "website": "https://ada.example.test",
                "open_source_urls": ["https://github.com/ada-example-test/job-applier"],
            }
        ),
    )
    assert filled.fillable is True
    assert filled.value == "https://github.com/ada-example-test/job-applier"
    assert filled.value != "https://github.com/ada-example-test"
    assert filled.value != "https://ada.example.test"


def test_optional_gitlab_username_stays_blank_when_missing() -> None:
    mapped = map_question(
        DiscoveredField(
            label="What is your GitLab username?",
            required=False,
            field_type="text",
        ),
        _profile(),
    )
    assert mapped.kind is QuestionKind.GITLAB_USERNAME
    assert mapped.fillable is False
    assert mapped.value is None


def test_current_residence_is_not_citizenship_or_work_auth() -> None:
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
            "work_authorizations": [],
            "requires_visa_sponsorship": True,
        },
        application_policy={"relocation": {"willing": True}},
    )
    country = map_question(
        DiscoveredField(
            label="What is your current country of residence?",
            required=True,
            field_type="select",
            options=["Germany", "Uzbekistan", "Netherlands"],
        ),
        profile,
    )
    citizenship = map_question(
        DiscoveredField(label="What is your citizenship / nationality?", required=True),
        profile,
    )
    assert country.kind is QuestionKind.COUNTRY
    assert country.value == "Uzbekistan"
    assert country.value != "Germany"
    assert citizenship.fillable is False
    assert profile.work_authorization_for("Uzbekistan") is None


def test_current_location_sponsorship_does_not_use_netherlands_hsm() -> None:
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
    options = ["Yes, Netherlands Highly Skilled Migrant Visa", "No"]
    current = map_question(
        DiscoveredField(
            label="Will you now or in the future require sponsorship for a visa to remain in your current location?",
            required=True,
            field_type="select",
            options=options,
        ),
        profile,
    )
    generic = map_question(
        DiscoveredField(
            label="Will you now or in the future require visa sponsorship?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    relocate = map_question(
        DiscoveredField(
            label="Are you willing to relocate to the Netherlands?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    netherlands = map_question(
        DiscoveredField(
            label="Will you require visa sponsorship to work in the Netherlands?",
            field_type="select",
            options=options,
        ),
        profile,
    )
    assert current.kind is QuestionKind.VISA_SPONSORSHIP
    assert current.fillable is False
    assert current.value is None
    assert current.unresolved_reason == (
        "current-location sponsorship requires country-specific fact for Uzbekistan"
    )
    assert generic.kind is QuestionKind.VISA_SPONSORSHIP
    assert generic.value == "Yes"
    assert relocate.kind is QuestionKind.RELOCATION
    assert relocate.value == "Yes"
    assert netherlands.fillable is True
    assert netherlands.value == "Yes, Netherlands Highly Skilled Migrant Visa"


def _profile_without_identity_country(current_location: str, **overrides: object) -> CandidateProfile:
    payload: dict[str, object] = dict(overrides)
    payload["identity"] = {
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": "+15555550100",
        "current_location": current_location,
        "country": None,
    }
    return _profile(**payload)


@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require sponsorship for a visa to remain in your current location?",
        "Will you now or in the future require sponsorship for a visa to remain in your current country?",
        "Do you now or will you in the future require sponsorship for a visa in your current residence?",
        "Will you require a visa sponsorship for where you currently live?",
    ],
)
def test_current_location_sponsorship_explicit_false_maps_to_no(label: str) -> None:
    profile = _profile_without_identity_country(
        "Tashkent, Uzbekistan",
        work_eligibility={
            "work_authorizations": [
                {"country": "Uzbekistan", "authorized": False, "requires_sponsorship": True},
            ],
            "current_location_requires_visa_sponsorship": False,
        },
    )
    mapped = map_question(
        DiscoveredField(label=label, field_type="select", options=["Yes", "No"]),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is True
    assert mapped.value == "No"


@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require sponsorship for a visa to remain in your current location?",
        "Will you now or in the future require sponsorship for a visa to remain in your current country?",
        "Do you now or will you in the future require sponsorship for a visa in your current residence?",
        "Will you require a visa sponsorship for where you currently live?",
    ],
)
def test_current_location_sponsorship_explicit_true_maps_to_yes(label: str) -> None:
    profile = _profile_without_identity_country(
        "Tashkent, Uzbekistan",
        work_eligibility={
            "work_authorizations": [],
            "current_location_requires_visa_sponsorship": True,
        },
    )
    mapped = map_question(
        DiscoveredField(label=label, field_type="select", options=["Yes", "No"]),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is True
    assert mapped.value == "Yes"


def test_current_location_sponsorship_no_answer_without_explicit_fact() -> None:
    """Unset stays unresolved even when current_location names a country with
    a matching work_authorizations fact: current-location scope must not
    derive its answer from identity.current_location or work_authorizations.
    """
    profile = _profile_without_identity_country(
        "Tashkent, Uzbekistan",
        work_eligibility={
            "work_authorizations": [
                {"country": "Uzbekistan", "authorized": False, "requires_sponsorship": True},
            ],
        },
    )
    mapped = map_question(
        DiscoveredField(
            label="Will you now or in the future require sponsorship for a visa to remain in your current location?",
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is False
    assert mapped.value is None
    assert mapped.unresolved_reason == (
        "current-location sponsorship requires country-specific fact for current residence"
    )


# --- Optional current-company/current-employer stays blank without an explicit fact ---


@pytest.mark.parametrize(
    "label",
    ["Current company", "Current Employer", "Present employer", "Current organization"],
)
def test_current_employer_stays_blank_without_explicit_fact(label: str) -> None:
    profile = _profile()
    mapped = map_question(DiscoveredField(label=label, name="org", field_type="text"), profile)
    assert mapped.kind is QuestionKind.CURRENT_EMPLOYER
    assert mapped.fillable is False
    assert mapped.value is None


def test_current_employer_is_never_llm_eligible() -> None:
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile()
    field = DiscoveredField(label="Current company", name="org", field_type="text")
    mapped = map_question(field, profile)
    assert is_llm_eligible_question(field, mapped) is False


def test_current_employer_stays_blank_even_with_explicit_current_employer() -> None:
    """Optional current-company/current-employer fields never disclose
    `employment.current_employer`, even when it is set explicitly.
    """
    profile = _profile(employment={"current_employer": "Acme Corp"})
    mapped = map_question(DiscoveredField(label="Current company", name="org", field_type="text"), profile)
    assert mapped.kind is QuestionKind.CURRENT_EMPLOYER
    assert mapped.fillable is False
    assert mapped.value is None


def test_current_employer_does_not_shadow_employment_restrictions() -> None:
    """"...restrictions with your current employer or a past employer?" must
    still classify as an employment-restriction question, not current-employer.
    """
    profile = _profile(application_policy={"has_employment_or_post_employment_restrictions": False})
    mapped = map_question(
        DiscoveredField(
            label=(
                "Are you subject to any employment agreements and/or post-employment "
                "restrictions with your current employer or a past employer?"
            ),
            field_type="select",
            options=["Yes", "No"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.EMPLOYMENT_RESTRICTIONS
    assert mapped.value == "No"


# --- Working-arrangement (onsite/hybrid vs remote-scope) preference ---

_WORKING_ARRANGEMENT_OPTIONS = [
    "Onsite (+ 2 remote days per week)",
    "Remote in France, Germany, Spain, Portugal, Italy or Serbia",
    "Remote outside of France, Germany, Spain, Portugal, Italy or Serbia",
    "Open to onsite or remote in France, Germany, Spain, Portugal, Italy or Serbia",
]


@pytest.mark.parametrize(
    "label",
    [
        "What working arrangement are you ideally looking for? (we will discuss it during the hiring process)",
        "What work arrangement are you ideally looking for?",
    ],
)
def test_working_arrangement_picks_remote_outside_listed_countries(label: str) -> None:
    profile = _profile(
        application_policy={"remote_work_arrangement": {"preference": "REMOTE_OUTSIDE_LISTED_COUNTRIES"}}
    )
    mapped = map_question(
        DiscoveredField(label=label, field_type="radio", required=True, options=_WORKING_ARRANGEMENT_OPTIONS),
        profile,
    )
    assert mapped.kind is QuestionKind.REMOTE_WORK_ARRANGEMENT
    assert mapped.fillable is True
    assert mapped.value == "Remote outside of France, Germany, Spain, Portugal, Italy or Serbia"


def test_working_arrangement_unset_preference_is_not_fillable() -> None:
    profile = _profile()
    mapped = map_question(
        DiscoveredField(
            label="What working arrangement are you ideally looking for?",
            field_type="radio",
            required=True,
            options=_WORKING_ARRANGEMENT_OPTIONS,
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.REMOTE_WORK_ARRANGEMENT
    assert mapped.fillable is False
    assert mapped.value is None


def test_working_arrangement_is_never_llm_eligible() -> None:
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile()
    field = DiscoveredField(
        label="What working arrangement are you ideally looking for?",
        field_type="radio",
        required=True,
        options=_WORKING_ARRANGEMENT_OPTIONS,
    )
    mapped = map_question(field, profile)
    assert is_llm_eligible_question(field, mapped) is False


def test_working_arrangement_does_not_infer_from_citizenship_or_relocation() -> None:
    """Only the explicit remote_work_arrangement preference may answer this;
    citizenship and generic relocation willingness must not substitute.
    """
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Paris, France",
            "country": "France",
        },
        work_eligibility={"citizenship": ["France"]},
        application_policy={"relocation": {"willing": True}},
    )
    mapped = map_question(
        DiscoveredField(
            label="What working arrangement are you ideally looking for?",
            field_type="radio",
            required=True,
            options=_WORKING_ARRANGEMENT_OPTIONS,
        ),
        profile,
    )
    assert mapped.fillable is False


# --- Sponsorship: one option among several "Yes" options, disambiguated by
# explicit relocation intent rather than list position ---

_FIVE_OPTION_SPONSORSHIP_OPTIONS = [
    "Yes - I need a visa and I would like to relocate",
    "Yes - I need a visa but I have already relocated to one of your locations",
    "No - I already have a visa or a European nationality so I can relocate",
    "No - I do not want to relocate",
    "No - I already have a visa or a European nationality and I already live in one of your locations",
]


def test_sponsorship_five_option_group_picks_first_yes_when_relocation_intent_true() -> None:
    profile = _profile(
        work_eligibility={"requires_visa_sponsorship": True},
        application_policy={"relocation": {"willing": True}},
    )
    mapped = map_question(
        DiscoveredField(
            label="Do you need a visa sponsorship to work in one of our locations?",
            field_type="radio",
            required=True,
            options=_FIVE_OPTION_SPONSORSHIP_OPTIONS,
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is True
    assert mapped.value == "Yes - I need a visa and I would like to relocate"


def test_sponsorship_five_option_group_stays_unresolved_without_relocation_fact() -> None:
    """Two generic "Yes" options (relocate vs already relocated) must not be
    disambiguated by list position when no relocation fact is configured.
    """
    profile = _profile(work_eligibility={"requires_visa_sponsorship": True})
    mapped = map_question(
        DiscoveredField(
            label="Do you need a visa sponsorship to work in one of our locations?",
            field_type="radio",
            required=True,
            options=_FIVE_OPTION_SPONSORSHIP_OPTIONS,
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.VISA_SPONSORSHIP
    assert mapped.fillable is False
    assert mapped.value is None


# --- Closed named skill-set single-choice questions ---


@pytest.mark.parametrize(
    "label",
    [
        "Which of these languages are you most proficient in: Go, Ruby or Python?",
        "Which of these technologies are you most experienced with: Go, Ruby, or Python?",
        "Which of these languages is your strongest: Go, Ruby or Python?",
    ],
)
def test_named_skill_set_matches_explicit_primary_language(label: str) -> None:
    profile = _profile(employment={"primary_programming_language": "Python"})
    mapped = map_question(DiscoveredField(label=label, field_type="textarea"), profile)
    assert mapped.kind is QuestionKind.SKILL_SET_CHOICE
    assert mapped.fillable is True
    assert mapped.value == "Python"


def test_named_skill_set_never_falls_back_to_generic_tech_stack() -> None:
    """Regression for the observed live bug: a Go/Ruby/Python question must
    never be answered with the candidate's unrelated top skills (Java, Kotlin).
    """
    profile = _profile(
        employment={
            "professional_tech_stack": ["Java", "Kotlin", "Spring Boot"],
            "primary_programming_language": "Java",
        }
    )
    field = DiscoveredField(
        label="Which of these languages are you most proficient in: Go, Ruby or Python?",
        field_type="textarea",
        required=True,
    )
    mapped = map_question(field, profile)
    assert mapped.kind is QuestionKind.SKILL_SET_CHOICE
    assert mapped.fillable is False
    assert mapped.value is None
    assert "Java" not in str(mapped.value)
    assert "Kotlin" not in str(mapped.value)


def test_named_skill_set_is_never_llm_eligible() -> None:
    from app.application.autofill.questions import is_llm_eligible_question

    profile = _profile(employment={"professional_tech_stack": ["Java", "Kotlin"]})
    field = DiscoveredField(
        label="Which of these languages are you most proficient in: Go, Ruby or Python?",
        field_type="textarea",
        required=True,
    )
    mapped = map_question(field, profile)
    assert is_llm_eligible_question(field, mapped) is False


def test_named_skill_set_with_options_matches_visible_option() -> None:
    profile = _profile(employment={"technology_years": [{"technology": "Ruby", "years": 3}]})
    mapped = map_question(
        DiscoveredField(
            label="Which of these languages are you most proficient in: Go, Ruby or Python?",
            field_type="radio",
            options=["Go", "Ruby", "Python"],
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.SKILL_SET_CHOICE
    assert mapped.fillable is True
    assert mapped.value == "Ruby"


def test_named_skill_set_ambiguous_multi_match_uses_explicit_primary_language() -> None:
    profile = _profile(
        employment={
            "professional_tech_stack": ["Go", "Ruby"],
            "primary_programming_language": "Ruby",
        }
    )
    mapped = map_question(
        DiscoveredField(
            label="Which of these languages are you most proficient in: Go, Ruby or Python?",
            field_type="textarea",
        ),
        profile,
    )
    assert mapped.kind is QuestionKind.SKILL_SET_CHOICE
    assert mapped.fillable is True
    assert mapped.value == "Ruby"
