from pathlib import Path


DEFAULT_PROFILE_PATH = Path("profiles/candidate_profile.md")


class ProfileLoadError(Exception):
    """Raised when candidate profile cannot be loaded."""


def load_candidate_profile(path: Path = DEFAULT_PROFILE_PATH) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileLoadError(f"Cannot read candidate profile: {path}") from exc

    if not content.strip():
        raise ProfileLoadError(f"Candidate profile is empty: {path}")
    return content
