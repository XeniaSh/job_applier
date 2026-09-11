from __future__ import annotations

from pathlib import Path

from app.application.candidate_profile import CandidateProfile


class ResumeResolutionError(Exception):
    """Raised when the Stage 1 resume file cannot be used."""


def resolve_default_resume_path(profile: CandidateProfile) -> Path:
    """Return the profile default resume path if the file exists.

    Does not read file contents.
    """
    raw = profile.application_files.default_resume.strip()
    path = Path(raw)
    if not path.is_file():
        raise ResumeResolutionError(f"Resume file not found: {path}")
    return path
