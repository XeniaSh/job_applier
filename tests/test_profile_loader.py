from pathlib import Path

import pytest

from app.profile_loader import ProfileLoadError, load_candidate_profile


def test_successful_profile_loading(tmp_path: Path) -> None:
    profile_file = tmp_path / "candidate_profile.md"
    profile_file.write_text("# Candidate\nJava backend", encoding="utf-8")

    content = load_candidate_profile(profile_file)

    assert content == "# Candidate\nJava backend"


def test_missing_profile(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing_profile.md"

    with pytest.raises(ProfileLoadError):
        load_candidate_profile(missing_file)
