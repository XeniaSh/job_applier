from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.email_imap_client import EmailAuthenticationError
from app.collectors.linkedin_email_collector import LinkedInEmailCollectReport, LinkedInProcessedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "test@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "app-password")


def _evaluation(decision: Decision) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        matched_points=["java"],
        gaps=["redis"],
        nuances=["remote"],
        match_percentage=88.9,
        matched_score=8.0,
        total_possible_score=9.0,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def test_default_output_excludes_ignore(monkeypatch) -> None:
    _set_env(monkeypatch)
    report = LinkedInEmailCollectReport(
        emails_found=1,
        vacancies_extracted=2,
        new_vacancies=2,
        analyzed=2,
        strong_matches=1,
        potential_matches=0,
        ignored=1,
        errors=0,
        processed=[
            LinkedInProcessedVacancy("1", "Java Backend", "Acme", "Remote", "https://www.linkedin.com/jobs/view/1/", "PARTIAL", _evaluation(Decision.STRONG_MATCH)),
            LinkedInProcessedVacancy("2", "Other role", "Acme", "Remote", "https://www.linkedin.com/jobs/view/2/", "MINIMAL", _evaluation(Decision.IGNORE)),
        ],
    )

    class FakeCollector:
        def __init__(self, email_client, analyzer, seen_jobs) -> None:
            _ = (email_client, analyzer, seen_jobs)

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            return report

    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-linkedin-email"])

    assert result.exit_code == 0
    assert "Решение: STRONG_MATCH" in result.output
    assert "Решение: IGNORE" not in result.output


def test_include_ignore_prints_ignore(monkeypatch) -> None:
    _set_env(monkeypatch)
    report = LinkedInEmailCollectReport(
        emails_found=1,
        vacancies_extracted=1,
        new_vacancies=1,
        analyzed=1,
        strong_matches=0,
        potential_matches=0,
        ignored=1,
        errors=0,
        processed=[
            LinkedInProcessedVacancy("2", "Other role", "Acme", "Remote", "https://www.linkedin.com/jobs/view/2/", "MINIMAL", _evaluation(Decision.IGNORE))
        ],
    )

    class FakeCollector:
        def __init__(self, email_client, analyzer, seen_jobs) -> None:
            _ = (email_client, analyzer, seen_jobs)

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            return report

    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-linkedin-email", "--include-ignore"])

    assert result.exit_code == 0
    assert "Решение: IGNORE" in result.output


def test_dry_run_prints_metadata_only(monkeypatch) -> None:
    _set_env(monkeypatch)
    report = LinkedInEmailCollectReport(
        emails_found=1,
        vacancies_extracted=1,
        new_vacancies=1,
        analyzed=0,
        strong_matches=0,
        potential_matches=0,
        ignored=0,
        errors=0,
        processed=[
            LinkedInProcessedVacancy(
                "1",
                "Java Backend",
                "Acme",
                "Remote",
                "https://www.linkedin.com/jobs/view/1/",
                "PARTIAL",
                None,
            )
        ],
    )

    class FakeCollector:
        def __init__(self, email_client, analyzer, seen_jobs) -> None:
            _ = (email_client, analyzer, seen_jobs)

        def collect_and_analyze(self, **kwargs):
            assert kwargs["dry_run"] is True
            assert kwargs["skip_seen"] is True
            assert kwargs["mark_seen"] is True
            assert kwargs["analyze_in_dry_run"] is False
            return report

    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-linkedin-email", "--dry-run", "--limit", "5"])

    assert result.exit_code == 0
    assert "Режим: DRY-RUN" in result.output
    assert "Решение:" not in result.output


def test_mailbox_password_never_logged(monkeypatch) -> None:
    _set_env(monkeypatch)

    class FakeCollector:
        def __init__(self, email_client, analyzer, seen_jobs) -> None:
            _ = (email_client, analyzer, seen_jobs)

        def collect_and_analyze(self, **kwargs):
            _ = kwargs
            raise EmailAuthenticationError("invalid credentials app-password")

    monkeypatch.setattr(cli_module, "LinkedInEmailCollector", FakeCollector)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["collect-linkedin-email"])

    assert result.exit_code != 0
    assert "app-password" not in result.output


def test_list_imap_folders_cli_output(monkeypatch) -> None:
    _set_env(monkeypatch)

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def list_mailboxes(self):
            return ["INBOX", "[Gmail]/LinkedIn Jobs"]

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["list-imap-folders"])

    assert result.exit_code == 0
    assert "Available IMAP folders:" in result.output
    assert "INBOX" in result.output
    assert "[Gmail]/LinkedIn Jobs" in result.output


def test_list_imap_folders_cli_non_zero_on_failure(monkeypatch) -> None:
    _set_env(monkeypatch)

    class FakeEmailClient:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        def list_mailboxes(self):
            raise EmailAuthenticationError("invalid auth")

    monkeypatch.setattr(cli_module, "EmailIMAPClient", FakeEmailClient)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["list-imap-folders"])

    assert result.exit_code != 0
