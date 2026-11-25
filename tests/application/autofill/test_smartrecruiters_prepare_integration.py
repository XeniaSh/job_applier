"""Real resolver/runtime/Telegram/review chain with synthetic intercepted leaves."""

from pathlib import Path
import time
from urllib.parse import urlsplit

import pytest
import respx
import yaml

import app.cli as cli
import app.application.autofill.service as service_module
import app.application.explicit_prepare_runtime as runtime
from app.application.autofill.browser import chromium_executable_available
from app.application.autofill.models import AutofillStatus
from app.application.autofill.review_session import default_review_registry
from app.telegram.client import parse_review_done_session_id
from tests.application.autofill.test_smartrecruiters_adapter import APP_URL, _digest, _posting_html, _profiles
from tests.application.autofill.test_smartrecruiters_stage2 import TwoStageSite, prepared_profile
from tests.application.autofill.test_smartrecruiters_resolver import ENDPOINT, ID, POSTING, SOURCE, detail
from tests.test_smartrecruiters_prepare import seed, update
from tests.test_telegram_target_company_prepare import _FakeClient

ROOT = Path(__file__).resolve().parents[3]
PAGE_ONE = ROOT / "tests/fixtures/autofill/smartrecruiters_page_one.html"
SCREENING = ROOT / "tests/fixtures/autofill/smartrecruiters_screening.js"
pytestmark = pytest.mark.skipif(not chromium_executable_available(), reason="Chromium is not installed")


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("offline preparation did not reach the expected lifecycle state")


@pytest.fixture
def production_prepare(monkeypatch, tmp_path):
    """Real production builder; replace only configuration, network, headed browser and LLM leaves."""
    registry = default_review_registry()
    registry.close_all()
    assert registry.wait_all_discarded(timeout=3)
    monkeypatch.chdir(tmp_path)  # real default profile loader can only read our synthetic overlay
    profiles, paths = _profiles(tmp_path / "resumes")
    monkeypatch.setattr(runtime, "_load_resume_profiles", lambda: profiles)
    monkeypatch.setattr(runtime, "_optional_llm_client", lambda: None)
    monkeypatch.setattr(runtime, "_cover_letter_document_settings", lambda: ("", "en", None))
    monkeypatch.setattr(runtime, "_auto_submit_enabled", lambda: True)  # SR must still never submit
    original_adapter = service_module.SmartRecruitersAdapter
    monkeypatch.setattr(service_module, "SmartRecruitersAdapter", lambda **kw: original_adapter(
        **kw, poll_ms=20, stable_samples=2, settle_timeout_ms=260,
        navigation_timeout_ms=500, hydration_timeout_ms=600, upload_timeout_ms=1500,
    ))
    submit_calls = []

    def forbidden_submit(*args, **kwargs):
        submit_calls.append(True)
        raise AssertionError("SmartRecruiters must never enter submission infrastructure")

    monkeypatch.setattr(service_module, "attempt_auto_submit", forbidden_submit)
    original_run = service_module.AutofillService.run
    results = []

    def observe_result(self, *args, **kwargs):
        result = original_run(self, *args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(service_module.AutofillService, "run", observe_result)
    sites = []

    def run(*, mode="complete", stage=None, variant="java_backend", profile_missing=False, attach_failure=False):
        title = "Software Engineer, AI Integration" if variant.startswith("ai_") else "Senior Backend Engineer"
        nz = variant.endswith("_nz")
        description = "Build LLM integrations." if variant.startswith("ai_") else "Build Java services."
        payload = detail(name=title, location={"city": "Wellington" if nz else "Tallinn", "country": "New Zealand" if nz else "EE"},
            jobAd={"sections": {"jobDescription": {"text": description + " Salary EUR 5000 - 7000 monthly gross."}}})
        if not profile_missing:
            (tmp_path / "candidate_profile.local.yaml").write_text(yaml.safe_dump(prepared_profile().model_dump()))
        storage, cache = seed(tmp_path)

        class Site(TwoStageSite):
            def __init__(self):
                super().__init__(stage=stage, initial={"challenge": mode == "application_challenge"})

            def _handle(self, route, request):
                parts = urlsplit(request.url)
                if parts.hostname != "jobs.smartrecruiters.com":
                    self.blocked.append(parts.hostname)
                    route.abort()
                    return
                self.requests.append(parts.path)
                if parts.path == urlsplit(POSTING).path:
                    if mode == "posting_challenge":
                        body = '<title>Verify you are human</title><div class="g-recaptcha"></div>'
                    else:
                        canonical = POSTING.replace(ID, "744000999999999") if mode == "repost" else POSTING
                        href = "https://evil.example/submit" if mode == "untrusted_apply" else APP_URL
                        body = _posting_html(canonical=canonical, links=[("I'm interested", href, "")])
                elif parts.path == urlsplit(APP_URL).path:
                    body = "<title>Unknown form</title>" if mode == "unsupported" else PAGE_ONE.read_text() + "<script>" + SCREENING.read_text() + "</script>"
                else:
                    route.abort()
                    return
                route.fulfill(status=200, content_type="text/html", body=body)

        def browser_factory(**kwargs):
            site = Site()
            sites.append(site)
            return site

        monkeypatch.setattr(service_module, "BrowserSession", browser_factory)

        class Client(_FakeClient):
            review_id = None
            state = None
            digests = None

            def edit_message_text(self, **kwargs):
                super().edit_message_text(**kwargs)
                buttons = kwargs.get("buttons")
                if buttons and buttons[0][0].callback_data.startswith("revdone:"):
                    if attach_failure:
                        raise RuntimeError("synthetic attach failure")
                    # This callback runs on the browser-owning worker thread.
                    self.state = sites[-1].page.evaluate("typeof s2State==='function' ? s2State() : null")
                    self.digests = sites[-1].page.evaluate("(window.__srUploads||[]).map(u=>u.digest)")
                    self.review_id = parse_review_done_session_id(buttons[0][0].callback_data)

        client = Client()
        with respx.mock as router:
            route = router.get(ENDPOINT).respond(200, json=payload)
            cli._process_callback_update(update=update(), client=client, storage=storage, configured_chat_id="222",
                application_prepare_cache=cache, application_prepare_service=None, application_prepare_sync=False)
            try:
                wait_until(lambda: client.review_id is not None or bool(results))
                if client.review_id:
                    assert sites[-1]._page is not None  # open during handoff
                    cli._process_callback_update(update=update(f"revdone:{client.review_id}"), client=client,
                        storage=storage, configured_chat_id="222")
                wait_until(lambda: bool(results))
                assert registry.wait_all_discarded(timeout=5)
            finally:
                registry.close_all()
                assert registry.wait_all_discarded(timeout=5)
            assert route.call_count == 1
        assert submit_calls == []
        assert all(site._page is None and site.blocked == [] and site.page_errors == [] for site in sites)
        assert storage.get_history_status(SOURCE, ID) != "APPLIED"
        assert storage.get_delivery(SOURCE, ID).status == "SENT"
        assert len(results) == 1 and results[0].submit_performed is False
        if client.state is not None:
            assert not {"Submit", "Enter", "form-submit"}.intersection(client.state["actions"])
        assert len(client.texts) == 1  # no unsolicited card or ordinary completion fallback
        return results[0], client, sites, paths

    try:
        yield run
    finally:
        registry.close_all()
        assert registry.wait_all_discarded(timeout=5)
        for site in sites:
            site.close()


@pytest.mark.parametrize("variant", ["java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz"])
def test_registered_runtime_selects_exact_profile_and_finishes_review_without_submit(production_prepare, variant, caplog):
    result, client, sites, paths = production_prepare(variant=variant)
    expected = AutofillStatus.NEEDS_MANUAL_INTERVENTION if variant.endswith("_nz") else AutofillStatus.READY_FOR_REVIEW
    assert result.status is expected
    assert result.resume_variant == variant and result.resume_uploaded
    assert client.state["uploads"] == 1
    assert client.state["actions"] == ["Next", "Back", "Next"]
    assert sites[0]._sr_init["challenge"] is False
    # The source path used the actual numeric public posting, not a guessed UUID.
    assert sites[0].requests[0] == urlsplit(POSTING).path
    assert client.digests == [_digest(paths[variant])]
    assert len({_digest(path) for path in paths.values()}) == 4
    assert result.resume_file == "Resume.pdf"
    assert client.review_id
    assert "ada@example.invalid" not in caplog.text and "55501234" not in caplog.text
    if expected is AutofillStatus.READY_FOR_REVIEW:
        assert result.unresolved_required_fields == []
        assert client.texts[0]["text"].startswith("Preparation completed.")
    else:
        assert result.unresolved_required_fields
        assert client.texts[0]["text"].startswith("Preparation incomplete")


@pytest.mark.parametrize("stage", [{"unknownRequired": True}, {"replaceResumeOnBack": True}, {"challenge": True}])
def test_registered_two_stage_partial_outcome_is_visible_and_never_submitted(production_prepare, stage):
    result, client, _, _ = production_prepare(stage=stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert client.texts[0]["text"].startswith("Preparation incomplete")
    if stage.get("unknownRequired"):
        assert any(r.label == "Synthetic unknown required legal question" for r in result.unresolved_required_fields)
    if stage.get("replaceResumeOnBack"):
        assert result.resume_attachment_incomplete and not result.resume_uploaded
    if stage.get("challenge"):
        assert "CAPTCHA" in client.texts[0]["text"]


@pytest.mark.parametrize("mode", ["posting_challenge", "application_challenge"])
def test_challenges_are_handed_off_without_candidate_writes(production_prepare, mode, caplog):
    result, client, _, _ = production_prepare(mode=mode)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.filled_fields == [] and not result.resume_uploaded
    assert "challenge" in client.texts[0]["text"].lower() or "CAPTCHA" in client.texts[0]["text"]
    assert "ada@example.invalid" not in caplog.text and "55501234" not in caplog.text


@pytest.mark.parametrize("mode", ["unsupported", "repost", "untrusted_apply"])
def test_unsupported_or_changed_posting_closes_without_review_or_filling(production_prepare, mode):
    result, client, _, _ = production_prepare(mode=mode)
    assert result.status is AutofillStatus.FAILED and not result.filled_fields
    assert client.review_id is None
    assert "not supported" in client.texts[0]["text"]


def test_missing_profile_fails_before_browser_setup(production_prepare):
    result, client, sites, _ = production_prepare(profile_missing=True)
    assert result.status is AutofillStatus.FAILED and sites == []
    assert "candidate profile" in client.texts[0]["text"]


def test_failed_review_notification_still_releases_browser_and_registry(production_prepare):
    result, client, sites, _ = production_prepare(attach_failure=True)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert sites[0]._page is None and client.review_id is None
    assert client.edits[-1]["buttons"] == []
