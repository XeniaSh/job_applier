from __future__ import annotations

from pathlib import Path

import pytest

from app.application.candidate_profile_loader import (
    CandidateProfileLoadError,
    load_structured_candidate_profile,
)

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
work_eligibility:
  requires_visa_sponsorship: false
  work_authorizations:
    - country: Germany
      authorized: true
"""

LOCAL_OVERLAY_YAML = """
identity:
  phone: "+15555550999"
  current_location: "Munich, Germany"
work_eligibility:
  requires_visa_sponsorship: true
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_example_only(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    missing_local = tmp_path / "candidate_profile.local.yaml"

    profile = load_structured_candidate_profile(
        example_path=example,
        local_path=missing_local,
    )

    assert profile.identity.first_name == "Ada"
    assert profile.identity.phone == "+15555550100"
    assert profile.work_eligibility.requires_visa_sponsorship is False


def test_local_overlay_replaces_selected_fields(tmp_path: Path) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    local = _write(tmp_path / "candidate_profile.local.yaml", LOCAL_OVERLAY_YAML)

    profile = load_structured_candidate_profile(
        example_path=example,
        local_path=local,
    )

    assert profile.identity.first_name == "Ada"
    assert profile.identity.phone == "+15555550999"
    assert profile.identity.current_location == "Munich, Germany"
    assert profile.work_eligibility.requires_visa_sponsorship is True
    assert profile.work_authorization_for("Germany") is True


def test_missing_example_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(CandidateProfileLoadError, match="not found"):
        load_structured_candidate_profile(example_path=missing, local_path=None)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    example = _write(tmp_path / "broken.yaml", "identity: [unterminated")
    with pytest.raises(CandidateProfileLoadError, match="not valid YAML"):
        load_structured_candidate_profile(example_path=example, local_path=None)


def test_loader_does_not_log_email_or_phone(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    example = _write(tmp_path / "candidate_profile.example.yaml", EXAMPLE_YAML)
    with caplog.at_level("INFO"):
        load_structured_candidate_profile(example_path=example, local_path=None)

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "ada.example@example.test" not in combined
    assert "+15555550100" not in combined
    assert "email" not in combined.lower() or "Loaded structured candidate profile" in combined
    assert "+15555550100" not in combined
