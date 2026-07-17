from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class NormalizedVacancy:
    source: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    employment: str | None
    description: str
    url: str
    published_at: str | None

    def to_analysis_text(self) -> str:
        lines = [f"Title: {self.title}"]
        if self.company:
            lines.append(f"Company: {self.company}")
        if self.location:
            lines.append(f"Location: {self.location}")
        if self.employment:
            lines.append(f"Employment: {self.employment}")
        if self.published_at:
            lines.append(f"Published at: {self.published_at}")
        lines.append("Description:")
        lines.append(self.description.strip())
        lines.append(f"Source URL: {self.url}")
        return "\n".join(lines).strip()

    def dedupe_key(self) -> tuple[str, str, str] | str:
        canonical_url = canonicalize_url(self.url)
        if canonical_url:
            return canonical_url
        return (
            (self.company or "").strip().lower(),
            self.title.strip().lower(),
            (self.location or "").strip().lower(),
        )


class VacancyCollector(Protocol):
    def collect(self) -> list[NormalizedVacancy]:
        ...


def canonicalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query="",
        fragment="",
    )
    rendered = urlunsplit(normalized).rstrip("/")
    return rendered or None
