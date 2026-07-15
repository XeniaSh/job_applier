from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.hh_collector import HHCollectReport, ProcessedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def _evaluation(decision: Decision) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        matched_points=["java"],
        gaps=["redis"],
        nuances=["remote"],
        match_percentage=88.9,
        matched_score=8.0,
        total_possible_score=9.0,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("HH_USER_AGENT", "job-vacancy-analyzer/0.1 contact@example.com")


def test_collect_hh_prints_only_strong_and_potential_by_default(monkeypatch) -> None:
    _set_env(monkeypatch)
    report = HHCollectReport(
        new_found=3,
        analyzed=3,
        strong_matches=1,
        potential_matches=1,
        ignored=1,
        errors=0,
        successful_searches=1,
        processed=[
            ProcessedVacancy("1", "Java Backend", "Acme", "https://hh.ru/1", _evaluation(Decision.STRONG_MATCH)),
            ProcessedVacancy("2", "Kotlin Backend", "Acme", "https://hh.ru/2", _evaluation(Decision.POTENTIAL_MATCH)),
            ProcessedVacancy("3", "Other role", "Acme", "https://hh.ru/3", _evaluation(Decision.IGNORE)),
        ],
    )

    class FakeCollector:
        def __init__(self, hh_client, analyzer, seen_jobs) -> None:
            _ = (hh_client, analyzer, seen_jobs)

        def collect_and_analyze(self, *, queries, limit):
            _ = (queries, limit)
            return report

    monkeypatch.setattr(cli_module, "HHCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-hh"])

    assert result.exit_code == 0
    assert "Решение: STRONG_MATCH" in result.output
    assert "Решение: POTENTIAL_MATCH" in result.output
    assert "Решение: IGNORE" not in result.output


def test_collect_hh_include_ignore_prints_ignore(monkeypatch) -> None:
    _set_env(monkeypatch)
    report = HHCollectReport(
        new_found=1,
        analyzed=1,
        strong_matches=0,
        potential_matches=0,
        ignored=1,
        errors=0,
        successful_searches=1,
        processed=[ProcessedVacancy("3", "Other role", "Acme", "https://hh.ru/3", _evaluation(Decision.IGNORE))],
    )

    class FakeCollector:
        def __init__(self, hh_client, analyzer, seen_jobs) -> None:
            _ = (hh_client, analyzer, seen_jobs)

        def collect_and_analyze(self, *, queries, limit):
            _ = (queries, limit)
            return report

    monkeypatch.setattr(cli_module, "HHCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-hh", "--include-ignore"])

    assert result.exit_code == 0
    assert "Решение: IGNORE" in result.output


def test_collect_hh_default_queries_used(monkeypatch) -> None:
    _set_env(monkeypatch)
    captured = {}

    class FakeCollector:
        def __init__(self, hh_client, analyzer, seen_jobs) -> None:
            _ = (hh_client, analyzer, seen_jobs)

        def collect_and_analyze(self, *, queries, limit):
            captured["queries"] = list(queries)
            captured["limit"] = limit
            return HHCollectReport(successful_searches=1)

    monkeypatch.setattr(cli_module, "HHCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-hh", "--limit", "7"])

    assert result.exit_code == 0
    assert captured["queries"] == list(cli_module.DEFAULT_HH_QUERIES)
    assert captured["limit"] == 7


def test_collect_hh_custom_queries_override_defaults(monkeypatch) -> None:
    _set_env(monkeypatch)
    captured = {}

    class FakeCollector:
        def __init__(self, hh_client, analyzer, seen_jobs) -> None:
            _ = (hh_client, analyzer, seen_jobs)

        def collect_and_analyze(self, *, queries, limit):
            captured["queries"] = list(queries)
            captured["limit"] = limit
            return HHCollectReport(successful_searches=1)

    monkeypatch.setattr(cli_module, "HHCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["collect-hh", "--query", "Java Backend", "--query", "JVM Developer"],
    )

    assert result.exit_code == 0
    assert captured["queries"] == ["Java Backend", "JVM Developer"]
