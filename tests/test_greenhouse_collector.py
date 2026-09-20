from __future__ import annotations

import httpx
import pytest

from app.collectors.greenhouse_collector import (
    GreenhouseCollectionError,
    GreenhouseCollector,
    clean_html_to_text,
    greenhouse_job_to_normalized,
    normalize_greenhouse_board,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "https://x"), response=httpx.Response(self.status_code))

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str) -> _FakeResponse:
        _ = url
        return self._responses.pop(0)


def test_html_cleanup_plain_text() -> None:
    html = "<div>Hello<br>World</div><ul><li>One</li><li>Two</li></ul>"
    cleaned = clean_html_to_text(html)
    assert "Hello" in cleaned
    assert "World" in cleaned
    assert "One" in cleaned
    assert "Two" in cleaned
    assert "<li>" not in cleaned
    assert cleaned.index("Hello") < cleaned.index("World") < cleaned.index("One")
    assert "\n" in cleaned[cleaned.index("Hello") : cleaned.index("One")]


def test_html_cleanup_entity_encoded_greenhouse_content() -> None:
    encoded = (
        "&lt;p&gt;Hello&lt;br&gt;World&lt;/p&gt;"
        '&lt;li data-font="Symbol"&gt;One&lt;/li&gt;'
    )
    cleaned = clean_html_to_text(encoded)
    assert "<p>" not in cleaned
    assert "<li>" not in cleaned
    assert "data-font" not in cleaned
    assert "Hello" in cleaned
    assert "World" in cleaned
    assert "One" in cleaned
    assert cleaned.index("Hello") < cleaned.index("World") < cleaned.index("One")
    assert "\n" in cleaned[cleaned.index("Hello") : cleaned.index("World")]
    assert "\n" in cleaned[cleaned.index("World") : cleaned.index("One")]


def test_html_cleanup_decodes_common_entities() -> None:
    assert "Tom & Jerry" == clean_html_to_text("Tom &amp; Jerry")
    nbsp_cleaned = clean_html_to_text("Hello&nbsp;World")
    assert "&nbsp;" not in nbsp_cleaned
    assert "&amp;" not in nbsp_cleaned
    assert "Hello" in nbsp_cleaned
    assert "World" in nbsp_cleaned


def test_board_normalization_slug_and_url() -> None:
    assert normalize_greenhouse_board("stripe") == "stripe"
    assert normalize_greenhouse_board("https://boards.greenhouse.io/notion") == "notion"
    assert normalize_greenhouse_board("https://job-boards.greenhouse.io/canva") == "canva"
    with pytest.raises(ValueError):
        normalize_greenhouse_board("   ")


def test_collect_parses_jobs_into_normalized(monkeypatch) -> None:
    payload = {
        "jobs": [
            {
                "id": 101,
                "title": "Backend Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/stripe/jobs/101",
                "location": {"name": "Remote"},
                "metadata": [{"name": "Employment Type", "value": "Full-time"}],
                "content": "<p>Build APIs</p><p>Own services</p>",
                "updated_at": "2026-07-16T10:00:00Z",
            }
        ]
    }

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient([_FakeResponse(200, payload)]))
    collector = GreenhouseCollector(boards=["stripe"])
    result = collector.collect()

    assert len(result) == 1
    item = result[0]
    assert item.source == "greenhouse"
    assert item.external_id == "101"
    assert item.title == "Backend Engineer"
    assert item.location == "Remote"
    assert item.employment == "Full-time"
    assert "Build APIs" in item.description
    assert "Own services" in item.description
    assert item.url.startswith("https://job-boards.greenhouse.io/")
    assert item.published_at == "2026-07-16T10:00:00Z"


def test_board_failure_raises_collection_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient([_FakeResponse(500, {})]))
    collector = GreenhouseCollector(boards=["stripe"])
    with pytest.raises(GreenhouseCollectionError):
        collector.collect()


@pytest.mark.parametrize(
    ("board", "job_id", "custom_absolute_url"),
    [
        ("stripe", 555001, "https://stripe.com/jobs/listing/staff-backend-engineer/555001"),
        ("databricks", 555002, "https://www.databricks.com/company/careers/open-positions?gh_jid=555002"),
        ("roblox", 555003, "https://careers.roblox.com/jobs/555003-senior-software-engineer"),
    ],
)
def test_target_company_source_rewrites_custom_domain_to_canonical_url(
    board: str, job_id: int, custom_absolute_url: str
) -> None:
    item = {
        "id": job_id,
        "title": "Senior Backend Engineer",
        "absolute_url": custom_absolute_url,
        "location": {"name": "Remote"},
        "content": "<p>Build things</p>",
    }

    normalized = greenhouse_job_to_normalized(
        item,
        source=f"target_company:greenhouse:{board}",
        company=board.title(),
    )

    assert normalized is not None
    assert normalized.external_id == str(job_id)
    assert normalized.url == f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    assert normalized.original_url == custom_absolute_url


def test_target_company_source_leaves_already_canonical_url_untouched() -> None:
    item = {
        "id": 909,
        "title": "Backend Engineer",
        "absolute_url": "https://job-boards.greenhouse.io/wolt/jobs/909",
        "content": "<p>Build things</p>",
    }

    normalized = greenhouse_job_to_normalized(item, source="target_company:greenhouse:wolt")

    assert normalized is not None
    assert normalized.url == "https://job-boards.greenhouse.io/wolt/jobs/909"
    assert normalized.original_url is None


def test_generic_greenhouse_source_does_not_rewrite_custom_domain_url() -> None:
    item = {
        "id": 555001,
        "title": "Staff Backend Engineer",
        "absolute_url": "https://stripe.com/jobs/listing/staff-backend-engineer/555001",
        "content": "<p>Build things</p>",
    }

    normalized = greenhouse_job_to_normalized(item)

    assert normalized is not None
    assert normalized.source == "greenhouse"
    assert normalized.url == "https://stripe.com/jobs/listing/staff-backend-engineer/555001"
    assert normalized.original_url is None
