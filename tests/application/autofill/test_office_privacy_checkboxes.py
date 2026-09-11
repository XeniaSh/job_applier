from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.candidate_profile import CandidateProfile

FIXTURE = Path("tests/fixtures/autofill/greenhouse_office_privacy.html")
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
                "requires_visa_sponsorship": False,
            },
            "application_files": {"default_resume": str(RESUME_FIXTURE)},
            "application_policy": {
                "relocation": {"willing": True},
                "office_work": {"willing": True},
                "privacy_acknowledgement": {"auto_acknowledge_required": True},
                "newsletter_opt_in": False,
                "sms_interview_updates": False,
            },
        }
    )


def _open() -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(FIXTURE)
    return session


def _field(fields: list[DiscoveredField], label_substring: str) -> DiscoveredField:
    needle = label_substring.lower()
    for item in fields:
        haystack = f"{item.label} {item.context}".lower()
        if needle in haystack:
            return item
    raise AssertionError(f"Field not found: {label_substring} in {[item.label for item in fields]}")


def test_office_hybrid_and_privacy_checkbox_fill() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)

        office = classify_field(_field(fields, "3 or more days per week in the office"), profile)
        assert office.kind is QuestionKind.OFFICE_WORK
        assert office.fill is True
        assert adapter.fill_field(session.page, office) is True
        assert adapter.read_back(session.page, office.field) == "Yes"

        hybrid = classify_field(_field(fields, "hybrid schedule"), profile)
        assert hybrid.kind is QuestionKind.OFFICE_WORK
        assert adapter.fill_field(session.page, hybrid) is True
        assert adapter.read_back(session.page, hybrid.field) == "Yes"

        location = classify_field(_field(fields, "Current location"), profile)
        assert location.kind is QuestionKind.LOCATION
        assert location.value == "Berlin, Germany"
        assert session.page.locator("#current_location").input_value() in {"", "Berlin, Germany"}
        assert "Amsterdam" not in (adapter.read_back(session.page, location.field) or "")

        work_auth = classify_field(_field(fields, "authorized to work in the Netherlands"), profile)
        assert work_auth.kind is QuestionKind.WORK_AUTHORIZATION
        assert work_auth.fill is False
        assert adapter.fill_field(session.page, work_auth) is False
        work_auth_value = adapter.read_back(session.page, work_auth.field)
        assert work_auth_value in {None, "", "Please select"}
        assert work_auth_value != "Yes"

        privacy = classify_field(_field(fields, "Point of Data Transfer"), profile)
        assert privacy.kind is QuestionKind.PRIVACY_CONSENT
        assert privacy.field.field_type == "checkbox"
        assert privacy.field.required is True
        assert privacy.fill is True
        assert adapter.fill_field(session.page, privacy) is True
        assert adapter.read_back(session.page, privacy.field) == "true"
        assert session.page.locator("#data_transfer_ack").get_attribute("aria-checked") == "true"

        native_privacy = classify_field(_field(fields, "acknowledge the privacy policy"), profile)
        assert native_privacy.kind is QuestionKind.PRIVACY_CONSENT
        assert adapter.fill_field(session.page, native_privacy) is True
        assert adapter.read_back(session.page, native_privacy.field) == "true"
        assert session.page.locator("#privacy_native").is_checked() is True

        hidden_privacy = classify_field(_field(fields, "processing my application data"), profile)
        assert hidden_privacy.kind is QuestionKind.PRIVACY_CONSENT
        assert adapter.fill_field(session.page, hidden_privacy) is True
        assert adapter.read_back(session.page, hidden_privacy.field) == "true"
        assert session.page.locator("#privacy_hidden").is_checked() is True

        newsletter = classify_field(_field(fields, "newsletter"), profile)
        assert newsletter.kind is QuestionKind.NEWSLETTER
        assert newsletter.fill is True
        assert newsletter.value in {False, "No"}
        assert adapter.fill_field(session.page, newsletter) is True
        assert adapter.read_back(session.page, newsletter.field) == "false"
        assert session.page.locator("#newsletter").is_checked() is False

        sms = classify_field(_field(fields, "TEXT/SMS"), profile)
        assert sms.kind is QuestionKind.SMS_UPDATES
        assert adapter.fill_field(session.page, sms) is True
        assert adapter.read_back(session.page, sms.field) == "No"

        criminal = classify_field(_field(fields, "criminal convictions"), profile)
        assert criminal.fill is False
        assert criminal.classification is FieldClassification.UNKNOWN_REQUIRED
        assert adapter.fill_field(session.page, criminal) is False
        assert adapter.read_back(session.page, criminal.field) == "false"

        certify = classify_field(_field(fields, "true and complete"), profile)
        assert certify.fill is False
        assert certify.classification is FieldClassification.UNKNOWN_REQUIRED
        assert adapter.fill_field(session.page, certify) is False
        assert adapter.read_back(session.page, certify.field) == "false"

        talent = classify_field(_field(fields, "talent pool"), profile)
        assert talent.kind is QuestionKind.NEWSLETTER
        assert adapter.fill_field(session.page, talent) is True
        assert adapter.read_back(session.page, talent.field) == "false"

        assert session.page.evaluate("window.__submitClicked") is False
        assert session.page.evaluate("window.__formSubmitted") is False
        assert profile.identity.current_location == "Berlin, Germany"
        assert profile.work_authorization_for("Netherlands") is None
    finally:
        session.close()


def test_checkbox_click_is_not_enough_without_checked_state() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        privacy = classify_field(
            _field(adapter.discover_fields(session.page), "Point of Data Transfer"),
            profile,
        )
        assert adapter.read_back(session.page, privacy.field) == "false"
        assert adapter.fill_field(session.page, privacy) is True
        assert adapter.read_back(session.page, privacy.field) == "true"
    finally:
        session.close()
