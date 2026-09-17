from __future__ import annotations

import httpx
import pytest
import respx

from app.application.autofill.resolver import (
    DefaultVacancyResolver,
    LeverTargetVacancyResolver,
    VacancyResolveError,
)
from app.company_watch.watchers.lever import lever_postings_endpoint


def _job(
    *,
    job_id: str | None = "abc123",
    title: str = "Java Backend Engineer",
    url: str = "https://jobs.lever.co/qonto/abc123",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": title,
        "hostedUrl": url,
        "categories": {"location": "Paris"},
        "descriptionPlain": "Java backend services and payments.",
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_resolves_target_company_lever_job() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(status_code=200, json=[_job()])
    )

    resolved = LeverTargetVacancyResolver().resolve(
        "target_company:lever:qonto",
        "abc123",
    )

    assert resolved.source == "target_company:lever:qonto"
    assert resolved.external_id == "abc123"
    assert resolved.title == "Java Backend Engineer"
    assert resolved.application_url == "https://jobs.lever.co/qonto/abc123"
    assert resolved.url == resolved.application_url
    assert resolved.vacancy is not None
    assert resolved.vacancy.source == "target_company:lever:qonto"


@respx.mock
def test_missing_job_is_a_clear_failure() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(status_code=200, json=[_job()])
    )

    with pytest.raises(VacancyResolveError, match="not found"):
        LeverTargetVacancyResolver().resolve(
            "target_company:lever:qonto",
            "does-not-exist",
        )


def test_bad_source_is_a_clear_failure() -> None:
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        LeverTargetVacancyResolver().resolve("lever:qonto", "abc123")


@respx.mock
def test_empty_application_url_is_resolve_failure() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(status_code=200, json=[_job(url="")])
    )
    with pytest.raises(VacancyResolveError, match="not found"):
        LeverTargetVacancyResolver().resolve(
            "target_company:lever:qonto",
            "abc123",
        )


@respx.mock
def test_default_resolver_dispatches_lever_and_greenhouse_by_source() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(status_code=200, json=[_job()])
    )
    resolved = DefaultVacancyResolver().resolve(
        "target_company:lever:qonto",
        "abc123",
    )
    assert resolved.external_id == "abc123"
    assert resolved.application_url.endswith("/abc123")


def test_default_resolver_rejects_unknown_source() -> None:
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        DefaultVacancyResolver().resolve("target_company:ashby:acme", "1")
