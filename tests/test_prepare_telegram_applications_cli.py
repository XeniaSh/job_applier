from dataclasses import dataclass
import threading

from typer.testing import CliRunner

import app.cli as cli_module
from app.application.preparation_service import PreparedApplication
from app.storage.telegram_delivery import STATUS_PREPARATION_FAILED, STATUS_PREPARED


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")


def _prepared(external_id: str, resume_path: str | None) -> PreparedApplication:
    return PreparedApplication(
        source="linkedin-email",
        external_id=external_id,
        title=f"Role {external_id}",
        company="ACME",
        location="Remote",
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        decision="POTENTIAL_MATCH",
        match_percentage=None,
        recommended_resume="java-backend",
        resume_path=resume_path,
        cover_letter="Short letter text.",
        language="en",
        warnings=[],
    )


def test_prepare_dry_run_sends_nothing_and_updates_nothing(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source
            return _prepared(external_id, None)

    class FakeStorage:
        def __init__(self):
            self.updated = []

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "1")]

        def update_status(self, **kwargs):
            self.updated.append(kwargs)

        def save_preparation(self, **kwargs):
            self.updated.append(kwargs)

        def mark_history_status(self, **kwargs):
            self.updated.append(kwargs)

    storage = FakeStorage()
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No telegram in dry-run")))

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications", "--dry-run"])
    assert result.exit_code == 0
    assert "PREPARED Role 1" in result.output
    assert "Сгенерировано пакетов: 1" in result.output
    assert "Подготовлено успешно: 0" in result.output
    assert "Ошибок: 0" in result.output
    assert "java-backend (PDF not found)" in result.output
    assert "PDF отсутствует: 1" in result.output
    assert storage.updated == []


def test_prepare_processed_oldest_first_and_success_updates_status(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    order: list[str] = []

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source
            order.append(external_id)
            return _prepared(external_id, "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.statuses = []
            self.meta = []

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "2"), ("linkedin-email", "5")]

        def update_status(self, **kwargs):
            self.statuses.append(kwargs)

        def save_preparation(self, **kwargs):
            self.meta.append(kwargs)

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, chat_id
            return ("123", 200 + int(external_id))

        def get_resume_cache(self, resume_name):
            _ = resume_name
            return None

        def save_resume_cache(self, **kwargs):
            _ = kwargs

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        edit_calls: int = 0
        text_calls: int = 0

        def edit_message_text(self, **kwargs):
            _ = kwargs
            self.edit_calls += 1

        def send_text_message(self, text: str):
            _ = text
            self.text_calls += 1

    class FakeResumeCacheService:
        def __init__(self, **kwargs):
            _ = kwargs
            self.calls = 0

        def get_or_upload(self, *, resume_name, chat_id, force_upload=False):
            _ = resume_name, chat_id, force_upload
            self.calls += 1
            if self.calls == 1:
                return type(
                    "ResumeResult",
                    (),
                    {"missing": False, "cache_hit": False, "uploaded": True, "telegram_file_id": "FILE_ID_1"},
                )()
            return type(
                "ResumeResult",
                (),
                {"missing": False, "cache_hit": True, "uploaded": False, "telegram_file_id": "FILE_ID_1"},
            )()

    storage = FakeStorage()
    fake_client = FakeClient("token", "123")
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(cli_module, "ResumeCacheService", FakeResumeCacheService)

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications", "--limit", "10"])
    assert result.exit_code == 0
    assert order == ["2", "5"]
    assert len(storage.statuses) == 2
    assert all(item["status"] == STATUS_PREPARED for item in storage.statuses)
    assert fake_client.edit_calls == 2
    assert fake_client.text_calls == 0
    assert "Сгенерировано пакетов: 2" in result.output
    assert "Подготовлено успешно: 2" in result.output
    assert "Отправлено в Telegram: 2" in result.output
    assert "PDF отправлено из кэша: 0" in result.output
    assert "PDF загружено заново: 0" in result.output


def test_prepare_failure_sets_preparation_failed(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source, external_id
            raise cli_module.ApplicationPreparationError("boom")

    class FakeStorage:
        def __init__(self):
            self.statuses = []

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "7")]

        def update_status(self, **kwargs):
            self.statuses.append(kwargs)

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 101)

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str

        def edit_message_text(self, **kwargs):
            _ = kwargs

    storage = FakeStorage()
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: FakeClient("token", "123"))
    monkeypatch.setattr(cli_module, "ResumeCacheService", lambda **kwargs: object())

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications"])
    assert result.exit_code == 0
    assert storage.statuses[0]["status"] == STATUS_PREPARATION_FAILED


def test_prepare_missing_pdf_does_not_fail(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source, external_id
            return _prepared("8", None)

    class FakeStorage:
        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "8")]

        def update_status(self, **kwargs):
            _ = kwargs

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 108)

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        edit_called: bool = False

        def edit_message_text(self, **kwargs):
            _ = kwargs
            self.edit_called = True

    client = FakeClient("token", "123")
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: FakeStorage())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(cli_module, "ResumeCacheService", lambda **kwargs: object())

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications"])
    assert result.exit_code == 0
    assert "PDF отсутствует: 0" in result.output
    assert "Ошибок PDF: 0" in result.output
    assert "Сгенерировано пакетов: 1" in result.output
    assert "Подготовлено успешно: 1" in result.output
    assert client.edit_called is True


def test_dry_run_generated_never_zero_after_prepared(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source
            return _prepared(external_id, None)

    class FakeStorage:
        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "100")]

        def update_status(self, **kwargs):
            _ = kwargs

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: FakeStorage())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli_module, "ResumeCacheService", lambda **kwargs: object())

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications", "--dry-run"])
    assert result.exit_code == 0
    assert "PREPARED Role 100" in result.output
    assert "Сгенерировано пакетов: 1" in result.output
    assert "Ошибок: 0" in result.output


def test_prepare_keeps_prepared_status_when_pdf_fails(monkeypatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: object())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())

    class FakeService:
        def __init__(self, **kwargs):
            _ = kwargs

        def prepare(self, source, external_id):
            _ = source, external_id
            return _prepared("9", "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.statuses = []

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "9")]

        def update_status(self, **kwargs):
            self.statuses.append(kwargs)

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 109)

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        edit_sent: int = 0

        def edit_message_text(self, **kwargs):
            _ = kwargs
            self.edit_sent += 1

    storage = FakeStorage()
    client = FakeClient("token", "123")
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(cli_module, "ResumeCacheService", cli_module.ResumeCacheService)

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications"])
    assert result.exit_code == 0
    assert client.edit_sent == 1
    assert any(item["status"] == STATUS_PREPARED for item in storage.statuses)
    assert "Ошибок PDF: 0" in result.output
    assert "telegram-token" not in result.output
    assert "%PDF" not in result.output


def test_callback_polling_does_not_prepare_cover_letters(monkeypatch) -> None:
    _set_env(monkeypatch)

    def fail_service(*args, **kwargs):
        raise AssertionError("PreparationService must not be used by poll-telegram-actions")

    monkeypatch.setattr(cli_module, "PreparationService", fail_service)

    class FakeStorage:
        def get_state(self, key):
            _ = key
            return None

        def set_state(self, key, value):
            _ = key, value

        def update_status(self, **kwargs):
            _ = kwargs

    class FakeClient:
        def __init__(self, token, chat_id):
            _ = token, chat_id

        def get_updates(self, offset, timeout=25):
            _ = offset, timeout
            return []

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: FakeStorage())
    monkeypatch.setattr(cli_module, "TelegramClient", FakeClient)

    result = CliRunner().invoke(cli_module.app, ["poll-telegram-actions", "--once"])
    assert result.exit_code == 0


def test_priority_vacancy_prepared_before_older_queue_item(monkeypatch) -> None:
    _set_env(monkeypatch)
    settings = cli_module.Settings()
    order: list[str] = []

    class FakeService:
        def prepare(self, source, external_id):
            _ = source
            order.append(external_id)
            return _prepared(external_id, "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.status = {
                ("linkedin-email", "4419655778"): "PREPARE_REQUESTED",
                ("linkedin-email", "4441937215"): "PREPARE_REQUESTED",
            }

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "4419655778"), ("linkedin-email", "4441937215")]

        def get_delivery(self, source, external_id):
            value = self.status.get((source, external_id))
            if value is None:
                return None
            return type("D", (), {"status": value})()

        def claim_for_preparation(self, *, source, external_id, chat_id):
            _ = chat_id
            key = (source, external_id)
            if self.status.get(key) != "PREPARE_REQUESTED":
                return False
            self.status[key] = "PREPARING"
            return True

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, chat_id
            return ("123", 200 + int(external_id[-2:]))

        def update_status(self, *, source, external_id, chat_id, status):
            _ = chat_id
            self.status[(source, external_id)] = status

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    class FakeClient:
        def edit_message_text(self, **kwargs):
            _ = kwargs

    storage = FakeStorage()
    result = cli_module._prepare_requested_applications(
        settings=settings,
        service=FakeService(),
        storage=storage,
        telegram_client=FakeClient(),
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
        priority_vacancy_keys=[("linkedin-email", "4441937215")],
    )
    assert order == ["4441937215", "4419655778"]
    assert result.prepared_successfully == 2


def test_priority_duplicates_and_preparing_ready_not_regenerated(monkeypatch) -> None:
    _set_env(monkeypatch)
    settings = cli_module.Settings()
    order: list[str] = []

    class FakeService:
        def prepare(self, source, external_id):
            _ = source
            order.append(external_id)
            return _prepared(external_id, "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.status = {
                ("linkedin-email", "1"): "PREPARE_REQUESTED",
                ("linkedin-email", "2"): "PREPARING",
                ("linkedin-email", "3"): "PREPARED",
            }

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "1")]

        def get_delivery(self, source, external_id):
            value = self.status.get((source, external_id))
            if value is None:
                return None
            return type("D", (), {"status": value})()

        def claim_for_preparation(self, *, source, external_id, chat_id):
            _ = chat_id
            key = (source, external_id)
            if self.status.get(key) != "PREPARE_REQUESTED":
                return False
            self.status[key] = "PREPARING"
            return True

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 42)

        def update_status(self, *, source, external_id, chat_id, status):
            _ = chat_id
            self.status[(source, external_id)] = status

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    class FakeClient:
        def edit_message_text(self, **kwargs):
            _ = kwargs

    storage = FakeStorage()
    result = cli_module._prepare_requested_applications(
        settings=settings,
        service=FakeService(),
        storage=storage,
        telegram_client=FakeClient(),
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
        priority_vacancy_keys=[
            ("linkedin-email", "1"),
            ("linkedin-email", "1"),
            ("linkedin-email", "2"),
            ("linkedin-email", "3"),
        ],
    )
    assert order == ["1"]
    assert result.prepared_successfully == 1


def test_failed_preparation_can_be_retried_with_claim(monkeypatch) -> None:
    _set_env(monkeypatch)
    settings = cli_module.Settings()
    calls = {"n": 0}

    class FakeService:
        def prepare(self, source, external_id):
            _ = source, external_id
            calls["n"] += 1
            if calls["n"] == 1:
                raise cli_module.ApplicationPreparationError("boom")
            return _prepared(external_id, "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.status = {("linkedin-email", "9"): "PREPARE_REQUESTED"}

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            rows = []
            for (source, external_id), value in self.status.items():
                if value == "PREPARE_REQUESTED":
                    rows.append((source, external_id))
            return rows

        def get_delivery(self, source, external_id):
            value = self.status.get((source, external_id))
            if value is None:
                return None
            return type("D", (), {"status": value})()

        def claim_for_preparation(self, *, source, external_id, chat_id):
            _ = chat_id
            key = (source, external_id)
            if self.status.get(key) != "PREPARE_REQUESTED":
                return False
            self.status[key] = "PREPARING"
            return True

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 77)

        def update_status(self, *, source, external_id, chat_id, status):
            _ = chat_id
            self.status[(source, external_id)] = status

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    class FakeClient:
        def edit_message_text(self, **kwargs):
            _ = kwargs

    storage = FakeStorage()
    first = cli_module._prepare_requested_applications(
        settings=settings,
        service=FakeService(),
        storage=storage,
        telegram_client=FakeClient(),
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
    )
    assert first.errors_count == 1
    storage.status[("linkedin-email", "9")] = "PREPARE_REQUESTED"
    second = cli_module._prepare_requested_applications(
        settings=settings,
        service=FakeService(),
        storage=storage,
        telegram_client=FakeClient(),
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
        priority_vacancy_keys=[("linkedin-email", "9")],
    )
    assert second.prepared_successfully == 1


def test_callback_acknowledged_while_other_generation_running_then_priority_runs_next(monkeypatch) -> None:
    _set_env(monkeypatch)
    settings = cli_module.Settings()
    generation_order: list[str] = []
    b_started = threading.Event()
    release_b = threading.Event()

    class BlockingService:
        def prepare(self, source, external_id):
            _ = source
            generation_order.append(external_id)
            if external_id == "4441994095":
                b_started.set()
                release_b.wait(timeout=5.0)
            return _prepared(external_id, "resumes/java-backend.pdf")

    class Storage:
        def __init__(self):
            self.lock = threading.Lock()
            self.state = {}
            self.status = {
                ("linkedin-email", "4441994095"): "PREPARE_REQUESTED",
                ("linkedin-email", "4419025659"): "SENT",
            }

        def get_state(self, key):
            return self.state.get(key)

        def set_state(self, key, value):
            self.state[key] = value

        def get_delivery(self, source, external_id):
            with self.lock:
                value = self.status.get((source, external_id))
            if value is None:
                return None
            return type("D", (), {"status": value})()

        def claim_for_preparation(self, *, source, external_id, chat_id):
            _ = chat_id
            key = (source, external_id)
            with self.lock:
                if self.status.get(key) != "PREPARE_REQUESTED":
                    return False
                self.status[key] = "PREPARING"
            return True

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            with self.lock:
                rows = [(s, e) for (s, e), value in self.status.items() if value == "PREPARE_REQUESTED"]
            rows.sort(key=lambda item: item[1])
            return rows

        def update_status(self, *, source, external_id, chat_id, status):
            _ = chat_id
            with self.lock:
                self.status[(source, external_id)] = status

        def update_delivery_and_history(self, **kwargs):
            with self.lock:
                self.status[(kwargs["source"], kwargs["external_id"])] = kwargs["delivery_status"]

        def get_history_title_company_url(self, source, external_id):
            _ = source, external_id
            return ("Role", "Company", "https://www.linkedin.com/jobs/view/1/")

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, chat_id
            return ("123", 10 if external_id == "4441994095" else 11)

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    class Client:
        def __init__(self):
            self.answers: list[tuple[str, str | None]] = []

        def answer_callback_query(self, callback_query_id, text=None):
            self.answers.append((callback_query_id, text))

        def edit_message_text(self, **kwargs):
            _ = kwargs

    storage = Storage()
    client = Client()

    worker_done = threading.Event()

    def run_first_generation():
        cli_module._prepare_requested_applications(
            settings=settings,
            service=BlockingService(),
            storage=storage,
            telegram_client=client,
            limit=20,
            dry_run=False,
            print_dry_run_items=False,
            priority_vacancy_keys=[("linkedin-email", "4441994095")],
        )
        worker_done.set()

    thread = threading.Thread(target=run_first_generation, daemon=True)
    thread.start()
    assert b_started.wait(timeout=5.0)

    callback_update = {
        "callback_query": {
            "id": "cb-A",
            "data": "prepare:li:4419025659",
            "message": {"chat": {"id": "123"}, "message_id": 11},
        }
    }
    cli_module._process_callback_update(
        update=callback_update,
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-A", "Добавлено в очередь на подготовку отклика") in client.answers
    assert generation_order == ["4441994095"]

    release_b.set()
    assert worker_done.wait(timeout=5.0)
    priority = cli_module._drain_prepare_priorities(storage=storage)
    cli_module._prepare_requested_applications(
        settings=settings,
        service=BlockingService(),
        storage=storage,
        telegram_client=client,
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
        priority_vacancy_keys=priority,
    )

    assert generation_order == ["4441994095", "4419025659"]


def test_prepare_stops_after_current_item_when_shutdown_requested(monkeypatch) -> None:
    _set_env(monkeypatch)
    settings = cli_module.Settings()
    generation_order: list[str] = []
    events: list[str] = []
    stop = {"requested": False}

    class FakeService:
        def prepare(self, source, external_id):
            _ = source
            generation_order.append(external_id)
            if external_id == "1":
                stop["requested"] = True
            return _prepared(external_id, "resumes/java-backend.pdf")

    class FakeStorage:
        def __init__(self):
            self.status = {
                ("linkedin-email", "1"): "PREPARE_REQUESTED",
                ("linkedin-email", "2"): "PREPARE_REQUESTED",
            }

        def list_by_status(self, *, chat_id, status, limit):
            _ = chat_id, status, limit
            return [("linkedin-email", "1"), ("linkedin-email", "2")]

        def get_delivery(self, source, external_id):
            value = self.status.get((source, external_id))
            if value is None:
                return None
            return type("D", (), {"status": value})()

        def claim_for_preparation(self, *, source, external_id, chat_id):
            _ = chat_id
            key = (source, external_id)
            if self.status.get(key) != "PREPARE_REQUESTED":
                return False
            self.status[key] = "PREPARING"
            return True

        def get_message_ref(self, *, source, external_id, chat_id):
            _ = source, external_id, chat_id
            return ("123", 42)

        def update_status(self, *, source, external_id, chat_id, status):
            _ = chat_id
            self.status[(source, external_id)] = status

        def save_preparation(self, **kwargs):
            _ = kwargs

        def mark_history_status(self, **kwargs):
            _ = kwargs

    class FakeClient:
        def edit_message_text(self, **kwargs):
            _ = kwargs

    result = cli_module._prepare_requested_applications(
        settings=settings,
        service=FakeService(),
        storage=FakeStorage(),
        telegram_client=FakeClient(),
        limit=20,
        dry_run=False,
        print_dry_run_items=False,
        timing_logger=events.append,
        stop_requested=lambda: stop["requested"],
    )

    assert generation_order == ["1"]
    assert result.prepared_successfully == 1
    assert "Shutdown pending after preparation" in events
    assert "Exiting after current task" in events
