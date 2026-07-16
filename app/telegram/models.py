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
