from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.storage.telegram_delivery import TelegramDeliveryStorage


def test_first_processing_creates_history_and_is_idempotent(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="1001",
        title="Java Backend Engineer",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/1001/",
        decision="POTENTIAL_MATCH",
        recommended_resume="java-backend",
    )
    rows = storage.list_application_history()
    assert len(rows) == 1
    first_seen = rows[0].first_seen_at

    storage.upsert_application_history(
        source="linkedin-email",
        external_id="1001",
        title="Changed title must not overwrite",
        company="Another",
        location="Another",
        url="https://www.linkedin.com/jobs/view/1001/",
        decision="STRONG_MATCH",
        recommended_resume="kotlin-backend",
    )
    rows_again = storage.list_application_history()
    assert len(rows_again) == 1
    assert rows_again[0].first_seen_at == first_seen
    assert rows_again[0].title == "Java Backend Engineer"


def test_lifecycle_timestamps_and_no_overwrite(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="1002",
        title="Role",
        company="A",
        location=None,
        url=None,
        decision=None,
        recommended_resume=None,
    )
    storage.mark_history_status(source="linkedin-email", external_id="1002", status="SENT", timestamp_field="sent_at")
    first_sent = storage.list_application_history()[0].sent_at
    storage.mark_history_status(source="linkedin-email", external_id="1002", status="SENT", timestamp_field="sent_at")
    second_sent = storage.list_application_history()[0].sent_at
    assert first_sent == second_sent

    storage.mark_history_status(
        source="linkedin-email",
        external_id="1002",
        status="PREPARED",
        timestamp_field="prepared_at",
    )
    storage.mark_history_status(
        source="linkedin-email",
        external_id="1002",
        status="APPLIED",
        timestamp_field="applied_at",
    )
    storage.mark_history_status(
        source="linkedin-email",
        external_id="1002",
        status="SKIPPED",
        timestamp_field="skipped_at",
    )
    row = storage.list_application_history()[0]
    assert row.sent_at is not None
    assert row.prepared_at is not None
    assert row.applied_at is not None
    assert row.skipped_at is not None


def test_history_filtering_and_stats(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = TelegramDeliveryStorage(db_path=db_path)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="1",
        title="Role A",
        company="Company A",
        location=None,
        url=None,
        decision="POTENTIAL_MATCH",
        recommended_resume="java-backend",
    )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="2",
        title="Role B",
        company="Company A",
        location=None,
        url=None,
        decision="POTENTIAL_MATCH",
        recommended_resume="java-backend",
    )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="3",
        title="Role C",
        company="Company B",
        location=None,
        url=None,
        decision="STRONG_MATCH",
        recommended_resume="kotlin-backend",
    )
    for external_id in ("1", "2", "3"):
        storage.mark_history_status(
            source="linkedin-email",
            external_id=external_id,
            status="SENT",
            timestamp_field="sent_at",
        )
    storage.mark_history_status(source="linkedin-email", external_id="1", status="PREPARED", timestamp_field="prepared_at")
    storage.mark_history_status(source="linkedin-email", external_id="2", status="PREPARED", timestamp_field="prepared_at")
    storage.mark_history_status(source="linkedin-email", external_id="1", status="APPLIED", timestamp_field="applied_at")
    storage.mark_history_status(source="linkedin-email", external_id="3", status="SKIPPED", timestamp_field="skipped_at")

    filtered = storage.list_application_history(status="APPLIED", limit=10)
    assert len(filtered) == 1
    assert filtered[0].external_id == "1"
    filtered_company = storage.list_application_history(company="company a", limit=10)
    assert len(filtered_company) == 2

    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    stats_output = CliRunner().invoke(cli_module.app, ["application-stats", "--days", "30"])
    assert stats_output.exit_code == 0
    assert "Found: 3" in stats_output.output
    assert "Sent to Telegram: 3" in stats_output.output
    assert "Prepared: 2" in stats_output.output
    assert "Applied: 1" in stats_output.output
    assert "Skipped: 1" in stats_output.output
    assert "Sent -> Prepared: 66.7%" in stats_output.output
    assert "Prepared -> Applied: 50.0%" in stats_output.output
    assert "Company A: 2" in stats_output.output
    assert "java-backend: 2" in stats_output.output

    history_output = CliRunner().invoke(
        cli_module.app,
        ["application-history", "--status", "APPLIED", "--limit", "10"],
    )
    assert history_output.exit_code == 0
    assert "APPLIED" in history_output.output
    assert "Role A" in history_output.output


def test_empty_stats_and_empty_history(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr(cli_module, "TelegramDeliveryStorage", lambda: TelegramDeliveryStorage(db_path=db_path))
    stats = CliRunner().invoke(cli_module.app, ["application-stats", "--days", "30"])
    assert stats.exit_code == 0
    assert "Sent -> Prepared: 0.0%" in stats.output
    assert "Prepared -> Applied: 0.0%" in stats.output

    history = CliRunner().invoke(cli_module.app, ["application-history"])
    assert history.exit_code == 0
    assert "Application history is empty." in history.output


def test_callbacks_update_delivery_and_history(monkeypatch, tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.save_sent(source="linkedin-email", external_id="777", chat_id="123", message_id=42)
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="777",
        title="Role",
        company="ACME",
        location=None,
        url=None,
        decision="POTENTIAL_MATCH",
        recommended_resume="java-backend",
    )

    calls = {"answers": []}

    class FakeClient:
        def answer_callback_query(self, callback_query_id, text=None):
            calls["answers"].append((callback_query_id, text))

        def edit_message_reply_markup(self, chat_id, message_id, buttons):
            _ = chat_id, message_id, buttons

    update_applied = {
        "callback_query": {
            "id": "cb-applied",
            "data": "applied:li:777",
            "message": {
                "chat": {"id": "123"},
                "message_id": 10,
                "reply_markup": {"inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/777/"}]]},
            },
        }
    }
    cli_module._process_callback_update(
        update=update_applied,
        client=FakeClient(),
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-applied", "Отклик отмечен как отправленный") in calls["answers"]
    row = storage.list_application_history(status="APPLIED", limit=10)
    assert len(row) == 1
    assert row[0].applied_at is not None

    update_skip = {
        "callback_query": {
            "id": "cb-skip",
            "data": "skip:li:777",
            "message": {
                "chat": {"id": "123"},
                "message_id": 11,
                "reply_markup": {"inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/777/"}]]},
            },
        }
    }
    cli_module._process_callback_update(
        update=update_skip,
        client=FakeClient(),
        storage=storage,
        configured_chat_id="123",
    )
    assert ("cb-skip", "Вакансия пропущена") in calls["answers"]
    row_skipped = storage.list_application_history(status="SKIPPED", limit=10)
    assert len(row_skipped) == 1
    assert row_skipped[0].skipped_at is not None


def test_no_full_descriptions_or_cover_letters_stored(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.upsert_application_history(
        source="linkedin-email",
        external_id="z1",
        title="Role",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/z1/",
        decision="POTENTIAL_MATCH",
        recommended_resume="java-backend",
    )
    with storage._connect() as conn:  # noqa: SLF001 - test introspection
        columns = [row[1] for row in conn.execute("pragma table_info(application_history)").fetchall()]
    assert "cover_letter" not in columns
    assert "vacancy_description" not in columns
    assert "candidate_profile" not in columns
