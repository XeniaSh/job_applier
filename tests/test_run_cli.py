from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.linkedin_email_collector import LinkedInEmailCollectReport, LinkedInProcessedVacancy
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation


def _set_env(monkeypatch, tmp_path: Path, *, interval: int = 300, poll_interval: int = 1) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("PIPELINE_INTERVAL_SECONDS", str(interval))
    monkeypatch.setenv("TELEGRAM_POLL_INTERVAL_SECONDS", str(poll_interval))


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="summary",
        matched_points=["java"],
        gaps=[],
        nuances=[],
        match_percentage=80.0,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=True,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _monotonic_stub(values: list[float]):
    state = {"index": 0}

    def _next():
        idx = state["index"]
        state["index"] += 1
        if idx >= len(values):
            return values[-1]
        return values[idx]

    return _next


def test_run_scheduler_interval_and_graceful_shutdown(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path, interval=10, poll_interval=1)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())

    class FakeCollector:
        def __init__(self, **kwargs):
            _ = kwargs
            self.calls = 0

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            self.calls += 1
            return LinkedInEmailCollectReport()

    collector = FakeCollector()
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: collector)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
            lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0),
    )

    monkeypatch.setattr(cli_module.time, "monotonic", _monotonic_stub([0.0, 1.0, 12.0, 13.0]))

    poll_calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        poll_calls["count"] += 1
        if poll_calls["count"] >= 3:
            raise KeyboardInterrupt()
        return None, 0

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert collector.calls == 2
    assert "Job Applier started." in result.output
    assert "Press Ctrl+C to stop." in result.output
    assert "Job Applier stopped." in result.output
    assert (tmp_path / "data" / "job_applier.lock").exists() is False


def test_run_collector_and_telegram_failures_recover(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path, interval=5, poll_interval=1)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0),
    )

    class FakeCollector:
        def __init__(self, **kwargs):
            _ = kwargs
            self.calls = 0

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("collector failed")
            report = LinkedInEmailCollectReport(new_vacancies=1)
            report.processed = []
            return report

    collector = FakeCollector()
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: collector)

    send_calls = {"count": 0}

    def fake_send(**kwargs):
        send_calls["count"] += 1
        if send_calls["count"] == 1:
            raise RuntimeError("telegram send failed")
        return 1

    monkeypatch.setattr(cli_module, "_send_processed_to_telegram", fake_send)

    monkeypatch.setattr(cli_module.time, "monotonic", _monotonic_stub([0.0, 6.0, 12.0, 18.0, 24.0]))

    poll_calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        poll_calls["count"] += 1
        if poll_calls["count"] >= 4:
            raise KeyboardInterrupt()
        return None, 0

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert collector.calls >= 2
    assert send_calls["count"] >= 2
    assert "Pipeline cycle failed:" in result.output
    assert "Job Applier stopped." in result.output


def test_run_prepare_request_triggers_application_generation(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path, interval=60, poll_interval=1)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("C", (), {"collect_and_analyze": lambda self, **k: LinkedInEmailCollectReport()})())
    monkeypatch.setattr(cli_module, "_send_processed_to_telegram", lambda **kwargs: 0)

    prepare_calls = {"count": 0}

    def fake_prepare(**kwargs):
        _ = kwargs
        prepare_calls["count"] += 1
        return cli_module.PreparationRunResult(1, 1, 1, 1, 0, 0, 1, 0, 0)

    monkeypatch.setattr(cli_module, "_prepare_requested_applications", fake_prepare)
    monkeypatch.setattr(cli_module.time, "monotonic", lambda: 0.0)

    poll_calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        poll_calls["count"] += 1
        if poll_calls["count"] == 1:
            return None, 1
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert prepare_calls["count"] == 1
    assert "Prepare request received" in result.output
    assert "Application generated" in result.output
    assert "Resume sent" in result.output


def test_run_lock_file_prevents_second_instance(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    lock_path = tmp_path / "data" / "job_applier.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("12345", encoding="utf-8")

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code != 0
    assert "Job Applier is already running." in result.output


def test_run_no_duplicate_processing_between_cycles(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path, interval=5, poll_interval=1)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0),
    )

    vacancy = LinkedInProcessedVacancy(
        external_id="4439900667",
        title="Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/4439900667/",
        content_completeness="PARTIAL",
        evaluation=_evaluation(),
    )

    class FakeCollector:
        def __init__(self, **kwargs):
            _ = kwargs
            self.calls = 0

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            self.calls += 1
            report = LinkedInEmailCollectReport(new_vacancies=1)
            report.processed = [vacancy]
            return report

    collector = FakeCollector()
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: collector)

    @dataclass
    class _Ref:
        chat_id: str
        message_id: int

    class FakeTelegramClient:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs
            self.calls = 0

        def send_vacancy_card(self, card):
            _ = card
            self.calls += 1
            return _Ref(chat_id="123", message_id=100 + self.calls)

    telegram_client = FakeTelegramClient()
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: telegram_client)

    monkeypatch.setattr(cli_module.time, "monotonic", _monotonic_stub([0.0, 6.0, 12.0, 18.0]))

    poll_calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        poll_calls["count"] += 1
        if poll_calls["count"] >= 3:
            raise KeyboardInterrupt()
        return None, 0

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert collector.calls >= 2
    assert telegram_client.calls == 1
