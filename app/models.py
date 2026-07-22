from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator


class Decision(str, Enum):
    STRONG_MATCH = "STRONG_MATCH"
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    IGNORE = "IGNORE"


class RecommendedResume(str, Enum):
    JAVA_BACKEND = "java-backend"
    KOTLIN_BACKEND = "kotlin-backend"
    FINTECH_BACKEND = "fintech-backend"
    AI_ADJACENT_BACKEND = "ai-adjacent-backend"


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

    @field_validator(
        "mandatory_skills",
        "optional_skills",
        "responsibilities",
        "employment_conditions",
        "location_restrictions",
        "uncertainties",
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


class CoverLetterResult(BaseModel):
    language: str
    cover_letter: str
    used_resume: str
