from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.storage.seen_jobs import DEFAULT_DB_PATH


@dataclass(frozen=True)
class ImapCheckpoint:
    source: str
    account_key: str
    folder: str
    last_uid: int
    uidvalidity: str | None
    updated_at: str


class ImapCheckpointStorage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get(self, *, source: str, account_key: str, folder: str) -> ImapCheckpoint | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select source, account_key, folder, last_uid, uidvalidity, updated_at
                from imap_checkpoints
                where source = ? and account_key = ? and folder = ?
                """,
                (source, account_key, folder),
            ).fetchone()
        if row is None:
            return None
        return ImapCheckpoint(
            source=str(row[0]),
            account_key=str(row[1]),
            folder=str(row[2]),
            last_uid=int(row[3]),
            uidvalidity=str(row[4]) if row[4] is not None else None,
            updated_at=str(row[5]),
        )

    def save(
        self,
        *,
        source: str,
        account_key: str,
        folder: str,
        last_uid: int,
        uidvalidity: str | None,
    ) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into imap_checkpoints (source, account_key, folder, last_uid, uidvalidity, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(source, account_key, folder) do update set
                    last_uid = excluded.last_uid,
                    uidvalidity = excluded.uidvalidity,
                    updated_at = excluded.updated_at
                """,
                (source, account_key, folder, int(last_uid), uidvalidity, updated_at),
            )
            conn.commit()

    def reset(self, *, source: str, account_key: str, folder: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                delete from imap_checkpoints
                where source = ? and account_key = ? and folder = ?
                """,
                (source, account_key, folder),
            )
            conn.commit()
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists imap_checkpoints (
                    source text not null,
                    account_key text not null,
                    folder text not null,
                    last_uid integer not null,
                    uidvalidity text,
                    updated_at text not null,
                    primary key (source, account_key, folder)
                )
                """
            )
            conn.commit()
