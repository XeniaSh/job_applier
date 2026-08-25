from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


DEFAULT_RESUME_PROFILES_PATH = Path("resume_profiles.yaml")
DEFAULT_RESUME_PROFILE_ID = "java"


class ResumeProfilesLoadError(Exception):
    """Raised when resume profile configuration cannot be loaded."""


@dataclass(frozen=True)
class ResumeProfile:
    id: str
    description: str
    pdf: Path


@dataclass(frozen=True)
class ResumeProfiles:
    profiles: tuple[ResumeProfile, ...]
    default_profile_id: str = DEFAULT_RESUME_PROFILE_ID

    def get(self, profile_id: str) -> ResumeProfile | None:
        normalized = profile_id.strip()
        return next((profile for profile in self.profiles if profile.id == normalized), None)

    @property
    def default(self) -> ResumeProfile:
        profile = self.get(self.default_profile_id)
        if profile is None:
            raise ResumeProfilesLoadError(
                f"Default resume profile '{self.default_profile_id}' is not configured."
            )
        return profile

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self.profiles)

    def format_for_prompt(self) -> str:
        sections = []
        for profile in self.profiles:
            sections.append(
                f"Profile ID: {profile.id}\n\n"
                f"Description:\n{profile.description.strip()}\n\n---"
            )
        return "\n\n".join(sections)


def load_resume_profiles(
    path: Path = DEFAULT_RESUME_PROFILES_PATH,
    *,
    default_profile_id: str = DEFAULT_RESUME_PROFILE_ID,
) -> ResumeProfiles:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ResumeProfilesLoadError(f"Cannot read resume profiles: {path}") from exc

    raw_profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ResumeProfilesLoadError(f"Resume profiles are missing or empty: {path}")

    profiles: list[ResumeProfile] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_profiles, start=1):
        if not isinstance(item, dict):
            raise ResumeProfilesLoadError(f"Resume profile #{index} must be an object.")
        profile_id = str(item.get("id") or "").strip()
        description = str(item.get("description") or "").strip()
        pdf_value = str(item.get("pdf") or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]+", profile_id):
            raise ResumeProfilesLoadError(f"Invalid resume profile id: {profile_id!r}")
        if profile_id in seen_ids:
            raise ResumeProfilesLoadError(f"Duplicate resume profile id: {profile_id}")
        if not description:
            raise ResumeProfilesLoadError(f"Resume profile '{profile_id}' has no description.")
        if not pdf_value:
            raise ResumeProfilesLoadError(f"Resume profile '{profile_id}' has no PDF path.")

        pdf_path = Path(pdf_value)
        if not pdf_path.is_absolute():
            pdf_path = path.parent / pdf_path
        profiles.append(
            ResumeProfile(
                id=profile_id,
                description=description,
                pdf=pdf_path,
            )
        )
        seen_ids.add(profile_id)

    loaded = ResumeProfiles(tuple(profiles), default_profile_id=default_profile_id)
    _ = loaded.default
    return loaded
