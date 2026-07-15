from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


DEFAULT_SKILLS_PROFILE_PATH = Path("profiles/candidate_skills.yml")


class SkillsProfileLoadError(Exception):
    """Raised when candidate skills profile cannot be loaded."""


class CandidateSkillsProfile(BaseModel):
    strong_skills: list[str] = Field(default_factory=list)
    practical_skills: list[str] = Field(default_factory=list)
    absent_skills: list[str] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    experience_years: int | None = None
    core_skills: list[str] = Field(default_factory=list)
    skill_weights: dict[str, int] = Field(default_factory=dict)


def load_candidate_skills(path: Path = DEFAULT_SKILLS_PROFILE_PATH) -> CandidateSkillsProfile:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillsProfileLoadError(f"Cannot read candidate skills profile: {path}") from exc

    if not raw_content.strip():
        raise SkillsProfileLoadError(f"Candidate skills profile is empty: {path}")

    try:
        payload = _parse_skills_yaml(raw_content)
        return CandidateSkillsProfile.model_validate(payload)
    except (ValueError, ValidationError, TypeError) as exc:
        raise SkillsProfileLoadError(f"Candidate skills profile is invalid: {path}") from exc


def _parse_skills_yaml(raw_content: str) -> dict[str, object]:
    result: dict[str, object] = {
        "strong_skills": [],
        "practical_skills": [],
        "absent_skills": [],
        "aliases": {},
        "experience_years": None,
        "core_skills": [],
        "skill_weights": {},
    }
    list_sections = {"strong_skills", "practical_skills", "absent_skills", "core_skills"}
    current_section: str | None = None
    current_alias_key: str | None = None

    for line_number, raw_line in enumerate(raw_content.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indentation == 0:
            if not stripped.endswith(":"):
                if ":" not in stripped:
                    raise ValueError(f"Invalid top-level key at line {line_number}")
                key, raw_value = [part.strip() for part in stripped.split(":", maxsplit=1)]
                if key != "experience_years":
                    raise ValueError(f"Unknown scalar key '{key}' at line {line_number}")
                if raw_value.lower() in {"", "null", "none"}:
                    result["experience_years"] = None
                    current_section = None
                    current_alias_key = None
                    continue
                result["experience_years"] = int(raw_value)
                current_section = None
                current_alias_key = None
                continue

            section = stripped[:-1].strip()
            if section not in result:
                raise ValueError(f"Unknown section '{section}' at line {line_number}")
            current_section = section
            current_alias_key = None
            continue

        if current_section is None:
            raise ValueError(f"Unexpected indentation at line {line_number}")

        if current_section in list_sections:
            if indentation != 2 or not stripped.startswith("- "):
                raise ValueError(f"Invalid list item at line {line_number}")
            value = stripped[2:].strip()
            if value:
                cast_list = result[current_section]
                if isinstance(cast_list, list):
                    cast_list.append(value)
            continue

        if current_section == "aliases":
            aliases = result["aliases"]
            if not isinstance(aliases, dict):
                raise ValueError("Invalid aliases section")
            if indentation == 2 and stripped.endswith(":"):
                alias_key = stripped[:-1].strip()
                aliases[alias_key] = []
                current_alias_key = alias_key
                continue
            if indentation == 4 and stripped.startswith("- ") and current_alias_key is not None:
                value = stripped[2:].strip()
                if value:
                    alias_values = aliases[current_alias_key]
                    if isinstance(alias_values, list):
                        alias_values.append(value)
                continue
            raise ValueError(f"Invalid aliases entry at line {line_number}")

        if current_section == "skill_weights":
            weights = result["skill_weights"]
            if not isinstance(weights, dict):
                raise ValueError("Invalid skill_weights section")
            if indentation != 2 or ":" not in stripped:
                raise ValueError(f"Invalid skill_weights entry at line {line_number}")
            skill, raw_weight = [part.strip() for part in stripped.split(":", maxsplit=1)]
            if not skill:
                raise ValueError(f"Invalid skill name in skill_weights at line {line_number}")
            if not raw_weight:
                raise ValueError(f"Missing skill weight value at line {line_number}")
            weights[skill] = int(raw_weight)
            continue

    return result
