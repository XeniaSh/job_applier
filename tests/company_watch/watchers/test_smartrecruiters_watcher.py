from __future__ import annotations

import httpx
import respx

from app.company_watch.models import TargetCompany
from app.company_watch.watchers.smartrecruiters import (
    SmartRecruitersTargetWatcher,
    smartrecruiters_postings_endpoint,
)


def _watcher(**overrides):
    return SmartRecruitersTargetWatcher(**overrides)


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Canva",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "smartrecruiters",
        "ats": "smartrecruiters",
        "job_board_url": "https://careers.smartrecruiters.com/canva",
        "role_keywords": ["java", "backend"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _job(
    *,
    job_id: str | None = "sr-1",
    title: str = "Java Backend Engineer",
    url: str = "https://jobs.smartrecruiters.com/Canva/sr-1",
    city: str = "Sydney",
    description: str = "Java backend services and cloud.",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": title,
        "ref": url,
        "location": {"city": city, "country": "Australia"},
        "releasedDate": "2026-09-01T00:00:00.000Z",
        "jobAd": {"sections": {"jobDescription": {"text": description}}},
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_skips_non_smartrecruiters_companies() -> None:
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
def test_fetches_and_maps_smartrecruiters_jobs() -> None:
    respx.get(smartrecruiters_postings_endpoint("canva")).mock(
        return_value=httpx.Response(status_code=200, json={"content": [_job()]})
    )
    result = _watcher().watch(_company())
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:smartrecruiters:canva"
    assert vacancy.external_id == "sr-1"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "Canva"
    assert vacancy.location == "Sydney, Australia"
    assert vacancy.url == "https://jobs.smartrecruiters.com/Canva/sr-1"
    assert "Java backend services" in vacancy.description
    assert result.errors == []


@respx.mock
def test_title_filters_and_empty_include() -> None:
    respx.get(smartrecruiters_postings_endpoint("canva")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "content": [
                    _job(job_id="101", title="Java Backend Engineer"),
                    _job(
                        job_id="202",
                        title="Warehouse Operator",
                        url="https://jobs.smartrecruiters.com/Canva/202",
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
    respx.get(smartrecruiters_postings_endpoint("canva")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "content": [
                    _job(job_id="101", title="Java Backend Engineer"),
                    _job(
                        job_id="404",
                        title="Staff Java Backend Engineer",
                        url="https://jobs.smartrecruiters.com/Canva/404",
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
def test_one_company_error_does_not_stop_other_companies() -> None:
    respx.get(smartrecruiters_postings_endpoint("canva")).mock(
        return_value=httpx.Response(status_code=500)
    )
    respx.get(smartrecruiters_postings_endpoint("other")).mock(
        return_value=httpx.Response(status_code=200, json={"content": [_job()]})
    )
    companies = [
        _company(name="Canva", job_board_url="https://careers.smartrecruiters.com/canva"),
        _company(
            name="Other",
            job_board_url="https://careers.smartrecruiters.com/other",
            role_keywords=["java"],
        ),
    ]
    result = _watcher().watch(companies)
    assert [item.company for item in result.vacancies] == ["Other"]
    assert len(result.errors) == 1
    assert result.errors[0].company_name == "Canva"
    assert result.errors[0].status_code == 500


@respx.mock
def test_source_and_external_id_are_stable() -> None:
    respx.get(smartrecruiters_postings_endpoint("canva")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "content": [
                    _job(job_id="101"),
                    _job(
                        job_id=None,
                        title="Platform Engineer",
                        url="https://jobs.smartrecruiters.com/Canva/fallback-id",
                        description="Backend platform and java services.",
                    ),
                ]
            },
        )
    )
    first = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    second = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    assert [item.source for item in first.vacancies] == [
        "target_company:smartrecruiters:canva",
        "target_company:smartrecruiters:canva",
    ]
    assert [item.external_id for item in first.vacancies] == ["101", "fallback-id"]
    assert [item.external_id for item in first.vacancies] == [
        item.external_id for item in second.vacancies
    ]
