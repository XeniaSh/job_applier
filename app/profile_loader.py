from dataclasses import dataclass
from pathlib import Path
import re


PRIMARY_PROFILE_PATH = Path("candidate_profile.md")
FALLBACK_PROFILE_PATH = Path("profiles/candidate_profile.md")


class ProfileLoadError(Exception):
    """Raised when candidate profile cannot be loaded."""


@dataclass(frozen=True)
class CandidateProfileContext:
    text: str
    preferred_language: str
    grammatical_gender: str


def load_candidate_profile(path: Path | None = None) -> str:
    resolved = _resolve_profile_path(path)
    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileLoadError(f"Cannot read candidate profile: {resolved}") from exc

    if not content.strip():
        raise ProfileLoadError(f"Candidate profile is empty: {resolved}")
    return content


def load_candidate_profile_context(
    *,
    path: Path | None = None,
    preferred_language: str | None = None,
    grammatical_gender: str | None = None,
) -> CandidateProfileContext:
    text = load_candidate_profile(path)
    profile_language = _extract_profile_field(text, "preferred language")
    profile_gender = _extract_profile_field(text, "grammatical gender")
    language = _normalize_language(profile_language or preferred_language or "en")
    gender = _normalize_gender(profile_gender or grammatical_gender or "neutral")
    return CandidateProfileContext(
        text=text,
        preferred_language=language,
        grammatical_gender=gender,
    )


def _resolve_profile_path(path: Path | None) -> Path:
    if path is not None:
        return path
    if PRIMARY_PROFILE_PATH.exists():
        return PRIMARY_PROFILE_PATH
    return FALLBACK_PROFILE_PATH


def _extract_profile_field(text: str, label: str) -> str | None:
    pattern = rf"(?im)^\s*{re.escape(label)}\s*:\s*([^\n]+)\s*$"
    match = re.search(pattern, text)
    if not match:
        return None
    value = " ".join(match.group(1).strip().split())
    return value or None


def _normalize_language(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"ru", "russian"}:
        return "ru"
    if normalized in {"en", "english"}:
        return "en"
    return "en"


def _normalize_gender(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"female", "male", "neutral"}:
        return normalized
    return "neutral"
