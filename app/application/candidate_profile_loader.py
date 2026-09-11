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
    """Load the example profile, overlaying a local file when it exists.

    Does not log email, phone, or other profile values.
    """
    example_file = Path(example_path)
    payload = _read_mapping(example_file, missing_message="Candidate profile example not found")

    if local_path is not None:
        local_file = Path(local_path)
        if local_file.exists():
            overlay = _read_mapping(
                local_file,
                missing_message="Candidate profile local overlay not found",
            )
            payload = _deep_merge(payload, overlay)
            logger.info(
                "Loaded structured candidate profile from %s with overlay %s",
                example_file,
                local_file,
            )
        else:
            logger.info("Loaded structured candidate profile from %s", example_file)
    else:
        logger.info("Loaded structured candidate profile from %s", example_file)

    try:
        return CandidateProfile.model_validate(payload)
    except ValidationError as exc:
        raise CandidateProfileLoadError(
            f"Candidate profile is invalid: {example_file}\n{exc}"
        ) from exc


def _read_mapping(path: Path, *, missing_message: str) -> dict[str, Any]:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CandidateProfileLoadError(f"{missing_message}: {path}") from exc
    except OSError as exc:
        raise CandidateProfileLoadError(f"Cannot read candidate profile: {path}") from exc

    if not raw_content.strip():
        raise CandidateProfileLoadError(f"Candidate profile is empty: {path}")

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise CandidateProfileLoadError(
            f"Candidate profile is not valid YAML: {path}\n{exc}"
        ) from exc

    if payload is None:
        raise CandidateProfileLoadError(f"Candidate profile is empty: {path}")
    if not isinstance(payload, dict):
        raise CandidateProfileLoadError(f"Candidate profile must be a mapping: {path}")
    return payload


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged
