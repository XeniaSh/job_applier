from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.resume import ResumeResolutionError, resolve_default_resume_path
from app.application.candidate_profile import CandidateProfile


def _profile(resume_path: str) -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": resume_path},
        }
    )


def test_resolves_existing_synthetic_resume(tmp_path: Path) -> None:
    resume = tmp_path / "synthetic_resume.txt"
    resume.write_bytes(b"%PDF-FAKE\n")
    path = resolve_default_resume_path(_profile(str(resume)))
    assert path == resume
    assert path.is_file()


def test_missing_resume_is_a_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    with pytest.raises(ResumeResolutionError, match="not found"):
        resolve_default_resume_path(_profile(str(missing)))
