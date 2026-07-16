from __future__ import annotations

import base64
import html
import quopri
import re
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from app.collectors.email_imap_client import RawEmailMessage
from app.collectors.linkedin_models import (
    ContentCompleteness,
    LinkedInEmailVacancy,
    ParserSource,
)


JOB_ID_PATTERNS = (
    re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"),
    re.compile(r"currentJobId=(\d+)"),
)
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
SKIP_URL_MARKERS = (
    "/help/",
    "/feed/",
    "/mypreferences/",
    "/unsubscribe",
    "/settings/",
    "/company/",
    "/in/",
    "/learning/",
)
NOISE_TEXT = {
    "view job",
    "see more jobs",
    "apply now",
    "manage job alerts",
    "actively recruiting",
    "easy apply",
    "job alert",
}
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
SEPARATOR_RE = re.compile(r"\s*[•·▪\u2022\ufffd]\s*")


@dataclass
class _Card:
    external_id: str
    url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    snippet: str | None = None


class _StructuredCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards_by_id: dict[str, _Card] = {}
        self.ordered_ids: list[str] = []
        self._current_card_id: str | None = None
        self._current_anchor_id: str | None = None
        self._current_anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if tag == "a" and href and _is_vacancy_url(href):
            job_id = _extract_job_id(href)
            if job_id is not None:
                self._current_anchor_id = job_id
                self._current_anchor_text = []
                self._current_card_id = job_id
                card = self.cards_by_id.get(job_id)
                if card is None:
                    card = _Card(external_id=job_id, url=_normalize_job_url(href, job_id))
                    self.cards_by_id[job_id] = card
                    self.ordered_ids.append(job_id)

        if tag == "img" and self._current_card_id is not None:
            alt = _clean_text(attr_map.get("alt"))
            if alt and alt.lower() not in NOISE_TEXT:
                card = self.cards_by_id[self._current_card_id]
                if not card.company:
                    card.company = alt

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_anchor_id is not None:
            text = _clean_text(" ".join(self._current_anchor_text))
            if text and text.lower() not in NOISE_TEXT:
                card = self.cards_by_id[self._current_anchor_id]
                if not card.title or card.title.lower().startswith("linkedin vacancy "):
                    card.title = text
            self._current_anchor_id = None
            self._current_anchor_text = []

    def handle_data(self, data: str) -> None:
        text = _clean_text(data)
        if not text:
            return

        if self._current_anchor_id is not None:
            self._current_anchor_text.append(text)
            return

        if self._current_card_id is None:
            return

        if text.lower() in NOISE_TEXT:
            return

        card = self.cards_by_id[self._current_card_id]
        _apply_text_to_card(card, text)


def parse_linkedin_email(raw_message: RawEmailMessage) -> list[LinkedInEmailVacancy]:
    html_content, plain_content = _extract_text_content(raw_message.email_message)

    structured_cards: list[LinkedInEmailVacancy] = []
    if html_content:
        parser = _StructuredCardParser()
        parser.feed(html_content)
        parser.close()
        for job_id in parser.ordered_ids:
            card = parser.cards_by_id[job_id]
            title = card.title or f"LinkedIn vacancy {job_id}"
            company = _clean_optional(card.company)
            location = _clean_optional(card.location)
            snippet = _clean_optional(card.snippet)
            completeness = _detect_content_completeness(
                title=title,
                company=company,
                location=location,
                snippet=snippet,
            )
            structured_cards.append(
                LinkedInEmailVacancy(
                    external_id=job_id,
                    title=title,
                    company=company,
                    location=location,
                    url=card.url,
                    snippet=snippet,
                    email_message_id=raw_message.message_id,
                    received_at=raw_message.received_at,
                    content_completeness=completeness,
                    parser_source=ParserSource.STRUCTURED_CARD,
                )
            )

    fallback_urls = _extract_links(html_content=html_content, plain_content=plain_content)
    by_id: dict[str, LinkedInEmailVacancy] = {item.external_id: item for item in structured_cards}
    ordered_ids = [item.external_id for item in structured_cards]
    for url, link_text in fallback_urls:
        external_id = _extract_job_id(url)
        if external_id is None or external_id in by_id:
            continue
        title = _clean_text(link_text) or f"LinkedIn vacancy {external_id}"
        snippet = _extract_snippet(plain_content, external_id)
        vacancy = LinkedInEmailVacancy(
            external_id=external_id,
            title=title,
            company=_extract_company_hint(plain_content),
            location=_extract_location_hint(plain_content),
            url=_normalize_job_url(url, external_id),
            snippet=snippet,
            email_message_id=raw_message.message_id,
            received_at=raw_message.received_at,
            content_completeness=_detect_content_completeness(
                title=title,
                company=None,
                location=None,
                snippet=snippet,
            ),
            parser_source=ParserSource.FALLBACK_URL,
        )
        by_id[external_id] = vacancy
        ordered_ids.append(external_id)

    return [by_id[job_id] for job_id in ordered_ids]


def extract_email_text_parts(message: Message) -> tuple[str, str]:
    return _extract_text_content(message)


def _extract_text_content(message: Message) -> tuple[str, str]:
    if message.is_multipart():
        html_parts: list[str] = []
        plain_parts: list[str] = []
        for part in message.walk():
            content_type = (part.get_content_type() or "").lower()
            if content_type not in {"text/html", "text/plain"}:
                continue
            decoded = _decode_part(part)
            if not decoded:
                continue
            if content_type == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)
        return ("\n".join(html_parts), "\n".join(plain_parts))

    body = _decode_part(message)
    if (message.get_content_type() or "").lower() == "text/html":
        return (body, "")
    return ("", body)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=False)
    if payload is None:
        return ""

    if isinstance(payload, str):
        transfer_encoding = (part.get("Content-Transfer-Encoding") or "").lower()
        raw_bytes = payload.encode("utf-8", errors="ignore")
        if transfer_encoding == "quoted-printable":
            raw_bytes = quopri.decodestring(raw_bytes)
        elif transfer_encoding == "base64":
            raw_bytes = base64.b64decode(raw_bytes, validate=False)
    else:
        raw_bytes = part.get_payload(decode=True) or b""

    charset = part.get_content_charset() or "utf-8"
    for candidate in (charset, "utf-8", "cp1251", "latin-1"):
        try:
            return raw_bytes.decode(candidate)
        except Exception:  # noqa: BLE001
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def _extract_links(html_content: str, plain_content: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    if html_content:
        parser = _SimpleLinkExtractor()
        parser.feed(html_content)
        parser.close()
        for href, text in parser.links:
            if _is_vacancy_url(href):
                links.append((href, text))
        for match in URL_PATTERN.findall(html_content):
            if _is_vacancy_url(match):
                links.append((match, ""))

    for match in URL_PATTERN.findall(plain_content):
        if _is_vacancy_url(match):
            links.append((match, ""))
    return links


class _SimpleLinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        text = _clean_text(data)
        if text:
            self._text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._href:
            self.links.append((self._href, _clean_text(" ".join(self._text_parts))))
        self._href = None
        self._text_parts = []


def _is_vacancy_url(url: str) -> bool:
    lower = url.lower()
    if "linkedin.com" not in lower:
        return False
    if any(marker in lower for marker in SKIP_URL_MARKERS):
        return False
    return _extract_job_id(url) is not None


def _extract_job_id(url: str) -> str | None:
    for pattern in JOB_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    current_job_id = params.get("currentJobId")
    if current_job_id:
        return current_job_id[0]
    return None


def _normalize_job_url(url: str, external_id: str) -> str:
    _ = url
    return f"https://www.linkedin.com/jobs/view/{external_id}/"


def _extract_snippet(plain_content: str, external_id: str) -> str | None:
    lines = [_clean_text(line) for line in plain_content.splitlines() if _clean_text(line)]
    for idx, line in enumerate(lines):
        if external_id in line:
            next_idx = idx + 1
            if next_idx < len(lines):
                snippet = lines[next_idx]
                if _is_noise_text(snippet):
                    return None
                return snippet
    return None


def _extract_company_hint(plain_content: str) -> str | None:
    match = re.search(r"Company:\s*([^\n]+)", plain_content, flags=re.IGNORECASE)
    if not match:
        return None
    value = _clean_text(match.group(1))
    return value or None


def _extract_location_hint(plain_content: str) -> str | None:
    match = re.search(r"Location:\s*([^\n]+)", plain_content, flags=re.IGNORECASE)
    if not match:
        return None
    value = _clean_text(match.group(1))
    return value or None


def _detect_content_completeness(
    title: str,
    company: str | None,
    location: str | None,
    snippet: str | None,
) -> ContentCompleteness:
    has_title = bool(title and not title.lower().startswith("linkedin vacancy "))
    has_company = bool(company)
    has_location = bool(location)
    has_snippet = bool(snippet and len(snippet.split()) >= 4)
    if has_title and has_company and has_location and has_snippet:
        return ContentCompleteness.FULL
    if has_title and (has_company or has_location or bool(snippet)):
        return ContentCompleteness.PARTIAL
    return ContentCompleteness.MINIMAL


def _apply_text_to_card(card: _Card, text: str) -> None:
    if _is_noise_text(text):
        return

    company, location = _split_company_location(text)
    if company:
        if not card.company:
            card.company = company
        if location and not card.location and (card.company == company or not card.company):
            card.location = location
        if card.company == company:
            return

    if location and not card.location:
        card.location = location
        return

    if not card.snippet and len(text.split()) >= 4:
        card.snippet = text


def _split_company_location(text: str) -> tuple[str | None, str | None]:
    parts = [part.strip() for part in SEPARATOR_RE.split(text) if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return (None, None)


def _is_noise_text(value: str) -> bool:
    normalized = _clean_text(value).lower()
    if not normalized:
        return True
    if normalized in NOISE_TEXT:
        return True
    if "unsubscribe" in normalized:
        return True
    if "see all jobs" in normalized:
        return True
    return False


def _clean_optional(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    return cleaned if cleaned else None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    unescaped = html.unescape(value)
    unescaped = ZERO_WIDTH_RE.sub("", unescaped)
    cleaned = " ".join(unescaped.replace("\xa0", " ").split())
    return cleaned.strip()
