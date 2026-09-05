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


def _vacancy() -> NormalizedVacancy:
    return NormalizedVacancy(
        source="target_company:greenhouse:agoda",
        external_id="101",
        title="Java Backend Engineer",
        company="Agoda",
        location="Bangkok",
        employment="Full-time",
        description="Java backend services",
        url="https://job-boards.greenhouse.io/agoda/jobs/101",
        published_at="2026-09-05T10:00:00Z",
    )


def _write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return config_file


def _fake_watcher(result: GreenhouseWatchResult, captured: dict[str, object]):
    class FakeWatcher:
        def __init__(self, **kwargs: object) -> None:
            captured["init_kwargs"] = kwargs

        def watch(self, companies: object) -> GreenhouseWatchResult:
            captured["companies"] = companies
            return result

    return FakeWatcher


def test_command_loads_config_and_calls_watcher(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=[_vacancy()], errors=[]), captured),
    )

    result = CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(config_file)],
    )

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [company.name for company in companies] == ["Agoda", "Qonto"]
    assert "Компаний в конфиге: 2" in result.output
    assert "Greenhouse-компаний: 1" in result.output
    assert "Найдено вакансий: 1" in result.output
    assert "Ошибок: 0" in result.output
    assert "company: Agoda" in result.output
    assert "title: Java Backend Engineer" in result.output
    assert "location: Bangkok" in result.output
    assert "url: https://job-boards.greenhouse.io/agoda/jobs/101" in result.output
    assert "source: target_company:greenhouse:agoda" in result.output
    assert "external_id: 101" in result.output


def test_command_prints_summary_for_empty_result(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(GreenhouseWatchResult(vacancies=[], errors=[]), {}),
    )

    result = CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(config_file)],
    )

    assert result.exit_code == 0
    assert "Компаний в конфиге: 2" in result.output
    assert "Greenhouse-компаний: 1" in result.output
    assert "Найдено вакансий: 0" in result.output
    assert "Ошибок: 0" in result.output
    assert "company:" not in result.output
    assert "external_id:" not in result.output


def test_command_prints_watcher_errors_without_failing(tmp_path: Path, monkeypatch) -> None:
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

    result = CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(config_file)],
    )

    assert result.exit_code == 0
    assert "Найдено вакансий: 1" in result.output
    assert "Ошибок: 1" in result.output
    assert "company: Agoda" in result.output
    assert "Ошибка Adyen: Greenhouse board 'adyen' request failed." in result.output


def test_missing_config_returns_nonzero_exit(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    result = CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(missing)],
    )

    assert result.exit_code != 0
    assert "not found" in result.output
    assert str(missing) in result.output


def test_invalid_yaml_returns_nonzero_exit(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("companies: not-a-list\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli_module.app,
        ["collect-target-companies-greenhouse", "--config", str(config_file)],
    )

    assert result.exit_code != 0
    assert "invalid" in result.output.lower()
