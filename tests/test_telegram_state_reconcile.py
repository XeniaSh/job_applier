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


def _seed_applied_with_undo(
    storage: TelegramDeliveryStorage,
    *,
    external_id: str,
    message_id: int,
    action_id: str,
    expired: bool = True,
) -> None:
    storage.save_sent(
        source="linkedin-email",
        external_id=external_id,
        chat_id="123",
        message_id=message_id,
    )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id=external_id,
        title="Java Backend",
        company="ACME",
        location="Remote",
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )
    storage.apply_terminal_action(
        source="linkedin-email",
        external_id=external_id,
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id=action_id,
    )
    if expired:
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "update telegram_deliveries set last_action_at = ? where external_id = ?",
                (expired_at, external_id),
            )
            conn.commit()


def test_expire_undo_success_clears_metadata_after_markup_edit(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_undo(storage, external_id="3", message_id=30, action_id="exp12345")
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
    assert delivery.previous_status is None


def test_expire_undo_keeps_metadata_when_telegram_edit_fails(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_undo(storage, external_id="31", message_id=31, action_id="failtoken")

    class FailingClient(_RecordingClient):
        def edit_message_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs)
            raise TelegramRequestError("edit failed", method="editMessageReplyMarkup")

    client = FailingClient()
    settings = SimpleNamespace(undo_window_seconds=600, telegram_chat_id="123")
    cleared = cli_module._expire_undo_buttons(
        settings=settings,
        storage=storage,
        telegram_client=client,
    )
    assert cleared == 0
    delivery = storage.get_delivery("linkedin-email", "31")
    assert delivery is not None
    assert delivery.last_action_id == "failtoken"
    assert delivery.previous_status == STATUS_SENT

    # Same row must remain eligible for the next maintenance tick.
    again = storage.list_expired_undo_deliveries(window_seconds=600, limit=10)
    assert any(item.external_id == "31" and item.last_action_id == "failtoken" for item in again)


def test_expire_undo_not_modified_still_clears_metadata(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_undo(storage, external_id="32", message_id=32, action_id="sameui01")

    class NotModifiedClient(_RecordingClient):
        def edit_message_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs)
            raise cli_module.TelegramMessageNotModifiedError("message is not modified")

    client = NotModifiedClient()
    settings = SimpleNamespace(undo_window_seconds=600, telegram_chat_id="123")
    cleared = cli_module._expire_undo_buttons(
        settings=settings,
        storage=storage,
        telegram_client=client,
    )
    assert cleared == 1
    delivery = storage.get_delivery("linkedin-email", "32")
    assert delivery is not None
    assert delivery.last_action_id is None


def test_expire_undo_stale_snapshot_does_not_clear_new_action(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_undo(storage, external_id="33", message_id=33, action_id="oldtoken1")
    stale = storage.list_expired_undo_deliveries(window_seconds=600, limit=10)
    assert len(stale) == 1
    assert stale[0].last_action_id == "oldtoken1"

    # New action replaces undo token before expiry tick processes the snapshot.
    storage.apply_terminal_action(
        source="linkedin-email",
        external_id="33",
        chat_id="123",
        new_status=STATUS_APPLIED,
        previous_status=STATUS_SENT,
        action="APPLIED",
        action_id="newtoken2",
    )

    class CaptureClient(_RecordingClient):
        pass

    # Force expiry path to see the stale token by feeding the old list logic through
    # get_delivery returning the new token while we call clear with expected old id.
    cleared = storage.clear_undo_metadata(
        source="linkedin-email",
        external_id="33",
        chat_id="123",
        expected_action_id="oldtoken1",
    )
    assert cleared is False
    delivery = storage.get_delivery("linkedin-email", "33")
    assert delivery is not None
    assert delivery.last_action_id == "newtoken2"

    # Full expiry tick must also skip clearing the new token.
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "update telegram_deliveries set last_action_at = ? where external_id = ?",
            (expired_at, "33"),
        )
        conn.commit()
    # Simulate stale loop body: current last_action_id differs from snapshot expected id.
    snapshot_expected = "oldtoken1"
    current = storage.get_delivery("linkedin-email", "33")
    assert current is not None
    assert current.last_action_id != snapshot_expected

    client = CaptureClient()
    settings = SimpleNamespace(undo_window_seconds=600, telegram_chat_id="123")
    # Monkeypatch list to return stale snapshot while DB already has newtoken2.
    storage.list_expired_undo_deliveries = lambda **kwargs: [  # type: ignore[method-assign]
        type(
            "D",
            (),
            {
                "source": "linkedin-email",
                "external_id": "33",
                "chat_id": "123",
                "message_id": 33,
                "status": STATUS_APPLIED,
                "last_action_id": "oldtoken1",
                "last_action_at": expired_at,
                "previous_status": STATUS_SENT,
                "last_action": "APPLIED",
                "sent_at": current.sent_at,
            },
        )()
    ]
    result = cli_module._expire_undo_buttons(
        settings=settings,
        storage=storage,
        telegram_client=client,
    )
    assert result == 0
    assert client.markup_edits == []
    after = storage.get_delivery("linkedin-email", "33")
    assert after is not None
    assert after.last_action_id == "newtoken2"


def test_expire_undo_one_failure_does_not_block_others(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_undo(storage, external_id="41", message_id=41, action_id="oktoken1")
    _seed_applied_with_undo(storage, external_id="42", message_id=42, action_id="badtoken")

    class PartialFailClient(_RecordingClient):
        def edit_message_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs)
            if int(kwargs["message_id"]) == 42:
                raise TelegramRequestError("edit failed", method="editMessageReplyMarkup")

    client = PartialFailClient()
    settings = SimpleNamespace(undo_window_seconds=600, telegram_chat_id="123")
    cleared = cli_module._expire_undo_buttons(
        settings=settings,
        storage=storage,
        telegram_client=client,
    )
    assert cleared == 1
    ok = storage.get_delivery("linkedin-email", "41")
    bad = storage.get_delivery("linkedin-email", "42")
    assert ok is not None and ok.last_action_id is None
    assert bad is not None and bad.last_action_id == "badtoken"


def test_dead_archived_helpers_removed() -> None:
    assert not hasattr(cli_module, "_edit_archived_card")
    assert not hasattr(cli_module, "_restore_card_after_undo")


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
