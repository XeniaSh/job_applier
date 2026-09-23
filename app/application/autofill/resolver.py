from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.collectors.greenhouse_collector import (
    GreenhouseCollectionError,
    build_greenhouse_http_client,
    canonical_greenhouse_embed_application_url,
    fetch_greenhouse_board_jobs,
    greenhouse_job_to_normalized,
)
from app.application.autofill.ashby_url import is_canonical_ashby_hosted_url, is_same_job_apply_url
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.watchers.ashby import (
    AshbyCollectionError,
    ashby_job_to_normalized,
    build_ashby_http_client,
    fetch_ashby_jobs,
)
from app.company_watch.watchers.lever import (
    LeverCollectionError,
    build_lever_http_client,
    fetch_lever_postings,
    lever_job_to_normalized,
)

TARGET_COMPANY_GREENHOUSE_PREFIX = "target_company:greenhouse:"
TARGET_COMPANY_LEVER_PREFIX = "target_company:lever:"
TARGET_COMPANY_ASHBY_PREFIX = "target_company:ashby:"


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
            if normalized.original_url:
                application_url = canonical_greenhouse_embed_application_url(board, wanted_id)
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


def parse_target_company_lever_source(source: str) -> str:
    cleaned = source.strip()
    if not cleaned.startswith(TARGET_COMPANY_LEVER_PREFIX):
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")
    slug = cleaned[len(TARGET_COMPANY_LEVER_PREFIX) :].strip().strip("/")
    if not slug or "/" in slug or ":" in slug:
        raise VacancyResolveError(f"Invalid Target Company Lever source: {source}")
    return slug.lower()


class LeverTargetVacancyResolver:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        slug = parse_target_company_lever_source(source)
        wanted_id = str(external_id).strip()
        if not wanted_id:
            raise VacancyResolveError("Vacancy external_id is empty.")

        try:
            with build_lever_http_client(
                timeout_seconds=self._timeout_seconds,
                user_agent=self._user_agent,
            ) as client:
                jobs = fetch_lever_postings(slug, client=client)
        except LeverCollectionError as exc:
            raise VacancyResolveError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise VacancyResolveError(f"Lever request failed for site '{slug}'.") from exc

        for item in jobs:
            normalized = lever_job_to_normalized(item, source=source)
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


def parse_target_company_ashby_source(source: str) -> str:
    cleaned = source.strip()
    if not cleaned.startswith(TARGET_COMPANY_ASHBY_PREFIX):
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")
    slug = cleaned[len(TARGET_COMPANY_ASHBY_PREFIX) :].strip().strip("/")
    if not slug or "/" in slug or ":" in slug:
        raise VacancyResolveError(f"Invalid Target Company Ashby source: {source}")
    return slug.lower()


def _ashby_job_url_matches_request(job_url: str, *, board_slug: str, posting_id: str) -> bool:
    """True only if `job_url`'s own path is exactly `/<board_slug>/<posting_id>`
    (board slug compared case-insensitively, posting id compared exactly, no
    extra path segments). Guards against an Ashby API record whose `jobUrl`
    disagrees with the `id` field it was matched on -- an inconsistent record
    must not resolve to a different job than the one requested.
    """
    parsed = urlsplit(job_url)
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) != 2:
        return False
    url_board, url_posting_id = segments
    return url_board.lower() == board_slug.lower() and url_posting_id == posting_id


class AshbyTargetVacancyResolver:
    """Resolves a Target Company Ashby vacancy using only the public,
    read-only Ashby posting API. Never opens a browser or touches the live
    hosted form DOM -- `url`/`application_url` are only ever the API's own
    `jobUrl`/`applyUrl` for the matched posting, and are rejected unless
    `applyUrl` is confirmed to be that exact job's own `/application` link
    (`ashby_url.is_same_job_apply_url`). Form support is separately gated
    by `AshbyAdapter` recognition after browser navigation.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        slug = parse_target_company_ashby_source(source)
        wanted_id = str(external_id).strip()
        if not wanted_id:
            raise VacancyResolveError("Vacancy external_id is empty.")

        try:
            with build_ashby_http_client(
                timeout_seconds=self._timeout_seconds,
                user_agent=self._user_agent,
            ) as client:
                jobs = fetch_ashby_jobs(slug, client=client)
        except AshbyCollectionError as exc:
            raise VacancyResolveError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise VacancyResolveError(f"Ashby request failed for board '{slug}'.") from exc

        for item in jobs:
            normalized = ashby_job_to_normalized(item, source=source)
            if normalized is None:
                continue
            if normalized.external_id != wanted_id:
                continue
            job_url = str(item.get("jobUrl") or "").strip() if isinstance(item, dict) else ""
            apply_url = str(item.get("applyUrl") or "").strip() if isinstance(item, dict) else ""
            if not is_canonical_ashby_hosted_url(job_url):
                raise VacancyResolveError(
                    f"Vacancy {source} {wanted_id} does not have a canonical Ashby job URL."
                )
            if not _ashby_job_url_matches_request(job_url, board_slug=slug, posting_id=wanted_id):
                raise VacancyResolveError(
                    f"Vacancy {source} {wanted_id} jobUrl does not match the requested "
                    "board and posting ID."
                )
            if not is_same_job_apply_url(job_url, apply_url):
                raise VacancyResolveError(
                    f"Vacancy {source} {wanted_id} has no confirmed same-posting Ashby apply URL."
                )
            return ResolvedVacancy(
                source=normalized.source,
                external_id=normalized.external_id,
                title=normalized.title,
                company=normalized.company,
                url=job_url,
                application_url=apply_url,
                vacancy=normalized,
            )

        raise VacancyResolveError(
            f"Vacancy {wanted_id} was not found for source {source}."
        )


class DefaultVacancyResolver:
    def __init__(
        self,
        greenhouse_resolver: GreenhouseTargetVacancyResolver | None = None,
        lever_resolver: LeverTargetVacancyResolver | None = None,
        ashby_resolver: AshbyTargetVacancyResolver | None = None,
    ) -> None:
        self._greenhouse = greenhouse_resolver or GreenhouseTargetVacancyResolver()
        self._lever = lever_resolver or LeverTargetVacancyResolver()
        self._ashby = ashby_resolver or AshbyTargetVacancyResolver()

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        cleaned = source.strip()
        if cleaned.startswith(TARGET_COMPANY_GREENHOUSE_PREFIX):
            return self._greenhouse.resolve(cleaned, external_id)
        if cleaned.startswith(TARGET_COMPANY_LEVER_PREFIX):
            return self._lever.resolve(cleaned, external_id)
        if cleaned.startswith(TARGET_COMPANY_ASHBY_PREFIX):
            return self._ashby.resolve(cleaned, external_id)
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")
