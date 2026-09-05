from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.watchers.greenhouse import GreenhouseCompanyError, GreenhouseWatchResult

MINIMAL_CONFIG = """
companies:
  - name: Agoda
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: greenhouse
    ats: greenhouse
    job_board_url: https://job-boards.greenhouse.io/agoda
    role_keywords: [java, backend]
  - name: Qonto
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: lever
    ats: lever
    job_board_url: https://jobs.lever.co/qonto
    role_keywords: [backend]
"""


def _vacancy(
    *,
    external_id: str = "101",
    title: str = "Java Backend Engineer",
    company: str = "Agoda",
    location: str = "Bangkok",
    description: str = "Java backend services",
) -> NormalizedVacancy:
    return NormalizedVacancy(
        source=f"target_company:greenhouse:{company.lower()}",
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        employment="Full-time",
        description=description,
        url=f"https://job-boards.greenhouse.io/{company.lower()}/jobs/{external_id}",
        published_at="2026-09-05T10:00:00Z",
    )


def _write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return config_file


def _fake_watcher(result: GreenhouseWatchResult, captured: dict[str, object] | None = None):
    captured = {} if captured is None else captured

    class FakeWatcher:
        def __init__(self, **kwargs: object) -> None:
            captured["init_kwargs"] = kwargs

        def watch(self, companies: object) -> GreenhouseWatchResult:
            captured["companies"] = companies
            return result

    return FakeWatcher


def _invoke(config_file: Path, *extra: str):
    return CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(config_file), *extra],
    )


def test_default_output_prints_summary_not_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=[_vacancy()], errors=[]), captured),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [company.name for company in companies] == ["Agoda", "Qonto"]
    assert "Companies in config: 2" in result.output
    assert "Greenhouse companies: 1" in result.output
    assert "Raw vacancies fetched: 0" in result.output
    assert "Vacancies after filtering: 1" in result.output
    assert "Vacancies found: 1" in result.output
    assert "Errors: 0" in result.output
    assert "Vacancies by company:" in result.output
    assert "Agoda: 1" in result.output
    assert "Errors by company:" in result.output
    assert "title: Java Backend Engineer" not in result.output
    assert "external_id: 101" not in result.output
    assert "url: https://job-boards.greenhouse.io/agoda/jobs/101" not in result.output


def test_show_vacancies_prints_vacancy_details(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=[_vacancy()], errors=[]), {}),
    )

    result = _invoke(config_file, "--show-vacancies")

    assert result.exit_code == 0
    assert "title: Java Backend Engineer" in result.output
    assert "company: Agoda" in result.output
    assert "location: Bangkok" in result.output
    assert "url: https://job-boards.greenhouse.io/agoda/jobs/101" in result.output
    assert "source: target_company:greenhouse:agoda" in result.output
    assert "external_id: 101" in result.output


def test_limit_restricts_console_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Java Backend Engineer"),
        _vacancy(external_id="202", title="Platform Engineer"),
        _vacancy(external_id="303", title="Payments Engineer"),
    ]
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=vacancies, errors=[]), {}),
    )

    result = _invoke(config_file, "--show-vacancies", "--limit", "1")

    assert result.exit_code == 0
    assert "title: Java Backend Engineer" in result.output
    assert "title: Platform Engineer" not in result.output
    assert "title: Payments Engineer" not in result.output
    assert "Showing 1 of 3 vacancies. Use --limit or --output to inspect more." in result.output


def test_output_writes_utf8_jsonl_with_all_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    output_file = tmp_path / "vacancies.jsonl"
    vacancies = [
        _vacancy(external_id="101", title="Java Backend Engineer"),
        _vacancy(
            external_id="202",
            title="Platform Engineer \u6c49",
            location="M\u00fcnchen",
            description="Distributed systems \u2014 Java",
        ),
    ]
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=vacancies, errors=[]), {}),
    )

    result = _invoke(config_file, "--output", str(output_file))

    assert result.exit_code == 0
    assert "title: Java Backend Engineer" not in result.output
    assert f"Wrote 2 vacancies to {output_file}" in result.output
    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["external_id"] == "101"
    assert first["description"] == "Java backend services"
    assert second["title"] == "Platform Engineer \u6c49"
    assert second["location"] == "M\u00fcnchen"
    assert second["description"] == "Distributed systems \u2014 Java"
    assert set(first) == {
        "company",
        "title",
        "location",
        "url",
        "source",
        "external_id",
        "description",
    }


def test_unicode_in_dynamic_fields_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(
            GreenhouseWatchResult(
                vacancies=[
                    _vacancy(
                        title="Senior Java \u6c49 Backend",
                        location="M\u00fcnchen",
                    )
                ],
                errors=[
                    GreenhouseCompanyError(
                        company_name="Vinted",
                        message="Unexpected symbol \u6c49 in payload",
                    )
                ],
            ),
            {},
        ),
    )

    result = _invoke(config_file, "--show-vacancies")

    assert result.exit_code == 0
    assert "Vacancies found: 1" in result.output
    assert "Errors: 1" in result.output
    assert "Vinted:" in result.output


def test_console_safe_text_replaces_unencodable_characters() -> None:
    safe = cli_module._console_safe_text("Java \u6c49 Backend", encoding="cp1252")
    assert "Java" in safe
    assert "\u6c49" not in safe
    safe.encode("cp1252")


def test_watcher_errors_appear_in_summary(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(
            GreenhouseWatchResult(
                vacancies=[_vacancy()],
                errors=[
                    GreenhouseCompanyError(
                        company_name="Adyen",
                        message="Greenhouse board 'adyen' request failed.",
                    )
                ],
            ),
            {},
        ),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies found: 1" in result.output
    assert "Errors: 1" in result.output
    assert "Errors by company:" in result.output
    assert "Adyen: Greenhouse board 'adyen' request failed." in result.output
    assert "title: Java Backend Engineer" not in result.output


def test_command_prints_summary_for_empty_result(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=[], errors=[]), {}),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies found: 0" in result.output
    assert "Errors: 0" in result.output
    assert "(none)" in result.output
    assert "title:" not in result.output
    assert "external_id:" not in result.output


def test_missing_config_returns_nonzero_exit(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    result = _invoke(missing)

    assert result.exit_code != 0
    assert "not found" in result.output
    assert str(missing) in result.output


def test_invalid_yaml_returns_nonzero_exit(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("companies: not-a-list\n", encoding="utf-8")

    result = _invoke(config_file)

    assert result.exit_code != 0
    assert "invalid" in result.output.lower()
