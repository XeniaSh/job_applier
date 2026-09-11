from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.questions import QuestionKind
from app.application.autofill.summary import format_privacy_acknowledgement_report
from app.application.candidate_profile import CandidateProfile

FIXTURE = Path("tests/fixtures/autofill/greenhouse_privacy_acknowledge.html")
REVERT_FIXTURE = Path("tests/fixtures/autofill/greenhouse_privacy_ack_revert.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "Berlin, Germany",
                "country": "Germany",
            },
            "work_eligibility": {
                "work_authorizations": [{"country": "Germany", "authorized": True}],
            },
            "application_files": {"default_resume": str(RESUME_FIXTURE)},
            "application_policy": {
                "privacy_acknowledgement": {"auto_acknowledge_required": True},
                "newsletter_opt_in": False,
            },
        }
    )


def _open(path: Path) -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(path)
    return session


def _field(fields: list[DiscoveredField], label_substring: str) -> DiscoveredField:
    needle = label_substring.lower()
    for item in fields:
        haystack = f"{item.label} {item.context}".lower()
        if needle in haystack:
            return item
    raise AssertionError(f"Field not found: {label_substring} in {[item.label for item in fields]}")


def test_live_like_privacy_checkbox_is_discovered_classified_and_checked() -> None:
    session = _open(FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        privacy = classify_field(_field(fields, "acknowledge/confirm"), profile)

        assert privacy.kind is QuestionKind.PRIVACY_CONSENT
        assert privacy.field.field_type == "checkbox"
        assert privacy.field.required is True
        assert privacy.field.name == "question_60985243[]"
        assert "point of data transfer" in f"{privacy.field.label} {privacy.field.context}".lower()
        assert "applicant privacy notice" in privacy.field.context.lower()
        assert privacy.fill is True

        native = session.page.locator('input[name="question_60985243[]"]')
        native.evaluate("el => { el.checked = true; }")
        session.page.evaluate("() => new Promise(resolve => queueMicrotask(resolve))")
        assert native.evaluate("el => el.checked") is False

        assert adapter.fill_field(session.page, privacy) is True
        assert adapter.read_back(session.page, privacy.field) == "true"
        assert native.evaluate("el => el.checked") is True
        assert native.get_attribute("aria-checked") == "true"
        assert adapter.last_privacy_trace is not None
        assert adapter.last_privacy_trace["discovered"] is True
        assert adapter.last_privacy_trace["classified"] == "required_privacy"
        assert adapter.last_privacy_trace["readback_checked"] is True
        assert adapter.last_privacy_trace["failure_reason"] is None
        control_type = str(adapter.last_privacy_trace["control_type"])
        assert "hidden" in control_type or "label" in control_type or "checkbox" in control_type
        assert "click" in str(adapter.last_privacy_trace["interaction_attempted"])

        newsletter = classify_field(_field(fields, "newsletter"), profile)
        assert newsletter.kind is QuestionKind.NEWSLETTER
        assert adapter.fill_field(session.page, newsletter) is True
        assert session.page.locator("#newsletter").is_checked() is False
        assert session.page.evaluate("window.__submitClicked") is False
    finally:
        session.close()


def test_privacy_fill_failure_records_diagnostic_trace() -> None:
    session = _open(REVERT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        privacy = classify_field(
            _field(adapter.discover_fields(session.page), "point of data transfer"),
            _profile(),
        )
        assert privacy.kind is QuestionKind.PRIVACY_CONSENT
        assert privacy.fill is True
        assert adapter.fill_field(session.page, privacy) is False
        assert adapter.read_back(session.page, privacy.field) == "false"
        trace = adapter.last_privacy_trace
        assert trace is not None
        assert trace["discovered"] is True
        assert trace["classified"] == "required_privacy"
        assert trace["readback_checked"] is False
        assert trace["failure_reason"] == "checked_state_did_not_persist"
        report = format_privacy_acknowledgement_report(trace)
        assert "privacy acknowledgement:" in report
        assert "discovered: yes" in report
        assert "classified: required_privacy" in report
        assert "readback_checked: false" in report
        assert "failure_reason: checked_state_did_not_persist" in report
    finally:
        session.close()


def test_privacy_diagnostic_report_shape() -> None:
    report = format_privacy_acknowledgement_report(
        {
            "discovered": True,
            "classified": "required_privacy",
            "control_type": "hidden input + styled/label-backed checkbox",
            "interaction_attempted": "click_associated_label",
            "readback_checked": False,
            "failure_reason": "checked_state_did_not_persist",
        }
    )
    assert report.splitlines() == [
        "privacy acknowledgement:",
        "  discovered: yes",
        "  classified: required_privacy",
        "  control_type: hidden input + styled/label-backed checkbox",
        "  interaction_attempted: click_associated_label",
        "  readback_checked: false",
        "  failure_reason: checked_state_did_not_persist",
    ]
