"""Retry-safe auxiliary Telegram message cleanup."""

from __future__ import annotations

from pathlib import Path
import threading

import app.cli as cli_module
from app.storage.telegram_delivery import STATUS_APPLIED, STATUS_PREPARED, TelegramDeliveryStorage


class _RecordingClient:
    def __init__(self, *, errors: dict[int, Exception] | None = None) -> None:
        self.deleted: list[int] = []
        self._errors = errors or {}

    def delete_message(self, *, chat_id: str, message_id: int) -> None:
        _ = chat_id
        self.deleted.append(message_id)
        error = self._errors.get(message_id)
        if error is not None:
            raise error


def _seed_applied_with_aux(
    storage: TelegramDeliveryStorage,
    *,
    external_id: str = "1",
    chat_id: str = "123",
    resume_message_id: int | None = 11,
    cover_letter_message_id: int | None = 12,
    cover_letter_txt_message_id: int | None = 13,
    cover_letter_pdf_message_id: int | None = 14,
) -> None:
    storage.save_sent(
        source="linkedin-email",
        external_id=external_id,
        chat_id=chat_id,
        message_id=1,
    )
    storage.update_status(
        source="linkedin-email",
        external_id=external_id,
        chat_id=chat_id,
        status=STATUS_APPLIED,
    )
    storage.save_preparation(
        source="linkedin-email",
        external_id=external_id,
        status=STATUS_PREPARED,
        resume_name="java",
        language="en",
        error_message=None,
        resume_message_id=resume_message_id,
        cover_letter_message_id=cover_letter_message_id,
        cover_letter_txt_message_id=cover_letter_txt_message_id,
        cover_letter_pdf_message_id=cover_letter_pdf_message_id,
    )


def test_cleanup_success_clears_each_message_id(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(storage)
    logs: list[str] = []
    client = _RecordingClient()

    done = cli_module._cleanup_aux_messages(
        storage=storage,
        client=client,  # type: ignore[arg-type]
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        log=logs.append,
    )

    assert done is True
    assert client.deleted == [11, 12, 13, 14]
    prep = storage.get_preparation("linkedin-email", "1")
    assert prep is not None
    assert prep.resume_message_id is None
    assert prep.cover_letter_message_id is None
    assert prep.cover_letter_txt_message_id is None
    assert prep.cover_letter_pdf_message_id is None
    assert any("Cleanup START" in line for line in logs)
    assert any("Cleanup delete resume" in line and "result=success" in line for line in logs)
    assert any("Cleanup delete cover" in line and "result=success" in line for line in logs)
    assert any("Cleanup FINISH" in line and "pending=0" in line for line in logs)


def test_partial_cleanup_keeps_failed_message_id(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(storage)
    client = _RecordingClient(
        errors={12: cli_module.TelegramRequestError("timeout", http_status=500)},
    )

    done = cli_module._cleanup_aux_messages(
        storage=storage,
        client=client,  # type: ignore[arg-type]
        source="linkedin-email",
        external_id="1",
        chat_id="123",
    )

    assert done is False
    prep = storage.get_preparation("linkedin-email", "1")
    assert prep is not None
    assert prep.resume_message_id is None
    assert prep.cover_letter_message_id == 12
    assert prep.cover_letter_txt_message_id is None
    assert prep.cover_letter_pdf_message_id is None


def test_transient_failure_retries_and_then_clears(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(
        storage,
        cover_letter_message_id=None,
        cover_letter_txt_message_id=None,
        cover_letter_pdf_message_id=None,
    )
    attempts = {"count": 0}

    class FlakyClient:
        def delete_message(self, *, chat_id: str, message_id: int) -> None:
            _ = chat_id, message_id
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise cli_module.TelegramRequestError("temporarily unavailable", http_status=429)

    client = FlakyClient()
    first = cli_module._cleanup_aux_messages(
        storage=storage,
        client=client,  # type: ignore[arg-type]
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        attempt=1,
    )
    assert first is False
    assert storage.get_preparation("linkedin-email", "1").resume_message_id == 11

    second = cli_module._cleanup_aux_messages(
        storage=storage,
        client=client,  # type: ignore[arg-type]
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        attempt=2,
    )
    assert second is True
    assert storage.get_preparation("linkedin-email", "1").resume_message_id is None
    assert attempts["count"] == 2


def test_message_not_found_is_treated_as_success(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(
        storage,
        cover_letter_message_id=None,
        cover_letter_txt_message_id=None,
        cover_letter_pdf_message_id=None,
    )
    logs: list[str] = []
    client = _RecordingClient(
        errors={
            11: cli_module.TelegramRequestError(
                "Telegram API request failed.",
                method="deleteMessage",
                http_status=400,
                description="Bad Request: message to delete not found",
            )
        }
    )

    done = cli_module._cleanup_aux_messages(
        storage=storage,
        client=client,  # type: ignore[arg-type]
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        log=logs.append,
    )

    assert done is True
    assert storage.get_preparation("linkedin-email", "1").resume_message_id is None
    assert any("result=permanent success" in line for line in logs)


def test_scheduler_rejects_duplicate_enqueue() -> None:
    scheduler = cli_module._CleanupScheduler()
    first = scheduler.enqueue(source="linkedin-email", external_id="1", chat_id="123")
    second = scheduler.enqueue(source="linkedin-email", external_id="1", chat_id="123")
    assert first is True
    assert second is False
    assert scheduler.pending_count() == 1
    job = scheduler.take_due()
    assert job is not None
    assert scheduler.pending_count() == 0
    still_tracked = scheduler.enqueue(source="linkedin-email", external_id="1", chat_id="123")
    assert still_tracked is False
    scheduler.release(job)
    after_release = scheduler.enqueue(source="linkedin-email", external_id="1", chat_id="123")
    assert after_release is True


def test_sweep_recovers_pending_cleanup_after_restart(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(storage)
    scheduler = cli_module._CleanupScheduler()
    queued = cli_module._sweep_pending_aux_cleanups(storage=storage, scheduler=scheduler)
    assert queued == 1
    assert scheduler.is_tracked("linkedin-email", "1")
    skipped = cli_module._sweep_pending_aux_cleanups(storage=storage, scheduler=scheduler)
    assert skipped == 0


def test_cleanup_worker_processes_queued_job(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_applied_with_aux(storage)
    scheduler = cli_module._CleanupScheduler()
    stop_event = threading.Event()

    class StoppingClient(_RecordingClient):
        def delete_message(self, *, chat_id: str, message_id: int) -> None:
            super().delete_message(chat_id=chat_id, message_id=message_id)
            if len(self.deleted) >= 4:
                stop_event.set()
                scheduler.wakeup()

    client = StoppingClient()
    scheduler.enqueue(source="linkedin-email", external_id="1", chat_id="123")
    cli_module._cleanup_worker_loop(
        telegram_client=client,  # type: ignore[arg-type]
        scheduler=scheduler,
        stop_event=stop_event,
        storage=storage,
        sweep_interval_seconds=60.0,
        log=lambda message: None,
    )
    prep = storage.get_preparation("linkedin-email", "1")
    assert prep is not None
    assert prep.resume_message_id is None
    assert prep.cover_letter_message_id is None
    assert client.deleted == [11, 12, 13, 14]
