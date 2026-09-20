from __future__ import annotations

import httpx
import pytest
import respx

from app.application.autofill.resolver import (
    DefaultVacancyResolver,
    GreenhouseTargetVacancyResolver,
    VacancyResolveError,
)
from app.collectors.greenhouse_collector import greenhouse_jobs_endpoint


def _job(
    *,
    job_id: int | None = 6886113,
    title: str = "Java Backend Engineer",
    url: str = "https://job-boards.greenhouse.io/agoda/jobs/6886113",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "absolute_url": url,
        "location": {"name": "Bangkok"},
        "content": "<p>Java backend</p>",
        "updated_at": "2026-09-05T10:00:00Z",
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_resolves_target_company_greenhouse_job() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )

    resolved = GreenhouseTargetVacancyResolver().resolve(
        "target_company:greenhouse:agoda",
        "6886113",
    )

    assert resolved.source == "target_company:greenhouse:agoda"
    assert resolved.external_id == "6886113"
    assert resolved.title == "Java Backend Engineer"
    assert resolved.application_url == "https://job-boards.greenhouse.io/agoda/jobs/6886113"
    assert resolved.url == resolved.application_url
    assert resolved.vacancy is not None
    assert resolved.vacancy.source == "target_company:greenhouse:agoda"


@respx.mock
def test_missing_job_is_a_clear_failure() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )

    with pytest.raises(VacancyResolveError, match="not found"):
        GreenhouseTargetVacancyResolver().resolve(
            "target_company:greenhouse:agoda",
            "999",
        )


def test_bad_source_is_a_clear_failure() -> None:
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        GreenhouseTargetVacancyResolver().resolve("greenhouse:agoda", "1")


@respx.mock
def test_empty_application_url_is_resolve_failure() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={"jobs": [_job(url="")]},
        )
    )
    with pytest.raises(VacancyResolveError, match="not found"):
        GreenhouseTargetVacancyResolver().resolve(
            "target_company:greenhouse:agoda",
            "6886113",
        )


@respx.mock
def test_default_resolver_uses_source_and_external_id_only() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )
    resolved = DefaultVacancyResolver().resolve(
        "target_company:greenhouse:agoda",
        "6886113",
    )
    assert resolved.external_id == "6886113"
    assert resolved.application_url.endswith("/6886113")


@respx.mock
@pytest.mark.parametrize(
    ("board", "job_id", "custom_absolute_url"),
    [
        ("stripe", 555001, "https://stripe.com/jobs/listing/staff-backend-engineer/555001"),
        ("databricks", 555002, "https://www.databricks.com/company/careers/open-positions?gh_jid=555002"),
        ("roblox", 555003, "https://careers.roblox.com/jobs/555003-senior-software-engineer"),
    ],
)
def test_resolve_returns_canonical_url_for_custom_domain_api_item(
    board: str, job_id: int, custom_absolute_url: str
) -> None:
    respx.get(greenhouse_jobs_endpoint(board)).mock(
        return_value=httpx.Response(
            status_code=200,
            json={"jobs": [_job(job_id=job_id, title="Staff Backend Engineer", url=custom_absolute_url)]},
        )
    )

    resolved = GreenhouseTargetVacancyResolver().resolve(
        f"target_company:greenhouse:{board}",
        str(job_id),
    )

    assert resolved.application_url == f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    assert resolved.url == resolved.application_url
    assert resolved.vacancy is not None
    assert resolved.vacancy.original_url == custom_absolute_url


def test_does_not_use_generic_greenhouse_boards_setting() -> None:
    import inspect

    from app.application.autofill import resolver as resolver_module

    source = inspect.getsource(resolver_module)
    assert "GREENHOUSE_BOARDS" not in source
