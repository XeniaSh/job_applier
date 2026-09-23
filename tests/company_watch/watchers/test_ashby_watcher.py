from __future__ import annotations

import httpx
import respx

from app.company_watch.models import TargetCompany
from app.company_watch.watchers.ashby import AshbyTargetWatcher, ashby_jobs_endpoint


def _watcher(**overrides):
    return AshbyTargetWatcher(**overrides)


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "TravelPerk",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "ashby",
        "ats": "ashby",
        "job_board_url": "https://jobs.ashbyhq.com/Perk",
        "role_keywords": ["java", "backend"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _job(
    *,
    job_id: str | None = "job-1",
    title: str = "Java Backend Engineer",
    url: str = "https://jobs.ashbyhq.com/Perk/job-1",
    location: str = "Barcelona",
    description: str = "Java backend services and payments.",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "jobUrl": url,
        "locationName": location,
        "employmentType": "FullTime",
        "descriptionPlain": description,
        "publishedAt": "2026-09-01T00:00:00Z",
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_skips_non_ashby_companies() -> None:
    company = _company(
        name="Agoda",
        watcher_type="greenhouse",
        ats="greenhouse",
        job_board_url="https://job-boards.greenhouse.io/agoda",
    )
    result = _watcher().watch(company)
    assert result.vacancies == []
    assert result.errors == []
    assert not respx.calls


@respx.mock
def test_fetches_and_maps_ashby_jobs() -> None:
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )
    result = _watcher().watch(_company())
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:ashby:perk"
    assert vacancy.external_id == "job-1"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "TravelPerk"
    assert vacancy.location == "Barcelona"
    assert vacancy.url == "https://jobs.ashbyhq.com/Perk/job-1"
    assert result.errors == []


@respx.mock
def test_title_filters_and_empty_include() -> None:
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id="101", title="Java Backend Engineer"),
                    _job(
                        job_id="202",
                        title="Warehouse Operator",
                        url="https://jobs.ashbyhq.com/Perk/202",
                        description="Warehouse operations.",
                    ),
                ]
            },
        )
    )
    filtered = _watcher().watch(_company(role_keywords=["java", "backend"]))
    assert [item.external_id for item in filtered.vacancies] == ["101"]
    unfiltered = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    assert [item.external_id for item in unfiltered.vacancies] == ["101", "202"]


@respx.mock
def test_exclude_title_keywords() -> None:
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id="101", title="Java Backend Engineer"),
                    _job(
                        job_id="404",
                        title="Staff Java Backend Engineer",
                        url="https://jobs.ashbyhq.com/Perk/404",
                    ),
                ]
            },
        )
    )
    result = _watcher().watch(
        _company(role_title_keywords=["java", "backend"], exclude_title_keywords=["staff"])
    )
    assert [item.external_id for item in result.vacancies] == ["101"]


@respx.mock
def test_fetches_and_maps_documented_api_shaped_job() -> None:
    # Shaped after Ashby's public posting API sample response
    # (https://api.ashbyhq.com/posting-api/job-board/<board>), which uses
    # `location` rather than the `locationName` field the other fixtures in
    # this file use.
    api_shaped_job = {
        "id": "5f6e7d8c-1234-5678-9abc-def012345678",
        "title": "Java Backend Engineer",
        "department": "Engineering",
        "team": "Payments",
        "employmentType": "FullTime",
        "location": "Barcelona, Spain",
        "isRemote": False,
        "descriptionPlain": "Java backend services and payments.",
        "descriptionHtml": "<p>Java backend services and payments.</p>",
        "publishedAt": "2026-09-01T00:00:00Z",
        "jobUrl": "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678",
        "applyUrl": "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678/application",
    }
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [api_shaped_job]})
    )
    result = _watcher().watch(_company(role_keywords=["java", "backend"]))
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:ashby:perk"
    assert vacancy.external_id == "5f6e7d8c-1234-5678-9abc-def012345678"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "TravelPerk"
    assert vacancy.location == "Barcelona, Spain"
    assert vacancy.description == "Java backend services and payments."
    assert vacancy.published_at == "2026-09-01T00:00:00Z"
    assert vacancy.url == "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678"
    assert result.errors == []


@respx.mock
def test_documented_api_shaped_job_excluded_by_title_prefilter() -> None:
    api_shaped_job = {
        "id": "202",
        "title": "Warehouse Operator",
        "employmentType": "FullTime",
        "location": "Barcelona, Spain",
        "descriptionPlain": "Warehouse operations.",
        "publishedAt": "2026-09-01T00:00:00Z",
        "jobUrl": "https://jobs.ashbyhq.com/Perk/202",
    }
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [api_shaped_job]})
    )
    result = _watcher().watch(_company(role_keywords=["java", "backend"]))
    assert result.vacancies == []
    assert result.errors == []


@respx.mock
def test_one_company_error_does_not_stop_other_companies() -> None:
    respx.get(ashby_jobs_endpoint("Perk")).mock(return_value=httpx.Response(status_code=500))
    respx.get(ashby_jobs_endpoint("other")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )
    companies = [
        _company(name="TravelPerk", job_board_url="https://jobs.ashbyhq.com/Perk"),
        _company(name="Other", job_board_url="https://jobs.ashbyhq.com/other", role_keywords=["java"]),
    ]
    result = _watcher().watch(companies)
    assert [item.company for item in result.vacancies] == ["Other"]
    assert len(result.errors) == 1
    assert result.errors[0].company_name == "TravelPerk"
    assert result.errors[0].status_code == 500


@respx.mock
def test_source_and_external_id_are_stable() -> None:
    respx.get(ashby_jobs_endpoint("Perk")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id="101"),
                    _job(
                        job_id=None,
                        title="Platform Engineer",
                        url="https://jobs.ashbyhq.com/Perk/fallback-id",
                        description="Backend platform and java services.",
                    ),
                ]
            },
        )
    )
    first = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    second = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    assert [item.source for item in first.vacancies] == [
        "target_company:ashby:perk",
        "target_company:ashby:perk",
    ]
    assert [item.external_id for item in first.vacancies] == ["101", "fallback-id"]
    assert [item.external_id for item in first.vacancies] == [
        item.external_id for item in second.vacancies
    ]
