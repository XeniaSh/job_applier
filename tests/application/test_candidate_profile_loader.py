from __future__ import annotations

import shutil
import traceback
from pathlib import Path

import pytest

from app.application.candidate_profile_loader import (
    CandidateProfileLoadError,
    load_structured_candidate_profile,
)

# The real tracked example profile is fully material (employment, salary,
# sensitive, consent, policy facts, etc.) unlike the trimmed EXAMPLE_YAML
# above, so it is used for the non-inheritance regression tests below.
REPO_EXAMPLE_PROFILE = Path(__file__).resolve().parents[2] / "candidate_profile.example.yaml"

EXAMPLE_YAML = """
identity:
  first_name: Ada
  last_name: Example
  email: ada.example@example.test
  phone: "+15555550100"
  current_location: "Berlin, Germany"
  country: Germany
application_files:
  default_resume: resumes/example_java_backend.pdf
  alternative_resumes:
    - resumes/example_alt.pdf
  cover_letter_strategy: generate
work_eligibility:
  citizenship:
    - Germany
  requires_visa_sponsorship: false
  work_authorizations:
    - country: Germany
      authorized: true
      requires_sponsorship: false
application_policy:
  prior_affiliations:
    - organization: Deloitte
      associated: false
  question_overrides:
    - question_contains: "engineering blog"
      answer: true
"""

LOCAL_IDENTITY_ONLY_YAML = """
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
"""

LOCAL_PARTIAL_NESTED_YAML = """
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
employment: {}
work_eligibility: {}
application_policy: {}
"""

LOCAL_FULLY_EXPLICIT_YAML = """
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
  country: Thailand
application_files:
  default_resume: resumes/local_resume.pdf
  alternative_resumes: []
  cover_letter_strategy: null
professional_links:
  open_source_urls: []
employment:
  years_of_experience: 0
  open_to_relocation: false
work_eligibility:
  citizenship: []
  requires_visa_sponsorship: false
  work_authorizations:
    - country: Thailand
      authorized: true
application_policy:
  default_no_undeclared_affiliations: false
  prior_affiliations: []
  question_overrides: []
"""


LOCAL_PARTIAL_SALARY_YAML = """
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
employment:
  salary_expectations:
    fill_salary: true
"""

LOCAL_SAME_AS_EXAMPLE_COUNTRY_YAML = """
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
  country: Germany
work_eligibility:
  citizenship:
    - Germany
  work_authorizations:
    - country: Germany
      authorized: true
      requires_sponsorship: false
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _rendered_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _copy_real_example(tmp_path: Path) -> Path:
    assert REPO_EXAMPLE_PROFILE.exists(), "tracked candidate_profile.example.yaml must exist"
    example = tmp_path / "candidate_profile.example.yaml"
    shutil.copyfile(REPO_EXAMPLE_PROFILE, example)
    return example


def test_missing_local_overlay_fails_safely(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    missing_local = tmp_path / "candidate_profile.local.yaml"

    with pytest.raises(CandidateProfileLoadError, match="not found"):
        load_structured_candidate_profile(example_path=example, local_path=missing_local)


def test_disabled_local_path_fails_safely(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)

    with pytest.raises(CandidateProfileLoadError, match="not configured"):
        load_structured_candidate_profile(example_path=example, local_path=None)


def test_identity_only_local_inherits_no_optional_material_facts(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_IDENTITY_ONLY_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.identity.first_name == "Rae"
    assert profile.identity.country is None
    # Non-factual application_files technical defaults may fall back to the example.
    assert profile.application_files.default_resume == "resumes/example_java_backend.pdf"
    # No factual data is inherited from the example.
    assert profile.work_eligibility.citizenship == []
    assert profile.work_eligibility.requires_visa_sponsorship is None
    assert profile.work_eligibility.work_authorizations == []
    assert profile.employment.years_of_experience is None
    assert profile.application_policy.prior_affiliations == []
    assert profile.application_policy.question_overrides == []
    assert profile.work_authorization_for("Germany") is None
    assert profile.is_national_of("Germany") is None
    assert profile.employment_restrictions_answer() is None
    assert profile.relocation_willingness() is None
    # Established model-level application policy defaults still apply.
    assert profile.application_policy.default_no_undeclared_affiliations is True
    assert profile.application_policy.prefer_not_to_disclose_gender is True


def test_partial_nested_maps_cannot_backfill_from_example(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_PARTIAL_NESTED_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.work_eligibility.citizenship == []
    assert profile.work_eligibility.work_authorizations == []
    assert profile.work_eligibility.requires_visa_sponsorship is None
    assert profile.employment.years_of_experience is None
    assert profile.application_policy.prior_affiliations == []
    assert profile.application_policy.question_overrides == []


def test_explicit_falsy_values_survive(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_FULLY_EXPLICIT_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.employment.years_of_experience == 0
    assert profile.employment.open_to_relocation is False
    assert profile.work_eligibility.citizenship == []
    assert profile.work_eligibility.requires_visa_sponsorship is False
    assert profile.application_policy.default_no_undeclared_affiliations is False
    assert profile.application_policy.prior_affiliations == []
    assert profile.application_policy.question_overrides == []
    assert profile.application_files.alternative_resumes == []
    assert profile.application_files.cover_letter_strategy is None
    # Explicit legitimate country/work authorization remains valid without
    # inferring other permissions (no citizenship, no sponsorship fact).
    assert profile.work_authorization_for("Thailand") is True
    assert profile.is_national_of("Thailand") is None
    assert profile.sponsorship_required_for("Thailand") is None


def test_fully_explicit_local_profile_keeps_working(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_FULLY_EXPLICIT_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.identity.first_name == "Rae"
    assert profile.application_files.default_resume == "resumes/local_resume.pdf"


def test_changing_example_fact_values_has_no_runtime_effect(tmp_path: Path) -> None:
    other_example = EXAMPLE_YAML.replace("Germany", "Poland").replace("Deloitte", "Acme")
    example_a = _write(tmp_path / "example_a.yaml", EXAMPLE_YAML)
    example_b = _write(tmp_path / "example_b.yaml", other_example)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_IDENTITY_ONLY_YAML)

    profile_a = load_structured_candidate_profile(example_path=example_a, local_path=local)
    profile_b = load_structured_candidate_profile(example_path=example_b, local_path=local)

    assert profile_a.work_eligibility.citizenship == []
    assert profile_b.work_eligibility.citizenship == []
    assert profile_a.application_policy.prior_affiliations == []
    assert profile_b.application_policy.prior_affiliations == []
    # Both still fall back to the (differing) example's non-factual
    # application_files technical defaults -- the only example contribution.
    assert profile_a.application_files.default_resume == "resumes/example_java_backend.pdf"
    assert profile_b.application_files.default_resume == "resumes/example_java_backend.pdf"


def test_missing_example_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        LOCAL_IDENTITY_ONLY_YAML,
    )
    with pytest.raises(CandidateProfileLoadError, match="invalid"):
        load_structured_candidate_profile(example_path=missing, local_path=local)


def test_invalid_yaml_does_not_leak_sentinel(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = "SENTINEL_YAML_BROKEN_VALUE"
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f'identity:\n  first_name: "{sentinel}\n',
    )

    with pytest.raises(CandidateProfileLoadError, match="not valid YAML") as excinfo:
        load_structured_candidate_profile(example_path=example, local_path=local)

    assert sentinel not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_invalid_schema_does_not_leak_sentinel(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = "SENTINEL_BAD_PREFERENCE_VALUE"
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f"""
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
application_policy:
  remote_work_arrangement:
    preference: "{sentinel}"
""",
    )

    with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
        load_structured_candidate_profile(example_path=example, local_path=local)

    assert sentinel not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_loader_does_not_log_email_or_phone(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_IDENTITY_ONLY_YAML)

    with caplog.at_level("INFO"):
        load_structured_candidate_profile(example_path=example, local_path=local)

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "rae.candidate@example.test" not in combined
    assert "+15555550999" not in combined


def test_unknown_top_level_key_does_not_leak_sentinel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = "SYNTHETIC_PRIVATE_FIELD_SENTINEL"
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f"""
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
{sentinel}: super-secret-value
""",
    )

    with caplog.at_level("INFO"):
        with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
            load_structured_candidate_profile(example_path=example, local_path=local)

    assert sentinel not in str(excinfo.value)
    assert sentinel not in _rendered_traceback(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert "<redacted>: extra_forbidden" in str(excinfo.value)
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert sentinel not in combined_logs


def test_unknown_nested_key_does_not_leak_sentinel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = "SYNTHETIC_PRIVATE_FIELD_SENTINEL"
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f"""
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
  {sentinel}: super-secret-value
""",
    )

    with caplog.at_level("INFO"):
        with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
            load_structured_candidate_profile(example_path=example, local_path=local)

    assert sentinel not in str(excinfo.value)
    assert sentinel not in _rendered_traceback(excinfo.value)
    assert excinfo.value.__cause__ is None
    # The known field path leading to the unexpected key is safe to disclose;
    # only the arbitrary unexpected key name itself is redacted.
    assert "identity.<redacted>: extra_forbidden" in str(excinfo.value)
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert sentinel not in combined_logs


def test_unknown_numeric_top_level_key_does_not_leak_sentinel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A bare numeric YAML key fails as ``invalid_key``, not ``extra_forbidden``."""
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = 9876543210123
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f"""
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
{sentinel}: super-secret-value
""",
    )

    with caplog.at_level("INFO"):
        with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
            load_structured_candidate_profile(example_path=example, local_path=local)

    assert str(sentinel) not in str(excinfo.value)
    assert str(sentinel) not in _rendered_traceback(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert "<redacted>: invalid_key" in str(excinfo.value)
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert str(sentinel) not in combined_logs


def test_unknown_numeric_nested_key_does_not_leak_sentinel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A bare numeric YAML key nested under a known field also fails as ``invalid_key``."""
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    sentinel = 9876543210123
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        f"""
identity:
  first_name: Rae
  last_name: Candidate
  email: rae.candidate@example.test
  phone: "+15555550999"
  {sentinel}: super-secret-value
""",
    )

    with caplog.at_level("INFO"):
        with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
            load_structured_candidate_profile(example_path=example, local_path=local)

    assert str(sentinel) not in str(excinfo.value)
    assert str(sentinel) not in _rendered_traceback(excinfo.value)
    assert excinfo.value.__cause__ is None
    # The known field path leading to the unexpected key is safe to disclose;
    # only the arbitrary unexpected key itself is redacted.
    assert "identity.<redacted>: invalid_key" in str(excinfo.value)
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert str(sentinel) not in combined_logs


def test_identity_only_local_with_real_example_inherits_no_material_facts(tmp_path: Path) -> None:
    """Regression for the fully-material tracked example (employment, salary,
    sensitive, consent, policy, etc.), which the trimmed module-level
    EXAMPLE_YAML above does not exercise.
    """
    example = _copy_real_example(tmp_path)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_IDENTITY_ONLY_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.identity.first_name == "Rae"
    assert profile.identity.current_location is None
    assert profile.identity.country is None
    assert profile.resides_in_any(["Germany"]) is None

    assert profile.professional_links.linkedin is None
    assert profile.professional_links.github is None
    assert profile.professional_links.website is None
    assert profile.professional_links.gitlab_username is None
    assert profile.professional_links.open_source_urls == []

    assert profile.employment.current_title is None
    assert profile.employment.current_employer is None
    assert profile.employment.school is None
    assert profile.employment.years_of_experience is None
    assert profile.employment.years_of_relevant_experience is None
    assert profile.employment.open_to_relocation is None
    assert profile.employment.relocation_destinations == []
    assert profile.employment.highest_academic_level is None
    assert profile.employment.postgraduate_studies_completed is None
    assert profile.employment.doctorate_awarded is None
    assert profile.employment.professional_tech_stack == []
    assert profile.employment.primary_programming_language is None
    assert profile.employment.technology_years == []
    assert profile.employment.salary_expectations is None
    assert profile.may_fill_salary() is False

    assert profile.work_eligibility.citizenship == []
    assert profile.work_eligibility.work_authorizations == []
    assert profile.work_eligibility.requires_visa_sponsorship is None
    assert profile.work_eligibility.current_location_requires_visa_sponsorship is None
    assert profile.work_authorization_for("Germany") is None
    assert profile.sponsorship_required_for("Germany") is None
    assert profile.sponsorship_answer_for_scope("current") == (None, None)

    assert profile.employee_relationship.has_relationship is None
    assert profile.employee_relationship.how_known is None
    assert profile.employee_relationship.employee_name is None
    assert profile.application_consent.privacy_data_processing is None
    assert profile.application_consent.recruiting_contact is None
    assert profile.sensitive.gender is None
    assert profile.sensitive.is_unset() is True
    assert profile.sensitive_answers_for_autofill() == {}
    assert profile.application_policy.has_employment_or_post_employment_restrictions is None
    assert profile.employment_restrictions_answer() is None
    assert profile.application_policy.prior_affiliations == []
    assert profile.application_policy.question_overrides == []

    # Established model-level application-policy defaults still apply --
    # these come from the Pydantic model's own field defaults, not the example.
    assert profile.application_policy.default_no_undeclared_affiliations is True
    assert profile.application_policy.prefer_not_to_disclose_gender is True
    assert profile.application_policy.relocation.willing is None
    assert profile.application_policy.office_work.willing is True
    assert profile.application_policy.remote_work_arrangement.preference is None
    assert profile.application_policy.privacy_acknowledgement.auto_acknowledge_required is True
    assert profile.application_policy.newsletter_opt_in is False
    assert profile.application_policy.sms_interview_updates is False

    # Non-factual application_files technical defaults may fall back to the example.
    assert profile.application_files.default_resume == "resumes/example_java_backend.pdf"


def test_explicit_fill_salary_without_amount_does_not_inherit_example_amount(
    tmp_path: Path,
) -> None:
    example = _copy_real_example(tmp_path)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_PARTIAL_SALARY_YAML)

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.employment.salary_expectations is not None
    assert profile.employment.salary_expectations.fill_salary is True
    assert profile.employment.salary_expectations.amount is None
    assert profile.employment.salary_expectations.currency is None
    assert profile.may_fill_salary() is True


def test_local_profile_matching_example_country_is_source_based_not_blacklisted(
    tmp_path: Path,
) -> None:
    """An explicit local fact that happens to match the example's demo value
    (Germany) must still be honored -- non-inheritance is based on where the
    fact came from (local vs. example), not a blacklist of values that
    resemble the example.
    """
    example = _copy_real_example(tmp_path)
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        LOCAL_SAME_AS_EXAMPLE_COUNTRY_YAML,
    )

    profile = load_structured_candidate_profile(example_path=example, local_path=local)

    assert profile.identity.country == "Germany"
    assert profile.is_national_of("Germany") is True
    assert profile.work_authorization_for("Germany") is True
    assert profile.sponsorship_required_for("Germany") is False
    # Other example-only facts are still absent.
    assert profile.employment.years_of_experience is None
    assert profile.professional_links.linkedin is None


def test_missing_required_identity_field_fails_without_backfill(tmp_path: Path) -> None:
    example = _copy_real_example(tmp_path)
    local = _write(
        tmp_path / "candidate_profile.local.yaml",
        """
identity:
  first_name: Rae
  last_name: Candidate
  phone: "+15555550999"
""",
    )

    with pytest.raises(CandidateProfileLoadError, match="invalid") as excinfo:
        load_structured_candidate_profile(example_path=example, local_path=local)

    assert "identity.email: missing" in str(excinfo.value)
