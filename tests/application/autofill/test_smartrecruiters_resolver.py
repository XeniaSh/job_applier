from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
import respx

from app.application.autofill.resolver import DefaultVacancyResolver, SmartRecruitersTargetVacancyResolver, VacancyResolveError
from app.application.autofill.service import default_adapter_for_source
from app.application.autofill.smartrecruiters import SmartRecruitersAdapter, SmartRecruitersFormError
from app.application.autofill.smartrecruiters_url import WISE_PREPARE_SOURCE, supports_prepare_vacancy
from app.company_watch.watchers.smartrecruiters import smartrecruiters_posting_endpoint

SOURCE = WISE_PREPARE_SOURCE
ID = "744000123456789"
POSTING = f"https://jobs.smartrecruiters.com/Wise/{ID}-engineer"
ENDPOINT = smartrecruiters_posting_endpoint("Wise", ID)


@pytest.fixture(autouse=True)
def enable_prepare(monkeypatch):
    monkeypatch.setenv("SMARTRECRUITERS_PREPARE_ENABLED", "true")


def detail(**changes):
    return {
        "id": ID, "uuid": "11111111-2222-3333-4444-555555555555", "active": True,
        "name": "Backend Engineer", "company": {"identifier": "Wise", "name": "Wise"},
        "postingUrl": POSTING, "location": {"city": "Tallinn", "country": "EE"},
        "jobAd": {"sections": {"jobDescription": {"text": "<p>Build Java services.</p><p>Salary EUR 5000 - 7000 monthly gross.</p>"}}},
        # This untrusted API shortcut is deliberately ignored.
        "applyUrl": "https://evil.example/submit?secret=PRIVATE",
        **changes,
    }


@respx.mock
def test_fresh_wise_detail_resolves_posting_not_guessed_publication():
    route = respx.get(ENDPOINT).respond(200, json=detail())
    resolved = DefaultVacancyResolver().resolve(SOURCE, ID)
    assert route.call_count == 1
    assert resolved.application_url == resolved.url == POSTING
    assert resolved.external_id == ID and resolved.company == "Wise"
    assert resolved.vacancy.location == "Tallinn, Estonia"
    assert "Salary EUR 5000" in resolved.vacancy.description
    adapter = default_adapter_for_source(SOURCE)
    assert isinstance(adapter, SmartRecruitersAdapter)
    assert adapter._prepare_screening and adapter._require_posting_identity
    assert adapter.allow_auto_submit is False
    adapter.bind_vacancy(resolved)
    assert adapter._expected_posting_id == ID
    assert adapter.entry_is_usable(SimpleNamespace(url=POSTING))
    with pytest.raises(SmartRecruitersFormError, match="identity changed"):
        adapter.prepare_page(SimpleNamespace(url=POSTING.replace(ID, "744000999999999")))
    with pytest.raises(SmartRecruitersFormError):
        adapter.bind_vacancy(replace(resolved, vacancy=replace(resolved.vacancy, url="https://evil.example")))
    with pytest.raises(SmartRecruitersFormError):
        adapter.bind_vacancy(replace(resolved, external_id="744000999999999"))


@pytest.mark.parametrize("changes", [
    {"id": "744000999999999"}, {"active": False}, {"company": {"identifier": "Canva"}},
    {"company": None}, {"company": {}}, {"postingUrl": None}, {"jobAd": {}},
    {"postingUrl": "https://jobs.smartrecruiters.com/Wise/744000999999999-engineer"},
    {"postingUrl": f"https://jobs.smartrecruiters.com/Canva/{ID}"},
    {"postingUrl": f"https://jobs.smartrecruiters.com/Wise/{ID}/apply"},
    {"postingUrl": f"https://jobs.smartrecruiters.com/Wise/{ID}?token=PRIVATE"},
    {"postingUrl": f"https://user:PRIVATE@jobs.smartrecruiters.com/Wise/{ID}"},
    {"postingUrl": f"http://jobs.smartrecruiters.com/Wise/{ID}"},
    {"postingUrl": f"https://jobs.smartrecruiters.com:443/Wise/{ID}"},
])
@respx.mock
def test_inconsistent_closed_or_untrusted_posting_is_rejected(changes):
    respx.get(ENDPOINT).respond(200, json=detail(**changes))
    with pytest.raises(VacancyResolveError):
        SmartRecruitersTargetVacancyResolver().resolve(SOURCE, ID)


@pytest.mark.parametrize("status", [301, 403, 404, 429, 500])
@respx.mock
def test_http_errors_and_redirects_fail_without_leaking_body(status, caplog):
    respx.get(ENDPOINT).respond(status, headers={"Location": "https://evil.example/PRIVATE"}, text="PRIVATE@example.invalid PRIVATE-SECRET")
    with pytest.raises(VacancyResolveError) as caught:
        SmartRecruitersTargetVacancyResolver().resolve(SOURCE, ID)
    assert "PRIVATE" not in str(caught.value) + caplog.text
    assert len(respx.calls) == 1


@pytest.mark.parametrize("source,identity", [
    ("target_company:smartrecruiters:canva", ID), ("smartrecruiters", ID),
    (SOURCE, "../secret"), (SOURCE, "11111111-2222-3333-4444-555555555555"),
    (SOURCE, "123"), (SOURCE, ID + "/apply"), (SOURCE, ID + "?x=1"),
])
@respx.mock
def test_unsupported_identity_never_starts_http_or_browser(source, identity):
    with pytest.raises(VacancyResolveError):
        SmartRecruitersTargetVacancyResolver().resolve(source, identity)
    assert not respx.calls


def test_callback_url_support_is_wise_only_and_same_numeric_id():
    assert supports_prepare_vacancy(SOURCE, ID, POSTING)
    assert not supports_prepare_vacancy(SOURCE, "744000999999999", POSTING)
    assert not supports_prepare_vacancy(SOURCE, ID, "https://jobs.smartrecruiters.com/oneclick-ui/company/Wise/publication/11111111-2222-3333-4444-555555555555")
    with pytest.raises(SmartRecruitersFormError):
        default_adapter_for_source("target_company:smartrecruiters:canva")
