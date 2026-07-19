from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


@dataclass(frozen=True)
class CollectorResult:
    source: str
    vacancies: list[NormalizedVacancy]


class Collector(Protocol):
    name: str

    def collect(self) -> CollectorResult:
        ...


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


def vacancy_identity(vacancy: NormalizedVacancy) -> str | None:
    external_id = vacancy.external_id.strip()
    if external_id:
        return f"{vacancy.source}:{external_id}"
    canonical_url = canonicalize_url(vacancy.url)
    if canonical_url:
        return f"url:{canonical_url}"
    title = vacancy.title.strip().lower()
    company = (vacancy.company or "").strip().lower()
    location = (vacancy.location or "").strip().lower()
    if not any([title, company, location]):
        return None
    source = vacancy.source.strip().lower()
    fingerprint = hashlib.sha1("|".join([source, title, company, location]).encode("utf-8")).hexdigest()[:24]
    return f"fp:{fingerprint}"
