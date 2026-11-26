from app.company_watch.seniority import classify_seniority
import json
from pathlib import Path

import pytest


def test_senior_backend_engineer_is_senior() -> None:
    result = classify_seniority("Senior Backend Engineer")
    assert result.label == "SENIOR"


def test_staff_java_engineer_is_staff_plus() -> None:
    result = classify_seniority("Staff Java Engineer")
    assert result.label == "STAFF_PLUS"


def test_principal_software_engineer_is_staff_plus() -> None:
    result = classify_seniority("Principal Software Engineer")
    assert result.label == "STAFF_PLUS"


def test_software_engineer_i_is_junior() -> None:
    result = classify_seniority("Software Engineer I (Java)")
    assert result.label == "JUNIOR"


def test_engineering_manager_is_lead_manager() -> None:
    result = classify_seniority("Engineering Manager")
    assert result.label == "LEAD_MANAGER"


def test_graduate_software_engineer_is_junior() -> None:
    result = classify_seniority("Graduate Software Engineer")
    assert result.label == "JUNIOR"


def test_intern_is_intern() -> None:
    result = classify_seniority("Software Engineering Intern")
    assert result.label == "INTERN"


def test_team_lead_is_lead_manager() -> None:
    result = classify_seniority("Java Team Lead")
    assert result.label == "LEAD_MANAGER"


def test_senior_engineering_manager_is_lead_manager_not_senior() -> None:
    result = classify_seniority("Senior Engineering Manager")
    assert result.label == "LEAD_MANAGER"


def test_title_without_level_is_unknown() -> None:
    result = classify_seniority("Java Backend Engineer")
    assert result.label == "UNKNOWN"


def test_engineer_ii_is_mid() -> None:
    result = classify_seniority("Software Engineer II")
    assert result.label == "MID"


def test_lead_software_engineer_backend_is_lead_manager() -> None:
    result = classify_seniority(
        "Lead Software Engineer - Back End (FinTech) (Bangkok based - Relocation provided)"
    )
    assert result.label == "LEAD_MANAGER"


@pytest.mark.parametrize("title,expected", [
    ("Senior Software Engineer I - Pricing", "SENIOR"),
    ("Senior Backend Engineer I", "SENIOR"),
    ("Junior Senior Software Engineer I", "JUNIOR"),
    ("Graduate Software Engineer I", "JUNIOR"),
    ("Senior Engineering Manager I", "LEAD_MANAGER"),
    ("Senior Staff Engineer I", "STAFF_PLUS"),
    ("Senior Software Engineer Intern", "INTERN"),
    ("Software Engineer III", "SENIOR"),
    ("Software Engineer II", "MID"),
    ("Software Engineer I", "JUNIOR"),
])
def test_mixed_title_precedence(title, expected):
    assert classify_seniority(title).label == expected


def test_historical_public_title_replay_changes_only_explicit_senior_i():
    data = json.loads((Path(__file__).parents[1] / "fixtures/discovery/seniority_replay.json").read_text())
    assert len(data["wise"]) == 49
    changed = []
    for row in data["wise"] + data["nz"]:
        assert classify_seniority(row["title"]).label == row["expected"], row
        if row["before"] != row["expected"]:
            changed.append(row)
            assert row["before"] == "JUNIOR"
            assert row["expected"] == "SENIOR"
            assert "senior" in row["title"].lower()
    assert len(changed) == 12
    assert all(x["before"] == x["expected"] for x in data["nz"])
