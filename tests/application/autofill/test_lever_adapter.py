from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter, LeverFormError
from app.application.autofill.models import FieldClassification
from app.application.candidate_profile import CandidateProfile

LEVER_FIXTURE = Path("tests/fixtures/autofill/lever_application.html")
UNRELATED_FIXTURE = Path("tests/fixtures/autofill/unrelated.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
            "country": "Germany",
        },
        "professional_links": {
            "linkedin": "https://www.linkedin.com/in/ada-example-test",
        },
        "application_files": {"default_resume": str(RESUME_FIXTURE)},
        "work_eligibility": {
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "sensitive": {"gender": "female"},
        "fill_sensitive_fields": False,
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def _open(path: Path) -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(path)
    return session


def _field(fields: list[DiscoveredField], label_substring: str) -> DiscoveredField:
    needle = label_substring.lower()
    for item in fields:
        if needle in item.label.lower():
            return item
    raise AssertionError(f"Field not found: {label_substring} in {[item.label for item in fields]}")


def test_recognizes_lever_fixture() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        assert LeverAdapter().recognize(session.page) is True
    finally:
        session.close()


def test_unrelated_html_is_unsupported_form() -> None:
    session = _open(UNRELATED_FIXTURE)
    try:
        adapter = LeverAdapter()
        assert adapter.recognize(session.page) is False
        with pytest.raises(LeverFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()


def test_discovers_labeled_fields() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        fields = LeverAdapter().discover_fields(session.page)
        labels = [item.label.lower() for item in fields]
        assert any("full name" in label for label in labels)
        assert any("resume" in label for label in labels)
        assert any("visa sponsorship" in label for label in labels)
        assert any("favorite ide" in label for label in labels)
        assert any("gender" in label for label in labels)

        full_name = _field(fields, "Full name")
        assert full_name.required is True
        assert full_name.field_type == "text"

        resume = _field(fields, "Resume")
        assert resume.field_type == "file"

        sponsorship = _field(fields, "visa sponsorship")
        assert sponsorship.field_type == "select"
        assert "Yes" in sponsorship.options

        work_auth = _field(fields, "authorized to work")
        assert work_auth.field_type == "radio"
        assert "Yes" in work_auth.options
    finally:
        session.close()


def test_fills_text_fields_and_reads_them_back() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        adapter = LeverAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        for label in ("Full name", "Email", "Phone", "LinkedIn"):
            discovered = _field(fields, label)
            classified = classify_field(discovered, profile)
            assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
            assert adapter.fill_field(session.page, classified) is True
            assert adapter.read_back(session.page, discovered) == classified.value
    finally:
        session.close()


def test_select_visa_sponsorship_and_gender() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        adapter = LeverAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)

        sponsorship = _field(fields, "visa sponsorship")
        classified = classify_field(sponsorship, profile)
        assert classified.value in {False, "No"}
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, sponsorship) == "No"

        gender = _field(fields, "Gender")
        classified_gender = classify_field(gender, profile)
        assert classified_gender.fill is True
        assert adapter.fill_field(session.page, classified_gender) is True
        assert adapter.read_back(session.page, gender) == "Decline to self-identify"
    finally:
        session.close()


def test_radio_work_authorization_and_unmapped_checkbox() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        adapter = LeverAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)

        work_auth = _field(fields, "authorized to work")
        classified = classify_field(work_auth, profile)
        assert classified.value is True
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, work_auth) == "Yes"

        newsletter = _field(fields, "newsletter")
        assert newsletter.field_type == "checkbox"
        assert adapter.read_back(session.page, newsletter) == "false"

        favorite_ide = _field(fields, "favorite IDE")
        classified_ide = classify_field(favorite_ide, profile)
        assert classified_ide.classification is FieldClassification.UNKNOWN_REQUIRED

        unknown = ClassifiedField(
            field=work_auth,
            classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            value="Maybe",
            fill=True,
        )
        assert adapter.fill_field(session.page, unknown) is False
        assert adapter.read_back(session.page, work_auth) == "Yes"
    finally:
        session.close()


def test_resume_upload_read_back() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        adapter = LeverAdapter()
        resume = _field(adapter.discover_fields(session.page), "Resume")
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
    finally:
        session.close()


def test_never_submits() -> None:
    session = _open(LEVER_FIXTURE)
    try:
        adapter = LeverAdapter()
        assert not hasattr(adapter, "submit")
        page = session.page
        assert page.evaluate("window.__submitClicked") is False
        assert page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()
