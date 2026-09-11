from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.collectors.greenhouse_collector import (
    GreenhouseCollectionError,
    build_greenhouse_http_client,
    fetch_greenhouse_board_jobs,
    greenhouse_job_to_normalized,
)
from app.collectors.vacancy_collector import NormalizedVacancy

TARGET_COMPANY_GREENHOUSE_PREFIX = "target_company:greenhouse:"


class VacancyResolveError(Exception):
    """Raised when a vacancy cannot be resolved for autofill."""


@dataclass(frozen=True)
class ResolvedVacancy:
    source: str
    external_id: str
    title: str
    company: str | None
    url: str
    application_url: str
    vacancy: NormalizedVacancy | None = None


class VacancyResolver(Protocol):
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy: ...


def parse_target_company_greenhouse_source(source: str) -> str:
    cleaned = source.strip()
    if not cleaned.startswith(TARGET_COMPANY_GREENHOUSE_PREFIX):
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")
    board = cleaned[len(TARGET_COMPANY_GREENHOUSE_PREFIX) :].strip().strip("/")
    if not board or "/" in board or ":" in board:
        raise VacancyResolveError(f"Invalid Target Company Greenhouse source: {source}")
    return board.lower()


class GreenhouseTargetVacancyResolver:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        board = parse_target_company_greenhouse_source(source)
        wanted_id = str(external_id).strip()
        if not wanted_id:
            raise VacancyResolveError("Vacancy external_id is empty.")

        try:
            with build_greenhouse_http_client(
                timeout_seconds=self._timeout_seconds,
                user_agent=self._user_agent,
            ) as client:
                jobs = fetch_greenhouse_board_jobs(board, client=client)
        except GreenhouseCollectionError as exc:
            raise VacancyResolveError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise VacancyResolveError(f"Greenhouse request failed for board '{board}'.") from exc

        for item in jobs:
            normalized = greenhouse_job_to_normalized(item, source=source)
            if normalized is None:
                continue
            if normalized.external_id != wanted_id:
                continue
            application_url = (normalized.url or "").strip()
            if not application_url:
                raise VacancyResolveError(
                    f"Vacancy {source} {wanted_id} has an empty application URL."
                )
            return ResolvedVacancy(
                source=normalized.source,
                external_id=normalized.external_id,
                title=normalized.title,
                company=normalized.company,
                url=application_url,
                application_url=application_url,
                vacancy=normalized,
            )

        raise VacancyResolveError(
            f"Vacancy {wanted_id} was not found for source {source}."
        )


class DefaultVacancyResolver:
    def __init__(self, greenhouse_resolver: GreenhouseTargetVacancyResolver | None = None) -> None:
        self._greenhouse = greenhouse_resolver or GreenhouseTargetVacancyResolver()

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        cleaned = source.strip()
        if cleaned.startswith(TARGET_COMPANY_GREENHOUSE_PREFIX):
            return self._greenhouse.resolve(cleaned, external_id)
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")
