from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from app.collectors.email_imap_client import RawEmailMessage
from app.collectors.linkedin_email_parser import parse_linkedin_email
from app.collectors.linkedin_models import ContentCompleteness, ParserSource


FIXTURES_DIR = Path("tests/fixtures/linkedin")


def _raw_message_from_html(html_payload: str) -> RawEmailMessage:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "Job alert"
    message.set_content("Plain fallback")
    message.add_alternative(html_payload, subtype="html")
    return RawEmailMessage(
        uid="1",
        message_id="<m1>",
        from_address="jobs-noreply@linkedin.com",
        subject="Job alert",
        received_at=datetime.now(timezone.utc),
        email_message=message,
    )


def test_structured_card_fields_extracted_from_fixture() -> None:
    html_payload = (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))

    assert len(vacancies) == 2
    first = vacancies[0]
    assert first.external_id == "1111111111"
    assert first.title == "Senior Java Backend Engineer"
    assert first.company == "Fictional Labs"
    assert first.location == "Berlin, Germany"
    assert first.snippet == "Build JVM services and improve payment platform reliability."
    assert first.url == "https://www.linkedin.com/jobs/view/1111111111/"
    assert first.parser_source == ParserSource.STRUCTURED_CARD
    assert first.content_completeness == ContentCompleteness.FULL


def test_multiple_cards_no_field_leakage() -> None:
    html_payload = (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))

    second = vacancies[1]
    assert second.external_id == "2222222222"
    assert second.title == "Kotlin Backend Developer"
    assert second.company == "Nimbus Bank"
    assert second.location == "Remote - Europe"
    assert second.parser_source == ParserSource.STRUCTURED_CARD
    assert second.content_completeness == ContentCompleteness.PARTIAL


def test_tracking_url_normalized_and_navigation_ignored() -> None:
    html_payload = (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))
    urls = [item.url for item in vacancies]

    assert "https://www.linkedin.com/jobs/view/1111111111/" in urls
    assert "https://www.linkedin.com/jobs/view/2222222222/" in urls
    assert all("/feed/" not in url for url in urls)
    assert all("unsubscribe" not in url for url in urls)


def test_current_job_id_fallback_and_minimal_when_only_url() -> None:
    html_payload = (FIXTURES_DIR / "alert_fallback_only.html").read_text(encoding="utf-8")
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))

    assert len(vacancies) == 1
    item = vacancies[0]
    assert item.external_id == "3333333333"
    assert item.url == "https://www.linkedin.com/jobs/view/3333333333/"
    assert item.parser_source == ParserSource.FALLBACK_URL
    assert item.content_completeness == ContentCompleteness.MINIMAL


def test_duplicate_prevention_between_structured_and_fallback() -> None:
    html_payload = (
        (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
        + "\nhttps://www.linkedin.com/jobs/view/1111111111/?tracking=dup"
    )
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))

    assert [item.external_id for item in vacancies] == ["1111111111", "2222222222"]
