from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from urllib.parse import urlparse, urlsplit

import httpx

from app.collectors.greenhouse_collector import clean_html_to_text
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.models import TargetCompany
from app.company_watch.prefilter import passes_role_prefilter

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "target_company:lever"
_RESPONSE_SNIPPET_LIMIT = 400


@dataclass(frozen=True)
class LeverCompanyError:
    company_name: str
    message: str
    slug: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    response_snippet: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class LeverWatchResult:
    vacancies: list[NormalizedVacancy]
    errors: list[LeverCompanyError]
    raw_fetched: int = 0


class LeverCollectionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        slug: str | None = None,
        endpoint: str | None = None,
        status_code: int | None = None,
        response_snippet: str | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.slug = slug
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_snippet = response_snippet
        self.error_type = error_type


class LeverTargetWatcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def watch(self, companies: TargetCompany | Sequence[TargetCompany]) -> LeverWatchResult:
        vacancies: list[NormalizedVacancy] = []
        errors: list[LeverCompanyError] = []
        raw_fetched = 0
        with _build_client(
            timeout_seconds=self._timeout_seconds,
            user_agent=self._user_agent,
        ) as client:
            for company in _as_company_list(companies):
                if not is_lever_target(company):
                    continue
                try:
                    collected, fetched = self._watch_company(company, client=client)
                    vacancies.extend(collected)
                    raw_fetched += fetched
                except LeverCollectionError as exc:
                    message = str(exc).strip() or f"Lever watch failed for '{company.name}'."
                    logger.warning("Lever watch failed for '%s': %s", company.name, message)
                    errors.append(
                        LeverCompanyError(
                            company_name=company.name,
                            message=message,
                            slug=exc.slug,
                            endpoint=exc.endpoint,
                            status_code=exc.status_code,
                            response_snippet=exc.response_snippet,
                            error_type=exc.error_type,
                        )
                    )
                except ValueError as exc:
                    message = str(exc).strip() or f"Lever watch failed for '{company.name}'."
                    logger.warning("Lever watch failed for '%s': %s", company.name, message)
                    errors.append(
                        LeverCompanyError(
                            company_name=company.name,
                            message=message,
                            error_type=type(exc).__name__,
                        )
                    )
        return LeverWatchResult(vacancies=vacancies, errors=errors, raw_fetched=raw_fetched)

    def _watch_company(
        self,
        company: TargetCompany,
        *,
        client: httpx.Client,
    ) -> tuple[list[NormalizedVacancy], int]:
        slug = resolve_lever_slug(company)
        source = f"{_SOURCE_PREFIX}:{slug}"
        jobs = fetch_lever_postings(slug, client=client)
        collected: list[NormalizedVacancy] = []
        raw_fetched = 0
        for item in jobs:
            vacancy = lever_job_to_normalized(item, source=source, company=company.name)
            if vacancy is None:
                continue
            raw_fetched += 1
            if not passes_role_prefilter(vacancy, company):
                continue
            collected.append(vacancy)
        return collected, raw_fetched


def is_lever_target(company: TargetCompany) -> bool:
    watcher_type = company.watcher_type.strip().lower()
    ats = (company.ats or "").strip().lower()
    return watcher_type == "lever" or ats == "lever"


def lever_postings_endpoint(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def resolve_lever_slug(company: TargetCompany) -> str:
    for raw in (company.job_board_url, company.career_url):
        if not raw:
            continue
        parsed = urlparse(raw.strip())
        host = parsed.netloc.lower()
        if host.endswith("lever.co"):
            parts = [part for part in parsed.path.split("/") if part]
            if parts:
                return parts[0].lower()
        if "://" not in raw.strip():
            return raw.strip().strip("/").lower()
    slug = "".join(ch for ch in company.name.lower() if ch.isalnum())
    if not slug:
        raise ValueError(f"Cannot resolve Lever site slug for company: {company.name}")
    return slug


def fetch_lever_postings(slug: str, *, client: httpx.Client) -> list[object]:
    endpoint = lever_postings_endpoint(slug)
    response: httpx.Response | None = None
    try:
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise _lever_request_error(slug, endpoint, exc, response=exc.response) from exc
    except httpx.HTTPError as exc:
        raise _lever_request_error(slug, endpoint, exc, response=response) from exc
    except ValueError as exc:
        raise _lever_request_error(slug, endpoint, exc, response=response) from exc

    if not isinstance(payload, list):
        logger.warning("Unexpected Lever payload for site '%s'", slug)
        return []
    return payload


def lever_job_to_normalized(
    item: object,
    *,
    source: str,
    company: str | None = None,
) -> NormalizedVacancy | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("text") or item.get("title") or "").strip()
    url = str(item.get("hostedUrl") or item.get("applyUrl") or "").strip()
    external_id = str(item.get("id") or "").strip()
    if not external_id and url:
        path = urlsplit(url).path.rstrip("/")
        external_id = path.rsplit("/", 1)[-1].strip() if path else ""
    if not title or not url or not external_id:
        return None

    categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
    location = str(categories.get("location") or "").strip() or None
    employment = str(categories.get("commitment") or "").strip() or None
    description = str(item.get("descriptionPlain") or "").strip()
    if not description:
        description = clean_html_to_text(str(item.get("description") or ""))
    if not description:
        description = title
    published_at = item.get("createdAt")
    published = str(published_at) if published_at not in (None, "") else None

    return NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company=company.strip() if isinstance(company, str) and company.strip() else None,
        location=location,
        employment=employment,
        description=description,
        url=url,
        published_at=published,
    )


def _build_client(*, timeout_seconds: float, user_agent: str) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=timeout_seconds, write=10.0, pool=5.0),
        headers={"User-Agent": user_agent},
    )


def _lever_request_error(
    slug: str,
    endpoint: str,
    exc: Exception,
    *,
    response: httpx.Response | None,
) -> LeverCollectionError:
    status_code = getattr(response, "status_code", None)
    error_type = type(exc).__name__
    if isinstance(status_code, int):
        message = f"Lever site '{slug}' request failed ({status_code})."
    else:
        message = f"Lever site '{slug}' request failed ({error_type})."
    snippet = None
    if response is not None:
        raw_text = getattr(response, "text", "") or ""
        snippet = " ".join(raw_text.split())
        if snippet and len(snippet) > _RESPONSE_SNIPPET_LIMIT:
            snippet = snippet[:_RESPONSE_SNIPPET_LIMIT] + "..."
        if not snippet:
            snippet = None
    return LeverCollectionError(
        message,
        slug=slug,
        endpoint=endpoint,
        status_code=status_code if isinstance(status_code, int) else None,
        response_snippet=snippet,
        error_type=error_type,
    )


def _as_company_list(companies: TargetCompany | Sequence[TargetCompany]) -> list[TargetCompany]:
    if isinstance(companies, TargetCompany):
        return [companies]
    return list(companies)
