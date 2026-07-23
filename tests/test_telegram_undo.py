from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.cli as cli_module
from app.storage.telegram_delivery import (
    STATUS_APPLIED,
    STATUS_PREPARED,
    STATUS_SENT,
    STATUS_SKIPPED,
    TelegramDeliveryStorage,
)
from app.telegram.client import TelegramRequestError, build_archived_buttons, parse_callback_data
from app.telegram.formatter import format_archived_vacancy_html


def _callback_update(*, callback_id: str, data: str, message_id: int = 10, chat_id: str = "123") -> dict:
    return {
        "callback_query": {
            "id": callback_id,
            "data": data,
            "message": {
                "chat": {"id": chat_id},
                "message_id": message_id,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/4443325085/"}]]
                },
            },
        }
    }


def _seed_sent(storage: TelegramDeliveryStorage, *, external_id: str = "4443325085", status: str = STATUS_SENT) -> None:
    storage.save_sent(
        source="linkedin-email",
        external_id=external_id,
        chat_id="123",
        message_id=10,
    )
    if status != STATUS_SENT:
        storage.update_status(
            source="linkedin-email",
            external_id=external_id,
            chat_id="123",
            status=status,
        )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id=external_id,
        title="Java Backend",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/4443325085/",
        decision="POTENTIAL_MATCH",
        decision_reason="Fits Java backend profile.",
        recommended_resume="java-backend",
    )


class _RecordingClient:
    def __init__(self, *, fail_edit: bool = False) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.edits: list[dict] = []
        self.fail_edit = fail_edit

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))

    def edit_message_text(self, **kwargs):
        if self.fail_edit:
            raise TelegramRequestError("edit failed")
        self.edits.append(kwargs)

    def delete_message(self, **kwargs):
        _ = kwargs


def test_sent_applied_undo_restores_sent(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()

    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    assert delivery.status == STATUS_APPLIED
    assert delivery.previous_status == STATUS_SENT
    assert delivery.last_action == "APPLIED"
    assert delivery.last_action_id
    assert "Marked as applied" in client.edits[0]["text"]
    assert client.edits[0]["buttons"][0][0].text == "↩️ Undo"
    assert client.edits[0]["buttons"][0][0].callback_data == (
        f"undo:li:4443325085:{delivery.last_action_id}"
    )

    cli_module._process_callback_update(
        update=_callback_update(
            callback_id="cb-undo",
            data=f"undo:li:4443325085:{delivery.last_action_id}",
        ),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    restored = storage.get_delivery("linkedin-email", "4443325085")
    assert restored is not None
    assert restored.status == STATUS_SENT
    assert restored.previous_status is None
    assert restored.last_action is None
    assert restored.last_action_id is None
    assert restored.last_action_at is None
    assert ("cb-undo", "Action undone") in client.answers
    assert any("🛠 Prepare" in btn.text for row in client.edits[-1]["buttons"] for btn in row)


def test_prepared_skipped_undo_restores_prepared(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage, status=STATUS_PREPARED)
    storage.save_preparation(
        source="linkedin-email",
        external_id="4443325085",
        status=STATUS_PREPARED,
        resume_name="java-backend",
        language="en",
        error_message=None,
        cover_letter="Cover",
        vacancy_title="Java Backend",
        vacancy_company="ACME",
        vacancy_url="https://www.linkedin.com/jobs/view/4443325085/",
    )
    client = _RecordingClient()

    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-skip", data="skip:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    assert delivery.status == STATUS_SKIPPED
    assert delivery.previous_status == STATUS_PREPARED
    assert "Marked as skipped" in client.edits[0]["text"]

    cli_module._process_callback_update(
        update=_callback_update(
            callback_id="cb-undo",
            data=f"undo:li:4443325085:{delivery.last_action_id}",
        ),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    restored = storage.get_delivery("linkedin-email", "4443325085")
    assert restored is not None
    assert restored.status == STATUS_PREPARED
    assert ("cb-undo", "Action undone") in client.answers
    assert any(btn.text == "📋 Copy Cover Letter" for row in client.edits[-1]["buttons"] for btn in row)


def test_undo_expired_does_not_change_status(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            update telegram_deliveries
            set last_action_at = ?
            where source = ? and external_id = ?
            """,
            (expired_at, "linkedin-email", "4443325085"),
        )
        conn.commit()

    edits_before = len(client.edits)
    cli_module._process_callback_update(
        update=_callback_update(
            callback_id="cb-undo",
            data=f"undo:li:4443325085:{delivery.last_action_id}",
        ),
        client=client,
        storage=storage,
        configured_chat_id="123",
        undo_window_seconds=600,
    )
    after = storage.get_delivery("linkedin-email", "4443325085")
    assert after is not None
    assert after.status == STATUS_APPLIED
    assert after.previous_status == STATUS_SENT
    assert ("cb-undo", "Undo period has expired.") in client.answers
    assert len(client.edits) == edits_before


def test_repeated_undo_is_idempotent(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    undo_data = f"undo:li:4443325085:{delivery.last_action_id}"
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo-1", data=undo_data),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo-2", data=undo_data),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-undo-2", "Action has already been undone.") in client.answers
    restored = storage.get_delivery("linkedin-email", "4443325085")
    assert restored is not None
    assert restored.status == STATUS_SENT


def test_undo_with_wrong_action_id_rejected(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo", data="undo:li:4443325085:deadbeef"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    after = storage.get_delivery("linkedin-email", "4443325085")
    assert after is not None
    assert after.status == STATUS_APPLIED
    assert ("cb-undo", "This action is no longer current.") in client.answers


def test_stale_undo_after_new_action_rejected(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-skip", data="skip:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    first = storage.get_delivery("linkedin-email", "4443325085")
    assert first is not None
    old_token = first.last_action_id
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    second = storage.get_delivery("linkedin-email", "4443325085")
    assert second is not None
    assert second.status == STATUS_APPLIED
    assert second.last_action_id != old_token

    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo-old", data=f"undo:li:4443325085:{old_token}"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    after = storage.get_delivery("linkedin-email", "4443325085")
    assert after is not None
    assert after.status == STATUS_APPLIED
    assert ("cb-undo-old", "This action is no longer current.") in client.answers


def test_telegram_edit_failure_keeps_applied_status(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient(fail_edit=True)
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:4443325085"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    assert delivery.status == STATUS_APPLIED
    assert delivery.previous_status == STATUS_SENT
    assert ("cb-applied", "Отклик отмечен как отправленный") in client.answers


def test_undo_without_previous_status_rejected(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage, status=STATUS_APPLIED)
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            update telegram_deliveries
            set previous_status = null,
                last_action = 'APPLIED',
                last_action_id = 'token123',
                last_action_at = ?
            where source = ? and external_id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), "linkedin-email", "4443325085"),
        )
        conn.commit()
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo", data="undo:li:4443325085:token123"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-undo", "Action has already been undone.") in client.answers
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    assert delivery.status == STATUS_APPLIED


def test_unknown_vacancy_callback_rejected(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    client = _RecordingClient()
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-applied", data="applied:li:999"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-applied", "Вакансия не найдена") in client.answers
    cli_module._process_callback_update(
        update=_callback_update(callback_id="cb-undo", data="undo:li:999:token123"),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-undo", "Вакансия не найдена") in client.answers


def test_double_applied_is_idempotent(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    client = _RecordingClient()
    update = _callback_update(callback_id="cb-1", data="applied:li:4443325085")
    cli_module._process_callback_update(
        update=update,
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    first = storage.get_delivery("linkedin-email", "4443325085")
    assert first is not None
    first_action_id = first.last_action_id
    update["callback_query"]["id"] = "cb-2"
    cli_module._process_callback_update(
        update=update,
        client=client,
        storage=storage,
        configured_chat_id="123",
    )
    second = storage.get_delivery("linkedin-email", "4443325085")
    assert second is not None
    assert second.status == STATUS_APPLIED
    assert second.last_action_id == first_action_id
    assert ("cb-2", "Отклик уже отмечен") in client.answers
    assert len(client.edits) == 1


def test_legacy_callback_formats_still_parse() -> None:
    assert parse_callback_data("applied:li:4443325085") == ("applied", "linkedin-email", "4443325085", None)
    assert parse_callback_data("skip:gh:12") == ("skip", "greenhouse", "12", None)
    assert parse_callback_data("prepare:li:1") == ("prepare", "linkedin-email", "1", None)
    with pytest.raises(ValueError):
        parse_callback_data("undo:li:1")


def test_archived_formatter_and_undo_button() -> None:
    text = format_archived_vacancy_html(applied=True, title="Java Backend", company="ACME")
    assert text.startswith("✅ Marked as applied")
    buttons = build_archived_buttons(
        "https://www.linkedin.com/jobs/view/1/",
        source="linkedin-email",
        external_id="1",
        action_id="abcd1234",
    )
    assert buttons[0][0].callback_data == "undo:li:1:abcd1234"
    assert buttons[1][0].text == "🔗 Open vacancy"


def test_apply_terminal_action_storage_fields(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage)
    token = storage.apply_terminal_action(
        source="linkedin-email",
        external_id="4443325085",
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id="fixedtok",
    )
    assert token == "fixedtok"
    delivery = storage.get_delivery("linkedin-email", "4443325085")
    assert delivery is not None
    assert delivery.status == STATUS_APPLIED
    assert delivery.previous_status == STATUS_SENT
    assert delivery.last_action == "APPLIED"
    assert delivery.last_action_id == "fixedtok"
    assert delivery.last_action_at is not None

    code, restored = storage.undo_terminal_action(
        source="linkedin-email",
        external_id="4443325085",
        chat_id="123",
        expected_action_id="fixedtok",
    )
    assert code == "ok"
    assert restored == STATUS_SENT
