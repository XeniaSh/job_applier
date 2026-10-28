from __future__ import annotations

import httpx
import pytest
import respx

from app.company_watch.models import TargetCompany
from app.company_watch.watchers.smartrecruiters import (
    SmartRecruitersTargetWatcher,
    _canonical_smartrecruiters_url,
    resolve_smartrecruiters_slug,
    smartrecruiters_posting_endpoint,
    smartrecruiters_postings_endpoint,
)


def _watcher(**overrides):
    return SmartRecruitersTargetWatcher(**overrides)


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Wise",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "smartrecruiters",
        "ats": "smartrecruiters",
        "job_board_url": "https://careers.smartrecruiters.com/Wise",
        "role_keywords": ["java", "backend"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _list_row(
    *,
    posting_id: str | None = "101",
    uuid: str | None = None,
    title: str = "Java Backend Engineer",
    city: str = "Tallinn",
    country: str = "EE",
    ref: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": title,
        "location": {"city": city, "country": country},
    }
    if posting_id is not None:
        row["id"] = posting_id
    if uuid is not None:
        row["uuid"] = uuid
    if ref is not None:
        row["ref"] = ref
    elif posting_id is not None:
        row["ref"] = f"https://api.smartrecruiters.com/v1/companies/Wise/postings/{posting_id}"
    return row


_NOT_GIVEN = object()


def _list_page(
    rows: list[dict[str, object]], *, total_found: int | None | object = _NOT_GIVEN
) -> dict[str, object]:
    # Defaults to a realistic, self-consistent totalFound (the full row
    # count) so ordinary single-page fixtures actually prove completion
    # instead of relying on the "short page" fallback. Tests that
    # deliberately exercise missing pagination metadata pass
    # total_found=None to opt out.
    payload: dict[str, object] = {"content": rows}
    if total_found is _NOT_GIVEN:
        total_found = len(rows)
    if total_found is not None:
        payload["totalFound"] = total_found
    return payload


def _detail_payload(
    *,
    posting_id: str = "101",
    uuid: str | None = None,
    active: bool = True,
    title: str = "Java Backend Engineer",
    posting_url: str | None = "https://jobs.smartrecruiters.com/Wise/101-java-backend-engineer",
    apply_url: str | None = "https://jobs.smartrecruiters.com/Wise/101/apply",
    city: str = "Tallinn",
    country: str = "EE",
    sections: dict[str, str] | None = None,
) -> dict[str, object]:
    if sections is None:
        sections = {
            "jobDescription": "<p>Build the <b>rules platform</b>.</p>",
            "qualifications": "<ul><li>Java</li><li>Kotlin</li></ul>",
        }
    payload: dict[str, object] = {
        "id": posting_id,
        "active": active,
        "name": title,
        "location": {"city": city, "country": country},
        "jobAd": {"sections": {key: {"text": value} for key, value in sections.items()}},
    }
    if uuid is not None:
        payload["uuid"] = uuid
    if posting_url is not None:
        payload["postingUrl"] = posting_url
    if apply_url is not None:
        payload["applyUrl"] = apply_url
    return payload


def _mock_list_once(slug: str, payload: dict[str, object], *, status_code: int = 200) -> None:
    respx.get(smartrecruiters_postings_endpoint(slug)).mock(
        return_value=httpx.Response(status_code=status_code, json=payload)
    )


def _mock_detail(slug: str, posting_id: str, payload: dict[str, object], *, status_code: int = 200) -> None:
    respx.get(smartrecruiters_posting_endpoint(slug, posting_id)).mock(
        return_value=httpx.Response(status_code=status_code, json=payload)
    )


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
def test_fetches_list_then_detail_and_maps_full_description() -> None:
    _mock_list_once("Wise", _list_page([_list_row()]))
    _mock_detail("Wise", "101", _detail_payload())

    result = _watcher().watch(_company())

    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.source == "target_company:smartrecruiters:wise"
    assert vacancy.external_id == "101"
    assert vacancy.title == "Java Backend Engineer"
    assert vacancy.company == "Wise"
    assert vacancy.location == "Tallinn, EE"
    assert "Build the rules platform." in vacancy.description
    assert "Java" in vacancy.description and "Kotlin" in vacancy.description
    assert "<" not in vacancy.description
    assert result.errors == []
    assert result.raw_fetched == 1
    assert result.unique_count == 1
    assert result.title_matched_count == 1
    assert result.detail_fetched_count == 1


@respx.mock
def test_location_preserves_full_location_and_remote_hybrid_evidence() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="102", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    remote_detail = _detail_payload(posting_id="101")
    remote_detail["location"] = {"fullLocation": "Remote, , Estonia", "remote": True}
    hybrid_detail = _detail_payload(posting_id="102")
    hybrid_detail["location"] = {"city": "Tallinn", "country": "ee", "hybrid": True}
    _mock_detail(slug, "101", remote_detail)
    _mock_detail(slug, "102", hybrid_detail)

    result = _watcher().watch(_company())

    by_id = {v.external_id: v for v in result.vacancies}
    assert by_id["101"].location == "Remote, , Estonia"
    assert by_id["102"].location == "Tallinn, ee (Hybrid)"


@respx.mock
def test_public_url_uses_posting_url_not_ref_or_apply_url() -> None:
    _mock_list_once("Wise", _list_page([_list_row()]))
    _mock_detail(
        "Wise",
        "101",
        _detail_payload(posting_url="https://jobs.smartrecruiters.com/Wise/101-rules-platform"),
    )

    result = _watcher().watch(_company())

    assert result.vacancies[0].url == "https://jobs.smartrecruiters.com/Wise/101-rules-platform"


@respx.mock
def test_fallback_url_when_posting_url_missing_or_untrusted() -> None:
    _mock_list_once("Wise", _list_page([_list_row()]))
    _mock_detail("Wise", "101", _detail_payload(posting_url=None))

    result = _watcher().watch(_company())

    assert result.vacancies[0].url == "https://jobs.smartrecruiters.com/Wise/101"


@respx.mock
def test_fallback_url_when_posting_url_is_untrusted_host() -> None:
    _mock_list_once("Wise", _list_page([_list_row()]))
    _mock_detail(
        "Wise",
        "101",
        _detail_payload(posting_url="https://evil.example.com/Wise/101"),
    )

    result = _watcher().watch(_company())

    assert result.vacancies[0].url == "https://jobs.smartrecruiters.com/Wise/101"


@respx.mock
def test_detail_fetch_only_for_title_matches_not_all_raw_rows() -> None:
    _mock_list_once(
        "Wise",
        _list_page(
            [
                _list_row(posting_id="101", title="Java Backend Engineer"),
                _list_row(posting_id="202", title="Warehouse Operator"),
            ]
        ),
    )
    _mock_detail("Wise", "101", _detail_payload(posting_id="101"))

    result = _watcher().watch(_company(role_keywords=["java", "backend"]))

    assert [v.external_id for v in result.vacancies] == ["101"]
    assert result.raw_fetched == 2
    assert result.title_matched_count == 1
    assert result.detail_fetched_count == 1
    detail_calls = [call for call in respx.calls if "/postings/" in str(call.request.url.path)]
    assert len(detail_calls) == 1


@respx.mock
def test_multi_page_pagination_advances_offset_using_actual_page_size() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows_page1 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in range(1, 3)]
    rows_page2 = [_list_row(posting_id="3", title="Java Backend Engineer")]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=_list_page(rows_page1, total_found=3))
        if offset == 2:
            return httpx.Response(200, json=_list_page(rows_page2, total_found=3))
        raise AssertionError(f"unexpected offset {offset}")

    respx.get(endpoint).mock(side_effect=_responder)
    for posting_id in ("1", "2", "3"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher(page_limit=2).watch(_company())

    assert result.pages_fetched == 2
    assert result.raw_fetched == 3
    assert result.pages_truncated is False
    assert {v.external_id for v in result.vacancies} == {"1", "2", "3"}


@respx.mock
def test_short_page_without_pagination_metadata_confirmed_complete_by_empty_next_page() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows = [_list_row(posting_id="1", title="Java Backend Engineer")]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=_list_page(rows, total_found=None))
        if offset == 1:
            return httpx.Response(200, json=_list_page([], total_found=None))
        raise AssertionError(f"unexpected offset {offset}")

    respx.get(endpoint).mock(side_effect=_responder)
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher(page_limit=100).watch(_company())

    # A page shorter than requested, with no totalFound and no reported
    # limit, is not by itself proof of completion -- only the subsequent
    # empty page honestly establishes that pagination is done.
    assert result.pages_fetched == 2
    assert result.raw_fetched == 1
    assert result.pages_truncated is False
    assert [v.external_id for v in result.vacancies] == ["1"]


@respx.mock
def test_short_capped_pages_without_any_pagination_metadata_are_not_silently_complete() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    # Server silently caps every page at 2 rows (far below the requested
    # limit of 100) and never reports totalFound or limit. A naive "page
    # shorter than requested -> done" heuristic would wrongly stop after the
    # first page and miss ids 3/4.
    page1 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (1, 2)]
    page2 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (3, 4)]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        pages = {0: page1, 2: page2}
        if offset not in pages:
            raise AssertionError(f"unexpected offset {offset}")
        return httpx.Response(200, json=_list_page(pages[offset], total_found=None))

    respx.get(endpoint).mock(side_effect=_responder)
    for posting_id in ("1", "2", "3", "4"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher(page_limit=100, max_pages=2).watch(_company())

    assert result.pages_fetched == 2
    assert result.raw_fetched == 4
    assert {v.external_id for v in result.vacancies} == {"1", "2", "3", "4"}
    # max_pages was exhausted without ever seeing an empty page or a
    # trustworthy limit signal, so this is honestly truncated rather than
    # silently claimed complete.
    assert result.pages_truncated is True


@respx.mock
def test_server_capped_page_size_keeps_paging_using_total_found() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    # Server caps each page at 2 rows even though limit=100 was requested;
    # totalFound=5 tells us two more rows remain after the first capped page.
    page1 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (1, 2)]
    page2 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (3, 4)]
    page3 = [_list_row(posting_id="5", title="Java Backend Engineer")]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        pages = {0: page1, 2: page2, 4: page3}
        if offset not in pages:
            raise AssertionError(f"unexpected offset {offset}")
        return httpx.Response(200, json=_list_page(pages[offset], total_found=5))

    respx.get(endpoint).mock(side_effect=_responder)
    for posting_id in ("1", "2", "3", "4", "5"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher(page_limit=100).watch(_company())

    assert result.pages_fetched == 3
    assert result.raw_fetched == 5
    assert result.pages_truncated is False


@respx.mock
def test_repeated_page_stops_pagination_and_reports_error_with_retained_data() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (1, 2)]

    def _responder(request: httpx.Request) -> httpx.Response:
        # Buggy server always returns the same page regardless of offset,
        # and never signals totalFound, so the only way to bound this is
        # "same content as last time -> stop".
        return httpx.Response(200, json=_list_page(rows, total_found=None))

    respx.get(endpoint).mock(side_effect=_responder)
    for posting_id in ("1", "2"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher(page_limit=2).watch(_company())

    # Both the first page and the repeated second page were actually
    # received over the wire, so raw_fetched counts both (4), even though
    # the repeated page's rows are discarded from further processing.
    assert result.raw_fetched == 4
    assert {v.external_id for v in result.vacancies} == {"1", "2"}
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "RepeatedPage"
    assert result.errors[0].company_name == "Wise"


@respx.mock
def test_malformed_later_page_retains_earlier_pages_data() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows_page1 = [_list_row(posting_id="1", title="Java Backend Engineer")]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=_list_page(rows_page1, total_found=99))
        return httpx.Response(200, json={"content": "not-a-list", "totalFound": 99})

    respx.get(endpoint).mock(side_effect=_responder)
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher(page_limit=1).watch(_company())

    assert result.raw_fetched == 1
    assert [v.external_id for v in result.vacancies] == ["1"]
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "MalformedPayload"


@respx.mock
def test_failing_page_request_retains_earlier_pages_data() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows_page1 = [_list_row(posting_id="1", title="Java Backend Engineer")]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=_list_page(rows_page1, total_found=99))
        return httpx.Response(500)

    respx.get(endpoint).mock(side_effect=_responder)
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher(page_limit=1).watch(_company())

    assert result.raw_fetched == 1
    assert [v.external_id for v in result.vacancies] == ["1"]
    assert len(result.errors) == 1
    assert result.errors[0].status_code == 500


@respx.mock
def test_max_pages_bound_marks_pagination_truncated_not_silently_complete() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    # Every page is exactly page_limit-sized with no totalFound, so the
    # watcher cannot tell it's done; max_pages must stop it and flag it.
    respx.get(endpoint).mock(
        return_value=httpx.Response(
            200,
            json=_list_page([_list_row(posting_id="1", title="Java Backend Engineer")], total_found=None),
        )
    )
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher(page_limit=1, max_pages=1).watch(_company())

    assert result.pages_fetched == 1
    assert result.pages_truncated is True


@respx.mock
def test_max_detail_fetches_bound_truncates_and_flags() -> None:
    slug = "Wise"
    rows = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (1, 2, 3)]
    _mock_list_once(slug, _list_page(rows))
    # Only the first match (bounded by max_detail_fetches=1) should ever be
    # requested; mocking only "1" also proves "2"/"3" are never fetched,
    # since respx would fail the test on an unmocked request.
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher(max_detail_fetches=1).watch(_company())

    assert result.title_matched_count == 3
    assert result.detail_fetched_count == 1
    assert result.detail_truncated is True
    assert len(result.vacancies) == 1


@respx.mock
def test_duplicate_posting_within_one_page_is_deduped_before_detail_fetch() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="101", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    _mock_detail(slug, "101", _detail_payload(posting_id="101"))

    result = _watcher().watch(_company())

    assert result.raw_fetched == 2
    assert result.unique_count == 1
    assert result.duplicate_count == 1
    assert [v.external_id for v in result.vacancies] == ["101"]


@respx.mock
def test_distinct_ids_with_same_title_and_url_shape_are_kept_separate() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="102", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    _mock_detail(
        slug,
        "101",
        _detail_payload(posting_id="101", posting_url="https://jobs.smartrecruiters.com/Wise/same-shape"),
    )
    _mock_detail(
        slug,
        "102",
        _detail_payload(posting_id="102", posting_url="https://jobs.smartrecruiters.com/Wise/same-shape"),
    )

    result = _watcher().watch(_company())

    assert {v.external_id for v in result.vacancies} == {"101", "102"}


@respx.mock
def test_repeated_company_in_single_watch_call_is_deduped_in_output() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(slug, "101", _detail_payload(posting_id="101"))

    company = _company()
    result = _watcher().watch([company, company])

    assert [v.external_id for v in result.vacancies] == ["101"]
    assert result.duplicate_count == 1
    assert result.unique_count == 1
    assert result.title_matched_count == 1
    assert result.detail_fetched_count == 1
    # The board/posting were requested once in total, not once per repeated
    # company entry in the input list.
    list_calls = [call for call in respx.calls if str(call.request.url.path).endswith("/postings")]
    detail_calls = [call for call in respx.calls if str(call.request.url.path).endswith("/postings/101")]
    assert len(list_calls) == 1
    assert len(detail_calls) == 1


@respx.mock
def test_detail_inactive_posting_dropped_without_blocking_other_successes() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="102", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    _mock_detail(slug, "101", _detail_payload(posting_id="101", active=False))
    _mock_detail(slug, "102", _detail_payload(posting_id="102"))

    result = _watcher().watch(_company())

    assert [v.external_id for v in result.vacancies] == ["102"]
    assert len(result.job_errors) == 1
    assert result.job_errors[0].posting_id == "101"


@respx.mock
def test_detail_mismatched_identity_dropped_without_blocking_other_successes() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="102", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    # Detail endpoint for posting 101 returns a payload claiming to be a
    # different posting id -- must never be trusted as 101's vacancy.
    _mock_detail(slug, "101", _detail_payload(posting_id="999"))
    _mock_detail(slug, "102", _detail_payload(posting_id="102"))

    result = _watcher().watch(_company())

    assert [v.external_id for v in result.vacancies] == ["102"]
    assert len(result.job_errors) == 1
    assert result.job_errors[0].posting_id == "101"
    assert result.job_errors[0].error_type == "InvalidPostingDetail"


@respx.mock
def test_detail_request_failure_is_recorded_as_job_error_not_fatal() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="102", title="Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    _mock_detail(slug, "101", {}, status_code=500)
    _mock_detail(slug, "102", _detail_payload(posting_id="102"))

    result = _watcher().watch(_company())

    assert [v.external_id for v in result.vacancies] == ["102"]
    assert result.errors == []
    assert len(result.job_errors) == 1
    assert result.job_errors[0].posting_id == "101"
    assert result.job_errors[0].status_code == 500
    assert result.job_errors[0].endpoint == smartrecruiters_posting_endpoint(slug, "101")


@respx.mock
def test_exclude_title_keywords() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="101", title="Java Backend Engineer"),
        _list_row(posting_id="404", title="Staff Java Backend Engineer"),
    ]
    _mock_list_once(slug, _list_page(rows))
    _mock_detail(slug, "101", _detail_payload(posting_id="101"))

    result = _watcher().watch(
        _company(role_title_keywords=["java", "backend"], exclude_title_keywords=["staff"])
    )

    assert [item.external_id for item in result.vacancies] == ["101"]


@respx.mock
def test_identity_prefers_id_then_uuid_then_parsed_ref() -> None:
    slug = "Wise"
    rows = [
        _list_row(posting_id="1", title="Java Backend Engineer"),
        _list_row(posting_id=None, uuid="uuid-2", title="Java Backend Engineer"),
        _list_row(
            posting_id=None,
            uuid=None,
            title="Java Backend Engineer",
            ref="https://api.smartrecruiters.com/v1/companies/Wise/postings/3",
        ),
    ]
    _mock_list_once(slug, _list_page(rows))
    for posting_id in ("1", "uuid-2", "3"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher().watch(_company())

    assert {v.external_id for v in result.vacancies} == {"1", "uuid-2", "3"}


@respx.mock
def test_identity_dropped_when_ref_is_lookalike_host_or_malformed() -> None:
    slug = "Wise"
    rows = [
        _list_row(
            posting_id=None,
            uuid=None,
            title="Java Backend Engineer",
            ref="https://evil.example.com/v1/companies/Wise/postings/3",
        ),
        _list_row(
            posting_id=None,
            uuid=None,
            title="Java Backend Engineer",
            ref="not-a-url",
        ),
    ]
    _mock_list_once(slug, _list_page(rows))

    result = _watcher().watch(_company())

    assert result.vacancies == []
    assert result.raw_fetched == 2
    assert result.unique_count == 0
    # No detail endpoint should ever be called for an unresolved identity.
    assert all("/postings/3" not in str(call.request.url) for call in respx.calls)


def test_resolve_slug_rejects_lookalike_hosts() -> None:
    company = _company(job_board_url="https://careers.smartrecruiters.com.evil.net/Wise")
    try:
        resolve_smartrecruiters_slug(company)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for lookalike host")


def test_resolve_slug_rejects_malformed_multisegment_bare_string() -> None:
    company = _company(job_board_url=None, career_url="foo/bar")
    try:
        resolve_smartrecruiters_slug(company)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for malformed bare slug")


def test_resolve_slug_accepts_simple_safe_bare_identifier() -> None:
    company = _company(job_board_url=None, career_url="Wise")
    assert resolve_smartrecruiters_slug(company) == "Wise"


@respx.mock
def test_one_company_error_does_not_stop_other_companies() -> None:
    respx.get(smartrecruiters_postings_endpoint("Canva")).mock(
        return_value=httpx.Response(status_code=500)
    )
    _mock_list_once("Wise", _list_page([_list_row(posting_id="101")]))
    _mock_detail("Wise", "101", _detail_payload(posting_id="101"))

    companies = [
        _company(name="Canva", job_board_url="https://careers.smartrecruiters.com/Canva"),
        _company(name="Wise", job_board_url="https://careers.smartrecruiters.com/Wise"),
    ]
    result = _watcher().watch(companies)

    assert [item.company for item in result.vacancies] == ["Wise"]
    assert len(result.errors) == 1
    assert result.errors[0].company_name == "Canva"
    assert result.errors[0].status_code == 500


@respx.mock
def test_source_and_external_id_are_stable_across_calls() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(slug, "101", _detail_payload(posting_id="101"))

    first = _watcher().watch(_company())
    second = _watcher().watch(_company())

    assert [v.source for v in first.vacancies] == ["target_company:smartrecruiters:wise"]
    assert [v.external_id for v in first.vacancies] == [v.external_id for v in second.vacancies]


# --- A: detail completeness must reflect real role content -----------------


@respx.mock
def test_detail_with_empty_jobad_sections_is_job_error_not_full_completeness() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(slug, "101", _detail_payload(posting_id="101", sections={}))

    result = _watcher().watch(_company())

    assert result.vacancies == []
    assert len(result.job_errors) == 1
    assert result.job_errors[0].posting_id == "101"


@respx.mock
def test_detail_missing_jobad_entirely_is_job_error() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    detail = {
        "id": "101",
        "active": True,
        "name": "Java Backend Engineer",
        "location": {"city": "Tallinn", "country": "EE"},
    }
    _mock_detail(slug, "101", detail)

    result = _watcher().watch(_company())

    assert result.vacancies == []
    assert len(result.job_errors) == 1


@respx.mock
def test_detail_with_only_company_description_section_is_job_error() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(
        slug,
        "101",
        _detail_payload(
            posting_id="101",
            sections={"companyDescription": "<p>We are a great company to work for.</p>"},
        ),
    )

    result = _watcher().watch(_company())

    assert result.vacancies == []
    assert len(result.job_errors) == 1


@respx.mock
def test_detail_with_qualifications_only_section_is_accepted() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(
        slug,
        "101",
        _detail_payload(posting_id="101", sections={"qualifications": "<li>Java</li><li>Kotlin</li>"}),
    )

    result = _watcher().watch(_company())

    assert len(result.vacancies) == 1
    assert result.vacancies[0].content_completeness == "FULL"
    assert "Java" in result.vacancies[0].description


@respx.mock
def test_detail_with_full_multi_section_jobad_is_accepted() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    _mock_detail(
        slug,
        "101",
        _detail_payload(
            posting_id="101",
            sections={
                "companyDescription": "<p>We build payments infrastructure.</p>",
                "jobDescription": "<p>Build the rules platform.</p>",
                "qualifications": "<li>Java</li><li>Kotlin</li>",
            },
        ),
    )

    result = _watcher().watch(_company())

    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.content_completeness == "FULL"
    assert "Build the rules platform." in vacancy.description
    assert "Java" in vacancy.description


@respx.mock
def test_detail_board_identifier_mismatch_is_job_error() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    detail = _detail_payload(posting_id="101")
    detail["company"] = {"identifier": "SomeOtherCompany"}
    _mock_detail(slug, "101", detail)

    result = _watcher().watch(_company())

    assert result.vacancies == []
    assert len(result.job_errors) == 1
    assert result.job_errors[0].posting_id == "101"


@respx.mock
def test_detail_matching_board_identifier_is_accepted() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id="101")]))
    detail = _detail_payload(posting_id="101")
    detail["company"] = {"identifier": "Wise"}
    _mock_detail(slug, "101", detail)

    result = _watcher().watch(_company())

    assert [v.external_id for v in result.vacancies] == ["101"]


@respx.mock
def test_external_id_uses_canonical_detail_id_when_list_only_has_uuid() -> None:
    slug = "Wise"
    _mock_list_once(slug, _list_page([_list_row(posting_id=None, uuid="uuid-101")]))
    detail = _detail_payload(posting_id="101", uuid="uuid-101", posting_url=None)
    _mock_detail(slug, "uuid-101", detail)

    result = _watcher().watch(_company())

    assert len(result.vacancies) == 1
    # Even though the list row only exposed a UUID alias, the normalized
    # output uses the detail's own safe canonical id so output stays stable
    # regardless of which alias the list happened to expose.
    assert result.vacancies[0].external_id == "101"
    assert result.vacancies[0].url == "https://jobs.smartrecruiters.com/Wise/101"


# --- B: canonical public posting URL must be tightly scoped -----------------


def test_canonical_url_rejects_api_postings_url() -> None:
    url = _canonical_smartrecruiters_url(
        "https://api.smartrecruiters.com/v1/companies/Wise/postings/101",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101"


def test_canonical_url_rejects_apply_path() -> None:
    url = _canonical_smartrecruiters_url(
        "https://jobs.smartrecruiters.com/Wise/101/apply",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101"


def test_canonical_url_rejects_referral_path() -> None:
    url = _canonical_smartrecruiters_url(
        "https://jobs.smartrecruiters.com/Wise/101/referral/abc123",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101"


def test_canonical_url_rejects_other_employer_board() -> None:
    url = _canonical_smartrecruiters_url(
        "https://jobs.smartrecruiters.com/OtherCo/101-some-role",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101"


def test_canonical_url_rejects_other_posting_id() -> None:
    url = _canonical_smartrecruiters_url(
        "https://jobs.smartrecruiters.com/Wise/999-some-other-role",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101"


def test_canonical_url_strips_tracking_query_and_fragment() -> None:
    url = _canonical_smartrecruiters_url(
        "https://jobs.smartrecruiters.com/Wise/101-rules-platform?trk=referral&utm_source=x#top",
        slug="Wise",
        posting_id="101",
    )
    assert url == "https://jobs.smartrecruiters.com/Wise/101-rules-platform"


# --- C: slug resolution must parse the real URL shape per host -------------


def test_resolve_slug_from_api_postings_url() -> None:
    company = _company(
        job_board_url=None,
        career_url="https://api.smartrecruiters.com/v1/companies/Wise/postings",
    )
    assert resolve_smartrecruiters_slug(company) == "Wise"


def test_resolve_slug_rejects_credentials_in_url() -> None:
    company = _company(
        job_board_url="https://evil:pwned@careers.smartrecruiters.com/Wise",
        career_url=None,
    )
    with pytest.raises(ValueError):
        resolve_smartrecruiters_slug(company)


def test_resolve_slug_rejects_malformed_api_path() -> None:
    company = _company(
        job_board_url=None,
        career_url="https://api.smartrecruiters.com/v1/companies/Wise/apply",
    )
    with pytest.raises(ValueError):
        resolve_smartrecruiters_slug(company)


# --- E: honest pagination counters and bounds -------------------------------


@respx.mock
def test_raw_fetched_counts_non_dict_list_elements_too() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    rows = [_list_row(posting_id="1", title="Java Backend Engineer"), "not-a-row", None]

    respx.get(endpoint).mock(return_value=httpx.Response(200, json=_list_page(rows, total_found=1)))
    _mock_detail(slug, "1", _detail_payload(posting_id="1"))

    result = _watcher().watch(_company())

    assert result.raw_fetched == 3
    assert result.unique_count == 1


@respx.mock
def test_server_reported_effective_limit_keeps_paging_without_total_found() -> None:
    slug = "Wise"
    endpoint = smartrecruiters_postings_endpoint(slug)
    # Server caps every page at 2 rows (reported via "limit") and never sends
    # totalFound; a page_size == effective_limit must NOT be read as "done".
    page1 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (1, 2)]
    page2 = [_list_row(posting_id=str(i), title="Java Backend Engineer") for i in (3, 4)]

    def _responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        pages = {0: page1, 2: page2}
        if offset not in pages:
            raise AssertionError(f"unexpected offset {offset}")
        payload = _list_page(pages[offset], total_found=None)
        payload["limit"] = 2
        return httpx.Response(200, json=payload)

    respx.get(endpoint).mock(side_effect=_responder)
    for posting_id in ("1", "2", "3", "4"):
        _mock_detail(slug, posting_id, _detail_payload(posting_id=posting_id))

    result = _watcher(page_limit=100, max_pages=2).watch(_company())

    assert result.pages_fetched == 2
    assert result.raw_fetched == 4
    # max_pages was exhausted without ever seeing a page shorter than the
    # server's own effective limit, so this is honestly truncated, not
    # silently claimed complete.
    assert result.pages_truncated is True


# --- F: construction-time bounds validation ---------------------------------


def test_watcher_rejects_non_positive_max_pages() -> None:
    with pytest.raises(ValueError):
        _watcher(max_pages=0)
    with pytest.raises(ValueError):
        _watcher(max_pages=-1)


def test_watcher_rejects_page_limit_out_of_range() -> None:
    with pytest.raises(ValueError):
        _watcher(page_limit=0)
    with pytest.raises(ValueError):
        _watcher(page_limit=101)


def test_watcher_rejects_max_detail_fetches_out_of_range() -> None:
    with pytest.raises(ValueError):
        _watcher(max_detail_fetches=-1)
    with pytest.raises(ValueError):
        _watcher(max_detail_fetches=101)


def test_watcher_accepts_zero_max_detail_fetches() -> None:
    assert _watcher(max_detail_fetches=0) is not None
