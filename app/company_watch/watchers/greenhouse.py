from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
import re
import time

import httpx

from app.collectors.greenhouse_collector import (
    GreenhouseCollectionError,
    build_greenhouse_http_client,
    fetch_greenhouse_board_jobs,
    greenhouse_job_to_normalized,
    normalize_greenhouse_board,
)
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.models import TargetCompany
from app.company_watch.prefilter import passes_role_prefilter

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "target_company:greenhouse"
_TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_NON_RETRYABLE_ERROR_TYPES = frozenset({"ValueError", "JSONDecodeError"})


@dataclass(frozen=True)
class GreenhouseCompanyError:
    company_name: str
    message: str
    slug: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    response_snippet: str | None = None
    error_type: str | None = None
    attempts: int | None = None


@dataclass(frozen=True)
class GreenhouseWatchResult:
    vacancies: list[NormalizedVacancy]
    errors: list[GreenhouseCompanyError]
    raw_fetched: int = 0


class GreenhouseTargetWatcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
        max_attempts: int = 3,
        delay_between_companies_seconds: float = 1.0,
        retry_backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._max_attempts = max(1, max_attempts)
        self._delay_between_companies_seconds = delay_between_companies_seconds
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep if sleep is not None else time.sleep

    def watch(
        self,
        companies: TargetCompany | Sequence[TargetCompany],
    ) -> GreenhouseWatchResult:
        vacancies: list[NormalizedVacancy] = []
        errors: list[GreenhouseCompanyError] = []
        raw_fetched = 0
        pending_delay = False
        with build_greenhouse_http_client(
            timeout_seconds=self._timeout_seconds,
            user_agent=self._user_agent,
        ) as client:
            for company in _as_company_list(companies):
                if not is_greenhouse_target(company):
                    continue
                if pending_delay:
                    self._sleep_if_needed(self._delay_between_companies_seconds)
                pending_delay = True
                try:
                    collected, fetched = self._watch_company(company, client=client)
                    vacancies.extend(collected)
                    raw_fetched += fetched
                except GreenhouseCollectionError as exc:
                    errors.append(_company_error_from_collection(company, exc))
                except ValueError as exc:
                    message = str(exc).strip() or f"Greenhouse watch failed for '{company.name}'."
                    logger.warning("Greenhouse watch failed for '%s': %s", company.name, message)
                    errors.append(
                        GreenhouseCompanyError(
                            company_name=company.name,
                            message=message,
                            error_type=type(exc).__name__,
                        )
                    )
        return GreenhouseWatchResult(
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
        board = resolve_greenhouse_board_slug(company)
        source = f"{_SOURCE_PREFIX}:{board}"
        jobs = self._fetch_board_jobs(board, client=client)
        collected: list[NormalizedVacancy] = []
        raw_fetched = 0
        for item in jobs:
            vacancy = greenhouse_job_to_normalized(
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

    def _fetch_board_jobs(self, board: str, *, client: httpx.Client) -> list[object]:
        last_error: GreenhouseCollectionError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return fetch_greenhouse_board_jobs(board, client=client)
            except GreenhouseCollectionError as exc:
                last_error = exc
                _set_attempts(exc, attempt)
                if attempt >= self._max_attempts or not _is_transient_greenhouse_error(exc):
                    raise
                backoff = self._retry_backoff_seconds * (2 ** (attempt - 1))
                self._sleep_if_needed(backoff)
        assert last_error is not None
        raise last_error

    def _sleep_if_needed(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)


def is_greenhouse_target(company: TargetCompany) -> bool:
    watcher_type = company.watcher_type.strip().lower()
    ats = (company.ats or "").strip().lower()
    return watcher_type == "greenhouse" or ats == "greenhouse"


def resolve_greenhouse_board_slug(company: TargetCompany) -> str:
    for raw in (company.job_board_url, company.career_url):
        if not raw:
            continue
        if "://" not in raw or "greenhouse.io" in raw.lower():
            return normalize_greenhouse_board(raw)
    slug = re.sub(r"[^a-z0-9]+", "", company.name.lower())
    if not slug:
        raise ValueError(f"Cannot resolve Greenhouse board for company: {company.name}")
    return slug


def _is_transient_greenhouse_error(exc: GreenhouseCollectionError) -> bool:
    if exc.status_code is not None:
        return exc.status_code in _TRANSIENT_HTTP_STATUS_CODES
    error_type = exc.error_type or ""
    return error_type not in _NON_RETRYABLE_ERROR_TYPES


def _company_error_from_collection(
    company: TargetCompany,
    exc: GreenhouseCollectionError,
) -> GreenhouseCompanyError:
    message = str(exc).strip() or f"Greenhouse watch failed for '{company.name}'."
    logger.warning("Greenhouse watch failed for '%s': %s", company.name, message)
    return GreenhouseCompanyError(
        company_name=company.name,
        message=message,
        slug=exc.board,
        endpoint=exc.endpoint,
        status_code=exc.status_code,
        response_snippet=exc.response_snippet,
        error_type=exc.error_type,
        attempts=getattr(exc, "attempts", None),
    )


def _set_attempts(exc: GreenhouseCollectionError, attempts: int) -> None:
    setattr(exc, "attempts", attempts)


def _as_company_list(companies: TargetCompany | Sequence[TargetCompany]) -> list[TargetCompany]:
    if isinstance(companies, TargetCompany):
        return [companies]
    return list(companies)
