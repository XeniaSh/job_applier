from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.watchers.greenhouse import GreenhouseCompanyError, GreenhouseWatchResult
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)

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
  - name: Canonical
    priority: A
    language: english
    relocation_status: remote_global
    watcher_type: greenhouse
    ats: greenhouse
    job_board_url: https://job-boards.greenhouse.io/canonical
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


def _evaluation(
    *,
    decision: Decision = Decision.STRONG_MATCH,
    match_percentage: float | None = 86.0,
    matched_points: list[str] | None = None,
    decision_reason: str = "Java backend match",
    summary: str = "Strong Java backend role",
) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary=summary,
        decision_reason=decision_reason,
        matched_points=matched_points if matched_points is not None else ["java", "backend"],
        gaps=[],
        nuances=[],
        match_percentage=match_percentage,
        matched_score=12.0,
        total_possible_score=14.0,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
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


def _fake_analyzer(evaluation: VacancyEvaluation | list[VacancyEvaluation], captured: dict[str, object]):
    remaining = [evaluation] if isinstance(evaluation, VacancyEvaluation) else list(evaluation)

    class FakeAnalyzer:
        def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
            captured.setdefault("texts", [])
            captured["texts"].append(vacancy_text)
            captured.setdefault("completeness", [])
            captured["completeness"].append(content_completeness)
            if remaining:
                return remaining.pop(0)
            return _evaluation()

    return FakeAnalyzer()


def _patch_runtime(
    monkeypatch,
    *,
    watch_result: GreenhouseWatchResult,
    evaluation: VacancyEvaluation | list[VacancyEvaluation] | None = None,
    captured: dict[str, object] | None = None,
) -> dict[str, object]:
    captured = {} if captured is None else captured
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(watch_result, captured),
    )
    if evaluation is not None:
        analyzer = _fake_analyzer(evaluation, captured)
        monkeypatch.setattr(cli_module, "Settings", lambda: object())
        monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: analyzer)
    return captured


def _invoke(config_file: Path, *extra: str):
    return CliRunner().invoke(
        cli_module.app,
        ["analyze-target-companies-greenhouse", "--config", str(config_file), *extra],
    )


def test_command_runs_watcher_and_analyzer(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancy = _vacancy()
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[vacancy], errors=[], raw_fetched=5),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [item.name for item in companies] == ["Agoda", "Qonto", "Canonical"]
    assert captured["texts"]
    assert captured["texts"][0].startswith("Title: Java Backend Engineer")
    assert captured["completeness"] == ["FULL"]
    assert "[STRONG_MATCH] 86 - Agoda - Java Backend Engineer" in result.output
    assert "Location: Bangkok" in result.output
    assert "Reasons: java, backend" in result.output
    assert "URL: https://job-boards.greenhouse.io/agoda/jobs/101" in result.output


def test_limit_restricts_analyzed_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Java Backend Engineer"),
        _vacancy(external_id="202", title="Platform Engineer"),
        _vacancy(external_id="303", title="Payments Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--limit", "2")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 2
    assert "Analyzing 2 vacancies (limit: 2)" in result.output
    assert "Vacancies analyzed: 2" in result.output
    assert "Payments Engineer" not in result.output


def test_company_filter_selects_named_companies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[_vacancy()], errors=[], raw_fetched=1),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--company", "agoda")

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [item.name for item in companies] == ["Agoda"]
    assert "Selected companies: 1" in result.output
    assert "Greenhouse companies: 1" in result.output


def test_summary_includes_analyzed_and_decision_counts(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", company="Agoda", title="Java Backend Engineer"),
        _vacancy(external_id="202", company="Canonical", title="Platform Engineer"),
    ]
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=10),
        evaluation=[
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=86.0),
            _evaluation(
                decision=Decision.POTENTIAL_MATCH,
                match_percentage=54.0,
                matched_points=["backend"],
            ),
        ],
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Companies in config: 3" in result.output
    assert "Greenhouse companies: 2" in result.output
    assert "Raw vacancies fetched: 10" in result.output
    assert "Vacancies after prefilter: 2" in result.output
    assert "Analyzing 2 vacancies (limit: 30)" in result.output
    assert "Vacancies analyzed: 2" in result.output
    assert "Watcher errors: 0" in result.output
    assert "STRONG_MATCH: 1" in result.output
    assert "POTENTIAL_MATCH: 1" in result.output
    assert "IGNORE: 0" in result.output
    assert "Agoda: 1" in result.output
    assert "Canonical: 1" in result.output


def test_output_writes_utf8_json_results(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    output_file = tmp_path / "analysis.json"
    vacancy = _vacancy(title="Senior Java \u6c49 Backend")
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[vacancy],
            errors=[
                GreenhouseCompanyError(
                    company_name="Adyen",
                    message="Greenhouse board 'adyen' request failed (500).",
                    slug="adyen",
                    status_code=500,
                    attempts=3,
                )
            ],
            raw_fetched=1,
        ),
        evaluation=_evaluation(matched_points=["java", "backend"]),
    )

    result = _invoke(config_file, "--output", str(output_file))

    assert result.exit_code == 0
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["vacancies"][0]["company"] == "Agoda"
    assert payload["vacancies"][0]["title"] == "Senior Java \u6c49 Backend"
    assert payload["vacancies"][0]["location"] == "Bangkok"
    assert payload["vacancies"][0]["url"] == vacancy.url
    assert payload["vacancies"][0]["source"] == vacancy.source
    assert payload["vacancies"][0]["external_id"] == "101"
    assert payload["vacancies"][0]["decision"] == "STRONG_MATCH"
    assert payload["vacancies"][0]["match_percentage"] == 86.0
    assert payload["vacancies"][0]["decision_reason"] == "Java backend match"
    assert payload["errors"][0]["company_name"] == "Adyen"
    assert payload["errors"][0]["status_code"] == 500
    assert payload["errors"][0]["attempts"] == 3
    raw = output_file.read_bytes()
    assert "\\u6c49" not in raw.decode("utf-8")
    assert "\u6c49" in raw.decode("utf-8")


def test_watcher_errors_are_shown_without_failing(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[_vacancy()],
            errors=[
                GreenhouseCompanyError(
                    company_name="Adyen",
                    message="Greenhouse board 'adyen' request failed (404).",
                )
            ],
            raw_fetched=1,
        ),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies analyzed: 1" in result.output
    assert "Watcher errors: 1" in result.output
    assert "Adyen: Greenhouse board 'adyen' request failed (404)." in result.output


def test_missing_config_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    result = _invoke(missing)
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "missing" in result.output.lower()


def test_unknown_company_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[], errors=[]),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--company", "NoSuchCorp")

    assert result.exit_code != 0
    assert "Unknown company: NoSuchCorp" in result.output


def test_unicode_in_title_and_reasons_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[
                _vacancy(
                    title="Senior Java \u6c49 Backend",
                    location="M\u00fcnchen",
                )
            ],
            errors=[],
            raw_fetched=1,
        ),
        evaluation=_evaluation(
            matched_points=["java", "backend", "M\u00fcnchen"],
            decision_reason="Relocation to M\u00fcnchen",
        ),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies analyzed: 1" in result.output
    assert "Senior Java" in result.output
    assert "Reasons:" in result.output
