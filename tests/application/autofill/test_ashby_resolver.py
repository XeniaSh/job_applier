from __future__ import annotations

import httpx
import pytest
import respx

from app.application.autofill.resolver import (
    AshbyTargetVacancyResolver,
    DefaultVacancyResolver,
    VacancyResolveError,
)
from app.company_watch.watchers.ashby import ashby_jobs_endpoint


def _job(
    *,
    job_id: str | None = "5f6e7d8c-1234-5678-9abc-def012345678",
    title: str = "Backend Engineer",
    job_url: str = "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678",
    apply_url: str | None = "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678/application",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "jobUrl": job_url,
        "locationName": "Remote",
        "descriptionPlain": "Backend engineering role.",
    }
    if job_id is not None:
        payload["id"] = job_id
    if apply_url is not None:
        payload["applyUrl"] = apply_url
    return payload


@respx.mock
def test_resolves_target_company_ashby_job() -> None:
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )

    resolved = AshbyTargetVacancyResolver().resolve(
        "target_company:ashby:perk",
        "5f6e7d8c-1234-5678-9abc-def012345678",
    )

    assert resolved.source == "target_company:ashby:perk"
    assert resolved.external_id == "5f6e7d8c-1234-5678-9abc-def012345678"
    assert resolved.title == "Backend Engineer"
    assert resolved.url == "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678"
    assert (
        resolved.application_url
        == "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678/application"
    )
    assert resolved.vacancy is not None
    assert resolved.vacancy.source == "target_company:ashby:perk"


@respx.mock
def test_missing_job_is_a_clear_failure() -> None:
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )

    with pytest.raises(VacancyResolveError, match="not found"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "does-not-exist",
        )


def test_bad_source_is_a_clear_failure() -> None:
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        AshbyTargetVacancyResolver().resolve("ashby:perk", "abc123")


@respx.mock
def test_missing_apply_url_is_a_clear_failure() -> None:
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job(apply_url=None)]})
    )

    with pytest.raises(VacancyResolveError, match="no confirmed same-posting"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "5f6e7d8c-1234-5678-9abc-def012345678",
        )


@respx.mock
def test_apply_url_for_a_different_posting_is_rejected() -> None:
    # A malformed/mismatched applyUrl (e.g. pointing at another job's apply
    # page) must fail closed rather than being trusted at face value.
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(
                        apply_url="https://jobs.ashbyhq.com/Perk/some-other-job-id/application"
                    )
                ]
            },
        )
    )

    with pytest.raises(VacancyResolveError, match="no confirmed same-posting"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "5f6e7d8c-1234-5678-9abc-def012345678",
        )


@respx.mock
def test_non_canonical_job_url_is_rejected() -> None:
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={"jobs": [_job(job_url="https://careers.example.com/perk/job-1", apply_url=None)]},
        )
    )

    with pytest.raises(VacancyResolveError, match="canonical Ashby job URL"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "5f6e7d8c-1234-5678-9abc-def012345678",
        )


@respx.mock
def test_job_url_posting_id_mismatched_with_item_id_is_rejected() -> None:
    # The `id` field matches the request, but `jobUrl` itself points at a
    # different posting ID -- an inconsistent API record must fail closed
    # rather than resolving to the wrong job.
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(
                        job_url="https://jobs.ashbyhq.com/Perk/some-other-job-id",
                        apply_url="https://jobs.ashbyhq.com/Perk/some-other-job-id/application",
                    )
                ]
            },
        )
    )

    with pytest.raises(VacancyResolveError, match="does not match the requested"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "5f6e7d8c-1234-5678-9abc-def012345678",
        )


@respx.mock
def test_job_url_board_mismatched_with_requested_source_is_rejected() -> None:
    # jobUrl and applyUrl agree with each other and with the item's `id`,
    # but both point at a different job board than the requested source.
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(
                        job_url="https://jobs.ashbyhq.com/OtherBoard/5f6e7d8c-1234-5678-9abc-def012345678",
                        apply_url=(
                            "https://jobs.ashbyhq.com/OtherBoard/"
                            "5f6e7d8c-1234-5678-9abc-def012345678/application"
                        ),
                    )
                ]
            },
        )
    )

    with pytest.raises(VacancyResolveError, match="does not match the requested"):
        AshbyTargetVacancyResolver().resolve(
            "target_company:ashby:perk",
            "5f6e7d8c-1234-5678-9abc-def012345678",
        )


@respx.mock
def test_default_resolver_dispatches_ashby_by_source() -> None:
    respx.get(ashby_jobs_endpoint("perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )
    resolved = DefaultVacancyResolver().resolve(
        "target_company:ashby:perk",
        "5f6e7d8c-1234-5678-9abc-def012345678",
    )
    assert resolved.external_id == "5f6e7d8c-1234-5678-9abc-def012345678"
    assert resolved.application_url.endswith("/application")
