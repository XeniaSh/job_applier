"""Offline routed OneClick stage regressions. Every request is intercepted."""

from dataclasses import replace
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.application.autofill.browser import chromium_executable_available
from app.application.autofill.models import AutofillStatus
from app.application.autofill.service import AutofillService
from tests.application.autofill.test_smartrecruiters_adapter import (
    APP_PATH, APP_URL, HOST, VARIANTS, _NeverSubmit, _Site, _adapter, _digest, _profiles,
)
from tests.application.autofill.test_wise_screening import (
    CUSTOMER, OFFICE, OFFICE_OPTIONS, PWC, expectations, profile, vacancy,
)

pytestmark = pytest.mark.skipif(not chromium_executable_available(), reason="Playwright Chromium is not installed")
PAGE_ONE = Path("tests/fixtures/autofill/smartrecruiters_page_one.html")
SCREENING = Path("tests/fixtures/autofill/smartrecruiters_screening.js")


class TwoStageSite(_Site):
    def __init__(self, stage=None, initial=None):
        super().__init__(sr_init={"cityDelay": 20, "dialFilterDelay": 20, "uploadDelay": 20, **(initial or {})})
        self.stage = stage or {}
        self.terminal_state = None
        self.page_errors = []
        self._error_hook = False

    def start(self):
        super().start()
        if not self._error_hook:
            self._page.on("pageerror", lambda error: self.page_errors.append(error))
            self._error_hook = True
        self._context.add_init_script("window.S2_INIT = " + json.dumps(self.stage) + ";")

    def _handle(self, route, request):
        parts = urlsplit(request.url)
        if parts.hostname != HOST:
            self.blocked.append(str(parts.hostname))
            route.abort()
            return
        self.requests.append(parts.path)
        if parts.path == APP_PATH:
            route.fulfill(status=200, content_type="text/html", body=PAGE_ONE.read_text() + "<script>" + SCREENING.read_text() + "</script>")
        else:
            route.fulfill(status=404, body="synthetic unknown route")

    def close(self):
        if self._page is not None and not self._page.is_closed():
            self.terminal_state = self._page.evaluate("typeof s2State==='function' ? s2State() : null")
        super().close()


def prepared_profile():
    return profile(
        employment={"notice_period": "One month", "salary_expectations": expectations()},
        work_eligibility={"work_authorizations": [{"country": "Estonia", "authorized": False, "requires_sponsorship": True}]},
        application_consent={"privacy_data_processing": True, "recruiting_contact": True},
        application_policy={"newsletter_opt_in": True, "question_overrides": [
            {"question_contains": [CUSTOMER], "answer": False},
            {"question_contains": [OFFICE], "answer": OFFICE_OPTIONS[1]},
            {"question_contains": ["Were you involved, in any capacity", "audit of Wise"], "answer": False},
            {"question_contains": [PWC], "answer": False},
        ]},
    )


@pytest.fixture(autouse=True)
def no_private_profile(monkeypatch):
    monkeypatch.setattr("app.application.autofill.service._load_markdown_profile", lambda: "")


@pytest.fixture
def run_form(tmp_path):
    sites = []

    def run(stage=None, *, initial=None, candidate=None, variant="java_backend", generator=None, url=APP_URL, normalized=None):
        profiles, paths = _profiles(tmp_path)
        site = TwoStageSite(stage, initial)
        sites.append(site)
        adapter = _adapter(prepare_screening=True, poll_ms=20, stable_samples=2,
                           settle_timeout_ms=260, hydration_timeout_ms=700, navigation_timeout_ms=500)
        states = []

        class Resolver:
            def resolve(self, source, external_id):
                return replace(normalized or vacancy(), source=source, external_id=external_id, application_url=url)

        service = AutofillService(
            resolver=Resolver(), profile_loader=lambda: candidate or prepared_profile(),
            adapter=adapter, browser_factory=lambda: site,
            wait_for_review=lambda: states.append(site.page.evaluate("s2State()")),
            resume_profiles=profiles, resume_variant_override=variant,
            auto_submit_enabled=True, submit_adapter=_NeverSubmit(), answer_generator=generator,
        )
        result = service.run("target_company:smartrecruiters:wise", "1", keep_open=True)
        assert site.blocked == []
        assert site.page_errors == []
        assert site._page is None  # real service handoff/cleanup, not a mocked pipeline
        assert result.submit_performed is False
        state = states[0] if states else site.terminal_state
        assert state and all(action not in state["actions"] for action in ("Submit", "Enter", "form-submit"))
        return result, state, adapter, paths

    try:
        yield run
    finally:
        for site in sites:
            site.close()


@pytest.mark.parametrize("variant", VARIANTS)
def test_complete_two_stage_prepare_never_submits_even_when_auto_submit_enabled(run_form, variant):
    result, state, adapter, paths = run_form(variant=variant)
    assert result.status is AutofillStatus.READY_FOR_REVIEW, result.model_dump()
    assert result.unresolved_required_fields == []
    assert state["actions"] == ["Next", "Back", "Next"]
    assert state["location"] == APP_URL + "/screening"
    assert state["notice"] == "One month" and state["privacy"] is True
    assert state["marketing"] is False and state["talent"] is False
    assert state["uploads"] == 1 and result.resume_uploaded is True
    assert result.resume_variant == variant and result.resume_file == "Resume.pdf"
    assert adapter._upload_proof.digest == _digest(paths[variant])
    assert len({(r.name, r.label) for r in result.filled_fields}) == len(result.filled_fields)
    assert any("complete for manual review" in w for w in result.warnings)


def test_observed_query_and_uk_context_survive_stage_navigation(run_form):
    payload = prepared_profile().model_dump()
    payload["work_eligibility"]["work_authorizations"] = [
        {"country": "UK", "authorized": False, "requires_sponsorship": True},
    ]
    payload["employment"]["salary_expectations"] = expectations(80000, currency="GBP", period="annual")
    result, state, _, _ = run_form(
        candidate=profile(**payload), url=APP_URL + "?dcr_ci=Wise",
        normalized=vacancy("London, United Kingdom", "Salary GBP 68000 - 87500 yearly gross."),
    )
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert state["location"] == APP_URL + "/screening?dcr_ci=Wise"
    assert state["actions"] == ["Next", "Back", "Next"]
    assert state["eligibility"] == "I do not currently have work authorization and will require visa sponsorship"


@pytest.mark.parametrize("initial", [{"extraRequired": True}, {"invalidOn": "Email"}, {"upload": "error"}])
def test_page_one_required_validation_and_attachment_gate_next(run_form, initial):
    result, state, _, _ = run_form(initial=initial)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.unresolved_required_fields and state["actions"] == []


@pytest.mark.parametrize("stage", [
    {"navType": "submit"}, {"navFormAction": True}, {"navRename": True}, {"navDuplicate": True}, {"navDisabled": True},
])
def test_navigation_requires_positive_non_submission_identity(run_form, stage):
    result, state, _, _ = run_form(stage)
    expected = AutofillStatus.FAILED if stage.get("navDuplicate") else AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.status is expected
    assert state["actions"] == []


def test_back_is_verified_as_non_submitting_too(run_form):
    result, state, _, _ = run_form({"backType": "submit"})
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert state["actions"] == ["Next"]


@pytest.mark.parametrize("stage", [{"validationFail": True}, {"wrongPublication": True}, {"challenge": True}])
def test_failed_validation_destination_or_challenge_is_partial_and_bounded(run_form, stage):
    result, state, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert state["actions"] == ["Next"]
    assert not any("complete for manual review" in w for w in result.warnings)


def test_unknown_required_is_manual_never_generated_and_no_completion_claim(run_form):
    class NoGeneration:
        def generate(self, *args, **kwargs):
            raise AssertionError("Unknown and unsupported questions must not reach generation")

    result, state, _, _ = run_form({"unknownRequired": True, "searchDecoy": True}, generator=NoGeneration())
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert "Synthetic unknown required legal question" in {r.label for r in result.unresolved_required_fields}
    assert "Search options" not in {r.label for r in result.unresolved_required_fields}
    assert state["actions"] == ["Next", "Back", "Next"]


@pytest.mark.parametrize("stage,label", [
    ({"loseAnswer": True}, "What is your notice period?"),
    ({"invalidAnswer": True}, "What is your notice period?"),
    ({"changeQuestion": True}, "What is your notice period?"),
    ({"extraSelectedMulti": True}, "I identify my sexual orientation as (mark all that apply)"),
])
def test_final_answer_readback_demotes_stale_successes(run_form, stage, label):
    result, state, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert label in {r.label for r in result.unresolved_required_fields}
    assert label not in {r.label for r in result.filled_fields}
    assert state["actions"] == ["Next", "Back", "Next"]


@pytest.mark.parametrize("stage", [{"loseCandidate": True}, {"invalidCandidate": True}])
def test_candidate_values_and_own_validation_are_rechecked_on_back(run_form, stage):
    result, state, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert state["actions"] == ["Next", "Back"]
    assert any(r.name in {"firstName", "email"} for r in result.unresolved_required_fields)


@pytest.mark.parametrize("stage", [{"loseCandidateOnFinal": True}, {"invalidCandidateOnFinal": True}])
def test_final_next_cannot_hide_candidate_value_or_validation_loss(run_form, stage):
    result, state, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert state["actions"] == ["Next", "Back", "Next"]
    assert any(r.name in {"firstName", "email"} for r in result.unresolved_required_fields)


@pytest.mark.parametrize("stage", [{"dropResumeOnBack": True}, {"replaceResumeOnBack": True}, {"dropResumeOnFinal": True}])
def test_resume_loss_or_recreated_same_name_widget_is_not_attachment_proof(run_form, stage):
    result, state, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False and result.resume_attachment_incomplete is True
    assert "resume" in {r.name for r in result.unresolved_required_fields}
    assert "resume" not in {r.name for r in result.filled_fields}
    assert state["uploads"] == 1  # no replacement or refilling to mask loss


@pytest.mark.parametrize("stage", [{"radioRevert": True}, {"comboRevert": True}, {"multiNoProof": True},
                                    {"duplicateQuestion": True}, {"duplicateLabel": True},
                                    {"duplicateChoice": True}, {"comboNoProof": True}])
def test_uncommitted_custom_choices_and_duplicate_controls_remain_manual(run_form, stage):
    result, _, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.unresolved_required_fields


@pytest.mark.parametrize("stage", [{"newRequired": True}, {"lateNewRequired": True}])
def test_new_required_question_on_final_stage_is_not_lost_from_report(run_form, stage):
    result, _, _, _ = run_form(stage)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert "Synthetic newly required question" in {r.label for r in result.unresolved_required_fields}


def test_partial_screening_hydration_is_never_whole_form_complete(run_form):
    result, _, _, _ = run_form({"thinForm": True})
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert any("inventory is incomplete" in w for w in result.warnings)


def test_unset_factual_screening_answers_stay_manual(run_form):
    result, state, _, _ = run_form(candidate=profile())
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    labels = {r.label for r in result.unresolved_required_fields}
    assert {CUSTOMER, OFFICE, "What is your notice period?"} <= labels
    assert state["privacy"] is True and state["marketing"] is False and state["talent"] is False
