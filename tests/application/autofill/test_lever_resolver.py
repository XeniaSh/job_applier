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
        DefaultVacancyResolver().resolve("target_company:smartrecruiters:acme", "1")


@respx.mock
def test_eu_board_resolves_after_global_404() -> None:
    respx.get(lever_postings_endpoint("pnlfin", region="global")).mock(
        return_value=httpx.Response(status_code=404)
    )
    respx.get(lever_postings_endpoint("pnlfin", region="eu")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[_job(job_id="eu-1", url="https://jobs.eu.lever.co/pnlfin/eu-1")],
        )
    )

    resolved = LeverTargetVacancyResolver().resolve(
        "target_company:lever:pnlfin",
        "eu-1",
    )

    assert resolved.source == "target_company:lever:pnlfin"
    assert resolved.external_id == "eu-1"
    assert resolved.application_url == "https://jobs.eu.lever.co/pnlfin/eu-1"
    assert resolved.url == resolved.application_url


@respx.mock
def test_both_regions_missing_job_fails_closed() -> None:
    respx.get(lever_postings_endpoint("pnlfin", region="global")).mock(
        return_value=httpx.Response(status_code=404)
    )
    respx.get(lever_postings_endpoint("pnlfin", region="eu")).mock(
        return_value=httpx.Response(status_code=404)
    )

    with pytest.raises(VacancyResolveError):
        LeverTargetVacancyResolver().resolve(
            "target_company:lever:pnlfin",
            "eu-1",
        )


@respx.mock
def test_wrong_id_on_global_board_does_not_fall_back_to_eu() -> None:
    respx.get(lever_postings_endpoint("qonto", region="global")).mock(
        return_value=httpx.Response(status_code=200, json=[_job(job_id="abc123")])
    )
    eu_route = respx.get(lever_postings_endpoint("qonto", region="eu")).mock(
        return_value=httpx.Response(status_code=200, json=[_job(job_id="abc123")])
    )

    with pytest.raises(VacancyResolveError, match="not found"):
        LeverTargetVacancyResolver().resolve(
            "target_company:lever:qonto",
            "does-not-exist",
        )

    assert not eu_route.called


@respx.mock
def test_non_404_global_error_is_not_masked_by_eu_fallback() -> None:
    respx.get(lever_postings_endpoint("pnlfin", region="global")).mock(
        return_value=httpx.Response(status_code=500)
    )
    eu_route = respx.get(lever_postings_endpoint("pnlfin", region="eu")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[_job(job_id="eu-1", url="https://jobs.eu.lever.co/pnlfin/eu-1")],
        )
    )

    with pytest.raises(VacancyResolveError, match="500"):
        LeverTargetVacancyResolver().resolve(
            "target_company:lever:pnlfin",
            "eu-1",
        )

    assert not eu_route.called
