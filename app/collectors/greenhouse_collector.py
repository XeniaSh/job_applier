from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.collectors.vacancy_collector import NormalizedVacancy, VacancyCollector

logger = logging.getLogger(__name__)


class GreenhouseCollectionError(Exception):
    """Raised when Greenhouse board collection fails."""


class _HTMLToTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag in {"br", "p", "div", "li", "tr", "section"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = html.unescape(text)
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        return text.strip()


def clean_html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLToTextParser()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def normalize_greenhouse_board(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        raise ValueError("Greenhouse board is empty.")
    if "://" not in cleaned:
        return cleaned.lower()

    parsed = urlparse(cleaned)
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError(f"Cannot parse Greenhouse board from URL: {value}")
    if path_parts[0] in {"boards", "job-boards"} and len(path_parts) >= 2:
        return path_parts[1].lower()
    return path_parts[0].lower()


class GreenhouseCollector(VacancyCollector):
    SOURCE = "greenhouse"

    def __init__(
        self,
        *,
        boards: list[str],
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._boards = [normalize_greenhouse_board(item) for item in boards if item.strip()]
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def collect(self) -> list[NormalizedVacancy]:
        collected: list[NormalizedVacancy] = []
        with httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=self._timeout_seconds, write=10.0, pool=5.0),
            headers={"User-Agent": self._user_agent},
        ) as client:
            for board in self._boards:
                endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
                try:
                    response = client.get(endpoint)
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise GreenhouseCollectionError(f"Greenhouse board '{board}' request failed.") from exc

                jobs = payload.get("jobs", [])
                if not isinstance(jobs, list):
                    logger.warning("Unexpected Greenhouse payload for board '%s'", board)
                    continue
                for item in jobs:
                    normalized = _job_to_normalized(item=item)
                    if normalized is None:
                        continue
                    collected.append(normalized)

        return collected


def _job_to_normalized(item: Any) -> NormalizedVacancy | None:
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    external_id = str(raw_id).strip() if raw_id is not None else ""
    title = str(item.get("title") or "").strip()
    absolute_url = str(item.get("absolute_url") or "").strip()
    if not external_id or not title or not absolute_url:
        return None
    location = ((item.get("location") or {}).get("name") or None) if isinstance(item.get("location"), dict) else None
    metadata = item.get("metadata")
    employment = None
    if isinstance(metadata, list):
        for row in metadata:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip().lower()
            value = str(row.get("value") or "").strip()
            if name in {"employment type", "employment", "type"} and value:
                employment = value
                break

    content = str(item.get("content") or "")
    cleaned_description = clean_html_to_text(content)
    if not cleaned_description:
        cleaned_description = title

    company = None
    data_compliance = item.get("data_compliance")
    if isinstance(data_compliance, list):
        for row in data_compliance:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if text:
                company = text
                break

    return NormalizedVacancy(
        source=GreenhouseCollector.SOURCE,
        external_id=external_id,
        title=title,
        company=company,
        location=str(location).strip() if isinstance(location, str) and location.strip() else None,
        employment=employment,
        description=cleaned_description,
        url=absolute_url,
        published_at=str(item.get("updated_at") or item.get("first_published") or "") or None,
    )
