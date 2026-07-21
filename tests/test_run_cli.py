from pathlib import Path
import inspect
import re
import threading

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


def _evaluation(
    decision: Decision = Decision.POTENTIAL_MATCH,
    *,
    warning_signals: list[dict[str, str]] | None = None,
) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        decision_reason="Role is partially aligned with Java backend profile.",
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
        warning_signals=warning_signals or [],
    )


def _vacancy(
    *,
    source: str,
    external_id: str,
    title: str,
    company: str = "ACME",
    location: str = "Remote",
    description: str | None = None,
    snippet: str | None = None,
    alert_query: str | None = None,
    snippet_source: str | None = None,
    raw_text_preview: str | None = None,
) -> cli_module.NormalizedVacancy:
    return cli_module.NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        employment=None,
        description=description or title,
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        published_at=None,
        snippet=snippet,
        alert_query=alert_query,
        snippet_source=snippet_source,
        raw_text_preview=raw_text_preview,
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
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
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
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Title has Java/backend role signal",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
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
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": False,
                "reason": "Frontend title",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": ["frontend"],
                "decision": "REJECT",
            },
        )(),
    )
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
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": title != "Filtered Role",
                "reason": "Frontend title",
                "normalized_title": title.lower(),
                "positive_rules": ["java"] if title != "Filtered Role" else [],
                "negative_rules": ["frontend"] if title == "Filtered Role" else [],
                "decision": "PASS" if title != "Filtered Role" else "REJECT",
            },
        )(),
    )

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
    assert 'PREFILTER_CHECK title="Filtered Role" normalized=' in result.output
    assert 'PREFILTERED linkedin-email:2 title="Filtered Role" reason="Frontend title"' in result.output
    assert 'POTENTIAL linkedin-email:3 title="Potential Role" score=' in result.output
    assert 'reason="Role is partially aligned with Java backend profile."' in result.output
    assert "ALREADY_DELIVERED linkedin-email:3 Potential Role" in result.output


def test_run_non_verbose_does_not_print_parsed_blocks(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch, decision=Decision.POTENTIAL_MATCH)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="101", title="Senior Java Developer")]},
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Explicit Java/JVM signal in title",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: type("D", (), {"was_sent": lambda self, source, external_id, chat_id: True, "save_sent": lambda self, **kwargs: None, "mark_history_status": lambda self, **kwargs: None, "upsert_application_history": lambda self, **kwargs: None, "get_state": lambda self, key: None, "set_state": lambda self, key, value: None, "pop_state": lambda self, key: None, "list_by_status": lambda self, **kwargs: [], "claim_for_preparation": lambda self, **kwargs: False, "save_preparation": lambda self, prep: None, "get_preparation": lambda self, source, external_id: None, "clear_preparation_aux_message_ids": lambda self, source, external_id: None, "set_preparation_aux_message_id": lambda self, source, external_id, message_kind, message_id: None, "get_delivery": lambda self, source, external_id, chat_id: None, "recover_abandoned_preparing": lambda self, **kwargs: [], "count_by_status": lambda self, **kwargs: 0})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "PARSED linkedin-email:101" not in result.output
    assert "DECISION_INPUT_FIELDS" not in result.output
    assert "WARNING_SOURCE_TEXT" not in result.output


def test_run_verbose_logs_parsed_vacancy_inputs_and_warning_source(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _run_single_cycle(monkeypatch)

    long_raw_preview = "X" * 550

    class Analyzer:
        def analyze(self, *args, **kwargs):
            _ = args, kwargs
            return _evaluation(
                Decision.POTENTIAL_MATCH,
                warning_signals=[
                    {
                        "code": "lead_level",
                        "source": "description",
                        "evidence": "lead architecture decisions and mentor backend engineers",
                    }
                ],
            )

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: Analyzer())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: [
                    _vacancy(
                        source="linkedin-email",
                        external_id="88",
                        title="Senior Java Developer",
                        company="GCash",
                        location="Metro Manila",
                        description="Senior Java Developer responsible for backend services.",
                        snippet="Senior Java Developer responsible for backend services.",
                        alert_query="Java Kafka",
                        snippet_source="description",
                        raw_text_preview=long_raw_preview,
                    )
                ],
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Explicit Java/JVM signal in title",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(
        cli_module,
        "TelegramDeliveryStorage",
        lambda: type(
            "D",
            (),
            {
                "was_sent": lambda self, source, external_id, chat_id: True,
                "save_sent": lambda self, **kwargs: None,
                "mark_history_status": lambda self, **kwargs: None,
                "upsert_application_history": lambda self, **kwargs: None,
                "get_state": lambda self, key: None,
                "set_state": lambda self, key, value: None,
                "pop_state": lambda self, key: None,
                "list_by_status": lambda self, **kwargs: [],
                "claim_for_preparation": lambda self, **kwargs: False,
                "save_preparation": lambda self, prep: None,
                "get_preparation": lambda self, source, external_id: None,
                "clear_preparation_aux_message_ids": lambda self, source, external_id: None,
                "set_preparation_aux_message_id": lambda self, source, external_id, message_kind, message_id: None,
                "get_delivery": lambda self, source, external_id, chat_id: None,
                "recover_abandoned_preparing": lambda self, **kwargs: [],
                "count_by_status": lambda self, **kwargs: 0,
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert "PARSED linkedin-email:88" in result.output
    assert "analysis_text:" in result.output
    assert "VISIBLE_TEXT_PREVIEW:" in result.output
    assert "(first 500 characters)" in result.output
    assert "DECISION_INPUT_FIELDS title=yes company=yes location=yes snippet=yes alert_query=yes url=yes" in result.output
    assert "WARNING linkedin-email:88 code=lead_level source=\"description\"" in result.output
    assert "WARNING_SOURCE_TEXT" in result.output


def test_run_verbose_logs_empty_fields_as_empty(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _run_single_cycle(monkeypatch)

    class Analyzer:
        def analyze(self, *args, **kwargs):
            _ = args, kwargs
            return _evaluation(Decision.POTENTIAL_MATCH)

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: Analyzer())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: [
                    _vacancy(
                        source="linkedin-email",
                        external_id="89",
                        title="Software Engineer",
                        company="",
                        location="",
                        description="Software Engineer",
                        snippet=None,
                        alert_query=None,
                        snippet_source="missing",
                        raw_text_preview=None,
                    )
                ],
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "No incompatible title signal",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(
        cli_module,
        "TelegramDeliveryStorage",
        lambda: type(
            "D",
            (),
            {
                "was_sent": lambda self, source, external_id, chat_id: True,
                "save_sent": lambda self, **kwargs: None,
                "mark_history_status": lambda self, **kwargs: None,
                "upsert_application_history": lambda self, **kwargs: None,
                "get_state": lambda self, key: None,
                "set_state": lambda self, key, value: None,
                "pop_state": lambda self, key: None,
                "list_by_status": lambda self, **kwargs: [],
                "claim_for_preparation": lambda self, **kwargs: False,
                "save_preparation": lambda self, prep: None,
                "get_preparation": lambda self, source, external_id: None,
                "clear_preparation_aux_message_ids": lambda self, source, external_id: None,
                "set_preparation_aux_message_id": lambda self, source, external_id, message_kind, message_id: None,
                "get_delivery": lambda self, source, external_id, chat_id: None,
                "recover_abandoned_preparing": lambda self, **kwargs: [],
                "count_by_status": lambda self, **kwargs: 0,
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert "PARSED linkedin-email:89" in result.output
    assert "company:" in result.output and "<empty>" in result.output
    assert "location:" in result.output
    assert "snippet:" in result.output
    assert "alert_query:" in result.output
    assert "VISIBLE_TEXT_PREVIEW:" in result.output


def test_verbose_logging_does_not_trigger_extra_analysis_calls(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _run_single_cycle(monkeypatch)
    calls = {"count": 0}

    class Analyzer:
        def analyze(self, *args, **kwargs):
            _ = args, kwargs
            calls["count"] += 1
            return _evaluation(Decision.IGNORE)

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: Analyzer())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {"SOURCE": "linkedin-email", "collect": lambda self: [_vacancy(source="linkedin-email", external_id="11", title="Software Engineer - Backend (Remote)")]},
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Title has Java/backend role signal",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert calls["count"] == 1


def test_verbose_mode_logs_warning_details(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _run_single_cycle(monkeypatch)

    class Analyzer:
        def analyze(self, *args, **kwargs):
            _ = args, kwargs
            return _evaluation(
                Decision.POTENTIAL_MATCH,
                warning_signals=[
                    {
                        "code": "lead_level",
                        "source": "description",
                        "evidence": "lead architecture decisions and mentor engineers",
                    }
                ],
            )

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: Analyzer())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: [_vacancy(source="linkedin-email", external_id="77", title="Senior Java Developer")],
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Explicit Java/JVM signal in title",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(
        cli_module,
        "TelegramDeliveryStorage",
        lambda: type(
            "D",
            (),
            {
                "was_sent": lambda self, source, external_id, chat_id: True,
                "save_sent": lambda self, **kwargs: None,
                "mark_history_status": lambda self, **kwargs: None,
                "upsert_application_history": lambda self, **kwargs: None,
                "get_state": lambda self, key: None,
                "set_state": lambda self, key, value: None,
                "pop_state": lambda self, key: None,
                "list_by_status": lambda self, **kwargs: [],
                "claim_for_preparation": lambda self, **kwargs: False,
                "save_preparation": lambda self, prep: None,
                "get_preparation": lambda self, source, external_id: None,
                "clear_preparation_aux_message_ids": lambda self, source, external_id: None,
                "set_preparation_aux_message_id": lambda self, source, external_id, message_kind, message_id: None,
                "get_delivery": lambda self, source, external_id, chat_id: None,
                "recover_abandoned_preparing": lambda self, **kwargs: [],
                "count_by_status": lambda self, **kwargs: 0,
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert "WARNING linkedin-email:77 code=lead_level source=\"description\" evidence=\"lead architecture decisions and mentor engineers\"" in result.output


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
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "Title has Java/backend role signal",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
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


def test_log_timestamp_includes_seconds_and_milliseconds() -> None:
    stamp = cli_module._format_log_time()
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", stamp) is not None


def test_run_log_uses_shared_timestamp_formatter(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "_format_log_time", lambda: "07:12:08.142")
    cli_module._run_log("Prepare start linkedin-email:1")
    captured = capsys.readouterr()
    assert captured.out.strip() == "[07:12:08.142][main] Prepare start linkedin-email:1"


def test_run_log_allows_explicit_component_tag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "_format_log_time", lambda: "07:12:08.142")
    cli_module._run_log("Callback received prepare:linkedin-email:1", component="poller")
    captured = capsys.readouterr()
    assert captured.out.strip() == "[07:12:08.142][poller] Callback received prepare:linkedin-email:1"


def test_run_uses_prepare_worker_thread_and_no_process_spawn_path(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())
    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))

    observed: dict[str, str] = {}

    def fake_prepare_worker_loop(**kwargs) -> None:
        observed["thread_name"] = threading.current_thread().name
        stop_event = kwargs["stop_event"]
        if hasattr(stop_event, "set"):
            stop_event.set()

    monkeypatch.setattr(cli_module, "_prepare_worker_loop", fake_prepare_worker_loop)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert observed.get("thread_name") == "prepare-worker"

    run_source = inspect.getsource(cli_module.run_pipeline)
    worker_source = inspect.getsource(cli_module._prepare_worker_loop)
    forbidden_markers = (
        "subprocess",
        "multiprocessing",
        "ProcessPoolExecutor",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "os.spawn",
        "os.exec",
    )
    for marker in forbidden_markers:
        assert marker not in run_source
        assert marker not in worker_source


def test_run_restarts_cleanly_after_keyboard_interrupt(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    first = CliRunner().invoke(cli_module.app, ["run"])
    second = CliRunner().invoke(cli_module.app, ["run"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "already running" not in first.output.lower()
    assert "already running" not in second.output.lower()


def test_run_logs_recovered_abandoned_preparing_on_startup(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    class RecoveringStorage:
        def __init__(self):
            self.state = {}

        def recover_abandoned_preparing(self, *, worker_alive, timeout_seconds):
            _ = worker_alive, timeout_seconds
            return [("linkedin-email", "4441994095")]

        def get_state(self, key):
            return self.state.get(key)

        def set_state(self, key, value):
            self.state[key] = value

        def was_sent(self, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return False

        def save_sent(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return []

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", RecoveringStorage)

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert "Recovered abandoned preparation linkedin-email:4441994095" in result.output
    assert "Requeued recovered preparation linkedin-email:4441994095" in result.output


def test_recovered_preparation_reconciles_existing_message_without_duplicate(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    settings = cli_module.Settings()
    edited: list[dict] = []

    class Storage:
        def recover_abandoned_preparing(self, *, worker_alive, timeout_seconds):
            _ = worker_alive, timeout_seconds
            return [("linkedin-email", "100")]

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 77)

        def get_history_title_company_url(self, source, external_id):
            _ = source, external_id
            return ("Role", "ACME", "https://www.linkedin.com/jobs/view/100/")

        def get_preparation(self, source, external_id):
            _ = source, external_id
            return None

        def get_state(self, key):
            _ = key
            return None

        def set_state(self, key, value):
            _ = key, value

    class Client:
        def edit_message_text(self, **kwargs):
            edited.append(kwargs)

    storage = Storage()
    cli_module._recover_and_requeue_abandoned_preparations(
        settings=settings,
        storage=storage,  # type: ignore[arg-type]
        telegram_client=Client(),  # type: ignore[arg-type]
    )
    assert len(edited) == 1
    assert edited[0]["message_id"] == 77
    assert "Preparation was interrupted." in edited[0]["text"]
    assert "Retrying automatically..." in edited[0]["text"]


def test_recovered_preparation_edit_failure_does_not_block_requeue(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    settings = cli_module.Settings()
    captured: list[str] = []

    class Storage:
        def __init__(self):
            self.state = {}

        def recover_abandoned_preparing(self, *, worker_alive, timeout_seconds):
            _ = worker_alive, timeout_seconds
            return [("linkedin-email", "200")]

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 88)

        def get_history_title_company_url(self, source, external_id):
            _ = source, external_id
            return ("Role", "ACME", "https://www.linkedin.com/jobs/view/200/")

        def get_preparation(self, source, external_id):
            _ = source, external_id
            return None

        def get_state(self, key):
            return self.state.get(key)

        def set_state(self, key, value):
            self.state[key] = value

    class FailingClient:
        def edit_message_text(self, **kwargs):
            _ = kwargs
            raise cli_module.TelegramRequestError("fail", method="editMessageText", http_status=400, description="bad")

    monkeypatch.setattr(cli_module, "_run_log", lambda message, component="main": captured.append(f"[{component}] {message}"))
    storage = Storage()
    recovered = cli_module._recover_and_requeue_abandoned_preparations(
        settings=settings,
        storage=storage,  # type: ignore[arg-type]
        telegram_client=FailingClient(),  # type: ignore[arg-type]
    )
    assert recovered == [("linkedin-email", "200")]
    queue_raw = storage.get_state("prepare_priority_queue") or ""
    assert "linkedin-email:200" in queue_raw
    assert any("Failed to reconcile recovered preparation card linkedin-email:200" in line for line in captured)


def test_run_verbose_logs_shutdown_lifecycle(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    _bootstrap_common(monkeypatch)
    _run_single_cycle(monkeypatch)
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", lambda **kwargs: type("L", (), {"SOURCE": "linkedin-email", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "GreenhouseCollector", lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})())

    result = CliRunner().invoke(cli_module.app, ["run", "--verbose"])
    assert result.exit_code == 0
    assert "[main] Shutdown requested (KeyboardInterrupt)" in result.output
    assert "[main] Stopping Telegram poller" in result.output
    assert "[poller] Poller exiting" in result.output
    assert "[main] Signaling prepare worker" in result.output
    assert "[main] Waiting for worker thread" in result.output
    assert "[main] Worker joined" in result.output
    assert "[main] Releasing singleton lock" in result.output
    assert "[main] Singleton lock released" in result.output
    assert "[poller] Poller exited" in result.output
    assert "[main] Job Applier stopped." in result.output


def test_worker_shutdown_logs_single_pending_preserved_message(monkeypatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, tmp_path)
    settings = cli_module.Settings()
    logs: list[str] = []

    class WorkerStorage:
        def __init__(self):
            _ = None

        def count_by_status(self, *, chat_id, status):
            _ = chat_id, status
            return 1

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", WorkerStorage)
    monkeypatch.setattr(cli_module, "_run_log", lambda message, component="main": logs.append(f"[{component}] {message}"))

    stop_event = threading.Event()
    stop_event.set()
    wake_event = threading.Event()
    cli_module._prepare_worker_loop(
        settings=settings,
        service=object(),  # type: ignore[arg-type]
        telegram_client=object(),  # type: ignore[arg-type]
        stop_event=stop_event,
        wakeup_event=wake_event,
        verbose=True,
    )
    assert any("Pending preparations preserved count=1" in line for line in logs)
    assert not any("Queue not drained pending>=1" in line for line in logs)
    assert not any("Prepare queue continue pending=" in line for line in logs)


def test_lock_prevents_concurrent_external_run_and_allows_retry_after_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / "job_applier.lock"
    first = cli_module._JobApplierLock(lock_path)
    second = cli_module._JobApplierLock(lock_path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_lock_recovers_from_stale_pid_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / "job_applier.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999", encoding="utf-8")
    lock = cli_module._JobApplierLock(lock_path)
    assert lock.acquire() is True
    lock.release()


def test_lock_logs_acquire_and_release_lifecycle(tmp_path: Path) -> None:
    events: list[str] = []
    lock_path = tmp_path / "data" / "job_applier.lock"
    lock = cli_module._JobApplierLock(lock_path, log_fn=events.append)
    assert lock.acquire() is True
    lock.release()
    assert "Acquire requested" in events
    assert "Lock file created" in events
    assert "PID written" in events
    assert "Release requested" in events
    assert "Deleting lock file" in events
    assert "Lock file deleted" in events
    assert "Release completed" in events
