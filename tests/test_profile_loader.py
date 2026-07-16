from pathlib import Path

import pytest

from app.profile_loader import (
    ProfileLoadError,
    load_candidate_profile,
    load_candidate_profile_context,
)


def test_successful_profile_loading(tmp_path: Path) -> None:
    profile_file = tmp_path / "candidate_profile.md"
    profile_file.write_text("# Candidate\nJava backend", encoding="utf-8")

    content = load_candidate_profile(profile_file)

    assert content == "# Candidate\nJava backend"


def test_missing_profile(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing_profile.md"

    with pytest.raises(ProfileLoadError):
        load_candidate_profile(missing_file)


def test_profile_context_uses_config_for_language_and_gender(tmp_path: Path) -> None:
    profile_file = tmp_path / "candidate_profile.md"
    profile_file.write_text("Java Backend Engineer with seven years.", encoding="utf-8")

    context = load_candidate_profile_context(
        path=profile_file,
        preferred_language="ru",
        grammatical_gender="female",
    )
    assert context.preferred_language == "ru"
    assert context.grammatical_gender == "female"
