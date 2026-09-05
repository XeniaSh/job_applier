from pathlib import Path

import pytest

from app.company_watch.config_loader import (
    DEFAULT_TARGET_COMPANIES_PATH,
    TargetCompaniesConfigLoadError,
    load_target_companies_config,
)

MINIMAL_VALID_YAML = """
companies:
  - name: Acme
    priority: A
    language: english
    relocation_status: remote_global
    watcher_type: greenhouse
"""


def test_load_minimal_valid_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")

    config = load_target_companies_config(config_file)

    assert len(config.companies) == 1
    company = config.companies[0]
    assert company.name == "Acme"
    assert company.priority == "A"
    assert company.language == "english"
    assert company.relocation_status == "remote_global"
    assert company.watcher_type == "greenhouse"
    assert company.known_hiring_locations == []
    assert company.role_keywords == []
    assert company.role_title_keywords == []
    assert company.role_description_keywords == []
    assert company.exclude_title_keywords == []
    assert company.notes == []
    assert company.career_url is None
    assert company.job_board_url is None
    assert company.ats is None


def test_load_real_target_companies_yaml() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)

    assert config.companies
    names = [company.name for company in config.companies]
    assert "Agoda" in names
    agoda = next(company for company in config.companies if company.name == "Agoda")
    assert agoda.priority == "A"
    assert agoda.watcher_type == "greenhouse"
    assert agoda.known_hiring_locations
    assert agoda.role_keywords
    assert agoda.role_title_keywords
    assert agoda.career_url is not None
    assert agoda.job_board_url is not None

    vinted = next(company for company in config.companies if company.name == "Vinted")
    exness = next(company for company in config.companies if company.name == "Exness")
    assert vinted.watcher_type == "manual"
    assert vinted.ats == "custom"
    assert exness.watcher_type == "manual"
    assert exness.ats == "custom"


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.yaml"

    with pytest.raises(TargetCompaniesConfigLoadError, match="not found") as exc_info:
        load_target_companies_config(missing_file)

    assert str(missing_file) in str(exc_info.value)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("", "empty"),
        ("companies: not-a-list\n", "invalid"),
        ("- just a list\n", "must be a mapping"),
        ("other_key: []\n", "invalid"),
    ],
)
def test_invalid_structure_raises_clear_error(
    tmp_path: Path,
    content: str,
    match: str,
) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(TargetCompaniesConfigLoadError, match=match):
        load_target_companies_config(config_file)


@pytest.mark.parametrize(
    ("blank_field", "content"),
    [
        (
            "name",
            "companies:\n  - name: ' '\n    priority: A\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: greenhouse\n",
        ),
        (
            "priority",
            "companies:\n  - name: Acme\n    priority: ' '\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: greenhouse\n",
        ),
        (
            "watcher_type",
            "companies:\n  - name: Acme\n    priority: A\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: ' '\n",
        ),
    ],
)
def test_blank_required_fields_are_rejected(
    tmp_path: Path,
    blank_field: str,
    content: str,
) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(TargetCompaniesConfigLoadError, match=blank_field):
        load_target_companies_config(config_file)


def test_extra_field_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(
        "\n".join(
            [
                "companies:",
                "  - name: Acme",
                "    priority: A",
                "    language: english",
                "    relocation_status: remote_global",
                "    watcher_type: greenhouse",
                "    unexpected_typo: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TargetCompaniesConfigLoadError, match="unexpected_typo"):
        load_target_companies_config(config_file)
