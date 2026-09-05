from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator


class Decision(str, Enum):
    STRONG_MATCH = "STRONG_MATCH"
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    IGNORE = "IGNORE"


class RecommendedResume(str, Enum):
    JAVA = "java"
    JAVA_AI = "java_ai"

    @classmethod
    def _missing_(cls, value: object) -> "RecommendedResume | None":
        legacy_mapping = {
            "java-backend": cls.JAVA,
            "kotlin-backend": cls.JAVA,
            "fintech-backend": cls.JAVA,
            "ai-adjacent-backend": cls.JAVA_AI,
        }
        return legacy_mapping.get(value)


class RecommendedCoverTemplate(str, Enum):
    GENERIC = "generic"
    PRODUCT = "product"
    FINTECH = "fintech"
    AGENCY = "agency"
    AI_ADJACENT = "ai-adjacent"


class VacancyExtraction(BaseModel):
    mandatory_skills: list[str] = Field(default_factory=list)
    optional_skills: list[str] = Field(default_factory=list)
    minimum_experience_years: int | None = None
    seniority: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    employment_conditions: list[str] = Field(default_factory=list)
    location_restrictions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    role_type: str
    short_summary: str
    visa_sponsorship: str = "unknown"
    relocation_support: str = "unknown"
    remote_type: str = "unknown"
    work_authorization_requirement: str = "unknown"
    language_requirements: list[str] = Field(default_factory=list)

    @field_validator(
        "mandatory_skills",
        "optional_skills",
        "responsibilities",
        "employment_conditions",
        "location_restrictions",
        "uncertainties",
        "language_requirements",
        mode="before",
    )
    @classmethod
    def clean_list_fields(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return []

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = " ".join(item.strip().split()).lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned

    @field_validator("seniority", "role_type", "short_summary", mode="before")
    @classmethod
    def clean_string_fields(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @field_validator("minimum_experience_years", mode="before")
    @classmethod
    def clean_minimum_experience_years(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match is None:
                return None
            parsed = int(match.group(0))
            return parsed if parsed >= 0 else None
        return None

    @field_validator("visa_sponsorship", "relocation_support", mode="before")
    @classmethod
    def normalize_yes_no_unknown(cls, value: object) -> str:
        return _normalize_yes_no_unknown(value)

    @field_validator("remote_type", mode="before")
    @classmethod
    def normalize_remote_type_field(cls, value: object) -> str:
        return _normalize_remote_type(value)

    @field_validator("work_authorization_requirement", mode="before")
    @classmethod
    def normalize_work_authorization_field(cls, value: object) -> str:
        return _normalize_work_authorization(value)


def _normalize_yes_no_unknown(value: object) -> str:
    if value is None:
        return "unknown"
    text = " ".join(str(value).strip().lower().replace("_", " ").split())
    if text in {"", "unknown", "null", "none", "n/a", "na", "unspecified"}:
        return "unknown"
    if text in {"yes", "y", "true", "available", "provided", "supported"}:
        return "yes"
    if text in {"no", "n", "false", "unavailable", "unsupported"}:
        return "no"
    if text.startswith("yes"):
        return "yes"
    if text.startswith("no"):
        return "no"
    return "unknown"


def _normalize_remote_type(value: object) -> str:
    if value is None:
        return "unknown"
    text = " ".join(str(value).strip().lower().replace("-", " ").replace("_", " ").split())
    if text in {"", "unknown", "null", "none", "n/a", "na"}:
        return "unknown"
    if "worldwide" in text or "anywhere" in text or text == "global":
        return "worldwide"
    if "hybrid" in text:
        return "hybrid"
    if "onsite" in text or "on site" in text:
        return "onsite"
    if "country" in text or "region" in text:
        return "country_limited"
    return "unknown"


def _normalize_work_authorization(value: object) -> str:
    if value is None:
        return "unknown"
    text = " ".join(str(value).strip().lower().replace("-", " ").replace("_", " ").split())
    if text in {"", "unknown", "null", "none", "n/a", "na"}:
        return "unknown"
    if text in {"required", "must"}:
        return "required"
    if "not required" in text:
        return "not_required"
    if "required" in text:
        return "required"
    return "unknown"


class VacancyEvaluation(BaseModel):
    decision: Decision
    summary: str
    decision_reason: str = ""
    matched_points: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    nuances: list[str] = Field(default_factory=list)
    info_items: list[str] = Field(default_factory=list)
    match_percentage: float | None = None
    matched_score: float = 0.0
    total_possible_score: float = 0.0
    explicit_skill_count: int = 0
    evidence_sufficient: bool = False
    recommended_resume: RecommendedResume
    recommended_cover_template: RecommendedCoverTemplate
    warning_signals: list[dict[str, str]] = Field(default_factory=list)
    visa_sponsorship: str = "unknown"
    relocation_support: str = "unknown"
    remote_type: str = "unknown"
    work_authorization_requirement: str = "unknown"
    language_requirements: list[str] = Field(default_factory=list)
    location_restrictions: list[str] = Field(default_factory=list)


class CoverLetterResult(BaseModel):
    language: str
    cover_letter: str
    used_resume: str
