from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TelegramInlineButton:
    text: str
    url: str | None = None
    callback_data: str | None = None


@dataclass(frozen=True)
class TelegramVacancyCard:
    source: str
    external_id: str
    decision: str
    title: str
    company: str | None
    location: str | None
    url: str
    match_percentage: float | None
    gaps: list[str] = field(default_factory=list)
    nuances: list[str] = field(default_factory=list)
    recommended_resume: str = "java-backend"
    content_completeness: str = "PARTIAL"


@dataclass(frozen=True)
class TelegramMessageRef:
    chat_id: str
    message_id: int


@dataclass(frozen=True)
class TelegramDocumentRef:
    chat_id: str
    message_id: int
    file_id: str
    file_unique_id: str | None


@dataclass(frozen=True)
class TelegramDeliveryRecord:
    source: str
    external_id: str
    chat_id: str
    message_id: int
    sent_at: str
    status: str


@dataclass(frozen=True)
class TelegramResumeCacheRecord:
    resume_name: str
    file_path: str
    file_mtime_ns: int
    file_size: int
    telegram_file_id: str
    telegram_file_unique_id: str | None
    cached_at: str


@dataclass(frozen=True)
class ApplicationHistoryRecord:
    source: str
    external_id: str
    title: str | None
    company: str | None
    location: str | None
    url: str | None
    decision: str | None
    recommended_resume: str | None
    first_seen_at: str
    sent_at: str | None
    prepared_at: str | None
    applied_at: str | None
    skipped_at: str | None
    current_status: str
    display_date: str


@dataclass(frozen=True)
class ApplicationPreparationRecord:
    source: str
    external_id: str
    prepared_at: str | None
    resume_name: str | None
    language: str | None
    status: str
    error_message: str | None
    cover_letter: str | None
    vacancy_title: str | None
    vacancy_company: str | None
    vacancy_url: str | None
