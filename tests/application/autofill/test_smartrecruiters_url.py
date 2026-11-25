from __future__ import annotations

import pytest

from app.application.autofill.smartrecruiters_url import (
    ApplicationRef,
    PostingRef,
    is_base_application_url,
    parse_application_url,
    parse_posting_url,
    same_company_application_ref,
)

PUBLICATION = "11111111-2222-3333-4444-555555555555"
BASE = f"https://jobs.smartrecruiters.com/oneclick-ui/company/Wise/publication/{PUBLICATION}"
POSTING = PostingRef(company="Wise", posting_id="744000123456789")


def test_posting_url_accepts_numeric_id_with_optional_slug() -> None:
    assert parse_posting_url("https://jobs.smartrecruiters.com/Wise/744000123456789") == POSTING
    assert parse_posting_url("https://jobs.smartrecruiters.com/Wise/744000123456789-senior-engineer/") == POSTING


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://jobs.smartrecruiters.com/Wise/744000123456789",
        "https://jobs.smartrecruiters.com:444/Wise/744000123456789",
        "https://user@jobs.smartrecruiters.com/Wise/744000123456789",
        "https://jobs.smartrecruiters.com.evil.test/Wise/744000123456789",
        "https://www.smartrecruiters.com/Wise/744000123456789",
        "https://jobs.smartrecruiters.com/Wise/744000123456789?ref=x",
        "https://jobs.smartrecruiters.com/Wise/744000123456789#apply",
        "https://jobs.smartrecruiters.com/Canva/744000123456789",
        "https://jobs.smartrecruiters.com/Wise/123",
    ],
)
def test_posting_url_rejects_untrusted_shapes(url: str | None) -> None:
    assert parse_posting_url(url) is None


def test_application_url_base_and_screening() -> None:
    assert parse_application_url(BASE) == ApplicationRef("Wise", PUBLICATION, False)
    assert parse_application_url(f"{BASE}/screening") == ApplicationRef("Wise", PUBLICATION, True)
    assert parse_application_url(f"{BASE}?dcr_ci=Wise") == ApplicationRef("Wise", PUBLICATION, False)
    assert is_base_application_url(BASE) is True
    assert is_base_application_url(f"{BASE}/screening") is False


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}?dcr_ci=Other",
        f"{BASE}?dcr_ci=Wise&utm_source=x",
        f"{BASE}?other=1",
        f"{BASE}#frag",
        "https://jobs.smartrecruiters.com/oneclick-ui/company/Canva/publication/" + PUBLICATION,
        "https://jobs.smartrecruiters.com/oneclick-ui/company/Wise/publication/not-a-uuid",
        f"{BASE}/review",
        f"http://jobs.smartrecruiters.com/oneclick-ui/company/Wise/publication/{PUBLICATION}",
    ],
)
def test_application_url_rejects_untrusted_shapes(url: str) -> None:
    assert parse_application_url(url) is None


def test_same_company_application_ref_rejects_screening_and_other_company() -> None:
    assert same_company_application_ref(POSTING, BASE) == ApplicationRef("Wise", PUBLICATION, False)
    assert same_company_application_ref(POSTING, f"{BASE}/screening") is None
    assert same_company_application_ref(PostingRef("Canva", "1"), BASE) is None
    assert same_company_application_ref(POSTING, "https://example.test/apply") is None
