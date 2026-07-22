from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ContentCompleteness(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MINIMAL = "MINIMAL"


class ParserSource(str, Enum):
    STRUCTURED_CARD = "STRUCTURED_CARD"
    FALLBACK_URL = "FALLBACK_URL"


_DESCRIPTION_UNAVAILABLE = "<not available in LinkedIn email>"


@dataclass(frozen=True)
class LinkedInEmailVacancy:
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    snippet: str | None
    email_message_id: str
    received_at: datetime | None
    content_completeness: ContentCompleteness
    email_subject_context: str | None = None
    alert_query: str | None = None
    snippet_source: str = "missing"
    parser_source: ParserSource = ParserSource.FALLBACK_URL

    def to_analysis_text(self) -> str:
        lines = [f"Title: {self.title}"]
        if self.company:
            lines.append(f"Company: {self.company}")
        if self.location:
            lines.append(f"Location: {self.location}")
        lines.append("Description:")
        description = " ".join((self.snippet or "").split())
        lines.append(description if description else _DESCRIPTION_UNAVAILABLE)
        if self.alert_query:
            lines.append(f"Alert query: {self.alert_query}")
        lines.append(f"Source URL: {self.url}")
        lines.append(f"Content completeness: {self.content_completeness.value}")
        return "\n".join(lines)

    def description_for_normalized(self) -> str:
        return " ".join((self.snippet or "").split())
