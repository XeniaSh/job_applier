"""Bounded public RSS discovery. No settings, database, LLM or Telegram access."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.collectors.title_filter import evaluate_title
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.domain_policy import DomainPolicy, excluded_domain_reason
from app.company_watch.feasibility import assess_application_feasibility
from app.company_watch.seniority import classify_seniority
from app.title_rules import classify_title_match, jvm_title_hits

FEED_URL = "https://career.habr.com/vacancies/rss"


class HabrRSSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(default_factory=lambda: ["Java", "Kotlin", "JVM backend", "AI backend"], min_length=1, max_length=8)
    remote_only: bool = True
    max_pages: int = Field(default=2, ge=1, le=3)
    timeout_seconds: float = Field(default=15, gt=0, le=30, allow_inf_nan=False)
    max_response_bytes: int = Field(default=1_000_000, ge=1024, le=2_000_000)
    max_items: int = Field(default=100, ge=1, le=200)
    max_age_days: int = Field(default=30, ge=1, le=90)

    @field_validator("queries")
    @classmethod
    def valid_queries(cls, values: list[str]) -> list[str]:
        if any(not q.strip() or len(q) > 120 or any(ord(c) < 32 for c in q) for q in values):
            raise ValueError("queries must be nonempty plain search strings of at most 120 characters")
        return list(dict.fromkeys(q.strip() for q in values))


@dataclass(frozen=True)
class HabrRecord:
    vacancy: NormalizedVacancy
    salary: str | None
    remote_conditions: str | None
    grades: tuple[str, ...]
    salary_basis: str = "unspecified"
    queries: tuple[str, ...] = ()


@dataclass
class FeedDiagnostic:
    query_index: int
    pages: int = 0
    raw: int = 0
    invalid: int = 0
    stop: str = "page_limit"
    status: int | None = None
    page_new_ids: list[int] = field(default_factory=list)


@dataclass
class HabrRSSResult:
    records: list[HabrRecord] = field(default_factory=list)
    feeds: list[FeedDiagnostic] = field(default_factory=list)


class FeedError(ValueError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden = True

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = False

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    text = " ".join(" ".join(parser.parts).split())
    # Feeds are public, but recruiter contacts are not needed in pilot artifacts.
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|https?://t\.me/\S+|\+\d[\d ()-]{8,}\d", "[contact omitted]", text)
    return text


def canonical_habr_url(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme != "https" or parsed.netloc != "career.habr.com":
            return None
        match = re.fullmatch(r"/vacancies/([0-9]{5,15})/?", parsed.path)
        if match:
            return match[1], f"https://career.habr.com/vacancies/{match[1]}"
    except ValueError:
        pass
    return None


def _published(value: str) -> str | None:
    try:
        date = parsedate_to_datetime(value)
        return date.astimezone(timezone.utc).isoformat() if date.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def parse_feed(body: bytes, *, max_bytes: int = 1_000_000, max_items: int = 100) -> tuple[list[HabrRecord], int, int]:
    if len(body) > max_bytes:
        raise FeedError("response_too_large")
    try:
        text = body.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
            raise FeedError("unsafe_xml")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError) as exc:
        raise FeedError("invalid_xml") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise FeedError("unsupported_feed")
    items = root.findall("./channel/item")
    if len(items) > max_items:
        raise FeedError("item_limit")
    records = []
    invalid = 0
    for item in items:
        identity = canonical_habr_url(item.findtext("link", ""))
        guid = item.findtext("guid", "").strip()
        title_raw = _plain(item.findtext("title", ""))
        if not identity or (guid and guid != identity[0]) or not title_raw:
            invalid += 1
            continue
        wrapped = re.match(r"Требуется «(.+?)»(?:\s*\((.*)\))?$", title_raw)
        title = (wrapped[1] if wrapped else title_raw).strip()
        description = _plain(item.findtext("description", ""))
        author = _plain(item.findtext("author", ""))
        company_match = re.match(r"Компания «(.+?)» ищет", description)
        company = company_match[1] if company_match else (author if "[contact omitted]" not in author else "")
        # The advertised RSS has a stable prose prefix, not separate location/salary fields.
        prefix = re.sub(r"^Компания «.+?» ищет хорошего специалиста на вакансию «.+?»\.\s*", "", description)
        location_match = re.match(r"([^.!?]+\([^()]+\)(?:, [^.!?]+\([^()]+\))*)\.", prefix)
        location = location_match[1] if location_match else None
        salary_match = re.search(r"(?:(?:от|до)\s+)?\d[\d\s]*(?:(?:[—–-]|до)\s*\d[\d\s]*)?\s*(?:₽|\$|€|руб\.?|USD|EUR|RUB)", prefix, re.I)
        salary = salary_match[0].strip() if salary_match and "похожие специалисты" not in prefix.casefold() else None
        basis = "unspecified"
        if salary and re.search(r"на руки|после (?:вычета|уплаты) налог|\bnet\b", prefix, re.I):
            basis = "net"
        elif salary and re.search(r"до (?:вычета|уплаты) налог|\bgross\b", prefix, re.I):
            basis = "gross"
        remote = "Можно удалённо" if "можно удалённо" in description.casefold() else None
        if not remote and re.search(r"удал[её]н|\bremote\b", description, re.I):
            remote = "remote mentioned; conditions require review"
        grades = tuple(re.findall(r"#(junior|middle|senior|lead|intern)\b", description.casefold()))
        vacancy = NormalizedVacancy(
            source="habr", external_id=identity[0], title=title, company=company or None,
            location=location, employment="Полный рабочий день" if "Полный рабочий день" in description else None,
            description=description, url=identity[1], published_at=_published(item.findtext("pubDate", "")),
            snippet=description, content_completeness="PARTIAL", snippet_source="public_rss",
        )
        records.append(HabrRecord(vacancy, salary, remote, grades, salary_basis=basis))
    return records, len(items), invalid


def collect_rss(config: HabrRSSConfig, *, client: httpx.Client | None = None) -> HabrRSSResult:
    result = HabrRSSResult()
    unique: dict[str, HabrRecord] = {}
    owned = client is None
    client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
    try:
        for index, query in enumerate(dict.fromkeys(q.strip() for q in config.queries if q.strip())):
            diagnostic = FeedDiagnostic(query_index=index)
            result.feeds.append(diagnostic)
            seen: set[str] = set()
            for page in range(1, config.max_pages + 1):
                deadline = time.monotonic() + config.timeout_seconds
                try:
                    with client.stream("GET", FEED_URL, params={"q": query, "remote": int(config.remote_only), "page": page},
                                       timeout=config.timeout_seconds, follow_redirects=False) as response:
                        diagnostic.status = response.status_code
                        if response.status_code != 200:
                            diagnostic.stop = "http_error"
                            break  # no retries, alternate host or challenge handling
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() > deadline:
                                raise FeedError("deadline")
                            chunks.extend(chunk)
                            if len(chunks) > config.max_response_bytes:
                                raise FeedError("response_too_large")
                    records, raw, invalid = parse_feed(bytes(chunks), max_bytes=config.max_response_bytes, max_items=config.max_items)
                except httpx.TimeoutException:
                    diagnostic.stop = "timeout"
                    break
                except httpx.HTTPError:
                    diagnostic.stop = "network_error"
                    break
                except FeedError as exc:
                    diagnostic.stop = str(exc)  # only fixed local error codes, never response/exception text
                    break
                diagnostic.pages += 1
                diagnostic.raw += raw
                diagnostic.invalid += invalid
                ids = {r.vacancy.external_id for r in records}
                diagnostic.page_new_ids.append(len(ids - seen))
                for record in records:
                    key = record.vacancy.external_id
                    previous = unique.get(key, record)
                    unique[key] = replace(previous, queries=tuple(dict.fromkeys((*previous.queries, query))))
                if not raw:
                    diagnostic.stop = "empty"
                    break
                if not ids or ids <= seen:
                    diagnostic.stop = "repeated_page"
                    break
                seen.update(ids)
            if diagnostic.status in {401, 403, 429}:
                break  # stop the entire source, not another query after an access denial
    finally:
        if owned:
            client.close()
    result.records = list(unique.values())
    return result


def evaluate_record(record: HabrRecord, *, policy: DomainPolicy, now: datetime, max_age_days: int) -> dict:
    v = record.vacancy
    title = evaluate_title(v.title)
    classification = classify_title_match(v.title)
    seniority = classify_seniority(v.title).label
    domain = excluded_domain_reason(company=v.company or "", source=v.source, text=v.description, policy=policy)
    negatives = bool(re.search(r"\b(?:qa|qaa|aqa|sdet|android|ios|mobile|frontend|javascript)\b|тестиров|тестирован|автоматизаци[яи] тест|стаж[её]р", v.title, re.I))
    junior = seniority in {"JUNIOR", "INTERN", "LEAD_MANAGER"} or bool(set(record.grades) & {"junior", "intern", "lead"})
    date = datetime.fromisoformat(v.published_at) if v.published_at else None
    age = (now.astimezone(timezone.utc) - date).total_seconds() / 86400 if date else None
    fresh = age is not None and 0 <= age <= max_age_days
    jvm = bool(jvm_title_hits(v.title.casefold()))
    ai = bool(re.search(r"\b(?:AI|LLM|RAG)\b", v.title, re.I) and re.search(r"backend|back.end|бэкенд|бекенд", v.title, re.I))
    ai_review = bool(re.search(r"\b(?:LLM|RAG)\b", v.title, re.I) and re.search(r"разработчик|developer|engineer|инженер", v.title, re.I)
                     and not re.search(r"\b(?:data|ml|1c)\b|\.net\b|данных|\b1с\b", v.title, re.I) and not junior and not domain)
    unsuitable = domain or negatives or junior or not title.accepted or classification.rule in {"JAVA_QA_DOWNGRADE", "JAVA_ANDROID_DOWNGRADE"}
    # A plausible LLM/backend role rejected by today's Java-first filter is an
    # audit discrepancy, not proof that it is unsuitable under the revised goals.
    explicitly_unsuitable = bool(domain or negatives or junior or (unsuitable and not ai_review))
    relevant = bool((jvm or ai) and not unsuitable)
    # Use shared conservative eligibility. Russian worldwide wording is normalized
    # only when explicit and unnegated; geographic conflicts still win in the shared gate.
    worldwide = bool(re.search(r"(?:из любой (?:страны|точки мира)|по всему миру)", v.description, re.I))
    if re.search(r"\b(?:не|нельзя|невозможно)\b", v.description, re.I):
        worldwide = False
    # This observed Habr title wording is a country restriction, not worldwide
    # permission and not evidence of the candidate's residence or authorization.
    outside_limits = re.findall(r"(?:строго |только )?вне (?:РФ|РБ|России|Беларуси)\b", v.title + " " + v.description, re.I)
    feasibility = assess_application_feasibility(vacancy_text=v.description, location=v.location,
                                                remote_type="country_limited" if outside_limits else ("worldwide" if worldwide else "unknown"),
                                                location_restrictions=list(dict.fromkeys(outside_limits)))
    international = feasibility.remote_type == "worldwide" and feasibility.label == "LIKELY"
    restricted = feasibility.remote_type == "country_limited" or feasibility.work_authorization_requirement == "required"
    return {
        "id": v.external_id, "url": v.url, "title": v.title, "company": v.company,
        "location": v.location, "published_at": v.published_at, "salary": record.salary,
        "salary_basis": record.salary_basis, "remote_conditions": record.remote_conditions,
        "completeness": "PARTIAL", "queries": list(record.queries),
        "seniority": seniority, "fresh": fresh, "age_days": round(age, 1) if age is not None else None,
        "grades": list(record.grades), "ai_backend_review_candidate": ai_review,
        "jvm_candidate": relevant and jvm, "ai_backend_candidate": relevant and ai,
        "explicit_international_remote": international,
        "abroad_only_restriction": bool(outside_limits),
        "remote_geography_restricted": bool(record.remote_conditions and restricted),
        "remote_geography_unknown": bool(record.remote_conditions and not international and not restricted),
        "unsuitable": explicitly_unsuitable, "excluded_by_current_rules": bool(unsuitable),
        "domain_exclusion": bool(domain),
        "title_rule": classification.rule, "eligibility": "posting_support" if international else "manual",
    }
