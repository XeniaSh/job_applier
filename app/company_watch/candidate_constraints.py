from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.company_watch.seniority import SENIORITY_LABELS

DEFAULT_CANDIDATE_CONSTRAINTS_PATH = Path("config/candidate_constraints.yaml")


class CandidateConstraintsLoadError(Exception):
    """Raised when candidate constraints YAML cannot be loaded."""


class CandidateConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_languages: list[str] = Field(default_factory=list)
    requires_visa_sponsorship: bool
    open_to_relocation: bool
    open_to_remote_worldwide: bool
    target_seniority: list[str] = Field(default_factory=list)
    stretch_seniority: list[str] = Field(default_factory=list)
    excluded_seniority: list[str] = Field(default_factory=list)

    @field_validator(
        "known_languages",
        "target_seniority",
        "stretch_seniority",
        "excluded_seniority",
        mode="before",
    )
    @classmethod
    def coerce_str_lists(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value

    @field_validator("known_languages")
    @classmethod
    def normalize_languages(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = " ".join(item.strip().lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned

    @field_validator("target_seniority", "stretch_seniority", "excluded_seniority")
    @classmethod
    def normalize_seniority_labels(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        unknown: list[str] = []
        allowed = set(SENIORITY_LABELS)
        for item in value:
            normalized = " ".join(item.strip().upper().split())
            if not normalized or normalized in seen:
                continue
            if normalized not in allowed:
                unknown.append(normalized)
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        if unknown:
            allowed_text = ", ".join(SENIORITY_LABELS)
            raise ValueError(
                "Unknown seniority labels: " + ", ".join(unknown) + f". Expected {allowed_text}."
            )
        return cleaned


def load_candidate_constraints(
    path: str | Path = DEFAULT_CANDIDATE_CONSTRAINTS_PATH,
) -> CandidateConstraints:
    config_path = Path(path)
    try:
        raw_content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config not found: {config_path}"
        ) from exc
    except OSError as exc:
        raise CandidateConstraintsLoadError(
            f"Cannot read candidate constraints config: {config_path}"
        ) from exc

    if not raw_content.strip():
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config is empty: {config_path}"
        )

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config is not valid YAML: {config_path}\n{exc}"
        ) from exc

    if payload is None:
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config is empty: {config_path}"
        )
    if not isinstance(payload, dict):
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config must be a mapping: {config_path}"
        )

    try:
        return CandidateConstraints.model_validate(payload)
    except ValidationError as exc:
        raise CandidateConstraintsLoadError(
            f"Candidate constraints config is invalid: {config_path}\n{exc}"
        ) from exc
