from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
import re

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

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "target_company:greenhouse"


@dataclass(frozen=True)
class GreenhouseCompanyError:
    company_name: str
    message: str


@dataclass(frozen=True)
class GreenhouseWatchResult:
    vacancies: list[NormalizedVacancy]
    errors: list[GreenhouseCompanyError]


class GreenhouseTargetWatcher:
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
    ) -> GreenhouseWatchResult:
        vacancies: list[NormalizedVacancy] = []
        errors: list[GreenhouseCompanyError] = []
        with build_greenhouse_http_client(
            timeout_seconds=self._timeout_seconds,
            user_agent=self._user_agent,
        ) as client:
            for company in _as_company_list(companies):
                if not is_greenhouse_target(company):
                    continue
                try:
                    vacancies.extend(self._watch_company(company, client=client))
                except (GreenhouseCollectionError, ValueError) as exc:
                    message = str(exc).strip() or f"Greenhouse watch failed for '{company.name}'."
                    logger.warning("Greenhouse watch failed for '%s': %s", company.name, message)
                    errors.append(GreenhouseCompanyError(company_name=company.name, message=message))
        return GreenhouseWatchResult(vacancies=vacancies, errors=errors)

    def _watch_company(self, company: TargetCompany, *, client: httpx.Client) -> list[NormalizedVacancy]:
        board = resolve_greenhouse_board_slug(company)
        source = f"{_SOURCE_PREFIX}:{board}"
        jobs = fetch_greenhouse_board_jobs(board, client=client)
        collected: list[NormalizedVacancy] = []
        for item in jobs:
            vacancy = greenhouse_job_to_normalized(
                item,
                source=source,
                company=company.name,
            )
            if vacancy is None:
                continue
            if not matches_role_keywords(vacancy, company.role_keywords):
                continue
            collected.append(vacancy)
        return collected


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


def matches_role_keywords(vacancy: NormalizedVacancy, keywords: list[str]) -> bool:
    cleaned = [item.strip().casefold() for item in keywords if item.strip()]
    if not cleaned:
        return True
    haystack = f"{vacancy.title}\n{vacancy.description}".casefold()
    return any(keyword in haystack for keyword in cleaned)


def _as_company_list(companies: TargetCompany | Sequence[TargetCompany]) -> list[TargetCompany]:
    if isinstance(companies, TargetCompany):
        return [companies]
    return list(companies)
