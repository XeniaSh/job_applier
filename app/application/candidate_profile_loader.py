from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.application.candidate_profile import CandidateProfile

logger = logging.getLogger(__name__)

DEFAULT_EXAMPLE_PROFILE_PATH = Path("candidate_profile.example.yaml")
DEFAULT_LOCAL_PROFILE_PATH = Path("candidate_profile.local.yaml")


class CandidateProfileLoadError(Exception):
    """Raised when the structured candidate profile cannot be loaded."""


def load_structured_candidate_profile(
    *,
    example_path: str | Path = DEFAULT_EXAMPLE_PROFILE_PATH,
    local_path: str | Path | None = DEFAULT_LOCAL_PROFILE_PATH,
) -> CandidateProfile:
    """Load the candidate profile solely from the local overlay file.

    Every candidate fact (identity, employment, work eligibility, employee
    relationship, consent, application policy, sensitive data, etc.) is read
    only from ``local_path``. The example file is never a source of candidate
    facts -- it only supplies the non-factual ``application_files`` technical
    defaults (legacy ``default_resume`` / ``cover_letter_strategy``) kept for
    schema compatibility; the resume that is actually used always goes
    through the separate four-variant resume_profiles system.

    A missing or disabled (``local_path=None``) local profile raises
    ``CandidateProfileLoadError`` rather than silently loading the synthetic
    example as if it were a real candidate.

    Does not log or include email, phone, or other profile values -- or raw
    YAML/validation input -- in exceptions or logs.
    """
    if local_path is None:
        raise CandidateProfileLoadError(
            "Candidate profile local overlay is not configured (local_path is None). "
            "Set local_path to your candidate_profile.local.yaml before running autofill."
        )

    local_file = Path(local_path)
    if not local_file.exists():
        raise CandidateProfileLoadError(
            f"Candidate profile local overlay not found: {local_file}. "
            "Create this file with your own candidate facts before running autofill."
        )

    payload = _read_mapping(local_file)
    payload = _with_application_files_defaults(payload, Path(example_path))

    logger.info("Loaded structured candidate profile from local overlay %s", local_file)

    try:
        return CandidateProfile.model_validate(payload)
    except ValidationError as exc:
        raise CandidateProfileLoadError(
            f"Candidate profile is invalid: {local_file} ({_safe_validation_summary(exc)})"
        ) from None


def _with_application_files_defaults(payload: dict[str, Any], example_file: Path) -> dict[str, Any]:
    """Fill in non-factual application_files technical defaults from the example.

    Only the ``application_files`` section is ever read from the example
    file; no other section of the example contributes to the loaded profile.
    """
    example_application_files = _read_example_application_files(example_file)
    if example_application_files is None:
        return payload

    local_application_files = payload.get("application_files")
    merged = dict(payload)
    if isinstance(local_application_files, dict):
        merged["application_files"] = _deep_merge(example_application_files, local_application_files)
    elif "application_files" not in payload:
        merged["application_files"] = dict(example_application_files)
    return merged


def _read_example_application_files(example_file: Path) -> dict[str, Any] | None:
    if not example_file.exists():
        return None
    try:
        example_payload = _read_mapping(example_file)
    except CandidateProfileLoadError:
        return None
    value = example_payload.get("application_files")
    return value if isinstance(value, dict) else None


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CandidateProfileLoadError(f"Candidate profile file not found: {path}") from exc
    except OSError as exc:
        raise CandidateProfileLoadError(f"Cannot read candidate profile: {path}") from exc

    if not raw_content.strip():
        raise CandidateProfileLoadError(f"Candidate profile is empty: {path}")

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise CandidateProfileLoadError(
            f"Candidate profile is not valid YAML: {path}{_safe_yaml_error_location(exc)}"
        ) from None

    if payload is None:
        raise CandidateProfileLoadError(f"Candidate profile is empty: {path}")
    if not isinstance(payload, dict):
        raise CandidateProfileLoadError(f"Candidate profile must be a mapping: {path}")
    return payload


def _safe_yaml_error_location(exc: yaml.YAMLError) -> str:
    """Line/column only -- never the YAML content/scalar that caused the error."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return ""
    return f" (line {mark.line + 1}, column {mark.column + 1})"


_UNKNOWN_KEY_ERROR_TYPES = frozenset({"extra_forbidden", "invalid_key"})


def _safe_validation_summary(exc: ValidationError) -> str:
    """Field locations and error kinds only -- never raw input values.

    An ``extra_forbidden`` error's loc ends with the actual unexpected YAML
    key name, which is arbitrary user-controlled text and may itself be
    private data (unlike a declared field name). A non-string YAML key (e.g.
    a bare numeric key) fails model validation as ``invalid_key`` instead,
    with the same shape: loc ends with the raw offending key. Both kinds have
    that final segment redacted; everything before it is a known schema
    field name and stays.
    """
    parts = []
    for error in exc.errors():
        kind = error.get("type", "invalid")
        segments = [str(segment) for segment in error.get("loc", ())]
        if kind in _UNKNOWN_KEY_ERROR_TYPES and segments:
            segments[-1] = "<redacted>"
        loc = ".".join(segments)
        parts.append(f"{loc}: {kind}" if loc else kind)
    return "; ".join(parts) or "invalid"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged
