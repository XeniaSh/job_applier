from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path("data/jobs.db")


class SeenJobsStorage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def is_seen(self, source: str, external_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "select 1 from seen_jobs where source = ? and external_id = ?",
                (source, external_id),
            ).fetchone()
        return row is not None

    def mark_seen(self, source: str, external_id: str) -> None:
        seen_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into seen_jobs (source, external_id, seen_at)
                values (?, ?, ?)
                """,
                (source, external_id, seen_at),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists seen_jobs (
                    source text not null,
                    external_id text not null,
                    seen_at text not null,
                    primary key (source, external_id)
                )
                """
            )
            conn.commit()
