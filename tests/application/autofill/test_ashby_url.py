from __future__ import annotations

from app.application.autofill.ashby_url import is_canonical_ashby_hosted_url


def test_canonical_public_job_board_url_is_true() -> None:
    assert is_canonical_ashby_hosted_url("https://jobs.ashbyhq.com/Perk/job-1") is True


def test_canonical_url_with_uuid_job_id_is_true() -> None:
    assert (
        is_canonical_ashby_hosted_url(
            "https://jobs.ashbyhq.com/Perk/5f6e7d8c-1234-5678-9abc-def012345678"
        )
        is True
    )


def test_job_board_root_without_job_id_is_false() -> None:
    assert is_canonical_ashby_hosted_url("https://jobs.ashbyhq.com/Perk") is False


def test_custom_domain_is_false() -> None:
    # Discovered through Ashby, but the URL is not Ashby's own hosted shape.
    assert is_canonical_ashby_hosted_url("https://careers.travelperk.com/apply?job=1") is False


def test_app_ashbyhq_subdomain_is_false() -> None:
    # app.ashbyhq.com is Ashby's internal/admin surface, not the public board.
    assert is_canonical_ashby_hosted_url("https://app.ashbyhq.com/Perk/job-1") is False


def test_lookalike_hostname_is_false() -> None:
    assert is_canonical_ashby_hosted_url("https://evil.example.com/?x=jobs.ashbyhq.com/Perk/1") is False


def test_empty_url_is_false() -> None:
    assert is_canonical_ashby_hosted_url("") is False


def test_malformed_url_does_not_raise() -> None:
    assert is_canonical_ashby_hosted_url("not a url at all") is False


def test_http_scheme_is_false() -> None:
    # Telegram delivery validates HTTP(S) only, but the official jobUrl is
    # always HTTPS -- plain HTTP is not the confirmed canonical shape.
    assert is_canonical_ashby_hosted_url("http://jobs.ashbyhq.com/Perk/job-1") is False


def test_ftp_scheme_is_false() -> None:
    assert is_canonical_ashby_hosted_url("ftp://jobs.ashbyhq.com/Perk/job-1") is False


def test_schemeless_url_is_false() -> None:
    assert is_canonical_ashby_hosted_url("jobs.ashbyhq.com/Perk/job-1") is False
