from __future__ import annotations

import httpx
import respx

from app.company_watch.models import TargetCompany
from app.company_watch.watchers.lever import LeverTargetWatcher, lever_postings_endpoint


def _watcher(**overrides):
    return LeverTargetWatcher(**overrides)


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Qonto",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "lever",
        "ats": "lever",
        "job_board_url": "https://jobs.lever.co/qonto",
        "role_keywords": ["java", "backend"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _job(
    *,
    job_id: str | None = "abc123",
    title: str = "Java Backend Engineer",
    url: str = "https://jobs.lever.co/qonto/abc123",
    location: str = "Paris",
    description: str = "Java backend services and payments.",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": title,
        "hostedUrl": url,
        "categories": {"location": location, "commitment": "Full-time"},
        "descriptionPlain": description,
        "createdAt": 1693900000000,
    }
    if job_id is not None:
        payload["id"] = job_id
    return payload


@respx.mock
def test_skips_non_lever_companies() -> None:
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
def test_fetches_and_maps_lever_jobs() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(status_code=200, json=[_job()])
    )
    result = _watcher().watch(_company())
    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:lever:qonto"
    assert vacancy.external_id == "abc123"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "Qonto"
    assert vacancy.location == "Paris"
    assert vacancy.url == "https://jobs.lever.co/qonto/abc123"
    assert "Java backend services" in vacancy.description
    assert result.errors == []


@respx.mock
def test_role_keywords_filter_title_only() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[
                _job(job_id="101", title="Java Backend Engineer"),
                _job(
                    job_id="202",
                    title="Frontend Designer",
                    url="https://jobs.lever.co/qonto/202",
                    description="Figma and CSS.",
                ),
            ],
        )
    )
    result = _watcher().watch(_company(role_keywords=["java", "backend"]))
    assert [item.external_id for item in result.vacancies] == ["101"]


@respx.mock
def test_empty_include_keywords_do_not_filter() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[
                _job(job_id="101", title="Java Backend Engineer"),
                _job(
                    job_id="202",
                    title="Warehouse Operator",
                    url="https://jobs.lever.co/qonto/202",
                    description="Figma and CSS.",
                ),
            ],
        )
    )
    result = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    assert [item.external_id for item in result.vacancies] == ["101", "202"]


@respx.mock
def test_exclude_title_keywords_drop_matching_jobs() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[
                _job(job_id="101", title="Java Backend Engineer"),
                _job(
                    job_id="404",
                    title="Staff Java Backend Engineer",
                    url="https://jobs.lever.co/qonto/404",
                ),
            ],
        )
    )
    result = _watcher().watch(
        _company(
            role_title_keywords=["java", "backend"],
            exclude_title_keywords=["staff"],
        )
    )
    assert [item.external_id for item in result.vacancies] == ["101"]


@respx.mock
def test_one_company_error_does_not_stop_other_companies() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(return_value=httpx.Response(status_code=500))
    respx.get(lever_postings_endpoint("n26")).mock(
        return_value=httpx.Response(status_code=200, json=[_job(title="Java Backend Engineer")])
    )
    companies = [
        _company(name="Qonto", job_board_url="https://jobs.lever.co/qonto"),
        _company(name="N26", job_board_url="https://jobs.lever.co/n26", role_keywords=["java"]),
    ]
    result = _watcher().watch(companies)
    assert [item.company for item in result.vacancies] == ["N26"]
    assert len(result.errors) == 1
    assert result.errors[0].company_name == "Qonto"
    assert result.errors[0].status_code == 500
    assert result.errors[0].slug == "qonto"
    assert result.errors[0].endpoint == lever_postings_endpoint("qonto")


@respx.mock
def test_source_and_external_id_are_stable() -> None:
    respx.get(lever_postings_endpoint("qonto")).mock(
        return_value=httpx.Response(
            status_code=200,
            json=[
                _job(job_id="101"),
                _job(
                    job_id=None,
                    title="Platform Engineer",
                    url="https://jobs.lever.co/qonto/fallback-id",
                    description="Backend platform and java services.",
                ),
            ],
        )
    )
    first = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    second = _watcher().watch(_company(role_keywords=[], role_title_keywords=[]))
    assert [item.source for item in first.vacancies] == [
        "target_company:lever:qonto",
        "target_company:lever:qonto",
    ]
    assert [item.external_id for item in first.vacancies] == ["101", "fallback-id"]
    assert [item.external_id for item in first.vacancies] == [
        item.external_id for item in second.vacancies
    ]
