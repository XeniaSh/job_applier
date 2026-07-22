from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from app.collectors.email_imap_client import RawEmailMessage
from app.collectors.linkedin_email_parser import parse_linkedin_email, parse_linkedin_email_with_diagnostics
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


def test_parser_diagnostics_include_card_boundaries_and_visible_text() -> None:
    html_payload = (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
    vacancies, diagnostics = parse_linkedin_email_with_diagnostics(_raw_message_from_html(html_payload))

    assert len(vacancies) == 2
    assert diagnostics.cards_found == 2
    assert diagnostics.card_diagnostics
    first = diagnostics.card_diagnostics[0]
    assert first.card_index == 1
    assert first.card_begin is not None
    assert first.card_end is not None
    assert first.visible_text_length > 0
    assert first.visible_text_preview
    assert "<a " not in first.visible_text_preview.lower()
    assert "href=" not in first.visible_text_preview.lower()


def test_parser_diagnostics_show_alert_context_per_card() -> None:
    html_payload = (FIXTURES_DIR / "alert_structured_cards.html").read_text(encoding="utf-8")
    _, diagnostics = parse_linkedin_email_with_diagnostics(_raw_message_from_html(html_payload))

    assert diagnostics.card_diagnostics
    for card in diagnostics.card_diagnostics:
        assert card.email_subject_context == "Job alert"


def test_promotional_snippet_is_flagged_in_diagnostics() -> None:
    html_payload = """
    <html><body>
      <a href="https://www.linkedin.com/jobs/view/9999999999/">Java API Developer</a>
      <div>Nityo • Hybrid</div>
      <div>Stand out and let hirers know you're open to work.</div>
    </body></html>
    """
    vacancies, diagnostics = parse_linkedin_email_with_diagnostics(_raw_message_from_html(html_payload))
    assert vacancies[0].snippet is None
    assert vacancies[0].content_completeness == ContentCompleteness.PARTIAL
    assert diagnostics.cards_found == 1
    card = diagnostics.card_diagnostics[0]
    assert card.snippet_source == "promo"
    assert card.promotional_snippet_detected is True
    assert diagnostics.cards_with_promotional_snippet == 1


def test_alert_query_is_sanitized_from_subject() -> None:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "JVM Backend posted in the past 24 hours"
    message.set_content("Plain fallback")
    message.add_alternative('<html><body><a href="https://www.linkedin.com/jobs/view/1234567890/">Senior Java Developer</a></body></html>', subtype="html")
    raw = RawEmailMessage(
        uid="1",
        message_id="<m1>",
        from_address="jobs-noreply@linkedin.com",
        subject="JVM Backend posted in the past 24 hours",
        received_at=datetime.now(timezone.utc),
        email_message=message,
    )
    vacancies = parse_linkedin_email(raw)
    assert vacancies
    assert vacancies[0].alert_query == "JVM Backend"


def test_vacancy_subject_is_not_used_as_alert_query() -> None:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "Software Engineer - Backend (Remote) at Hire Feed"
    message.set_content("Plain fallback")
    message.add_alternative('<html><body><a href="https://www.linkedin.com/jobs/view/1234567891/">Senior Java Developer</a></body></html>', subtype="html")
    raw = RawEmailMessage(
        uid="1",
        message_id="<m2>",
        from_address="jobs-noreply@linkedin.com",
        subject="Software Engineer - Backend (Remote) at Hire Feed",
        received_at=datetime.now(timezone.utc),
        email_message=message,
    )
    vacancies = parse_linkedin_email(raw)
    assert vacancies
    assert vacancies[0].alert_query is None


def test_footer_does_not_extend_last_card_text() -> None:
    html_payload = """
    <html><body>
      <a href="https://www.linkedin.com/jobs/view/7000000001/">Senior Java Developer</a>
      <div>NE Group</div>
      <div>Hyderabad</div>
      <div>See all jobs</div>
      <div>Try Premium</div>
      <div>Install LinkedIn Widgets</div>
      <div>You are receiving Job Alert emails</div>
    </body></html>
    """
    vacancies, diagnostics = parse_linkedin_email_with_diagnostics(_raw_message_from_html(html_payload))
    assert len(vacancies) == 1
    card = diagnostics.card_diagnostics[0]
    assert "try premium" not in card.visible_text_preview.lower()
    assert "install linkedin widgets" not in card.visible_text_preview.lower()


def test_merged_linkedin_title_splits_company_and_location() -> None:
    html_payload = """
    <html><body>
      <a href="https://www.linkedin.com/jobs/view/5555555555/">
        Backend Lead (Java/Kotlin) Salmon Group Ltd · Yerevan (Remote)
      </a>
      <a href="https://www.linkedin.com/jobs/view/5555555556/">
        <img alt="Wirestock" src="https://media.licdn.com/logo.png" />
        Java Backend Engineer Wirestock · Yerevan, Armenia
      </a>
      <a href="https://www.linkedin.com/jobs/view/5555555557/">
        Senior Java Developer Polixis · Tbilisi (Hybrid)
      </a>
      <a href="https://www.linkedin.com/jobs/view/5555555558/">
        Java Software Engineer EPAM · Yerevan (Remote)
      </a>
    </body></html>
    """
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))
    by_id = {item.external_id: item for item in vacancies}

    salmon = by_id["5555555555"]
    assert salmon.title == "Backend Lead (Java/Kotlin)"
    assert salmon.company == "Salmon Group Ltd"
    assert salmon.location == "Yerevan (Remote)"

    wirestock = by_id["5555555556"]
    assert wirestock.title == "Java Backend Engineer"
    assert wirestock.company == "Wirestock"
    assert wirestock.location == "Yerevan, Armenia"

    polixis = by_id["5555555557"]
    assert polixis.title == "Senior Java Developer"
    assert polixis.company == "Polixis"
    assert polixis.location == "Tbilisi (Hybrid)"

    epam = by_id["5555555558"]
    assert epam.title == "Java Software Engineer"
    assert epam.company == "EPAM"
    assert epam.location == "Yerevan (Remote)"


def test_linkedin_ui_markers_stripped_from_title() -> None:
    html_payload = """
    <html><body>
      <a href="https://www.linkedin.com/jobs/view/6666666661/">
        Senior Java Backend Engineer Easy Apply Promoted
      </a>
      <div>ACME Corp · Berlin (Remote)</div>
      <a href="https://www.linkedin.com/jobs/view/6666666662/">
        Kotlin Developer Featured Actively Recruiting Hiring multiple candidates
      </a>
      <div>Nimbus · Remote - Europe</div>
    </body></html>
    """
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))
    by_id = {item.external_id: item for item in vacancies}

    first = by_id["6666666661"]
    assert first.title == "Senior Java Backend Engineer"
    assert "easy apply" not in first.title.lower()
    assert "promoted" not in first.title.lower()
    assert first.company == "ACME Corp"
    assert first.location == "Berlin (Remote)"

    second = by_id["6666666662"]
    assert second.title == "Kotlin Developer"
    assert "featured" not in second.title.lower()
    assert "actively recruiting" not in second.title.lower()
    assert "hiring multiple" not in second.title.lower()


def test_location_recovered_from_merged_title_when_field_empty() -> None:
    html_payload = """
    <html><body>
      <a href="https://www.linkedin.com/jobs/view/7777777771/">
        Backend Lead (Java/Kotlin) Salmon Group Ltd · Yerevan (Remote) Easy Apply
      </a>
    </body></html>
    """
    vacancies = parse_linkedin_email(_raw_message_from_html(html_payload))
    assert len(vacancies) == 1
    item = vacancies[0]
    assert item.title == "Backend Lead (Java/Kotlin)"
    assert item.company == "Salmon Group Ltd"
    assert item.location == "Yerevan (Remote)"
    assert item.location is not None and item.location.strip() != ""


def test_subject_from_vacancy_a_is_not_injected_into_vacancy_b_analysis_text() -> None:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "Software Engineer - Backend (Remote) at Hire Feed"
    message.set_content("Plain fallback")
    message.add_alternative(
        """
        <html><body>
          <a href="https://www.linkedin.com/jobs/view/8000000001/">Software Engineer - Backend (Remote)</a>
          <div>Hire Feed</div>
          <a href="https://www.linkedin.com/jobs/view/8000000002/">Senior Java Developer</a>
          <div>NE Group</div>
          <div>Hyderabad</div>
        </body></html>
        """,
        subtype="html",
    )
    raw = RawEmailMessage(
        uid="1",
        message_id="<m3>",
        from_address="jobs-noreply@linkedin.com",
        subject="Software Engineer - Backend (Remote) at Hire Feed",
        received_at=datetime.now(timezone.utc),
        email_message=message,
    )
    vacancies = parse_linkedin_email(raw)
    assert len(vacancies) >= 2
    second = vacancies[1]
    analysis_text = second.to_analysis_text()
    assert "Alert context:" not in analysis_text
    assert "Hire Feed" not in analysis_text
