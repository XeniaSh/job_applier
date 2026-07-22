from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.storage.seen_jobs import DEFAULT_DB_PATH
from app.telegram.models import (
    ApplicationHistoryRecord,
    ApplicationPreparationRecord,
    TelegramDeliveryRecord,
    TelegramResumeCacheRecord,
)

STATUS_SENT = "SENT"
STATUS_SKIPPED = "SKIPPED"
STATUS_PREPARE_REQUESTED = "PREPARE_REQUESTED"
STATUS_PREPARING = "PREPARING"
STATUS_FAILED = "FAILED"
STATUS_PREPARED = "PREPARED"
STATUS_PREPARATION_FAILED = "PREPARATION_FAILED"
STATUS_APPLIED = "APPLIED"
ALLOWED_STATUSES = {
    STATUS_SENT,
    STATUS_SKIPPED,
    STATUS_PREPARE_REQUESTED,
    STATUS_PREPARING,
    STATUS_FAILED,
    STATUS_PREPARED,
    STATUS_PREPARATION_FAILED,
    STATUS_APPLIED,
}
HISTORY_STATUS_FOUND = "FOUND"


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

    def count_by_status(self, *, chat_id: str, status: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                select count(*)
                from telegram_deliveries
                where chat_id = ? and status = ?
                """,
                (str(chat_id), status),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def claim_for_preparation(self, *, source: str, external_id: str, chat_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update telegram_deliveries
                set status = ?
                where source = ? and external_id = ? and chat_id = ? and status = ?
                """,
                (
                    STATUS_PREPARING,
                    source,
                    external_id,
                    str(chat_id),
                    STATUS_PREPARE_REQUESTED,
                ),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    """
                    insert into application_preparations (
                        source, external_id, prepared_at, resume_name, language, status, error_message, cover_letter, vacancy_title, vacancy_company, vacancy_url, resume_message_id, cover_letter_message_id
                    )
                    values (?, ?, ?, null, null, ?, null, null, null, null, null, null, null)
                    on conflict(source, external_id) do update set
                        prepared_at = excluded.prepared_at,
                        status = excluded.status,
                        error_message = null
                    """,
                    (
                        source,
                        external_id,
                        now,
                        STATUS_PREPARING,
                    ),
                )
            conn.commit()
        return cursor.rowcount > 0

    def recover_abandoned_preparing(
        self,
        *,
        worker_alive: bool,
        timeout_seconds: int,
    ) -> list[tuple[str, str]]:
        safe_timeout = max(1, int(timeout_seconds))
        now = datetime.now(timezone.utc)
        recovered: list[tuple[str, str]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                select d.source, d.external_id, d.chat_id, coalesce(p.prepared_at, d.sent_at) as marker_ts
                from telegram_deliveries d
                left join application_preparations p
                    on p.source = d.source and p.external_id = d.external_id
                where d.status = ?
                """,
                (STATUS_PREPARING,),
            ).fetchall()
            for source, external_id, chat_id, marker_ts in rows:
                should_recover = not worker_alive
                if worker_alive:
                    parsed = _parse_iso_datetime(marker_ts)
                    if parsed is None:
                        should_recover = True
                    else:
                        should_recover = (now - parsed) >= timedelta(seconds=safe_timeout)
                if not should_recover:
                    continue
                cursor = conn.execute(
                    """
                    update telegram_deliveries
                    set status = ?
                    where source = ? and external_id = ? and chat_id = ? and status = ?
                    """,
                    (
                        STATUS_PREPARE_REQUESTED,
                        str(source),
                        str(external_id),
                        str(chat_id),
                        STATUS_PREPARING,
                    ),
                )
                if cursor.rowcount == 0:
                    continue
                conn.execute(
                    """
                    update application_preparations
                    set status = ?
                    where source = ? and external_id = ? and status = ?
                    """,
                    (
                        STATUS_PREPARE_REQUESTED,
                        str(source),
                        str(external_id),
                        STATUS_PREPARING,
                    ),
                )
                conn.execute(
                    """
                    insert into application_history (
                        source, external_id, first_seen_at, current_status
                    )
                    values (?, ?, ?, ?)
                    on conflict(source, external_id) do update set
                        current_status = excluded.current_status
                    """,
                    (
                        str(source),
                        str(external_id),
                        now.isoformat(),
                        STATUS_PREPARE_REQUESTED,
                    ),
                )
                recovered.append((str(source), str(external_id)))
            conn.commit()
        return recovered

    def upsert_application_history(
        self,
        *,
        source: str,
        external_id: str,
        title: str | None,
        company: str | None,
        location: str | None,
        url: str | None,
        decision: str | None,
        decision_reason: str | None,
        recommended_resume: str | None,
    ) -> None:
        first_seen_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into application_history (
                    source, external_id, title, company, location, url, decision, decision_reason, recommended_resume, first_seen_at, current_status
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source, external_id) do update set
                    title = coalesce(application_history.title, excluded.title),
                    company = coalesce(application_history.company, excluded.company),
                    location = coalesce(application_history.location, excluded.location),
                    url = coalesce(application_history.url, excluded.url),
                    decision = coalesce(application_history.decision, excluded.decision),
                    decision_reason = coalesce(application_history.decision_reason, excluded.decision_reason),
                    recommended_resume = coalesce(application_history.recommended_resume, excluded.recommended_resume)
                """,
                (
                    source,
                    external_id,
                    title,
                    company,
                    location,
                    url,
                    decision,
                    decision_reason,
                    recommended_resume,
                    first_seen_at,
                    HISTORY_STATUS_FOUND,
                ),
            )
            conn.commit()

    def mark_history_status(
        self,
        *,
        source: str,
        external_id: str,
        status: str,
        timestamp_field: str | None = None,
    ) -> None:
        timestamp_fields = {
            "sent_at",
            "prepared_at",
            "applied_at",
            "skipped_at",
        }
        if timestamp_field is not None and timestamp_field not in timestamp_fields:
            raise ValueError(f"Unsupported history timestamp field: {timestamp_field}")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into application_history (
                    source, external_id, first_seen_at, current_status
                )
                values (?, ?, ?, ?)
                on conflict(source, external_id) do nothing
                """,
                (source, external_id, now, status),
            )
            if timestamp_field is None:
                conn.execute(
                    """
                    update application_history
                    set current_status = ?
                    where source = ? and external_id = ?
                    """,
                    (status, source, external_id),
                )
            else:
                conn.execute(
                    f"""
                    update application_history
                    set current_status = ?, {timestamp_field} = coalesce({timestamp_field}, ?)
                    where source = ? and external_id = ?
                    """,
                    (status, now, source, external_id),
                )
            conn.commit()

    def update_delivery_and_history(
        self,
        *,
        source: str,
        external_id: str,
        chat_id: str,
        delivery_status: str,
        history_status: str,
        timestamp_field: str | None = None,
    ) -> None:
        if delivery_status not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown status: {delivery_status}")
        timestamp_fields = {"sent_at", "prepared_at", "applied_at", "skipped_at"}
        if timestamp_field is not None and timestamp_field not in timestamp_fields:
            raise ValueError(f"Unsupported history timestamp field: {timestamp_field}")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update telegram_deliveries
                set status = ?
                where source = ? and external_id = ? and chat_id = ?
                """,
                (delivery_status, source, external_id, str(chat_id)),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Delivery not found: {source}:{external_id}:{chat_id}")
            conn.execute(
                """
                insert into application_history (
                    source, external_id, first_seen_at, current_status
                )
                values (?, ?, ?, ?)
                on conflict(source, external_id) do nothing
                """,
                (source, external_id, now, history_status),
            )
            if timestamp_field is None:
                conn.execute(
                    """
                    update application_history
                    set current_status = ?
                    where source = ? and external_id = ?
                    """,
                    (history_status, source, external_id),
                )
            else:
                conn.execute(
                    f"""
                    update application_history
                    set current_status = ?, {timestamp_field} = coalesce({timestamp_field}, ?)
                    where source = ? and external_id = ?
                    """,
                    (history_status, now, source, external_id),
                )
            conn.commit()

    def list_application_history(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        company: str | None = None,
        limit: int = 50,
    ) -> list[ApplicationHistoryRecord]:
        safe_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("current_status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if company:
            clauses.append("lower(company) like ?")
            params.append(f"%{company.strip().lower()}%")
        where_sql = f" where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select
                    source, external_id, title, company, location, url, decision, decision_reason, recommended_resume,
                    first_seen_at, sent_at, prepared_at, applied_at, skipped_at, current_status,
                    coalesce(applied_at, skipped_at, prepared_at, sent_at, first_seen_at) as display_date
                from application_history
                {where_sql}
                order by display_date desc, external_id desc
                limit ?
                """,
                (*params, safe_limit),
            ).fetchall()
        return [
            ApplicationHistoryRecord(
                source=str(row[0]),
                external_id=str(row[1]),
                title=str(row[2]) if row[2] is not None else None,
                company=str(row[3]) if row[3] is not None else None,
                location=str(row[4]) if row[4] is not None else None,
                url=str(row[5]) if row[5] is not None else None,
                decision=str(row[6]) if row[6] is not None else None,
                decision_reason=str(row[7]) if row[7] is not None else None,
                recommended_resume=str(row[8]) if row[8] is not None else None,
                first_seen_at=str(row[9]),
                sent_at=str(row[10]) if row[10] is not None else None,
                prepared_at=str(row[11]) if row[11] is not None else None,
                applied_at=str(row[12]) if row[12] is not None else None,
                skipped_at=str(row[13]) if row[13] is not None else None,
                current_status=str(row[14]),
                display_date=str(row[15]),
            )
            for row in rows
        ]

    def get_application_stats(self, *, days: int, source: str | None = None) -> dict:
        period_days = max(1, int(days))
        cutoff = datetime.now(timezone.utc).timestamp() - (period_days * 24 * 60 * 60)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        source_clause = " and source = ?" if source else ""
        source_params: tuple[object, ...] = (source,) if source else ()
        with self._connect() as conn:
            found = conn.execute(
                f"select count(*) from application_history where first_seen_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            sent = conn.execute(
                f"select count(*) from application_history where sent_at is not null and sent_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            prepare_requested = conn.execute(
                f"select count(*) from application_history where current_status = 'PREPARE_REQUESTED' and first_seen_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            prepared = conn.execute(
                f"select count(*) from application_history where prepared_at is not null and prepared_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            applied = conn.execute(
                f"select count(*) from application_history where applied_at is not null and applied_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            skipped = conn.execute(
                f"select count(*) from application_history where skipped_at is not null and skipped_at >= ?{source_clause}",
                (cutoff_iso, *source_params),
            ).fetchone()[0]
            top_companies_rows = conn.execute(
                f"""
                select company, count(*) as cnt
                from application_history
                where first_seen_at >= ? and company is not null and trim(company) <> ''{source_clause}
                group by company
                order by cnt desc, company asc
                limit 5
                """,
                (cutoff_iso, *source_params),
            ).fetchall()
            top_resumes_rows = conn.execute(
                f"""
                select recommended_resume, count(*) as cnt
                from application_history
                where first_seen_at >= ? and recommended_resume is not null and trim(recommended_resume) <> ''{source_clause}
                group by recommended_resume
                order by cnt desc, recommended_resume asc
                limit 5
                """,
                (cutoff_iso, *source_params),
            ).fetchall()
        return {
            "found": int(found),
            "sent": int(sent),
            "prepare_requested": int(prepare_requested),
            "prepared": int(prepared),
            "applied": int(applied),
            "skipped": int(skipped),
            "top_companies": [(str(row[0]), int(row[1])) for row in top_companies_rows],
            "top_resumes": [(str(row[0]), int(row[1])) for row in top_resumes_rows],
        }

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

    def save_prepare_cache(
        self,
        *,
        source: str,
        external_id: str,
        evaluation_json: str,
        analysis_text: str,
        title: str | None,
        company: str | None,
        location: str | None,
        url: str | None,
        content_completeness: str | None,
        snippet: str | None = None,
    ) -> None:
        cached_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into vacancy_prepare_cache (
                    source, external_id, evaluation_json, analysis_text,
                    title, company, location, url, content_completeness, snippet, cached_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source, external_id) do update set
                    evaluation_json = excluded.evaluation_json,
                    analysis_text = excluded.analysis_text,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    url = excluded.url,
                    content_completeness = excluded.content_completeness,
                    snippet = excluded.snippet,
                    cached_at = excluded.cached_at
                """,
                (
                    source,
                    external_id,
                    evaluation_json,
                    analysis_text,
                    title,
                    company,
                    location,
                    url,
                    content_completeness,
                    snippet,
                    cached_at,
                ),
            )
            conn.commit()

    def get_prepare_cache(self, source: str, external_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                    source, external_id, evaluation_json, analysis_text,
                    title, company, location, url, content_completeness, snippet, cached_at
                from vacancy_prepare_cache
                where source = ? and external_id = ?
                """,
                (source, external_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "source": str(row[0]),
            "external_id": str(row[1]),
            "evaluation_json": str(row[2]),
            "analysis_text": str(row[3]),
            "title": str(row[4]) if row[4] is not None else None,
            "company": str(row[5]) if row[5] is not None else None,
            "location": str(row[6]) if row[6] is not None else None,
            "url": str(row[7]) if row[7] is not None else None,
            "content_completeness": str(row[8]) if row[8] is not None else None,
            "snippet": str(row[9]) if row[9] is not None else None,
            "cached_at": str(row[10]),
        }

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
        cover_letter: str | None = None,
        vacancy_title: str | None = None,
        vacancy_company: str | None = None,
        vacancy_url: str | None = None,
        resume_message_id: int | None = None,
        cover_letter_message_id: int | None = None,
    ) -> None:
        prepared_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into application_preparations (
                    source, external_id, prepared_at, resume_name, language, status, error_message, cover_letter, vacancy_title, vacancy_company, vacancy_url, resume_message_id, cover_letter_message_id
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source, external_id) do update set
                    prepared_at = excluded.prepared_at,
                    resume_name = excluded.resume_name,
                    language = excluded.language,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    cover_letter = excluded.cover_letter,
                    vacancy_title = excluded.vacancy_title,
                    vacancy_company = excluded.vacancy_company,
                    vacancy_url = excluded.vacancy_url,
                    resume_message_id = coalesce(excluded.resume_message_id, application_preparations.resume_message_id),
                    cover_letter_message_id = coalesce(excluded.cover_letter_message_id, application_preparations.cover_letter_message_id)
                """,
                (
                    source,
                    external_id,
                    prepared_at,
                    resume_name,
                    language,
                    status,
                    error_message,
                    cover_letter,
                    vacancy_title,
                    vacancy_company,
                    vacancy_url,
                    int(resume_message_id) if resume_message_id is not None else None,
                    int(cover_letter_message_id) if cover_letter_message_id is not None else None,
                ),
            )
            conn.commit()

    def get_preparation(self, source: str, external_id: str) -> ApplicationPreparationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select source, external_id, prepared_at, resume_name, language, status, error_message, cover_letter, vacancy_title, vacancy_company, vacancy_url, resume_message_id, cover_letter_message_id
                from application_preparations
                where source = ? and external_id = ?
                """,
                (source, external_id),
            ).fetchone()
        if row is None:
            return None
        return ApplicationPreparationRecord(
            source=str(row[0]),
            external_id=str(row[1]),
            prepared_at=str(row[2]) if row[2] is not None else None,
            resume_name=str(row[3]) if row[3] is not None else None,
            language=str(row[4]) if row[4] is not None else None,
            status=str(row[5]),
            error_message=str(row[6]) if row[6] is not None else None,
            cover_letter=str(row[7]) if row[7] is not None else None,
            vacancy_title=str(row[8]) if row[8] is not None else None,
            vacancy_company=str(row[9]) if row[9] is not None else None,
            vacancy_url=str(row[10]) if row[10] is not None else None,
            resume_message_id=int(row[11]) if row[11] is not None else None,
            cover_letter_message_id=int(row[12]) if row[12] is not None else None,
        )

    def set_preparation_aux_message_id(
        self,
        *,
        source: str,
        external_id: str,
        resume_message_id: int | None = None,
        cover_letter_message_id: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update application_preparations
                set
                    resume_message_id = coalesce(?, resume_message_id),
                    cover_letter_message_id = coalesce(?, cover_letter_message_id)
                where source = ? and external_id = ?
                """,
                (
                    int(resume_message_id) if resume_message_id is not None else None,
                    int(cover_letter_message_id) if cover_letter_message_id is not None else None,
                    source,
                    external_id,
                ),
            )
            conn.commit()

    def clear_preparation_aux_message_ids(self, *, source: str, external_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update application_preparations
                set resume_message_id = null, cover_letter_message_id = null
                where source = ? and external_id = ?
                """,
                (source, external_id),
            )
            conn.commit()

    def get_history_title_company_url(self, source: str, external_id: str) -> tuple[str | None, str | None, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select title, company, url
                from application_history
                where source = ? and external_id = ?
                limit 1
                """,
                (source, external_id),
            ).fetchone()
        if row is None:
            return None, None, None
        return (
            str(row[0]) if row[0] is not None else None,
            str(row[1]) if row[1] is not None else None,
            str(row[2]) if row[2] is not None else None,
        )

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
                    cover_letter text,
                    vacancy_title text,
                    vacancy_company text,
                    vacancy_url text,
                    resume_message_id integer,
                    cover_letter_message_id integer,
                    primary key (source, external_id)
                )
                """
            )
            self._ensure_preparation_columns(conn)
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
            conn.execute(
                """
                create table if not exists application_history (
                    source text not null,
                    external_id text not null,
                    title text,
                    company text,
                    location text,
                    url text,
                    decision text,
                    decision_reason text,
                    recommended_resume text,
                    first_seen_at text not null,
                    sent_at text,
                    prepared_at text,
                    applied_at text,
                    skipped_at text,
                    current_status text not null,
                    primary key (source, external_id)
                )
                """
            )
            self._ensure_history_columns(conn)
            conn.execute(
                """
                create table if not exists vacancy_prepare_cache (
                    source text not null,
                    external_id text not null,
                    evaluation_json text not null,
                    analysis_text text not null,
                    title text,
                    company text,
                    location text,
                    url text,
                    content_completeness text,
                    snippet text,
                    cached_at text not null,
                    primary key (source, external_id)
                )
                """
            )
            conn.commit()

    def _ensure_preparation_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("pragma table_info(application_preparations)").fetchall()
        existing = {str(row[1]) for row in rows}
        extra_columns = {
            "cover_letter": "text",
            "vacancy_title": "text",
            "vacancy_company": "text",
            "vacancy_url": "text",
            "resume_message_id": "integer",
            "cover_letter_message_id": "integer",
        }
        for name, type_name in extra_columns.items():
            if name in existing:
                continue
            conn.execute(f"alter table application_preparations add column {name} {type_name}")

    def _ensure_history_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("pragma table_info(application_history)").fetchall()
        existing = {str(row[1]) for row in rows}
        if "decision_reason" not in existing:
            conn.execute("alter table application_history add column decision_reason text")


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
