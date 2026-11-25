"""Wise-only Telegram routing, old-card reconciliation and polling isolation."""

from dataclasses import replace
import threading

import pytest
import respx

import app.cli as cli
from app.application.autofill.models import AutofillFieldResult, AutofillStatus, FieldClassification, stage1_autofill_result
from app.application.autofill.review_session import default_review_registry
from app.application.prepare_application import PrepareApplicationService
from app.company_watch.wise_eligibility import WISE_LEGACY_MANUAL_EMPLOYER_SITE_INFO
from app.storage.telegram_delivery import STATUS_APPLIED, STATUS_SENT, STATUS_SKIPPED, TelegramDeliveryStorage
from app.telegram.application_prepare import format_application_prepare_completed_text
from app.telegram.client import APPLICATION_PREPARE_BUTTON_TEXT, build_action_buttons, parse_callback_data, source_supports_application_prepare
from tests.application.autofill.test_smartrecruiters_resolver import ID, POSTING, SOURCE, detail
from tests.application.autofill.test_wise_screening import vacancy
from tests.company_watch.watchers.test_smartrecruiters_watcher import _company
from tests.test_telegram_target_company_prepare import _FakeClient, _evaluation, _seed_cache
from app.company_watch.watchers.smartrecruiters import SmartRecruitersTargetWatcher, smartrecruiters_posting_endpoint, smartrecruiters_postings_endpoint


@pytest.fixture(autouse=True)
def registry_cleanup():
    registry = default_review_registry()
    registry.close_all()
    assert registry.wait_all_discarded(timeout=3)
    yield
    registry.close_all()
    assert registry.wait_all_discarded(timeout=3)


def seed(tmp_path, *, recommendation="APPLY_NOW", url=POSTING, applied=False):
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    storage.save_sent(source=SOURCE, external_id=ID, chat_id="222", message_id=50)
    storage.upsert_application_history(source=SOURCE, external_id=ID, title="Backend Engineer", company="Wise",
                                       location="Tallinn, Estonia", url=url, decision="STRONG_MATCH",
                                       decision_reason="Synthetic match", recommended_resume="java")
    if applied:
        storage.mark_history_status(source=SOURCE, external_id=ID, status=STATUS_APPLIED, timestamp_field="applied_at")
    normalized = replace(vacancy().vacancy, source=SOURCE, external_id=ID, url=url)
    cache = _seed_cache(tmp_path / "analysis.json", recommendation=recommendation, vacancy=normalized)
    return storage, cache


def update(data=None, *, url=POSTING, chat="222"):
    return {"update_id": 1, "callback_query": {
        "id": "wise-callback", "data": data or f"prepapp:tcs.wise:{ID}", "message": {
            "message_id": 50, "chat": {"id": chat}, "text": "Backend Engineer\nWise",
            "reply_markup": {"inline_keyboard": [[{"text": "Open vacancy", "url": url}]]},
        },
    }}


class Runner:
    def __init__(self, *, manual=False, entered=None, release=None):
        self.calls = []
        self.manual, self.entered, self.release = manual, entered, release

    def run(self, source, identity, *, keep_open=True):
        self.calls.append((source, identity, keep_open))
        if self.entered:
            self.entered.set()
            assert self.release.wait(10)
        return stage1_autofill_result(
            source=source, external_id=identity, application_url=POSTING,
            status=AutofillStatus.NEEDS_MANUAL_INTERVENTION if self.manual else AutofillStatus.READY_FOR_REVIEW,
            unresolved_required_fields=[AutofillFieldResult(label="Synthetic unsupported screening question",
                classification=FieldClassification.UNSUPPORTED, required=True)] if self.manual else [],
            resume_uploaded=True,
        )


def process(storage, cache, client, runner, *, callback=None, sync=True):
    cli._process_callback_update(
        update=callback or update(), client=client, storage=storage, configured_chat_id="222",
        application_prepare_cache=cache, application_prepare_service=PrepareApplicationService(runner, storage),
        application_prepare_sync=sync, undo_window_seconds=600,
    )


@pytest.mark.parametrize("source", [SOURCE, "tcs.wise"])
def test_canonical_wise_buttons_and_callbacks(source):
    buttons = build_action_buttons(source, ID, POSTING)
    assert buttons[0][0].text == APPLICATION_PREPARE_BUTTON_TEXT
    assert parse_callback_data(buttons[0][0].callback_data) == ("prepapp", SOURCE, ID, None)
    assert len(buttons[0][0].callback_data.encode()) <= 64
    assert not source_supports_application_prepare("target_company:smartrecruiters:canva")


@pytest.mark.parametrize("url", ["https://evil.example/PRIVATE", POSTING + "/apply", POSTING + "?x=1",
    POSTING.replace(ID, "744000999999999"), POSTING.replace("/Wise/", "/Canva/")])
def test_invalid_or_mismatched_card_has_no_button_and_no_worker(tmp_path, url):
    storage, cache = seed(tmp_path, url=url)
    client, runner = _FakeClient(), Runner()
    assert APPLICATION_PREPARE_BUTTON_TEXT not in [b.text for row in build_action_buttons(SOURCE, ID, url) for b in row]
    process(storage, cache, client, runner, callback=update(url=url))
    assert runner.calls == [] and client.texts == []
    assert "not available" in client.answers[0][1]


@pytest.mark.parametrize("manual", [False, True])
def test_eligible_previously_delivered_wise_vacancy_uses_cached_identity_without_resending_card(tmp_path, manual):
    storage, cache = seed(tmp_path)
    before = storage.get_delivery(SOURCE, ID)
    client, runner = _FakeClient(), Runner(manual=manual)
    process(storage, cache, client, runner)
    assert runner.calls == [(SOURCE, ID, True)]
    assert len(client.texts) == 1 and client.texts[0]["reply_to"] == 50  # requested outcome only
    text = client.texts[0]["text"]
    assert text.startswith("Preparation incomplete" if manual else "Preparation completed.")
    assert "Unresolved required fields: " + ("1" if manual else "0") in text
    assert "Submit was not performed." in text
    assert storage.get_delivery(SOURCE, ID).status == STATUS_SENT
    assert storage.get_delivery(SOURCE, ID).sent_at == before.sent_at
    assert storage.get_history_status(SOURCE, ID) != STATUS_APPLIED
    assert any(b.text == APPLICATION_PREPARE_BUTTON_TEXT for edit in client.edits for row in edit["buttons"] for b in row)


@pytest.mark.parametrize("recommendation,applied", [("SKIP", False), ("UNKNOWN", False), ("APPLY_NOW", True)])
def test_old_cards_preserve_recommendation_and_applied_gates(tmp_path, recommendation, applied):
    storage, cache = seed(tmp_path, recommendation=recommendation, applied=applied)
    client, runner = _FakeClient(), Runner()
    process(storage, cache, client, runner)
    assert runner.calls == []
    assert len(client.texts) == 1
    assert "completed" not in client.texts[0]["text"].lower()


def test_reconciliation_refreshes_only_old_existing_card_and_preserves_cache():
    evaluation = _evaluation().model_copy(update={"info_items": [WISE_LEGACY_MANUAL_EMPLOYER_SITE_INFO, "Other info"]})
    payload = {"evaluation_json": evaluation.model_dump_json(), "url": POSTING, "title": "Backend Engineer"}

    class Cache:
        def get_prepare_cache(self, *args):
            return payload

    original = dict(payload)
    text, buttons = cli._rebuild_sent_card(storage=Cache(), source=SOURCE, external_id=ID, title="Engineer", company="Wise", url=POSTING)
    assert "no autofill for Wise" not in text
    assert "Review and submit manually" in text
    assert buttons[0][0].text == APPLICATION_PREPARE_BUTTON_TEXT
    assert payload == original


def test_ready_result_with_unresolved_required_is_never_called_complete():
    result = Runner(manual=True).run(SOURCE, ID).model_copy(update={"status": AutofillStatus.READY_FOR_REVIEW})
    assert format_application_prepare_completed_text(result).startswith("Preparation incomplete")


def test_unconfirmed_resume_requests_inspection_before_replacement():
    result = Runner(manual=True).run(SOURCE, ID).model_copy(update={"resume_attachment_incomplete": True, "resume_uploaded": False})
    text = format_application_prepare_completed_text(result)
    assert "inspect its current attachment before replacing" in text
    assert "Preparation completed." not in text


@respx.mock
def test_background_wise_prepare_does_not_block_discovery_or_telegram_polling(tmp_path):
    storage, cache = seed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    client, runner = _FakeClient(), Runner(entered=entered, release=release)
    process(storage, cache, client, runner, sync=False)
    assert entered.wait(3) and client.answers[0][1] == "Preparation starting"
    try:
        respx.get(smartrecruiters_postings_endpoint("Wise")).respond(200, json={"content": [{"id": ID, "name": "Backend Engineer"}], "totalFound": 1})
        respx.get(smartrecruiters_posting_endpoint("Wise", ID)).respond(200, json=detail())
        collected = SmartRecruitersTargetWatcher().watch(_company())
        assert len(collected.vacancies) == 1 and not release.is_set()
        storage.save_sent(source="linkedin-email", external_id="other", chat_id="222", message_id=51)
        other = update("skip:li:other", url="https://www.linkedin.com/jobs/view/123/")
        client.get_updates = lambda **kwargs: [other]
        offset, count = cli._poll_telegram_actions_once(client=client, storage=storage,
            configured_chat_id="222", offset=0, timeout=0, application_prepare_sync=False)
        assert offset == 2 and count == 0
        assert storage.get_delivery("linkedin-email", "other").status == STATUS_SKIPPED
        assert not release.is_set()
    finally:
        release.set()
        assert default_review_registry().wait_all_discarded(timeout=5)
