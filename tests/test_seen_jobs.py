from pathlib import Path

from app.storage.seen_jobs import SeenJobsStorage


def test_seen_jobs_persisted_in_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    storage = SeenJobsStorage(db_path=db_path)

    assert not storage.is_seen("hh", "123")
    storage.mark_seen("hh", "123")
    assert storage.is_seen("hh", "123")

    reloaded = SeenJobsStorage(db_path=db_path)
    assert reloaded.is_seen("hh", "123")
