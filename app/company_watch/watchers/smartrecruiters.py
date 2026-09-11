from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from urllib.parse import urlparse, urlsplit

import httpx

from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.models import TargetCompany
from app.company_watch.prefilter import passes_role_prefilter

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "target_company:smartrecruiters"
_RESPONSE_SNIPPET_LIMIT = 400


@dataclass(frozen=True)
class SmartRecruitersCompanyError:
    company_name: str
    message: str
    slug: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    response_snippet: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class SmartRecruitersWatchResult:
    vacancies: list[NormalizedVacancy]
    errors: list[SmartRecruitersCompanyError]
    raw_fetched: int = 0


class SmartRecruitersCollectionError(Exception):
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


class SmartRecruitersTargetWatcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def watch(
        self,
        companies: TargetCompany | Sequence[TargetCompany],
    ) -> SmartRecruitersWatchResult:
        vacancies: list[NormalizedVacancy] = []
        errors: list[SmartRecruitersCompanyError] = []
        raw_fetched = 0
        with _build_client(
            timeout_seconds=self._timeout_seconds,
            user_agent=self._user_agent,
        ) as client:
            for company in _as_company_list(companies):
                if not is_smartrecruiters_target(company):
                    continue
                try:
                    collected, fetched = self._watch_company(company, client=client)
                    vacancies.extend(collected)
                    raw_fetched += fetched
                except SmartRecruitersCollectionError as exc:
                    message = str(exc).strip() or f"SmartRecruiters watch failed for '{company.name}'."
                    logger.warning("SmartRecruiters watch failed for '%s': %s", company.name, message)
                    errors.append(
                        SmartRecruitersCompanyError(
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
                    message = str(exc).strip() or f"SmartRecruiters watch failed for '{company.name}'."
                    logger.warning("SmartRecruiters watch failed for '%s': %s", company.name, message)
                    errors.append(
                        SmartRecruitersCompanyError(
                            company_name=company.name,
                            message=message,
                            error_type=type(exc).__name__,
                        )
                    )
        return SmartRecruitersWatchResult(
            vacancies=vacancies,
            errors=errors,
            raw_fetched=raw_fetched,
        )

    def _watch_company(
        self,
        company: TargetCompany,
        *,
        client: httpx.Client,
    ) -> tuple[list[NormalizedVacancy], int]:
        slug = resolve_smartrecruiters_slug(company)
        source = f"{_SOURCE_PREFIX}:{slug.lower()}"
        jobs = fetch_smartrecruiters_postings(slug, client=client)
        collected: list[NormalizedVacancy] = []
        raw_fetched = 0
        for item in jobs:
            vacancy = smartrecruiters_job_to_normalized(
                item,
                source=source,
                company=company.name,
            )
            if vacancy is None:
                continue
            raw_fetched += 1
            if not passes_role_prefilter(vacancy, company):
                continue
            collected.append(vacancy)
        return collected, raw_fetched


def is_smartrecruiters_target(company: TargetCompany) -> bool:
    watcher_type = company.watcher_type.strip().lower()
    ats = (company.ats or "").strip().lower()
    return watcher_type == "smartrecruiters" or ats == "smartrecruiters"


def smartrecruiters_postings_endpoint(slug: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"


def resolve_smartrecruiters_slug(company: TargetCompany) -> str:
    for raw in (company.job_board_url, company.career_url):
        if not raw:
            continue
        parsed = urlparse(raw.strip())
        host = parsed.netloc.lower()
        if "smartrecruiters.com" in host:
            parts = [part for part in parsed.path.split("/") if part]
            if parts:
                return parts[0]
        if "://" not in raw.strip():
            return raw.strip().strip("/")
    raise ValueError(f"Cannot resolve SmartRecruiters company slug for: {company.name}")


def fetch_smartrecruiters_postings(slug: str, *, client: httpx.Client) -> list[object]:
    endpoint = smartrecruiters_postings_endpoint(slug)
    response: httpx.Response | None = None
    try:
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise _request_error(slug, endpoint, exc, response=exc.response) from exc
    except httpx.HTTPError as exc:
        raise _request_error(slug, endpoint, exc, response=response) from exc
    except ValueError as exc:
        raise _request_error(slug, endpoint, exc, response=response) from exc

    jobs = payload.get("content") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        logger.warning("Unexpected SmartRecruiters payload for company '%s'", slug)
        return []
    return jobs


def smartrecruiters_job_to_normalized(
    item: object,
    *,
    source: str,
    company: str | None = None,
) -> NormalizedVacancy | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("name") or item.get("title") or "").strip()
    url = str(item.get("ref") or item.get("applyUrl") or "").strip()
    external_id = str(item.get("id") or "").strip()
    if not external_id and url:
        path = urlsplit(url).path.rstrip("/")
        external_id = path.rsplit("/", 1)[-1].strip() if path else ""
    if not title or not url or not external_id:
        return None
    location = _location_name(item.get("location"))
    employment = None
    type_of_employment = item.get("typeOfEmployment")
    if isinstance(type_of_employment, dict):
        employment = str(type_of_employment.get("label") or "").strip() or None
    description = _job_ad_text(item.get("jobAd")) or title
    published = str(item.get("releasedDate") or "") or None
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


def _location_name(location: object) -> str | None:
    if not isinstance(location, dict):
        return None
    parts = [
        str(location.get("city") or "").strip(),
        str(location.get("region") or "").strip(),
        str(location.get("country") or "").strip(),
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or None


def _job_ad_text(job_ad: object) -> str:
    if not isinstance(job_ad, dict):
        return ""
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        text = str(section.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _build_client(*, timeout_seconds: float, user_agent: str) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=timeout_seconds, write=10.0, pool=5.0),
        headers={"User-Agent": user_agent},
    )


def _request_error(
    slug: str,
    endpoint: str,
    exc: Exception,
    *,
    response: httpx.Response | None,
) -> SmartRecruitersCollectionError:
    status_code = getattr(response, "status_code", None)
    error_type = type(exc).__name__
    if isinstance(status_code, int):
        message = f"SmartRecruiters company '{slug}' request failed ({status_code})."
    else:
        message = f"SmartRecruiters company '{slug}' request failed ({error_type})."
    snippet = None
    if response is not None:
        raw_text = getattr(response, "text", "") or ""
        snippet = " ".join(raw_text.split())
        if snippet and len(snippet) > _RESPONSE_SNIPPET_LIMIT:
            snippet = snippet[:_RESPONSE_SNIPPET_LIMIT] + "..."
        if not snippet:
            snippet = None
    return SmartRecruitersCollectionError(
        message,
        slug=slug,
        endpoint=endpoint,
        status_code=status_code if isinstance(status_code, int) else None,
        response_snippet=snippet,
        error_type=error_type,
    )


def _as_company_list(
    companies: TargetCompany | Sequence[TargetCompany],
) -> list[TargetCompany]:
    if isinstance(companies, TargetCompany):
        return [companies]
    return list(companies)
