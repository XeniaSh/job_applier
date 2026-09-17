from __future__ import annotations

from pathlib import Path

import yaml

from app.application.candidate_profile import CandidateProfile

EXAMPLE_PROFILE_PATH = Path("candidate_profile.example.yaml")


def test_example_profile_yaml_loads_into_schema() -> None:
    raw = yaml.safe_load(EXAMPLE_PROFILE_PATH.read_text(encoding="utf-8"))
    profile = CandidateProfile.model_validate(raw)

    assert profile.identity.first_name == "Ada"
    assert profile.identity.email.endswith("@example.test")
    assert profile.work_eligibility.requires_visa_sponsorship is False
    assert profile.work_authorization_for("Germany") is True
    assert profile.application_files.default_resume.endswith(".pdf")
    assert profile.fill_sensitive_fields is False
    assert profile.employment.salary_expectations is not None
    assert profile.employment.salary_expectations.fill_salary is False
    assert profile.employment.professional_tech_stack == ["Java", "Kotlin", "Spring Boot", "PostgreSQL", "Kafka"]
    assert profile.awarded_academic_level() == "MASTERS"
    assert profile.employment.postgraduate_studies_completed is True
    assert profile.employment.doctorate_awarded is False
    assert profile.employee_relationship.has_relationship is False
    assert profile.application_policy.relocation.willing is True
    assert profile.application_policy.office_work.willing is True
    assert profile.application_policy.privacy_acknowledgement.auto_acknowledge_required is True
    assert profile.application_policy.prior_affiliations[0].organization == "Deloitte"
    assert profile.application_policy.prior_affiliations[0].associated is False
    assert profile.application_policy.has_employment_or_post_employment_restrictions is False
    assert profile.employment_restrictions_answer() is False
    assert profile.application_policy.newsletter_opt_in is False
    assert profile.application_policy.sms_interview_updates is False
    assert profile.application_policy.prefer_not_to_disclose_gender is True
    assert profile.gender_for_autofill() == "prefer not to disclose"
    assert profile.question_override_answer("Have you read our engineering blog?") is True
    assert profile.question_override_answer("Have you applied in the past 6 months?") is False
    assert profile.application_consent.privacy_data_processing is True
    assert profile.website_for_autofill() == "https://ada.example.test"
    assert profile.primary_programming_language() == "Java"
    assert profile.gitlab_username_for_autofill() is None
    assert profile.open_source_urls_for_autofill() == []
    # The example ships with no technology_years entries: deep-merging the
    # example with a private overlay must never invent a value the user
    # never configured themselves.
    assert profile.employment.technology_years == []
    assert profile.technology_years_for("Java") is None
    assert profile.technology_years_for("Kotlin") is None
