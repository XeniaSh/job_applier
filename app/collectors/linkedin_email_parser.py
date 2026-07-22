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
    "promoted",
    "featured",
    "hiring multiple candidates",
    "be an early applicant",
}
# Phrases stripped from titles/companies even when glued to other text.
LINKEDIN_UI_MARKER_RE = re.compile(
    r"(?i)\b(?:"
    r"easy\s+apply|"
    r"promoted|"
    r"featured|"
    r"actively\s+recruiting|"
    r"hiring\s+multiple\s+candidates|"
    r"be\s+an\s+early\s+applicant|"
    r"view\s+job|"
    r"see\s+more\s+jobs|"
    r"apply\s+now"
    r")\b"
)
TRAILING_COMPANY_LEGAL_RE = re.compile(
    r"^(?P<title>.+?)\s+"
    r"(?P<company>"
    r"[A-Z0-9][\w&.\'-]*(?:\s+(?:[A-Z0-9][\w&.\'-]*|and|&|of|the|de|da|di))*"
    r"\s+(?:Ltd\.?|LLC|Inc\.?|GmbH|AG|S\.?A\.?|B\.?V\.?|PLC|Corp\.?|Corporation|Group|Co\.?)"
    r")\s*$"
)
TRAILING_COMPANY_AFTER_ROLE_RE = re.compile(
    r"^(?P<title>.+?\b(?:Engineer|Developer|Lead|Architect|Programmer|Specialist|"
    r"Manager|Analyst|Scientist|Designer|Consultant|Intern)"
    r"(?:\s*\([^)]*\))?)\s+"
    r"(?P<company>[A-Z][\w&.\'-]*(?:\s+[A-Z][\w&.\'-]*){0,4})\s*$"
)
LOCATION_LIKE_RE = re.compile(
    r"(?i)\b(?:remote|hybrid|on[\s-]?site|worldwide|europe|emea|americas|"
    r"yerevan|berlin|london|paris|amsterdam|moscow|tbilisi|baku|warsaw|"
    r"germany|armenia|netherlands|poland|georgia|azerbaijan|uk|usa|uae)\b"
)
PROMOTIONAL_MARKERS = (
    "stand out and let hirers know",
    "try premium",
    "install linkedin widgets",
    "stay updated at a glance",
    "this email was intended for",
    "you are receiving job alert emails",
    "unsubscribe",
    "linkedin corporation",
    "see all jobs",
)
FOOTER_BOUNDARY_MARKERS = (
    "see all jobs",
    "try premium",
    "install linkedin widgets",
    "this email was intended for",
    "you are receiving job alert emails",
    "unsubscribe",
)
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
    visible_text_parts: list[str] = None  # type: ignore[assignment]
    card_begin: int | None = None
    card_end: int | None = None
    text_length: int = 0
    visible_text_length: int = 0
    snippet_source: str = "missing"
    promotional_snippet_detected: bool = False

    def __post_init__(self) -> None:
        if self.visible_text_parts is None:
            self.visible_text_parts = []


@dataclass(frozen=True)
class ParserCardDiagnostic:
    card_index: int
    external_id: str
    title: str
    company: str | None
    email_subject_context: str | None
    alert_query: str | None
    snippet_source: str
    promotional_snippet_detected: bool
    card_begin: int | None
    card_end: int | None
    text_length: int
    visible_text_length: int
    visible_text_preview: str


@dataclass(frozen=True)
class ParserExtractionDiagnostics:
    cards_found: int
    cards_with_description: int
    cards_with_real_snippet: int
    cards_with_promotional_snippet: int
    cards_without_snippet: int
    cards_with_only_title: int
    card_diagnostics: list[ParserCardDiagnostic]


class _StructuredCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards_by_id: dict[str, _Card] = {}
        self.ordered_ids: list[str] = []
        self._current_card_id: str | None = None
        self._current_anchor_id: str | None = None
        self._current_anchor_text: list[str] = []
        self._node_index = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._node_index += 1
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
                if card.card_begin is None:
                    card.card_begin = self._node_index

        if tag == "img" and self._current_card_id is not None:
            alt = _clean_text(attr_map.get("alt"))
            if alt and alt.lower() not in NOISE_TEXT:
                card = self.cards_by_id[self._current_card_id]
                if not card.company:
                    card.company = alt

    def handle_endtag(self, tag: str) -> None:
        self._node_index += 1
        if tag == "a" and self._current_anchor_id is not None:
            text = _clean_text(" ".join(self._current_anchor_text))
            text = _strip_linkedin_ui_markers(text)
            if text and text.lower() not in NOISE_TEXT:
                card = self.cards_by_id[self._current_anchor_id]
                if not card.title or card.title.lower().startswith("linkedin vacancy "):
                    _assign_anchor_text_to_card(card, text)
                card.card_end = self._node_index
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
        if _is_footer_boundary_text(text):
            card.card_end = self._node_index
            self._current_card_id = None
            return
        card.visible_text_parts.append(text)
        card.visible_text_length += len(text)
        card.text_length += len(data or "")
        card.card_end = self._node_index
        _apply_text_to_card(card, text)


def parse_linkedin_email(raw_message: RawEmailMessage) -> list[LinkedInEmailVacancy]:
    vacancies, _ = parse_linkedin_email_with_diagnostics(raw_message)
    return vacancies


def parse_linkedin_email_with_diagnostics(
    raw_message: RawEmailMessage,
) -> tuple[list[LinkedInEmailVacancy], ParserExtractionDiagnostics]:
    html_content, plain_content = _extract_text_content(raw_message.email_message)
    email_subject_context = _clean_optional(raw_message.subject)
    alert_query = _extract_alert_query(email_subject_context)

    structured_cards: list[LinkedInEmailVacancy] = []
    card_diagnostics: list[ParserCardDiagnostic] = []
    if html_content:
        parser = _StructuredCardParser()
        parser.feed(html_content)
        parser.close()
        for card_index, job_id in enumerate(parser.ordered_ids, start=1):
            card = parser.cards_by_id[job_id]
            title, company, location = _finalize_card_fields(
                title=card.title or f"LinkedIn vacancy {job_id}",
                company=card.company,
                location=card.location,
            )
            snippet = _clean_optional(card.snippet)
            snippet_source = card.snippet_source
            promotional_snippet = card.promotional_snippet_detected
            visible_text_preview = _build_visible_text_preview(card.visible_text_parts)
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
                    email_subject_context=email_subject_context,
                    alert_query=alert_query,
                    snippet_source=snippet_source,
                    parser_source=ParserSource.STRUCTURED_CARD,
                )
            )
            card_diagnostics.append(
                ParserCardDiagnostic(
                    card_index=card_index,
                    external_id=job_id,
                    title=title,
                    company=company,
                    email_subject_context=email_subject_context,
                    alert_query=alert_query,
                    snippet_source=snippet_source,
                    promotional_snippet_detected=promotional_snippet,
                    card_begin=card.card_begin,
                    card_end=card.card_end,
                    text_length=card.text_length,
                    visible_text_length=card.visible_text_length,
                    visible_text_preview=visible_text_preview,
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
        company_hint = _extract_company_hint(plain_content)
        location_hint = _extract_location_hint(plain_content)
        title, company_hint, location_hint = _finalize_card_fields(
            title=title,
            company=company_hint,
            location=location_hint,
        )
        snippet = _extract_snippet(plain_content, external_id)
        snippet_source = _classify_snippet_source(snippet)
        if snippet_source == "promo":
            snippet = None
            snippet_source = "missing"
        vacancy = LinkedInEmailVacancy(
            external_id=external_id,
            title=title,
            company=company_hint,
            location=location_hint,
            url=_normalize_job_url(url, external_id),
            snippet=snippet,
            email_message_id=raw_message.message_id,
            received_at=raw_message.received_at,
            content_completeness=_detect_content_completeness(
                title=title,
                company=company_hint,
                location=location_hint,
                snippet=snippet,
            ),
            email_subject_context=email_subject_context,
            alert_query=alert_query,
            snippet_source=snippet_source,
            parser_source=ParserSource.FALLBACK_URL,
        )
        by_id[external_id] = vacancy
        ordered_ids.append(external_id)
        card_diagnostics.append(
            ParserCardDiagnostic(
                card_index=len(card_diagnostics) + 1,
                external_id=external_id,
                title=title,
                company=vacancy.company,
                email_subject_context=email_subject_context,
                alert_query=alert_query,
                snippet_source=snippet_source,
                promotional_snippet_detected=False,
                card_begin=None,
                card_end=None,
                text_length=len(plain_content),
                visible_text_length=len(plain_content),
                visible_text_preview=_truncate_text(_clean_text(plain_content), max_len=500),
            )
        )

    vacancies = [by_id[job_id] for job_id in ordered_ids]
    diagnostics = _build_parser_extraction_diagnostics(vacancies=vacancies, cards=card_diagnostics)
    return vacancies, diagnostics


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

    if _is_promotional_text(text):
        card.promotional_snippet_detected = True
        if card.snippet_source == "missing":
            card.snippet_source = "promo"
        return

    cleaned = _strip_linkedin_ui_markers(text)
    if not cleaned or _is_noise_text(cleaned):
        return

    company, location = _split_company_location(cleaned)
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

    if not card.snippet and len(cleaned.split()) >= 4:
        card.snippet = cleaned
        card.snippet_source = _classify_nonpromo_snippet_source(cleaned)


def _assign_anchor_text_to_card(card: _Card, text: str) -> None:
    """Assign job-link text, splitting merged title/company/location when needed."""
    title, company, location = _unmerge_title_company_location(
        title=text,
        company=card.company,
        location=card.location,
    )
    card.title = title
    if company and not card.company:
        card.company = company
    if location and not card.location:
        card.location = location


def _finalize_card_fields(
    *,
    title: str,
    company: str | None,
    location: str | None,
) -> tuple[str, str | None, str | None]:
    cleaned_title = _strip_linkedin_ui_markers(title) or title
    cleaned_company = _clean_optional(_strip_linkedin_ui_markers(company) if company else None)
    cleaned_location = _clean_optional(_strip_linkedin_ui_markers(location) if location else None)
    return _unmerge_title_company_location(
        title=cleaned_title,
        company=cleaned_company,
        location=cleaned_location,
    )


def _unmerge_title_company_location(
    *,
    title: str,
    company: str | None,
    location: str | None,
) -> tuple[str, str | None, str | None]:
    working_title = _strip_linkedin_ui_markers(_clean_text(title))
    working_company = _clean_optional(company)
    working_location = _clean_optional(location)
    if not working_title:
        return title, working_company, working_location

    if SEPARATOR_RE.search(working_title):
        parts = [part.strip() for part in SEPARATOR_RE.split(working_title) if part.strip()]
        if len(parts) >= 2:
            left = parts[0]
            right = parts[-1]
            if len(parts) >= 3 and not working_company:
                working_company = parts[1]
            if not working_location:
                working_location = right
            elif working_location.lower() != right.lower() and LOCATION_LIKE_RE.search(right):
                # Prefer explicit location recovered from the merged title.
                working_location = right
            working_title = left

    working_title, extracted_company = _split_title_and_company(working_title, working_company)
    if extracted_company and not working_company:
        working_company = extracted_company

    # Last-chance location recovery when company·location leaked into title without separator.
    if not working_location:
        recovered = _recover_location_from_text(working_title)
        if recovered is not None:
            working_title, working_location = recovered

    working_title = _strip_linkedin_ui_markers(working_title)
    return (
        working_title or title,
        _clean_optional(working_company),
        _clean_optional(working_location),
    )


def _split_title_and_company(title: str, known_company: str | None) -> tuple[str, str | None]:
    if known_company:
        company = known_company.strip()
        if company and title.lower().endswith(company.lower()):
            trimmed = title[: -len(company)].strip(" -–—|·•")
            if trimmed:
                return trimmed, company

    legal_match = TRAILING_COMPANY_LEGAL_RE.match(title)
    if legal_match:
        return legal_match.group("title").strip(), legal_match.group("company").strip()

    role_match = TRAILING_COMPANY_AFTER_ROLE_RE.match(title)
    if role_match:
        candidate = role_match.group("company").strip()
        if candidate and not LOCATION_LIKE_RE.search(candidate) and candidate.lower() not in NOISE_TEXT:
            return role_match.group("title").strip(), candidate

    return title, known_company


def _recover_location_from_text(title: str) -> tuple[str, str] | None:
    """Recover trailing location fragments like '… Yerevan (Remote)' without a middle-dot."""
    match = re.search(
        r"^(?P<title>.+?)\s+"
        r"(?P<location>"
        r"(?:[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,2})"
        r"(?:\s*\((?:Remote|Hybrid|On[\s-]?site|Office)[^)]*\))?"
        r")\s*$",
        title,
    )
    if not match:
        return None
    location = match.group("location").strip()
    if not LOCATION_LIKE_RE.search(location):
        return None
    cleaned_title = match.group("title").strip()
    if not cleaned_title or cleaned_title.lower() == location.lower():
        return None
    return cleaned_title, location


def _strip_linkedin_ui_markers(value: str | None) -> str:
    if not value:
        return ""
    cleaned = LINKEDIN_UI_MARKER_RE.sub(" ", value)
    return _clean_text(cleaned)


def _split_company_location(text: str) -> tuple[str | None, str | None]:
    cleaned = _strip_linkedin_ui_markers(text)
    parts = [part.strip() for part in SEPARATOR_RE.split(cleaned) if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return (None, None)


def _is_noise_text(value: str) -> bool:
    normalized = _strip_linkedin_ui_markers(_clean_text(value)).lower()
    if not normalized:
        return True
    if normalized in NOISE_TEXT:
        return True
    if "unsubscribe" in normalized:
        return True
    if "see all jobs" in normalized:
        return True
    return False


def _is_promotional_text(value: str) -> bool:
    normalized = _clean_text(value).lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in PROMOTIONAL_MARKERS)


def _is_footer_boundary_text(value: str) -> bool:
    normalized = _clean_text(value).lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in FOOTER_BOUNDARY_MARKERS)


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


def _truncate_text(value: str, *, max_len: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len]


def _build_visible_text_preview(parts: list[str]) -> str:
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        return ""
    return _truncate_text(text, max_len=500)


def _classify_snippet_source(snippet: str | None) -> str:
    if snippet is None or not snippet.strip():
        return "missing"
    lowered = snippet.strip().lower()
    if _is_promotional_text(lowered):
        return "promo"
    return _classify_nonpromo_snippet_source(lowered)


def _classify_nonpromo_snippet_source(snippet: str) -> str:
    lowered = snippet.strip().lower()
    words = lowered.split()
    if len(words) <= 6:
        return "subtitle"
    if len(words) <= 20:
        return "body"
    return "description"


def _extract_alert_query(subject: str | None) -> str | None:
    cleaned = _clean_optional(subject)
    if not cleaned:
        return None
    match = re.match(r"^\s*\"?(?P<query>.+?)\"?\s+posted\s+(?:in the past|on)\b", cleaned, flags=re.IGNORECASE)
    if not match:
        return None
    query = _clean_text(match.group("query"))
    if not query:
        return None
    query_lower = query.lower()
    if " at " in query_lower:
        return None
    if len(query.split()) > 6:
        return None
    if not any(token in query_lower for token in ("java", "kotlin", "jvm", "spring", "backend")):
        return None
    if any(token in query_lower for token in ("engineer", "developer", "software", "architect", "analyst")):
        return None
    if any(marker in query_lower for marker in (" at ", " - ")):
        return None
    return query


def _build_parser_extraction_diagnostics(
    *,
    vacancies: list[LinkedInEmailVacancy],
    cards: list[ParserCardDiagnostic],
) -> ParserExtractionDiagnostics:
    cards_found = len(cards)
    cards_with_description = sum(1 for card in cards if card.snippet_source in {"description", "body"})
    cards_with_promotional_snippet = sum(1 for card in cards if card.snippet_source == "promo")
    cards_without_snippet = sum(1 for card in cards if card.snippet_source == "missing")
    cards_with_real_snippet = sum(1 for card in cards if card.snippet_source in {"description", "subtitle", "body"})
    cards_with_only_title = sum(
        1
        for vacancy in vacancies
        if vacancy.title.strip()
        and not (vacancy.company or "").strip()
        and not (vacancy.location or "").strip()
        and not (vacancy.snippet or "").strip()
    )
    return ParserExtractionDiagnostics(
        cards_found=cards_found,
        cards_with_description=cards_with_description,
        cards_with_real_snippet=cards_with_real_snippet,
        cards_with_promotional_snippet=cards_with_promotional_snippet,
        cards_without_snippet=cards_without_snippet,
        cards_with_only_title=cards_with_only_title,
        card_diagnostics=cards,
    )
