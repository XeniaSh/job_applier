from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.linkedin_email_collector import LinkedInEmailCollectReport, LinkedInProcessedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")


def _evaluation(decision: Decision) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        matched_points=["java"],
        gaps=[],
        nuances=[],
        match_percentage=72.0,
        matched_score=0.0,
        total_possible_score=0.0,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def test_collect_greenhouse_requires_boards(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.delenv("GREENHOUSE_BOARDS", raising=False)
    result = CliRunner().invoke(cli_module.app, ["collect-greenhouse"])
    assert result.exit_code != 0
    assert "GREENHOUSE_BOARDS не задан" in result.output


def test_collect_greenhouse_uses_board_option(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("GREENHOUSE_BOARDS", "stripe")
    used = {"boards": None}

    class FakeCollector:
        def __init__(self, *, boards, timeout_seconds=20.0, user_agent="x"):  # noqa: ARG002
            used["boards"] = boards

        def collect(self):
            return []

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: object())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", FakeCollector)
    monkeypatch.setattr(
        cli_module,
        "_analyze_collected_vacancies",
        lambda **kwargs: LinkedInEmailCollectReport(),
    )

    result = CliRunner().invoke(cli_module.app, ["collect-greenhouse", "--board", "notion"])
    assert result.exit_code == 0
    assert used["boards"] == ["notion"]


def test_collect_greenhouse_include_ignore(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("GREENHOUSE_BOARDS", "stripe")
    report = LinkedInEmailCollectReport(
        analyzed=1,
        ignored=1,
        processed=[
            LinkedInProcessedVacancy(
                external_id="900",
                title="Some role",
                company="Stripe",
                location="Remote",
                url="https://job-boards.greenhouse.io/stripe/jobs/900",
                content_completeness="FULL",
                evaluation=_evaluation(Decision.IGNORE),
                source="greenhouse",
            )
        ],
    )

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: object())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("C", (), {"collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "_analyze_collected_vacancies", lambda **kwargs: report)

    result = CliRunner().invoke(cli_module.app, ["collect-greenhouse", "--include-ignore"])
    assert result.exit_code == 0
    assert "Решение: IGNORE" in result.output
