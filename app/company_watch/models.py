from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _empty_str_to_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _none_to_empty_list(value: object) -> object:
    if value is None:
        return []
    return value


class TargetCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    priority: str
    language: str
    relocation_status: str
    watcher_type: str
    category: str | None = None
    russian_speaking_signal: bool | None = None
    hiring_modes: list[str] = Field(default_factory=list)
    known_hiring_locations: list[str] = Field(default_factory=list)
    remote: str | None = None
    ats: str | None = None
    career_url: str | None = None
    job_board_url: str | None = None
    java_backend_relevance: str | None = None
    role_keywords: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _require_non_empty_str(value, "name")

    @field_validator("priority", mode="before")
    @classmethod
    def validate_priority(cls, value: object) -> str:
        return _require_non_empty_str(value, "priority")

    @field_validator("watcher_type", mode="before")
    @classmethod
    def validate_watcher_type(cls, value: object) -> str:
        return _require_non_empty_str(value, "watcher_type")

    @field_validator(
        "ats",
        "career_url",
        "job_board_url",
        "java_backend_relevance",
        "category",
        "remote",
        mode="before",
    )
    @classmethod
    def coerce_optional_str(cls, value: object) -> str | None:
        return _empty_str_to_none(value)

    @field_validator(
        "hiring_modes",
        "known_hiring_locations",
        "role_keywords",
        "notes",
        mode="before",
    )
    @classmethod
    def coerce_list_fields(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return _none_to_empty_list(value)


class TargetCompaniesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[TargetCompany]
