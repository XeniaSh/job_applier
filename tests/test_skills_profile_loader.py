from pathlib import Path

import pytest

from app.skills_profile_loader import SkillsProfileLoadError, load_candidate_skills


def test_successful_skills_profile_loading(tmp_path: Path) -> None:
    skills_file = tmp_path / "candidate_skills.yml"
    skills_file.write_text(
        "\n".join(
            [
                "experience_years: 6",
                "strong_skills:",
                "  - java",
                "practical_skills:",
                "  - ci/cd",
                "absent_skills:",
                "  - redis",
                "aliases:",
                "  concurrency:",
                "    - multithreading",
                "core_skills:",
                "  - java",
                "skill_weights:",
                "  java: 10",
            ]
        ),
        encoding="utf-8",
    )

    profile = load_candidate_skills(skills_file)

    assert profile.strong_skills == ["java"]
    assert profile.absent_skills == ["redis"]
    assert profile.aliases["concurrency"] == ["multithreading"]
    assert profile.experience_years == 6
    assert profile.core_skills == ["java"]
    assert profile.skill_weights["java"] == 10


def test_invalid_skills_profile_raises_error(tmp_path: Path) -> None:
    skills_file = tmp_path / "candidate_skills.yml"
    skills_file.write_text("not: [valid", encoding="utf-8")

    with pytest.raises(SkillsProfileLoadError):
        load_candidate_skills(skills_file)
