from datetime import datetime, timezone
from html import escape

import httpx
import pytest
from pydantic import ValidationError

from app.collectors.habr_rss import (
    FeedError, HabrRSSConfig, canonical_habr_url, collect_rss, evaluate_record, parse_feed,
)
from app.company_watch.domain_policy import DomainPolicy
from scripts.pilot_habr_rss import build_report

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


def item(job_id="1000000001", title="Senior Java/Kotlin Developer", description=None, date="Wed, 25 Nov 2026 12:00:00 +0300"):
    description = description if description is not None else (
        f"Компания «Synthetic Example» ищет хорошего специалиста на вакансию «{title}». "
        "Алматы (Казахстан). От 200 000 ₽. Полный рабочий день. Можно удалённо. Требуемые навыки: #senior, #Java."
    )
    return (f"<item><title>{escape('Требуется «' + title + '»')}</title><description>{escape(description)}</description>"
            f"<author>Synthetic Example</author><pubDate>{date}</pubDate><link>https://career.habr.com/vacancies/{job_id}</link>"
            f"<guid>{job_id}</guid></item>")


def feed(*items):
    return ("<?xml version='1.0' encoding='UTF-8'?><rss><channel>" + "".join(items) + "</channel></rss>").encode()


def evaluate(xml=None, **kwargs):
    record = parse_feed(xml or feed(item()))[0][0]
    return evaluate_record(record, policy=DomainPolicy(), now=NOW, max_age_days=30, **kwargs)


def test_synthetic_public_metadata_and_partial_content():
    records, raw, invalid = parse_feed(feed(item()))
    record = records[0]
    assert (raw, invalid) == (1, 0)
    assert record.vacancy.source == "habr"
    assert record.vacancy.external_id == "1000000001"
    assert record.vacancy.title == "Senior Java/Kotlin Developer"
    assert record.vacancy.company == "Synthetic Example"
    assert record.vacancy.location == "Алматы (Казахстан)"
    assert record.vacancy.published_at == "2026-11-25T09:00:00+00:00"
    assert record.salary == "От 200 000 ₽"
    assert record.grades == ("senior",)
    assert record.remote_conditions == "Можно удалённо"
    assert record.vacancy.content_completeness == "PARTIAL"
    row = evaluate()
    assert row["jvm_candidate"] and row["fresh"]
    assert row["remote_geography_unknown"] and not row["explicit_international_remote"]
    assert row["eligibility"] == "manual"


@pytest.mark.parametrize("url", [
    "http://career.habr.com/vacancies/1000000001", "https://evil.example/vacancies/1000000001",
    "https://career.habr.com@evil.example/vacancies/1000000001", "https://career.habr.com:443/vacancies/1000000001",
    "https://career.habr.com/vacancies/not-an-id", "https://career.habr.com/vacancies/1000000001/apply",
])
def test_reject_untrusted_posting_links(url):
    assert canonical_habr_url(url) is None


def test_canonical_url_omits_tracking_and_fragment():
    assert canonical_habr_url("https://career.habr.com/vacancies/1000000001/?ref=test#apply") == (
        "1000000001", "https://career.habr.com/vacancies/1000000001",
    )


def test_link_guid_mismatch_and_invalid_identity_counted():
    xml = feed(item().replace("<guid>1000000001", "<guid>1000000002"), item(job_id="evil"))
    assert parse_feed(xml) == ([], 2, 2)


@pytest.mark.parametrize("xml", [
    b"<!DOCTYPE rss [<!ENTITY x 'secret'>]><rss><channel/></rss>",
    b"<!ENTITY x SYSTEM 'file:///private'><rss/>", b"<rss>", b"<html>Access denied</html>",
    "<rss><channel/></rss>".encode("utf-16"),
])
def test_unsafe_malformed_and_non_utf8_xml_fail_closed(xml):
    with pytest.raises(FeedError):
        parse_feed(xml)


def test_payload_and_item_limits():
    with pytest.raises(FeedError, match="response_too_large"):
        parse_feed(feed(item()), max_bytes=10)
    with pytest.raises(FeedError, match="item_limit"):
        parse_feed(feed(item(), item()), max_items=1)


def test_missing_metadata_bad_dates_and_freshness():
    xml = feed("<item><title>Java developer</title><link>https://career.habr.com/vacancies/1000000001</link></item>")
    row = evaluate(xml)
    assert row["published_at"] is None and row["salary"] is None and row["company"] is None
    assert not row["fresh"] and row["age_days"] is None
    assert not evaluate(feed(item(date="Wed, 01 Jan 2020 12:00:00 +0300")))["fresh"]
    assert not evaluate(feed(item(date="Fri, 01 Jan 2027 12:00:00 +0300")))["fresh"]
    assert not evaluate(feed(item(date="yesterday")))["fresh"]


def test_html_contact_redaction_and_salary_estimate_not_offer():
    xml = feed(item(description="<p>Можно удалённо. Похожие специалисты получают 200 000 ₽. mail@example.test +7 (999) 123-45-67</p>"))
    record = parse_feed(xml)[0][0]
    assert "<p>" not in record.vacancy.description
    assert "mail@example.test" not in record.vacancy.description
    assert "+7" not in record.vacancy.description
    assert record.salary is None


def test_explicit_salary_basis_and_range_preserved():
    record = parse_feed(feed(item(description="От 200 000 до 350 000 ₽ на руки. Можно удалённо.")))[0][0]
    assert record.salary == "От 200 000 до 350 000 ₽"
    assert record.salary_basis == "net"


def test_author_email_and_html_script_are_not_company_metadata():
    xml = feed(item(description="<script>SECRET</script><p>Можно удалённо.</p>")).replace(
        b"<author>Synthetic Example</author>", b"<author>contact@example.test</author>",
    )
    record = parse_feed(xml)[0][0]
    assert "SECRET" not in record.vacancy.description
    assert record.vacancy.company is None


@pytest.mark.parametrize("title,grade", [
    ("QAA на Java", "senior"), ("QA Automation Java", "senior"), ("SDET Java", "senior"),
    ("Android Kotlin Developer", "senior"), ("Senior JavaScript Developer", "senior"),
    ("Java Developer", "junior"), ("Стажёр Java", "intern"), ("Engineering Manager Java", "senior"),
])
def test_false_positive_titles_and_junior_grades_are_not_jvm_shortlist(title, grade):
    row = evaluate(feed(item(title=title, description=f"Можно удалённо. #{grade}")))
    assert not row["jvm_candidate"] and row["unsuitable"]


def test_ai_review_is_separate_from_current_deterministic_admission():
    row = evaluate(feed(item(title="Python-разработчик AI-сервисов (LLM / RAG)")))
    assert row["ai_backend_review_candidate"]
    assert not row["ai_backend_candidate"]  # existing non-Java title rule preserved
    assert row["excluded_by_current_rules"] and not row["unsuitable"]
    assert not evaluate(feed(item(title="Claude Code Backend Developer")))["ai_backend_review_candidate"]
    assert not evaluate(feed(item(title="Senior LLM Engineer (.NET)")))["ai_backend_review_candidate"]


@pytest.mark.parametrize("text,international,restricted", [
    ("Можно удалённо. Работа из любой страны.", True, False),
    ("Можно удалённо. Remote worldwide.", True, False),
    ("Можно удалённо.", False, False),
    ("Можно удалённо только из РФ. Remote worldwide.", False, True),
    ("Нельзя работать из любой страны. Можно удалённо.", False, False),
    ("Remote worldwide. Must have the right to work in the UK.", False, True),
])
def test_remote_is_not_international_eligibility(text, international, restricted):
    row = evaluate(feed(item(description=text)))
    assert row["explicit_international_remote"] is international
    assert row["remote_geography_restricted"] is restricted
    assert row["eligibility"] == ("posting_support" if international else "manual")


def test_observed_outside_rf_title_is_a_limit_not_worldwide_permission():
    row = evaluate(feed(item(title="AI Security Researcher (строго вне РФ и вне РБ)")))
    assert row["abroad_only_restriction"] and row["remote_geography_restricted"]
    assert not row["remote_geography_unknown"] and not row["explicit_international_remote"]
    assert row["eligibility"] == "manual"


@pytest.mark.parametrize("domain,text", [
    ("speculative_trading", "We are a forex broker."), ("crypto_exchange", "Мы — криптобиржа."),
    ("gambling", "Our client is an online casino."), ("defense", "We are a defense contractor."),
    ("adult_entertainment", "We are an adult entertainment company."),
])
def test_shared_domain_gate(domain, text):
    record = parse_feed(feed(item(description=text)))[0][0]
    row = evaluate_record(record, policy=DomainPolicy(excluded_domains=[domain]), now=NOW, max_age_days=30)
    assert row["domain_exclusion"] and row["unsuitable"] and not row["jvm_candidate"]


def test_cross_query_dedup_and_pagination_stops_on_repeated_ids():
    requests = []

    def handler(request):
        requests.append(request)
        page = request.url.params["page"]
        return httpx.Response(200, content=feed(item(), *([item(job_id="1000000002")] if page != "1" else [])))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_rss(HabrRSSConfig(queries=["Java", "Kotlin"], max_pages=3), client=client)
    assert len(requests) == 6
    assert all(request.url.params["remote"] == "1" for request in requests)
    assert all(request.method == "GET" and request.url.host == "career.habr.com" for request in requests)
    assert len(result.records) == 2
    assert result.records[0].queries == ("Java", "Kotlin")
    assert [f.stop for f in result.feeds] == ["repeated_page", "repeated_page"]
    report = build_report(result, HabrRSSConfig(), now=NOW)
    assert report["counts"]["raw"] == 10
    assert report["counts"]["unique"] == 2
    assert report["counts"]["duplicates"] == 8
    assert report["duplicate_rate"] == 0.8


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_no_redirect_retry_or_access_denial_workaround(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://evil.example"}, text="token=SECRET")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_rss(HabrRSSConfig(queries=["Java", "Kotlin"]), client=client)
    assert not result.records
    assert len(calls) == (1 if status in {401, 403, 429} else 2)
    assert all(f.stop == "http_error" and f.status == status for f in result.feeds)
    assert "SECRET" not in str(result)


def test_timeout_is_sanitized_and_next_query_can_run():
    def handler(request):
        if request.url.params["q"] == "Java":
            raise httpx.ReadTimeout("token=SECRET", request=request)
        return httpx.Response(200, content=feed(item()))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_rss(HabrRSSConfig(queries=["Java", "Kotlin"], max_pages=1), client=client)
    assert result.feeds[0].stop == "timeout" and len(result.records) == 1
    assert "SECRET" not in str(result)


def test_stream_byte_limit_and_invalid_feed_have_fixed_diagnostics():
    def handler(request):
        if request.url.params["q"] == "Java":
            return httpx.Response(200, content=b"x" * 1025)
        return httpx.Response(200, content=b"<html>secret error body</html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_rss(HabrRSSConfig(queries=["Java", "Kotlin"], max_response_bytes=1024), client=client)
    assert [f.stop for f in result.feeds] == ["response_too_large", "unsupported_feed"]
    assert not result.records and "secret" not in str(result)


def test_elapsed_request_deadline_is_bounded(monkeypatch):
    from app.collectors import habr_rss
    times = iter([0, 100, 200, 300])
    monkeypatch.setattr(habr_rss.time, "monotonic", lambda: next(times))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=feed(item())))) as client:
        result = collect_rss(HabrRSSConfig(queries=["Java"], max_pages=1), client=client)
    assert result.feeds[0].stop == "deadline"
    assert not result.records


@pytest.mark.parametrize("values", [
    {"queries": [""]}, {"queries": ["Java\nTOKEN"]}, {"queries": ["a" * 121]},
    {"max_pages": 20}, {"timeout_seconds": float("inf")}, {"max_response_bytes": 100000000},
])
def test_configuration_is_bounded(values):
    with pytest.raises(ValidationError):
        HabrRSSConfig(**values)


def test_pilot_rejects_output_outside_isolated_directory_before_network(monkeypatch, tmp_path):
    from scripts import pilot_habr_rss as pilot
    monkeypatch.setattr("sys.argv", ["pilot", "--output", str(tmp_path / "report.json")])
    monkeypatch.setattr(pilot, "collect_rss", lambda *_: pytest.fail("must not access network"))
    with pytest.raises(SystemExit) as error:
        pilot.main()
    assert error.value.code == 2


def test_pilot_import_graph_does_not_load_private_runtime_services():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", (
        "import sys; import scripts.pilot_habr_rss; "
        "assert not any(m in sys.modules for m in "
        "['app.config', 'app.cli', 'app.llm_client', 'app.storage', 'app.telegram.client'])"
    )], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
