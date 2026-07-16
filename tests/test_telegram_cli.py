from dataclasses import dataclass

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.linkedin_email_collector import LinkedInEmailCollectReport, LinkedInProcessedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)
from app.storage.telegram_delivery import STATUS_PREPARE_REQUESTED, STATUS_PREPARED, TelegramDeliveryStorage


def _set_base_env(monkeypatch, *, with_telegram: bool = True) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    if with_telegram:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    else:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def _evaluation(decision: Decision = Decision.POTENTIAL_MATCH) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        matched_points=["java"],
        gaps=[],
        nuances=["Описание вакансии неполное — требуется открыть LinkedIn"],
        match_percentage=None,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=False,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def test_send_linkedin_telegram_dry_run_no_telegram_and_no_delivery(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=False)

    class FakeCollector:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def collect_and_analyze(self, **kwargs):
            assert kwargs["dry_run"] is True
            assert kwargs["analyze_in_dry_run"] is True
            assert kwargs["skip_seen"] is False
            assert kwargs["mark_seen"] is False
            report = LinkedInEmailCollectReport(emails_found=1, analyzed=1)
            report.processed = [
                LinkedInProcessedVacancy(
                    external_id="1",
                    title="Java Backend",
                    company="ACME",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/1/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(),
                )
            ]
            return report

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    class FakeSeenStorage:
        def is_seen(self, source, external_id):
            _ = source, external_id
            return False

        def mark_seen(self, source, external_id):
            raise AssertionError("mark_seen must not be called in telegram dry-run")

    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: FakeSeenStorage())
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)

    class FakeDeliveryStorage:
        def __init__(self):
            self.saved = 0

        def was_sent(self, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return False

        def save_sent(self, **kwargs):
            _ = kwargs
            self.saved += 1

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    storage = FakeDeliveryStorage()
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)

    def fail_client(*args, **kwargs):
        raise AssertionError("TelegramClient should not be created in dry-run")

    monkeypatch.setattr(cli_module, "TelegramClient", fail_client)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["send-linkedin-telegram", "--dry-run", "--limit", "1"])

    assert result.exit_code == 0
    assert "Отправлено в Telegram: 0" in result.output
    assert "Подготовлено карточек: 1" in result.output
    assert storage.saved == 0


def test_send_linkedin_telegram_continue_on_failure_and_deduplicate(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=True)

    class FakeCollector:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            report = LinkedInEmailCollectReport(emails_found=2, analyzed=3)
            report.processed = [
                LinkedInProcessedVacancy(
                    external_id="1",
                    title="Role 1",
                    company="A",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/1/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(),
                ),
                LinkedInProcessedVacancy(
                    external_id="2",
                    title="Role 2",
                    company="B",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/2/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(),
                ),
                LinkedInProcessedVacancy(
                    external_id="3",
                    title="Role 3",
                    company="C",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/3/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(),
                ),
            ]
            return report

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "SeenJobsStorage",
        lambda: type("S", (), {"is_seen": lambda self, source, external_id: False})(),
    )
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)

    class FakeDeliveryStorage:
        def __init__(self):
            self.saved: list[str] = []

        def was_sent(self, source, external_id, chat_id):
            _ = source, chat_id
            return external_id == "1"

        def save_sent(self, **kwargs):
            self.saved.append(kwargs["external_id"])

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    storage = FakeDeliveryStorage()
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)

    @dataclass
    class _Ref:
        chat_id: str
        message_id: int

    class FakeTelegramClient:
        def __init__(self, bot_token, chat_id):
            _ = bot_token, chat_id
            self.calls = 0

        def send_vacancy_card(self, card):
            self.calls += 1
            if card.external_id == "2":
                raise cli_module.TelegramRequestError("failed")
            return _Ref(chat_id="123", message_id=100 + self.calls)

    monkeypatch.setattr(cli_module, "TelegramClient", FakeTelegramClient)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["send-linkedin-telegram", "--limit", "3"])

    assert result.exit_code == 0
    assert "Уже отправлялись: 1" in result.output
    assert "Ошибок отправки: 1" in result.output
    assert storage.saved == ["3"]


def test_send_dry_run_ignores_seen_and_delivered_but_reports_info(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=False)

    class FakeCollector:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            report = LinkedInEmailCollectReport(emails_found=1, analyzed=2, unique_vacancies=2)
            report.processed = [
                LinkedInProcessedVacancy(
                    external_id="10",
                    title="Senior Java Engineer",
                    company="A",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/10/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.POTENTIAL_MATCH),
                ),
                LinkedInProcessedVacancy(
                    external_id="11",
                    title="Kotlin Backend Developer",
                    company="B",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/11/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.STRONG_MATCH),
                ),
            ]
            return report

    class FakeSeen:
        def is_seen(self, source, external_id):
            _ = source
            return external_id == "10"

    class FakeDeliveryStorage:
        def __init__(self):
            self.saved = 0

        def was_sent(self, source, external_id, chat_id):
            _ = source, chat_id
            return external_id == "11"

        def save_sent(self, **kwargs):
            _ = kwargs
            self.saved += 1

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: FakeSeen())
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    storage = FakeDeliveryStorage()
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["send-linkedin-telegram", "--dry-run", "--verbose", "--limit", "5"],
    )

    assert result.exit_code == 0
    assert "INFO ALREADY_SEEN Senior Java Engineer" in result.output
    assert "INFO ALREADY_DELIVERED Kotlin Backend Developer" in result.output
    assert "WOULD_SEND POTENTIAL_MATCH Senior Java Engineer" in result.output
    assert "WOULD_SEND STRONG_MATCH Kotlin Backend Developer" in result.output
    assert "Подготовлено карточек: 2" in result.output
    assert "Отправлено в Telegram: 0" in result.output
    assert storage.saved == 0


def test_send_real_allows_seen_jobs_but_skips_already_delivered(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=True)

    class FakeCollector:
        def __init__(self, **kwargs):
            _ = kwargs

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            report = LinkedInEmailCollectReport(emails_found=1, analyzed=2, unique_vacancies=2)
            report.processed = [
                LinkedInProcessedVacancy(
                    external_id="20",
                    title="Seen but should send",
                    company="A",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/20/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.POTENTIAL_MATCH),
                ),
                LinkedInProcessedVacancy(
                    external_id="21",
                    title="Delivered before",
                    company="B",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/21/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.POTENTIAL_MATCH),
                ),
            ]
            return report

    class FakeSeen:
        def is_seen(self, source, external_id):
            _ = source
            return external_id == "20"

    class FakeDeliveryStorage:
        def __init__(self):
            self.saved: list[str] = []

        def was_sent(self, source, external_id, chat_id):
            _ = source, chat_id
            return external_id == "21"

        def save_sent(self, **kwargs):
            self.saved.append(kwargs["external_id"])

        def upsert_application_history(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    @dataclass
    class _Ref:
        chat_id: str
        message_id: int

    class FakeTelegramClient:
        def __init__(self, bot_token, chat_id):
            _ = bot_token, chat_id

        def send_vacancy_card(self, card):
            return _Ref(chat_id="123", message_id=100 + int(card.external_id))

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: FakeSeen())
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    storage = FakeDeliveryStorage()
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", FakeTelegramClient)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["send-linkedin-telegram", "--limit", "5"])

    assert result.exit_code == 0
    assert storage.saved == ["20"]
    assert "Уже отправлялись: 1" in result.output


def test_verbose_outcome_and_prepared_cards_counter(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=False)

    class FakeCollector:
        def __init__(self, **kwargs):
            _ = kwargs

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            report = LinkedInEmailCollectReport(emails_found=1, analyzed=2, unique_vacancies=4)
            report.processed = [
                LinkedInProcessedVacancy(
                    external_id="30",
                    title="Ignore role",
                    company="A",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/30/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.IGNORE),
                ),
                LinkedInProcessedVacancy(
                    external_id="31",
                    title="Frontend Engineer",
                    company="B",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/31/",
                    content_completeness="PARTIAL",
                    evaluation=None,
                    skipped_by_prefilter=True,
                ),
                LinkedInProcessedVacancy(
                    external_id="32",
                    title="Potential role",
                    company="C",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/32/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.POTENTIAL_MATCH),
                ),
                LinkedInProcessedVacancy(
                    external_id="33",
                    title="Strong role",
                    company="D",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/33/",
                    content_completeness="PARTIAL",
                    evaluation=_evaluation(Decision.STRONG_MATCH),
                ),
            ]
            return report

    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "SeenJobsStorage", lambda: type("S", (), {"is_seen": lambda self, s, e: False})())
    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    monkeypatch.setattr(
        cli_module,
        "TelegramDeliveryStorage",
        lambda: type(
            "D",
            (),
            {
                "was_sent": lambda self, s, e, c: False,
                "save_sent": lambda self, **k: None,
                "upsert_application_history": lambda self, **k: None,
                "mark_history_status": lambda self, **k: None,
            },
        )(),
    )
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["send-linkedin-telegram", "--dry-run", "--verbose", "--no-include-strong"],
    )
    assert result.exit_code == 0
    assert "SKIP IGNORE Ignore role" in result.output
    assert "SKIP TITLE_FILTER Frontend Engineer" in result.output
    assert "WOULD_SEND POTENTIAL_MATCH Potential role" in result.output
    assert "SKIP STRONG_MATCH Strong role" in result.output
    assert "Подготовлено карточек: 1" in result.output


def test_poll_callbacks_skip_prepare_unknown_and_wrong_chat() -> None:
    calls = {"status": [], "answers": [], "edits": []}

    class FakeStorage:
        def update_delivery_and_history(self, **kwargs):
            calls["status"].append(kwargs)

        def set_state(self, key, value):
            calls.setdefault("state", []).append((key, value))

        def get_state(self, key):
            _ = key
            return None

    fake_storage = FakeStorage()

    class FakeClient:
        def answer_callback_query(self, callback_query_id, text=None):
            calls["answers"].append((callback_query_id, text))

        def edit_message_reply_markup(self, chat_id, message_id, buttons):
            calls["edits"].append((chat_id, message_id, buttons))

    client = FakeClient()
    skip_update = {
        "callback_query": {
            "id": "cb1",
            "data": "skip:li:4439013108",
            "message": {
                "chat": {"id": "123"},
                "message_id": 10,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/4439013108/"}]]
                },
            },
        }
    }
    prepare_update = {
        "callback_query": {
            "id": "cb2",
            "data": "prepare:li:4439013109",
            "message": {
                "chat": {"id": "123"},
                "message_id": 11,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/4439013109/"}]]
                },
            },
        }
    }
    applied_update = {
        "callback_query": {
            "id": "cb5",
            "data": "applied:li:4439013111",
            "message": {
                "chat": {"id": "123"},
                "message_id": 14,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/4439013111/"}]]
                },
            },
        }
    }
    unknown_update = {
        "callback_query": {
            "id": "cb3",
            "data": "bad",
            "message": {"chat": {"id": "123"}, "message_id": 12},
        }
    }
    wrong_chat_update = {
        "callback_query": {
            "id": "cb4",
            "data": "skip:li:4439013110",
            "message": {"chat": {"id": "999"}, "message_id": 13},
        }
    }

    cli_module._process_callback_update(
        update=skip_update,
        client=client,
        storage=fake_storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=prepare_update,
        client=client,
        storage=fake_storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=unknown_update,
        client=client,
        storage=fake_storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=wrong_chat_update,
        client=client,
        storage=fake_storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=applied_update,
        client=client,
        storage=fake_storage,
        configured_chat_id="123",
    )

    assert any(item["delivery_status"] == "SKIPPED" and item["history_status"] == "SKIPPED" for item in calls["status"])
    assert any(
        item["delivery_status"] == "PREPARE_REQUESTED" and item["history_status"] == "PREPARE_REQUESTED"
        for item in calls["status"]
    )
    assert any(item["delivery_status"] == "APPLIED" and item["history_status"] == "APPLIED" for item in calls["status"])
    assert ("cb1", "Вакансия пропущена") in calls["answers"]
    assert ("cb2", "Добавлено в очередь на подготовку отклика") in calls["answers"]
    assert ("cb5", "Отклик отмечен как отправленный") in calls["answers"]
    assert ("cb3", "Некорректное действие") in calls["answers"]
    assert ("cb4", "Действие недоступно для этого чата") in calls["answers"]
    assert len(calls["edits"]) == 3
    for _, _, buttons in calls["edits"]:
        assert len(buttons) == 1
        assert len(buttons[0]) == 1
        assert buttons[0][0].text == "🔗 Open LinkedIn"
        assert str(buttons[0][0].url).startswith("https://www.linkedin.com/jobs/view/")


def test_poll_telegram_actions_persists_offset_and_repeated_updates(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=True)
    processed: list[int] = []

    class FakeStorage:
        def __init__(self):
            self.state = {}

        def get_state(self, key):
            return self.state.get(key)

        def set_state(self, key, value):
            self.state[key] = value

        def update_delivery_and_history(self, **kwargs):
            _ = kwargs

    storage = FakeStorage()
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)

    class FakeClient:
        def __init__(self, token, chat_id):
            _ = token, chat_id

        def get_updates(self, offset, timeout=25):
            _ = timeout
            if offset is None:
                return [
                    {
                        "update_id": 101,
                        "callback_query": {
                            "id": "cb1",
                            "data": "skip:li:4439013108",
                            "message": {"chat": {"id": "123"}, "message_id": 10},
                        },
                    }
                ]
            return []

        def answer_callback_query(self, callback_query_id, text=None):
            _ = callback_query_id, text

        def edit_message_reply_markup(self, chat_id, message_id, buttons):
            _ = chat_id, message_id, buttons

    monkeypatch.setattr(cli_module, "TelegramClient", FakeClient)

    original = cli_module._process_callback_update

    def wrapped(**kwargs):
        processed.append(int(kwargs["update"]["update_id"]))
        return original(**kwargs)

    monkeypatch.setattr(cli_module, "_process_callback_update", wrapped)

    runner = CliRunner()
    first = runner.invoke(cli_module.app, ["poll-telegram-actions", "--once", "--timeout", "1"])
    second = runner.invoke(cli_module.app, ["poll-telegram-actions", "--once", "--timeout", "1"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert processed == [101]
    assert storage.state["telegram_update_offset"] == "102"


def test_telegram_chat_id_output(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")

    class FakeClient:
        def __init__(self, bot_token, chat_id):
            _ = bot_token, chat_id

        def get_updates(self, offset, timeout=25):
            _ = offset, timeout
            return [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {
                            "id": 123456789,
                            "type": "private",
                            "first_name": "Kseniia",
                            "username": "username",
                        }
                    },
                }
            ]

    monkeypatch.setattr(cli_module, "TelegramClient", FakeClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["telegram-chat-id"])
    assert result.exit_code == 0
    assert "Найдены чаты:" in result.output
    assert "123456789 — Kseniia (@username)" in result.output


def test_missing_telegram_settings_break_only_telegram_commands(monkeypatch) -> None:
    _set_base_env(monkeypatch, with_telegram=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    runner = CliRunner()
    telegram_result = runner.invoke(cli_module.app, ["send-linkedin-telegram", "--limit", "1"])
    assert telegram_result.exit_code != 0

    class FakeEmailClient:
        def __init__(self, **kwargs):
            _ = kwargs

        def fetch_linkedin_messages(self):
            return []

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type("Collector", (), {"collect_and_analyze": lambda self, **k: LinkedInEmailCollectReport()})(),
    )
    ok_result = runner.invoke(cli_module.app, ["collect-linkedin-email", "--dry-run", "--limit", "1"])
    assert ok_result.exit_code == 0


def test_telegram_debug_lists_records_newest_first_and_filters(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = TelegramDeliveryStorage(db_path=db_path)
    storage.save_sent(source="linkedin-email", external_id="300", chat_id="123", message_id=10)
    storage.set_status("linkedin-email", "300", STATUS_PREPARE_REQUESTED)
    storage.save_sent(source="linkedin-email", external_id="301", chat_id="123", message_id=11)
    storage.set_status("linkedin-email", "301", STATUS_PREPARED)
    storage.save_sent(source="other-source", external_id="302", chat_id="123", message_id=12)
    storage.set_status("other-source", "302", STATUS_PREPARE_REQUESTED)

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    runner = CliRunner()

    result = runner.invoke(cli_module.app, ["telegram-debug"])
    assert result.exit_code == 0
    assert "external_id" in result.output
    assert result.output.find("302") < result.output.find("301") < result.output.find("300")

    status_result = runner.invoke(cli_module.app, ["telegram-debug", "--status", "PREPARE_REQUESTED"])
    assert status_result.exit_code == 0
    assert "\n301          " not in status_result.output
    assert "\n300          " in status_result.output
    assert "\n302          " in status_result.output

    source_result = runner.invoke(cli_module.app, ["telegram-debug", "--source", "other-source"])
    assert source_result.exit_code == 0
    assert "\n302          " in source_result.output
    assert "\n301          " not in source_result.output

    limit_result = runner.invoke(cli_module.app, ["telegram-debug", "--limit", "1"])
    assert limit_result.exit_code == 0
    assert "\n302          " in limit_result.output
    assert "\n301          " not in limit_result.output


def test_telegram_debug_empty_and_status_validation(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    runner = CliRunner()

    empty_result = runner.invoke(cli_module.app, ["telegram-debug"])
    assert empty_result.exit_code == 0
    assert "Telegram delivery records not found." in empty_result.output

    bad_status = runner.invoke(cli_module.app, ["telegram-debug", "--status", "UNKNOWN"])
    assert bad_status.exit_code != 0
    assert "Unknown status" in bad_status.output


def test_telegram_reset_updates_status_and_prints_transition(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = TelegramDeliveryStorage(db_path=db_path)
    storage.save_sent(source="linkedin-email", external_id="4439900667", chat_id="123", message_id=42)
    storage.set_status("linkedin-email", "4439900667", STATUS_PREPARED)

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    runner = CliRunner()

    result = runner.invoke(cli_module.app, ["telegram-reset", "4439900667"])
    assert result.exit_code == 0
    assert "Updated linkedin-email:4439900667" in result.output
    assert "PREPARED -> PREPARE_REQUESTED" in result.output

    updated = TelegramDeliveryStorage(db_path=db_path).get_delivery("linkedin-email", "4439900667")
    assert updated is not None
    assert updated.status == STATUS_PREPARE_REQUESTED


def test_telegram_reset_rejects_unknown_status_and_missing_record(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    runner = CliRunner()

    bad_status = runner.invoke(cli_module.app, ["telegram-reset", "4439900667", "--status", "UNKNOWN"])
    assert bad_status.exit_code != 0
    assert "Unknown status" in bad_status.output

    missing = runner.invoke(cli_module.app, ["telegram-reset", "4439900667"])
    assert missing.exit_code != 0
    assert "Delivery record not found" in missing.output


def test_telegram_resume_cache_hides_full_file_id(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = TelegramDeliveryStorage(db_path=db_path)
    storage.save_resume_cache(
        resume_name="java-backend",
        file_path=str(tmp_path / "resumes" / "java-backend.pdf"),
        file_mtime_ns=10,
        file_size=20,
        telegram_file_id="FILE_ID_1234567890_LONG",
        telegram_file_unique_id="UNIQ",
    )
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    result = CliRunner().invoke(cli_module.app, ["telegram-resume-cache"])
    assert result.exit_code == 0
    assert "FILE_ID_1234..." in result.output
    assert "FILE_ID_1234567890_LONG" not in result.output


def test_telegram_clear_resume_cache_removes_metadata_only(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    pdf = resumes_dir / "java-backend.pdf"
    pdf.write_bytes(b"%PDF")
    storage = TelegramDeliveryStorage(db_path=db_path)
    storage.save_resume_cache(
        resume_name="java-backend",
        file_path=str(pdf),
        file_mtime_ns=10,
        file_size=20,
        telegram_file_id="FILE_ID_1234567890_LONG",
        telegram_file_unique_id="UNIQ",
    )
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    result = CliRunner().invoke(
        cli_module.app,
        ["telegram-clear-resume-cache", "java-backend", "--yes"],
    )
    assert result.exit_code == 0
    assert "Deleted resume cache: java-backend" in result.output
    assert pdf.exists() is True
    assert TelegramDeliveryStorage(db_path=db_path).get_resume_cache("java-backend") is None


def test_telegram_cache_resumes_warmup_and_force(monkeypatch, tmp_path) -> None:
    _set_base_env(monkeypatch, with_telegram=True)
    monkeypatch.setenv("RESUMES_DIR", str(tmp_path / "resumes"))
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    (resumes_dir / "java-backend.pdf").write_bytes(b"%PDF one")
    (resumes_dir / "kotlin-backend.pdf").write_bytes(b"%PDF two")
    db_path = tmp_path / "jobs.db"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs
            self.calls = 0

        def send_document(self, *, file_path, caption):
            _ = file_path, caption
            self.calls += 1
            return type(
                "DocRef",
                (),
                {
                    "chat_id": "123",
                    "message_id": 100 + self.calls,
                    "file_id": f"FILE_ID_{self.calls}",
                    "file_unique_id": f"UNIQ_{self.calls}",
                },
            )()

    fake_client = FakeClient()
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))

    first = CliRunner().invoke(cli_module.app, ["telegram-cache-resumes"])
    assert first.exit_code == 0
    assert "java-backend: uploaded" in first.output
    assert "kotlin-backend: uploaded" in first.output
    assert "fintech-backend: missing" in first.output

    second = CliRunner().invoke(cli_module.app, ["telegram-cache-resumes"])
    assert second.exit_code == 0
    assert "java-backend: cached" in second.output
    assert "kotlin-backend: cached" in second.output

    forced = CliRunner().invoke(cli_module.app, ["telegram-cache-resumes", "--resume", "java-backend", "--force"])
    assert forced.exit_code == 0
    assert "java-backend: uploaded" in forced.output
