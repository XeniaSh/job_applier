from pathlib import Path
import re

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.email_imap_client import EmailConnectionError
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation


def _set_env(monkeypatch, tmp_path: Path, *, interval: int = 300, poll_interval: int = 1) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("PIPELINE_INTERVAL_SECONDS", str(interval))
    monkeypatch.setenv("TELEGRAM_POLL_INTERVAL_SECONDS", str(poll_interval))


def _evaluation(decision: Decision = Decision.POTENTIAL_MATCH) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
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


def _vacancy(*, source: str, external_id: str, title: str) -> cli_module.NormalizedVacancy:
    return cli_module.NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company="ACME",
        location="Remote",
        employment=None,
        description=title,
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        published_at=None,
    )


def _bootstrap_common(monkeypatch, *, decision: Decision = Decision.POTENTIAL_MATCH) -> None:
    analyzer = type("A", (), {"analyze": lambda self, *args, **kwargs: _evaluation(decision)})()
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: analyzer)
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0),
    )


def _run_single_cycle(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "_poll_telegram_actions_once",
        lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )


def test_run_all_seen_cycle_logs_and_reason(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "should_accept_title", lambda title: True)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id=str(i), title=f"Role {i}") for i in range(1, 4)]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: True, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "linkedin-email: extracted=3 unique=3 new=0 already_seen=3 invalid_identity=0 prefiltered=0 errors=0" in result.output
    assert "Analysis: analyzed=0 strong=0 potential=0 ignore=0 title_filtered=0 errors=0" in result.output
    assert "No vacancies analyzed: all unique vacancies were already seen." in result.output


def test_run_title_filter_only_reason(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "should_accept_title", lambda title: False)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id=str(i), title=f"Role {i}") for i in range(1, 3)]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Analysis: analyzed=0 strong=0 potential=0 ignore=0 title_filtered=2 errors=0" in result.output
    assert "No vacancies analyzed: all candidates were removed by title filter." in result.output


def test_run_collector_error_safe_and_other_source_continues(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GREENHOUSE_BOARDS", "stripe")
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {"SOURCE": "linkedin-email", "collect": lambda self: (_ for _ in ()).throw(EmailConnectionError("timeout mail-password"))},
        )(),
    )
    monkeypatch.setattr(
        cli_module,
        "GreenhouseCollector",
        lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: [_vacancy(source="greenhouse", external_id="10", title="Green Role")]})(),
    )
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: True, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "linkedin-email: failed — IMAP connection timeout to imap.gmail.com." in result.output
    assert "greenhouse: extracted=1 unique=1 new=0 already_seen=1 invalid_identity=0 prefiltered=0 errors=0" in result.output
    assert "mail-password" not in result.output


def test_run_telegram_poll_409_clear_message(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "_poll_telegram_actions_once",
        lambda **kwargs: (_ for _ in ()).throw(cli_module.TelegramRequestError("Telegram getUpdates HTTP 409.")),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())
    monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Telegram poll failed:" in result.output
    assert "HTTP 409 conflict — another getUpdates poller may be running." in result.output


def test_run_verbose_mode_prints_per_vacancy_outcomes(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch, decision=Decision.POTENTIAL_MATCH)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: [
                    _vacancy(source="linkedin-email", external_id="1", title="Seen Role"),
                    _vacancy(source="linkedin-email", external_id="2", title="Filtered Role"),
                    _vacancy(source="linkedin-email", external_id="3", title="Potential Role"),
                ],
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "should_accept_title", lambda title: title != "Filtered Role")

    class Seen:
        def is_seen(self, source, external_id):
            return external_id == "1"

        def mark_seen(self, source, external_id):
            _ = source, external_id

    class Deliveries:
        def was_sent(self, source, external_id, chat_id):
            _ = source, chat_id
            return external_id == "3"

        def save_sent(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def get_state(self, key):
            _ = key
            return None

        def set_state(self, key, value):
            _ = key, value

    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: Seen())
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: Deliveries())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert "ALREADY_SEEN linkedin-email:1 Seen Role" in result.output
    assert "PREFILTERED linkedin-email:2 Filtered Role" in result.output
    assert "POTENTIAL linkedin-email:3 Potential Role" in result.output
    assert "ALREADY_DELIVERED linkedin-email:3 Potential Role" in result.output


def test_run_default_mode_hides_titles(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="1", title="Hidden Role")]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: True, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Hidden Role" not in result.output


def test_run_timing_fields_non_negative(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    match = re.search(r"Timing: collect=(\d+)ms analyze=(\d+)ms telegram=(\d+)ms cycle=(\d+)ms", result.output)
    assert match is not None
    assert all(int(item) >= 0 for item in match.groups())


def test_run_counters_use_current_cycle_items_only(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="1", title="Role")]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())

    class Seen:
        historical_seen_total = 999

        def is_seen(self, source, external_id):
            _ = source, external_id
            return False

        def mark_seen(self, source, external_id):
            _ = source, external_id

    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: Seen())
    monkeypatch.setattr(cli_module, "should_accept_title", lambda title: True)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: type("R", (), {"message_id": 1})()})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "new=1 already_seen=0 invalid_identity=0 prefiltered=0 errors=0" in result.output


def test_run_repeated_cycles_produce_stable_logs(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path, interval=1)
    _bootstrap_common(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="1", title="Role")]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: True, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())
    monotonic_values = {"i": 0.0}

    def fake_monotonic():
        monotonic_values["i"] += 1.0
        return monotonic_values["i"]

    monkeypatch.setattr(cli_module.time, "monotonic", fake_monotonic)

    calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        calls["count"] += 1
        if calls["count"] >= 4:
            raise KeyboardInterrupt()
        return None, 0

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)
    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert result.output.count("linkedin-email: extracted=1 unique=1 new=0 already_seen=1 invalid_identity=0 prefiltered=0 errors=0") >= 2


def test_run_logs_do_not_leak_secrets(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "very-secret-token")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "very-secret-password")
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: (_ for _ in ()).throw(EmailConnectionError("timeout very-secret-password")),
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "very-secret-token" not in result.output
    assert "very-secret-password" not in result.output


def test_run_reports_invalid_identity_bucket_for_unaccounted_uniques(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)

    def bad_vacancy(idx: int) -> cli_module.NormalizedVacancy:
        return cli_module.NormalizedVacancy(
            source="linkedin-email",
            external_id="",
            title="",
            company=None,
            location=None,
            employment=None,
            description="",
            url="not-a-url",
            published_at=None,
        )

    vacancies = [_vacancy(source="linkedin-email", external_id=str(i), title=f"Seen {i}") for i in range(1, 21)]
    vacancies.extend(bad_vacancy(i) for i in range(21, 35))

    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: vacancies})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: external_id.isdigit(), "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "linkedin-email: extracted=34 unique=34 new=0 already_seen=20 invalid_identity=14 prefiltered=0 errors=0" in result.output
    assert "No vacancies analyzed: 14 vacancies had no usable identity." in result.output
    assert "No vacancies analyzed: all unique vacancies were already seen." not in result.output


def test_run_polling_failure_does_not_change_vacancy_counters(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="1", title="Seen")]})(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: True, "mark_seen": lambda self, source, external_id: None})(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    calls = {"count": 0}

    def fake_poll(**kwargs):
        _ = kwargs
        calls["count"] += 1
        if calls["count"] == 1:
            raise cli_module.TelegramRequestError("temporary request failed")
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", fake_poll)
    monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Telegram poll failed:" in result.output
    assert "linkedin-email: extracted=1 unique=1 new=0 already_seen=1 invalid_identity=0 prefiltered=0 errors=0" in result.output
    assert "Telegram: eligible=0 already_delivered=0 sent=0 errors=0" in result.output


def test_run_poll_failure_logs_method_status_and_description(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "_poll_telegram_actions_once",
        lambda **kwargs: (_ for _ in ()).throw(
            cli_module.TelegramRequestError(
                "Telegram API request failed.",
                method="editMessageText",
                http_status=400,
                error_code=400,
                description="Bad Request: message to edit not found",
            )
        ),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())
    monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Telegram poll failed:" in result.output
    assert "method=editMessageText" in result.output
    assert "HTTP 400" in result.output
    assert 'description="Bad Request: message to edit not found"' in result.output
