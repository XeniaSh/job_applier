from datetime import datetime, timezone
from email.message import EmailMessage

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.email_imap_client import (
    EmailAuthenticationError,
    RawEmailMessage,
)


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "test@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "app-password")


def _raw_message(*, subject: str, html: str | None, plain: str, message_id: str) -> RawEmailMessage:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if html is not None:
        message.set_content(plain)
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(plain)

    return RawEmailMessage(
        uid=message_id.strip("<>"),
        message_id=message_id,
        from_address="jobs-noreply@linkedin.com",
        subject=subject,
        received_at=datetime(2026, 7, 17, 8, 24, tzinfo=timezone.utc),
        email_message=message,
    )


def test_preview_one_email_cli_output(monkeypatch) -> None:
    _set_env(monkeypatch)

    message = _raw_message(
        subject="Java Backend jobs for you",
        html='<a href="https://www.linkedin.com/jobs/view/123/">Senior Java Backend Engineer</a>',
        plain="fallback",
        message_id="<m1>",
    )

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            return [message]

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["preview-linkedin-email"])

    assert result.exit_code == 0
    assert "Found 1 LinkedIn Job Alert emails" in result.output
    assert "Email #1" in result.output
    assert "Job ID:" in result.output
    assert "123" in result.output
    assert "Parser source:" in result.output
    assert "STRUCTURED_CARD" in result.output


def test_preview_multiple_emails_plain_text_fallback_and_duplicates(monkeypatch) -> None:
    _set_env(monkeypatch)
    message_1 = _raw_message(
        subject="Job alert",
        html=None,
        plain="https://www.linkedin.com/jobs/view/1/",
        message_id="<m1>",
    )
    message_2 = _raw_message(
        subject="New jobs",
        html=None,
        plain="\n".join(
            [
                "https://www.linkedin.com/jobs/view/1/",
                "https://www.linkedin.com/jobs/search/?currentJobId=2",
            ]
        ),
        message_id="<m2>",
    )

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            return [message_1, message_2]

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["preview-linkedin-email", "--limit-emails", "2", "--limit-vacancies", "20"],
    )

    assert result.exit_code == 0
    assert "Emails processed: 2" in result.output
    assert "Vacancies extracted: 3" in result.output
    assert "Duplicate job IDs: 1" in result.output
    assert "Structured cards: 0" in result.output
    assert "Fallback URLs: 2" in result.output


def test_preview_malformed_email_does_not_stop(monkeypatch) -> None:
    _set_env(monkeypatch)
    message_1 = _raw_message(
        subject="Job alert",
        html=None,
        plain="https://www.linkedin.com/jobs/view/1/",
        message_id="<bad>",
    )
    message_2 = _raw_message(
        subject="Job alert",
        html=None,
        plain="https://www.linkedin.com/jobs/view/2/",
        message_id="<good>",
    )

    original_parser = cli_module.parse_linkedin_email

    def fake_parser(raw_message):
        if raw_message.message_id == "<bad>":
            raise ValueError("broken email")
        return original_parser(raw_message)

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            return [message_1, message_2]

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    monkeypatch.setattr(cli_module, "parse_linkedin_email", fake_parser)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["preview-linkedin-email", "--limit-emails", "2"])

    assert result.exit_code == 0
    assert "Warning: failed to parse email" in result.output
    assert "Parsing errors: 1" in result.output
    assert "Emails processed: 2" in result.output


def test_preview_empty_mailbox(monkeypatch) -> None:
    _set_env(monkeypatch)

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            return []

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["preview-linkedin-email"])

    assert result.exit_code == 0
    assert "Found 0 LinkedIn Job Alert emails" in result.output
    assert "Emails processed: 0" in result.output


def test_preview_save_html(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    message = _raw_message(
        subject="Job alert",
        html='<a href="https://www.linkedin.com/jobs/view/1/">Role</a>',
        plain="fallback",
        message_id="<m1>",
    )

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            return [message]

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["preview-linkedin-email", "--save-html"])

    assert result.exit_code == 0
    files = list((tmp_path / "data" / "debug").glob("linkedin_email_*.html"))
    assert len(files) == 1
    assert "linkedin.com/jobs/view/1/" in files[0].read_text(encoding="utf-8")


def test_preview_non_zero_on_auth_failure(monkeypatch) -> None:
    _set_env(monkeypatch)

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def fetch_linkedin_messages(self):
            raise EmailAuthenticationError("bad auth")

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["preview-linkedin-email"])

    assert result.exit_code != 0

