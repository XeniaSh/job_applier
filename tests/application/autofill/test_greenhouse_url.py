from __future__ import annotations

from app.application.autofill.greenhouse_url import is_canonical_greenhouse_hosted_url


def test_canonical_job_boards_url_is_true() -> None:
    assert is_canonical_greenhouse_hosted_url("https://job-boards.greenhouse.io/agoda/jobs/8184766") is True


def test_canonical_regional_subdomain_url_is_true() -> None:
    assert is_canonical_greenhouse_hosted_url("https://job-boards.eu.greenhouse.io/jetbrains/jobs/4881348101") is True


def test_legacy_boards_subdomain_url_is_true() -> None:
    assert is_canonical_greenhouse_hosted_url("https://boards.greenhouse.io/somecompany/jobs/123") is True


def test_custom_domain_embed_url_is_false() -> None:
    # Elastic: Greenhouse discovery, but a custom-domain embed apply page.
    assert is_canonical_greenhouse_hosted_url("https://jobs.elastic.co/jobs?gh_jid=8148720") is False


def test_lookalike_hostname_is_false() -> None:
    # "greenhouse.io" as a substring elsewhere must not count as the host.
    assert is_canonical_greenhouse_hosted_url("https://evil.example.com/?x=greenhouse.io/jobs/1") is False


def test_greenhouse_io_without_jobs_path_is_false() -> None:
    assert is_canonical_greenhouse_hosted_url("https://job-boards.greenhouse.io/agoda") is False


def test_empty_url_is_false() -> None:
    assert is_canonical_greenhouse_hosted_url("") is False


def test_malformed_url_does_not_raise() -> None:
    assert is_canonical_greenhouse_hosted_url("not a url at all") is False
