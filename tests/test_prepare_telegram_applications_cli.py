from dataclasses import dataclass

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

        def get_resume_cache(self, resume_name):
            _ = resume_name
            return None

        def save_resume_cache(self, **kwargs):
            _ = kwargs

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        text_calls: int = 0
        doc_upload_calls: int = 0
        doc_cached_calls: int = 0

        def send_prepared_application(self, **kwargs):
            _ = kwargs
            self.text_calls += 1

        def send_text_message(self, text: str):
            _ = text
            self.text_calls += 1

        def send_document(self, **kwargs):
            _ = kwargs
            self.doc_upload_calls += 1
            return type("DocRef", (), {"chat_id": "123", "message_id": 100, "file_id": "FILE_ID_1", "file_unique_id": "UNIQ_1"})()

        def send_document_by_file_id(self, **kwargs):
            _ = kwargs
            self.doc_cached_calls += 1
            return type("DocRef", (), {"chat_id": "123", "message_id": 101, "file_id": "FILE_ID_1", "file_unique_id": "UNIQ_1"})()

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
    assert fake_client.doc_upload_calls == 0
    assert fake_client.doc_cached_calls == 1
    assert "Сгенерировано пакетов: 2" in result.output
    assert "Подготовлено успешно: 2" in result.output
    assert "Отправлено в Telegram: 2" in result.output
    assert "PDF отправлено из кэша: 1" in result.output
    assert "PDF загружено заново: 1" in result.output


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

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str

        def send_text_message(self, text: str):
            _ = text

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

        def get_resume_cache(self, resume_name):
            _ = resume_name
            return None

        def save_resume_cache(self, **kwargs):
            _ = kwargs

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        doc_called: bool = False

        def send_prepared_application(self, **kwargs):
            _ = kwargs

        def send_text_message(self, text: str):
            _ = text

        def send_document(self, **kwargs):
            self.doc_called = True

    client = FakeClient("token", "123")
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: FakeStorage())
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(cli_module, "ResumeCacheService", lambda **kwargs: type("S", (), {"get_or_upload": lambda self, **k: type("R", (), {"missing": True, "cache_hit": False, "uploaded": False, "telegram_file_id": None})()})())

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications"])
    assert result.exit_code == 0
    assert "PDF отсутствует: 1" in result.output
    assert "Ошибок PDF: 0" in result.output
    assert "Сгенерировано пакетов: 1" in result.output
    assert "Подготовлено успешно: 1" in result.output
    assert client.doc_called is False


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

        def get_resume_cache(self, resume_name):
            _ = resume_name
            return None

        def save_resume_cache(self, **kwargs):
            _ = kwargs

    @dataclass
    class FakeClient:
        bot_token: str
        chat_id: str
        text_sent: int = 0

        def send_prepared_application(self, **kwargs):
            _ = kwargs
            self.text_sent += 1

        def send_text_message(self, text: str):
            _ = text

        def send_document(self, **kwargs):
            raise cli_module.TelegramRequestError("upload failed")

    storage = FakeStorage()
    client = FakeClient("token", "123")
    monkeypatch.setattr(cli_module, "PreparationService", FakeService)
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: storage)
    monkeypatch.setattr(cli_module, "TelegramClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(cli_module, "ResumeCacheService", cli_module.ResumeCacheService)

    result = CliRunner().invoke(cli_module.app, ["prepare-telegram-applications"])
    assert result.exit_code == 0
    assert client.text_sent == 1
    assert any(item["status"] == STATUS_PREPARED for item in storage.statuses)
    assert "Ошибок PDF: 1" in result.output
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
