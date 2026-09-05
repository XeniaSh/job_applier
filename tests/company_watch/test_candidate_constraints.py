from pathlib import Path

import pytest

from app.company_watch.candidate_constraints import (
    DEFAULT_CANDIDATE_CONSTRAINTS_PATH,
    CandidateConstraintsLoadError,
    load_candidate_constraints,
)

MINIMAL_YAML = """
known_languages:
  - English
  - Russian
requires_visa_sponsorship: true
open_to_relocation: true
open_to_remote_worldwide: true
"""


def test_load_minimal_candidate_constraints(tmp_path: Path) -> None:
    config_file = tmp_path / "candidate_constraints.yaml"
    config_file.write_text(MINIMAL_YAML, encoding="utf-8")

    constraints = load_candidate_constraints(config_file)

    assert constraints.known_languages == ["english", "russian"]
    assert constraints.requires_visa_sponsorship is True
    assert constraints.open_to_relocation is True
    assert constraints.open_to_remote_worldwide is True


def test_load_real_candidate_constraints_yaml() -> None:
    constraints = load_candidate_constraints(DEFAULT_CANDIDATE_CONSTRAINTS_PATH)

    assert "english" in constraints.known_languages
    assert "russian" in constraints.known_languages
    assert constraints.requires_visa_sponsorship is True
    assert constraints.open_to_relocation is True
    assert constraints.open_to_remote_worldwide is True


def test_missing_constraints_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(CandidateConstraintsLoadError, match="not found"):
        load_candidate_constraints(missing)
