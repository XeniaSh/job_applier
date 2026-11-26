from dataclasses import asdict
import json

import httpx
import pytest

from app.collectors.habr_details import HabrDetailClient, parse_detail


def page(description=None, external_id="1000000001", **fields):
    description = description if description is not None else "<p>Разрабатывать Java backend сервисы и REST API. Spring Boot, PostgreSQL, Kafka. Можно удалённо, страны уточняются. Требуется практический опыт разработки и тестирования.</p>"
    return '<script type="application/ld+json">' + json.dumps({
        "@type": "JobPosting", "identifier": {"value": external_id}, "description": description, **fields,
    }) + "</script>"


def test_complete_matching_job_text_excludes_other_page_sections():
    result = parse_detail(page() + "<div>Similar specialists earn 999999 ₽</div>", "1000000001", now=1000)
    assert result.completeness == "FULL"
    assert "Java backend" in result.text and "999999" not in result.text
    assert "<p>" not in result.text


def test_wrong_id_challenge_and_image_only_are_not_complete():
    assert parse_detail(page(external_id="1000000002"), "1000000001", now=1000).completeness == "UNAVAILABLE"
    assert parse_detail("<html>Access restricted CAPTCHA</html>", "1000000001", now=1000).status == "no_matching_jobposting"
    assert parse_detail(page('<img src="https://example.test/jd.png">'), "1000000001", now=1000).status == "image_content"
    assert parse_detail(page('Some duties <img src="jd.png">'), "1000000001", now=1000).completeness == "PARTIAL"
    assert parse_detail(page("Short description"), "1000000001", now=1000).completeness == "PARTIAL"


def test_expired_job_is_explicit():
    assert parse_detail(page(validThrough="2020-01-01"), "1000000001", now=1800000000).expired


def test_cache_persists_across_restart_and_throttles_requests(tmp_path):
    calls, sleeps = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=page(external_id=request.url.path.rsplit("/", 1)[1]))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        kwargs = dict(client=client, clock=lambda: 1000, monotonic=lambda: 1000, sleep=sleeps.append)
        service = HabrDetailClient(tmp_path / "details.json", **kwargs)
        service.fetch("https://career.habr.com/vacancies/1000000001")
        service.fetch("https://career.habr.com/vacancies/1000000002")
        restarted = HabrDetailClient(tmp_path / "details.json", **kwargs)
        assert restarted.fetch("https://career.habr.com/vacancies/1000000001").completeness == "FULL"
        assert len(calls) == 2 and sleeps == [1.0]
        assert all(r.method == "GET" for r in calls)


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_access_denials_and_errors_keep_sanitized_fallback(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret_token=PRIVATE", headers={"location": "https://other.test"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = HabrDetailClient(tmp_path / "details.json", client=client, min_interval_seconds=0)
        first = service.fetch("https://career.habr.com/vacancies/1000000001")
        second = service.fetch("https://career.habr.com/vacancies/1000000002")
        assert first.completeness == second.completeness == "UNAVAILABLE"
        assert "PRIVATE" not in json.dumps(asdict(first))
        assert len(calls) == (1 if status in {403, 429} else 2)


def test_invalid_url_never_requests_network(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))) as client:
        result = HabrDetailClient(tmp_path / "cache.json", client=client).fetch("https://other.test/vacancies/12345")
    assert result.status == "invalid_url" and not (tmp_path / "cache.json").exists()


def test_budget_reuses_cache_but_does_not_request_missing_detail(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page()))) as client:
        service = HabrDetailClient(tmp_path / "cache.json", client=client)
        service.fetch("https://career.habr.com/vacancies/1000000001")
        assert service.fetch("https://career.habr.com/vacancies/1000000001", allow_network=False).completeness == "FULL"
        assert service.fetch("https://career.habr.com/vacancies/1000000002", allow_network=False).status == "budget_limit"
        assert service.requests == 1


def test_cached_access_denial_stops_other_urls_after_restart(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, text="denied"))) as client:
        service = HabrDetailClient(tmp_path / "cache.json", client=client, clock=lambda: 1000)
        service.fetch("https://career.habr.com/vacancies/1000000001")
        restarted = HabrDetailClient(tmp_path / "cache.json", client=client, clock=lambda: 1300)
        assert restarted.fetch("https://career.habr.com/vacancies/1000000002").status == "access_restricted"
        assert restarted.requests == 0
