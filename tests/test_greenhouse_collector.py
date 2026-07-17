from __future__ import annotations

import httpx
import pytest

from app.collectors.greenhouse_collector import (
    GreenhouseCollectionError,
    GreenhouseCollector,
    clean_html_to_text,
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
