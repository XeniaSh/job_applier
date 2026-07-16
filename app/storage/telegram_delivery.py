from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.storage.seen_jobs import DEFAULT_DB_PATH
from app.telegram.models import TelegramDeliveryRecord, TelegramResumeCacheRecord

STATUS_SENT = "SENT"
STATUS_SKIPPED = "SKIPPED"
STATUS_PREPARE_REQUESTED = "PREPARE_REQUESTED"
STATUS_FAILED = "FAILED"
STATUS_PREPARED = "PREPARED"
STATUS_PREPARATION_FAILED = "PREPARATION_FAILED"
STATUS_APPLIED = "APPLIED"
ALLOWED_STATUSES = {
    STATUS_SENT,
    STATUS_SKIPPED,
    STATUS_PREPARE_REQUESTED,
    STATUS_FAILED,
    STATUS_PREPARED,
    STATUS_PREPARATION_FAILED,
    STATUS_APPLIED,
}


class TelegramDeliveryStorage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def was_sent(self, source: str, external_id: str, chat_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                select 1 from telegram_deliveries
                where source = ? and external_id = ? and chat_id = ? and status = ?
                """,
                (source, external_id, str(chat_id), STATUS_SENT),
            ).fetchone()
        return row is not None

    def save_sent(
        self,
        *,
        source: str,
        external_id: str,
        chat_id: str,
        message_id: int,
    ) -> None:
        self._save(
            source=source,
            external_id=external_id,
            chat_id=chat_id,
            message_id=message_id,
            status=STATUS_SENT,
        )

    def update_status(self, *, source: str, external_id: str, chat_id: str, status: str) -> None:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._connect() as conn:
            conn.execute(
                """
                update telegram_deliveries
                set status = ?
                where source = ? and external_id = ? and chat_id = ?
                """,
                (status, source, external_id, str(chat_id)),
            )
            conn.commit()

    def list_deliveries(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[TelegramDeliveryRecord]:
        safe_limit = int(limit)
        if safe_limit < 1:
            safe_limit = 1
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"Unknown status: {status}")
            clauses.append("status = ?")
            params.append(status)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        where_sql = f" where {' and '.join(clauses)}" if clauses else ""
        sql = (
            "select source, external_id, chat_id, message_id, sent_at, status "
            f"from telegram_deliveries{where_sql} "
            "order by sent_at desc, external_id desc "
            "limit ?"
        )
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            TelegramDeliveryRecord(
                source=str(row[0]),
                external_id=str(row[1]),
                chat_id=str(row[2]),
                message_id=int(row[3]),
                sent_at=str(row[4]),
                status=str(row[5]),
            )
            for row in rows
        ]

    def get_delivery(self, source: str, external_id: str) -> TelegramDeliveryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select source, external_id, chat_id, message_id, sent_at, status
                from telegram_deliveries
                where source = ? and external_id = ?
                order by sent_at desc
                limit 1
                """,
                (source, external_id),
            ).fetchone()
        if row is None:
            return None
        return TelegramDeliveryRecord(
            source=str(row[0]),
            external_id=str(row[1]),
            chat_id=str(row[2]),
            message_id=int(row[3]),
            sent_at=str(row[4]),
            status=str(row[5]),
        )

    def set_status(self, source: str, external_id: str, status: str) -> None:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update telegram_deliveries
                set status = ?
                where source = ? and external_id = ?
                """,
                (status, source, external_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Delivery not found: {source}:{external_id}")

    def delete_delivery(self, source: str, external_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                delete from telegram_deliveries
                where source = ? and external_id = ?
                """,
                (source, external_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def list_by_status(self, *, chat_id: str, status: str, limit: int) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select source, external_id
                from telegram_deliveries
                where chat_id = ? and status = ?
                order by sent_at asc
                limit ?
                """,
                (str(chat_id), status, int(limit)),
            ).fetchall()
        return [(str(source), str(external_id)) for source, external_id in rows]

    def get_resume_cache(self, resume_name: str) -> TelegramResumeCacheRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select resume_name, file_path, file_mtime_ns, file_size, telegram_file_id, telegram_file_unique_id, cached_at
                from telegram_resume_cache
                where resume_name = ?
                """,
                (resume_name,),
            ).fetchone()
        if row is None:
            return None
        return TelegramResumeCacheRecord(
            resume_name=str(row[0]),
            file_path=str(row[1]),
            file_mtime_ns=int(row[2]),
            file_size=int(row[3]),
            telegram_file_id=str(row[4]),
            telegram_file_unique_id=str(row[5]) if row[5] is not None else None,
            cached_at=str(row[6]),
        )

    def save_resume_cache(
        self,
        *,
        resume_name: str,
        file_path: str,
        file_mtime_ns: int,
        file_size: int,
        telegram_file_id: str,
        telegram_file_unique_id: str | None,
    ) -> None:
        cached_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into telegram_resume_cache (
                    resume_name, file_path, file_mtime_ns, file_size, telegram_file_id, telegram_file_unique_id, cached_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(resume_name) do update set
                    file_path = excluded.file_path,
                    file_mtime_ns = excluded.file_mtime_ns,
                    file_size = excluded.file_size,
                    telegram_file_id = excluded.telegram_file_id,
                    telegram_file_unique_id = excluded.telegram_file_unique_id,
                    cached_at = excluded.cached_at
                """,
                (
                    resume_name,
                    file_path,
                    int(file_mtime_ns),
                    int(file_size),
                    telegram_file_id,
                    telegram_file_unique_id,
                    cached_at,
                ),
            )
            conn.commit()

    def delete_resume_cache(self, resume_name: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "delete from telegram_resume_cache where resume_name = ?",
                (resume_name,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def list_resume_cache(self) -> list[TelegramResumeCacheRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select resume_name, file_path, file_mtime_ns, file_size, telegram_file_id, telegram_file_unique_id, cached_at
                from telegram_resume_cache
                order by resume_name asc
                """
            ).fetchall()
        return [
            TelegramResumeCacheRecord(
                resume_name=str(row[0]),
                file_path=str(row[1]),
                file_mtime_ns=int(row[2]),
                file_size=int(row[3]),
                telegram_file_id=str(row[4]),
                telegram_file_unique_id=str(row[5]) if row[5] is not None else None,
                cached_at=str(row[6]),
            )
            for row in rows
        ]

    def save_preparation(
        self,
        *,
        source: str,
        external_id: str,
        status: str,
        resume_name: str | None,
        language: str | None,
        error_message: str | None,
    ) -> None:
        prepared_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into application_preparations (
                    source, external_id, prepared_at, resume_name, language, status, error_message
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    external_id,
                    prepared_at,
                    resume_name,
                    language,
                    status,
                    error_message,
                ),
            )
            conn.commit()

    def get_message_ref(self, *, source: str, external_id: str, chat_id: str) -> tuple[str, int] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select chat_id, message_id
                from telegram_deliveries
                where source = ? and external_id = ? and chat_id = ?
                """,
                (source, external_id, str(chat_id)),
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), int(row[1])

    def get_state(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("select value from telegram_state where key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row[0])

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into telegram_state (key, value) values (?, ?)
                on conflict(key) do update set value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def _save(
        self,
        *,
        source: str,
        external_id: str,
        chat_id: str,
        message_id: int,
        status: str,
    ) -> None:
        sent_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into telegram_deliveries (
                    source, external_id, chat_id, message_id, sent_at, status
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (source, external_id, str(chat_id), int(message_id), sent_at, status),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists telegram_deliveries (
                    source text not null,
                    external_id text not null,
                    chat_id text not null,
                    message_id integer not null,
                    sent_at text not null,
                    status text not null,
                    primary key (source, external_id, chat_id)
                )
                """
            )
            conn.execute(
                """
                create table if not exists telegram_state (
                    key text primary key,
                    value text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists application_preparations (
                    source text not null,
                    external_id text not null,
                    prepared_at text,
                    resume_name text,
                    language text,
                    status text not null,
                    error_message text,
                    primary key (source, external_id)
                )
                """
            )
            conn.execute(
                """
                create table if not exists telegram_resume_cache (
                    resume_name text primary key,
                    file_path text not null,
                    file_mtime_ns integer not null,
                    file_size integer not null,
                    telegram_file_id text not null,
                    telegram_file_unique_id text,
                    cached_at text not null
                )
                """
            )
            conn.commit()
