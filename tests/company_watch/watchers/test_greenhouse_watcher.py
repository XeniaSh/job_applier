from __future__ import annotations

import httpx
import respx

from app.collectors.greenhouse_collector import greenhouse_jobs_endpoint
from app.company_watch.models import TargetCompany
from app.company_watch.watchers.greenhouse import GreenhouseTargetWatcher


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Agoda",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "greenhouse",
        "ats": "greenhouse",
        "job_board_url": "https://job-boards.greenhouse.io/agoda",
        "role_keywords": ["java", "backend"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _job(
    *,
    job_id: int | None = 101,
    title: str = "Java Backend Engineer",
    url: str = "https://job-boards.greenhouse.io/agoda/jobs/101",
    location: str = "Bangkok",
    content: str = "<p>Java backend services and distributed systems.</p>",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "absolute_url": url,
        "location": {"name": location},
        "content": content,
        "updated_at": "2026-09-05T10:00:00Z",
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_skips_non_greenhouse_companies() -> None:
    company = _company(
        name="Qonto",
        watcher_type="lever",
        ats="lever",
        job_board_url="https://jobs.lever.co/qonto",
    )

    result = GreenhouseTargetWatcher().watch(company)

    assert result.vacancies == []
    assert result.errors == []
    assert not respx.calls


@respx.mock
def test_fetches_and_maps_greenhouse_jobs() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )

    result = GreenhouseTargetWatcher().watch(_company())

    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:greenhouse:agoda"
    assert vacancy.external_id == "101"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "Agoda"
    assert vacancy.location == "Bangkok"
    assert vacancy.url == "https://job-boards.greenhouse.io/agoda/jobs/101"
    assert "Java backend services" in vacancy.description
    assert result.errors == []


@respx.mock
def test_role_keywords_filter_irrelevant_jobs() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id=101, title="Java Backend Engineer"),
                    _job(
                        job_id=202,
                        title="Frontend Designer",
                        url="https://job-boards.greenhouse.io/agoda/jobs/202",
                        content="<p>Figma and CSS.</p>",
                    ),
                ]
            },
        )
    )

    result = GreenhouseTargetWatcher().watch(_company(role_keywords=["java", "backend"]))

    assert [item.external_id for item in result.vacancies] == ["101"]


@respx.mock
def test_empty_role_keywords_do_not_filter() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id=101, title="Java Backend Engineer"),
                    _job(
                        job_id=202,
                        title="Warehouse Operator",
                        url="https://job-boards.greenhouse.io/agoda/jobs/202",
                        content="<p>Figma and CSS.</p>",
                    ),
                ]
            },
        )
    )

    result = GreenhouseTargetWatcher().watch(_company(role_keywords=[]))

    assert [item.external_id for item in result.vacancies] == ["101", "202"]


@respx.mock
def test_one_company_error_does_not_stop_other_companies() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(status_code=500)
    )
    respx.get(greenhouse_jobs_endpoint("adyen")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job(title="Java Backend Engineer")]})
    )
    companies = [
        _company(name="Agoda", job_board_url="https://job-boards.greenhouse.io/agoda"),
        _company(
            name="Adyen",
            job_board_url="https://job-boards.greenhouse.io/adyen",
            role_keywords=["java"],
        ),
    ]

    result = GreenhouseTargetWatcher().watch(companies)

    assert [item.company for item in result.vacancies] == ["Adyen"]
    assert len(result.errors) == 1
    assert result.errors[0].company_name == "Agoda"
    assert "agoda" in result.errors[0].message.lower()


@respx.mock
def test_source_and_external_id_are_stable() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id=101),
                    _job(
                        job_id=None,
                        title="Platform Engineer",
                        url="https://job-boards.greenhouse.io/agoda/jobs/404",
                        content="<p>Backend platform and java services.</p>",
                    ),
                ]
            },
        )
    )

    first = GreenhouseTargetWatcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    second = GreenhouseTargetWatcher().watch(_company(role_keywords=[], role_title_keywords=[]))

    assert [item.source for item in first.vacancies] == [
        "target_company:greenhouse:agoda",
        "target_company:greenhouse:agoda",
    ]
    assert [item.external_id for item in first.vacancies] == ["101", "404"]
    assert [item.external_id for item in first.vacancies] == [item.external_id for item in second.vacancies]
    assert [item.source for item in first.vacancies] == [item.source for item in second.vacancies]


@respx.mock
def test_ats_greenhouse_is_watched_even_if_watcher_type_differs() -> None:
    respx.get(greenhouse_jobs_endpoint("elastic")).mock(
        return_value=httpx.Response(status_code=200, json={"jobs": [_job()]})
    )
    company = _company(
        name="Elastic",
        watcher_type="custom",
        ats="greenhouse",
        job_board_url="https://job-boards.greenhouse.io/elastic",
        role_keywords=[],
    )

    result = GreenhouseTargetWatcher().watch(company)

    assert len(result.vacancies) == 1
    assert result.vacancies[0].source == "target_company:greenhouse:elastic"


def test_watch_accepts_a_single_company_without_treating_model_as_sequence() -> None:
    company = _company(watcher_type="lever", ats="lever")
    result = GreenhouseTargetWatcher().watch(company)
    assert result.vacancies == []
    assert result.errors == []


@respx.mock
def test_description_only_keyword_is_ignored() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(
                        job_id=303,
                        title="Warehouse Operator",
                        url="https://job-boards.greenhouse.io/agoda/jobs/303",
                        content="<p>Java backend services and kotlin.</p>",
                    )
                ]
            },
        )
    )

    result = GreenhouseTargetWatcher().watch(_company(role_keywords=["java", "backend"]))

    assert result.raw_fetched == 1
    assert result.vacancies == []


@respx.mock
def test_exclude_title_keywords_drop_matching_jobs() -> None:
    respx.get(greenhouse_jobs_endpoint("agoda")).mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "jobs": [
                    _job(job_id=101, title="Java Backend Engineer"),
                    _job(
                        job_id=404,
                        title="Staff Java Backend Engineer",
                        url="https://job-boards.greenhouse.io/agoda/jobs/404",
                    ),
                ]
            },
        )
    )

    result = GreenhouseTargetWatcher().watch(
        _company(
            role_title_keywords=["java", "backend"],
            exclude_title_keywords=["staff"],
        )
    )

    assert result.raw_fetched == 2
    assert [item.external_id for item in result.vacancies] == ["101"]
