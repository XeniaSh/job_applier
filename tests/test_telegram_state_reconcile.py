from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import app.cli as cli_module
from app.storage.telegram_delivery import (
    STATUS_APPLIED,
    STATUS_PREPARATION_FAILED,
    STATUS_PREPARING,
    STATUS_SENT,
    TelegramDeliveryStorage,
)
from app.telegram.client import TelegramRequestError


class _RecordingClient:
    def __init__(self) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.edits: list[dict] = []
        self.markup_edits: list[dict] = []

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))

    def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    def edit_message_reply_markup(self, **kwargs):
        self.markup_edits.append(kwargs)

    def delete_message(self, **kwargs):
        _ = kwargs


def _callback(*, callback_id: str, data: str, message_id: int = 10) -> dict:
    return {
        "callback_query": {
            "id": callback_id,
            "data": data,
            "message": {
                "chat": {"id": "123"},
                "message_id": message_id,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/1/"}]]
                },
            },
        }
    }


def test_already_applied_reconciles_stale_telegram_buttons(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="1", chat_id="123", message_id=10)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="1",
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/1/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )
    storage.apply_terminal_action(
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id="tok12345",
    )
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback(callback_id="cb-again", data="applied:li:1"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-again", "Отклик уже отмечен") in client.answers
    assert len(client.edits) == 1
    assert "Marked as applied" in client.edits[0]["text"]
    assert any(btn.text == "↩️ Undo" for row in client.edits[0]["buttons"] for btn in row)


def test_expired_undo_callback_reconciles_without_undo_button(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="2", chat_id="123", message_id=20)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="2",
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/2/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )
    storage.apply_terminal_action(
        source="linkedin-email",
        external_id="2",
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id="oldtoken1",
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "update telegram_deliveries set last_action_at = ? where external_id = ?",
            (expired_at, "2"),
        )
        conn.commit()
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback(callback_id="cb-undo", data="undo:li:2:oldtoken1", message_id=20),
        client=client,
        storage=storage,
        configured_chat_id="123",
        undo_window_seconds=600,
    )
    assert ("cb-undo", "Undo period has expired.") in client.answers
    assert len(client.edits) == 1
    assert "Marked as applied" in client.edits[0]["text"]
    assert all(btn.text != "↩️ Undo" for row in client.edits[0]["buttons"] for btn in row)
    delivery = storage.get_delivery("linkedin-email", "2")
    assert delivery is not None
    assert delivery.last_action_id is None


def test_expire_undo_buttons_uses_markup_only(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="3", chat_id="123", message_id=30)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="3",
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/3/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )
    storage.apply_terminal_action(
        source="linkedin-email",
        external_id="3",
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id="exp12345",
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "update telegram_deliveries set last_action_at = ? where external_id = ?",
            (expired_at, "3"),
        )
        conn.commit()

    client = _RecordingClient()
    settings = SimpleNamespace(undo_window_seconds=600, telegram_chat_id="123")
    cleared = cli_module._expire_undo_buttons(
        settings=settings,
        storage=storage,
        telegram_client=client,
    )
    assert cleared == 1
    assert len(client.markup_edits) == 1
    assert all(btn.text != "↩️ Undo" for row in client.markup_edits[0]["buttons"] for btn in row)
    delivery = storage.get_delivery("linkedin-email", "3")
    assert delivery is not None
    assert delivery.last_action_id is None


def test_prepare_unexpected_exception_leaves_failed_not_preparing(tmp_path: Path, monkeypatch) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="4", chat_id="123", message_id=40)
    storage.update_status(
        source="linkedin-email",
        external_id="4",
        chat_id="123",
        status="PREPARE_REQUESTED",
    )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="4",
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/4/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )

    class BoomService:
        def prepare(self, source, external_id):
            _ = source, external_id
            raise RuntimeError("boom")

    client = _RecordingClient()
    settings = SimpleNamespace(
        telegram_chat_id="123",
        undo_window_seconds=600,
        resumes_dir=tmp_path / "resumes",
    )
    result = cli_module._prepare_one_application(
        source="linkedin-email",
        external_id="4",
        settings=settings,
        service=BoomService(),
        storage=storage,
        telegram_client=client,
        dry_run=False,
        print_dry_run_items=False,
        timing_logger=None,
    )
    assert result.errors_count == 1
    delivery = storage.get_delivery("linkedin-email", "4")
    assert delivery is not None
    assert delivery.status == STATUS_PREPARATION_FAILED
    assert delivery.status != STATUS_PREPARING
    assert any("Retry preparation" in btn.text for row in client.edits[0]["buttons"] for btn in row)


def test_reconcile_builds_ui_from_db_status(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="5", chat_id="123", message_id=50)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="5",
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/5/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )
    client = _RecordingClient()
    cli_module._reconcile_vacancy_message(
        storage=storage,
        client=client,
        source="linkedin-email",
        external_id="5",
        chat_id="123",
        url="https://www.linkedin.com/jobs/view/5/",
        title="Java Backend",
        company="ACME",
    )
    assert len(client.edits) == 1
    assert any(btn.text == "🛠 Prepare" for row in client.edits[0]["buttons"] for btn in row)
