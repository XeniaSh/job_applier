from pathlib import Path

import pytest

from app.storage.telegram_delivery import (
    STATUS_FAILED,
    STATUS_APPLIED,
    STATUS_PREPARING,
    STATUS_PREPARED,
    STATUS_PREPARE_REQUESTED,
    STATUS_PREPARATION_FAILED,
    STATUS_SENT,
    STATUS_SKIPPED,
    TelegramDeliveryStorage,
)


def test_delivery_persist_and_status_update(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = TelegramDeliveryStorage(db_path=db_path)

    assert storage.was_sent("linkedin-email", "1", "123") is False
    storage.save_sent(source="linkedin-email", external_id="1", chat_id="123", message_id=42)
    assert storage.was_sent("linkedin-email", "1", "123") is True
    assert storage.get_message_ref(source="linkedin-email", external_id="1", chat_id="123") == ("123", 42)

    storage.update_status(
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        status=STATUS_SKIPPED,
    )
    assert storage.was_sent("linkedin-email", "1", "123") is False
    storage.update_status(
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        status=STATUS_PREPARE_REQUESTED,
    )

    storage.save_sent(source="linkedin-email", external_id="1", chat_id="123", message_id=42)
    storage.update_status(
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        status=STATUS_SENT,
    )
    assert storage.was_sent("linkedin-email", "1", "123") is True
    storage.update_status(
        source="linkedin-email",
        external_id="1",
        chat_id="123",
        status=STATUS_APPLIED,
    )


def test_telegram_state_persistence(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    assert storage.get_state("telegram_update_offset") is None
    storage.set_state("telegram_update_offset", "100")
    assert storage.get_state("telegram_update_offset") == "100"


def test_list_by_status_and_preparation_metadata(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="5", chat_id="123", message_id=1)
    storage.update_status(
        source="linkedin-email",
        external_id="5",
        chat_id="123",
        status=STATUS_PREPARE_REQUESTED,
    )
    storage.save_sent(source="linkedin-email", external_id="6", chat_id="123", message_id=2)
    storage.update_status(
        source="linkedin-email",
        external_id="6",
        chat_id="123",
        status=STATUS_PREPARATION_FAILED,
    )
    rows = storage.list_by_status(chat_id="123", status=STATUS_PREPARE_REQUESTED, limit=10)
    assert rows == [("linkedin-email", "5")]

    storage.save_preparation(
        source="linkedin-email",
        external_id="5",
        status=STATUS_PREPARED,
        resume_name="java-backend",
        language="en",
        error_message=None,
    )
    storage.set_preparation_aux_message_id(
        source="linkedin-email",
        external_id="5",
        resume_message_id=321,
        cover_letter_message_id=322,
    )
    prep = storage.get_preparation("linkedin-email", "5")
    assert prep is not None
    assert prep.resume_message_id == 321
    assert prep.cover_letter_message_id == 322
    storage.clear_preparation_aux_message_ids(source="linkedin-email", external_id="5")
    cleared = storage.get_preparation("linkedin-email", "5")
    assert cleared is not None
    assert cleared.resume_message_id is None
    assert cleared.cover_letter_message_id is None


def test_list_deliveries_filters_order_and_limit(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="100", chat_id="123", message_id=1)
    storage.save_sent(source="linkedin-email", external_id="101", chat_id="123", message_id=2)
    storage.save_sent(source="other-source", external_id="102", chat_id="123", message_id=3)
    storage.set_status("linkedin-email", "100", STATUS_PREPARE_REQUESTED)
    storage.set_status("linkedin-email", "101", STATUS_PREPARED)
    storage.set_status("other-source", "102", STATUS_FAILED)

    all_rows = storage.list_deliveries()
    assert [row.external_id for row in all_rows] == ["102", "101", "100"]

    prepared_rows = storage.list_deliveries(status=STATUS_PREPARED)
    assert [row.external_id for row in prepared_rows] == ["101"]

    source_rows = storage.list_deliveries(source="linkedin-email")
    assert [row.external_id for row in source_rows] == ["101", "100"]

    limited_rows = storage.list_deliveries(limit=1)
    assert [row.external_id for row in limited_rows] == ["102"]


def test_get_set_and_delete_delivery(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="200", chat_id="123", message_id=77)

    record = storage.get_delivery("linkedin-email", "200")
    assert record is not None
    assert record.message_id == 77

    storage.set_status("linkedin-email", "200", STATUS_PREPARE_REQUESTED)
    updated = storage.get_delivery("linkedin-email", "200")
    assert updated is not None
    assert updated.status == STATUS_PREPARE_REQUESTED

    assert storage.delete_delivery("linkedin-email", "200") is True
    assert storage.get_delivery("linkedin-email", "200") is None
    assert storage.delete_delivery("linkedin-email", "200") is False


def test_sql_parameters_are_safe_and_other_tables_unchanged(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    source = "linkedin-email'; DROP TABLE telegram_state; --"
    external_id = "555'; DROP TABLE application_preparations; --"
    storage.save_sent(source=source, external_id=external_id, chat_id="123", message_id=99)
    storage.save_preparation(
        source="linkedin-email",
        external_id="x1",
        status=STATUS_PREPARED,
        resume_name="java-backend",
        language="en",
        error_message=None,
    )
    storage.set_state("telegram_update_offset", "500")

    storage.set_status(source, external_id, STATUS_SKIPPED)
    record = storage.get_delivery(source, external_id)
    assert record is not None
    assert record.status == STATUS_SKIPPED

    # Ensure unrelated tables are untouched.
    assert storage.get_state("telegram_update_offset") == "500"
    rows = storage.list_by_status(chat_id="123", status=STATUS_SKIPPED, limit=10)
    assert (source, external_id) in rows


def test_set_status_validation_and_missing_record(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    with pytest.raises(ValueError):
        storage.set_status("linkedin-email", "1", "WRONG_STATUS")
    with pytest.raises(KeyError):
        storage.set_status("linkedin-email", "404", STATUS_PREPARED)


def test_claim_for_preparation_is_atomic(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="777", chat_id="123", message_id=1)
    storage.update_status(
        source="linkedin-email",
        external_id="777",
        chat_id="123",
        status=STATUS_PREPARE_REQUESTED,
    )
    assert storage.claim_for_preparation(source="linkedin-email", external_id="777", chat_id="123") is True
    assert storage.claim_for_preparation(source="linkedin-email", external_id="777", chat_id="123") is False
    row = storage.get_delivery("linkedin-email", "777")
    assert row is not None
    assert row.status == STATUS_PREPARING


def test_resume_cache_crud(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    assert storage.get_resume_cache("java-backend") is None

    storage.save_resume_cache(
        resume_name="java-backend",
        file_path=str(tmp_path / "resumes" / "java-backend.pdf"),
        file_mtime_ns=123,
        file_size=456,
        telegram_file_id="FILE_ID_1234567890",
        telegram_file_unique_id="UNIQ_123",
    )
    record = storage.get_resume_cache("java-backend")
    assert record is not None
    assert record.file_size == 456
    assert record.telegram_file_id == "FILE_ID_1234567890"

    rows = storage.list_resume_cache()
    assert [row.resume_name for row in rows] == ["java-backend"]

    assert storage.delete_resume_cache("java-backend") is True
    assert storage.delete_resume_cache("java-backend") is False
    assert storage.get_resume_cache("java-backend") is None
