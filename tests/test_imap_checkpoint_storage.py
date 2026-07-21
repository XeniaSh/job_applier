from pathlib import Path

from app.storage.imap_checkpoint import ImapCheckpointStorage


def test_checkpoints_are_isolated_by_account_and_folder(tmp_path: Path) -> None:
    storage = ImapCheckpointStorage(db_path=tmp_path / "jobs.db")
    storage.save(
        source="linkedin-email",
        account_key="sha256:a",
        folder="INBOX",
        last_uid=10,
        uidvalidity="1",
    )
    storage.save(
        source="linkedin-email",
        account_key="sha256:a",
        folder="Alerts",
        last_uid=20,
        uidvalidity="1",
    )
    storage.save(
        source="linkedin-email",
        account_key="sha256:b",
        folder="INBOX",
        last_uid=30,
        uidvalidity="2",
    )

    inbox_a = storage.get(source="linkedin-email", account_key="sha256:a", folder="INBOX")
    alerts_a = storage.get(source="linkedin-email", account_key="sha256:a", folder="Alerts")
    inbox_b = storage.get(source="linkedin-email", account_key="sha256:b", folder="INBOX")

    assert inbox_a is not None and inbox_a.last_uid == 10
    assert alerts_a is not None and alerts_a.last_uid == 20
    assert inbox_b is not None and inbox_b.last_uid == 30
