from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter, GreenhouseFormError
from app.application.autofill.models import FieldClassification
from app.application.candidate_profile import CandidateProfile

GREENHOUSE_FIXTURE = Path("tests/fixtures/autofill/greenhouse_application.html")
JOB_BOARDS_FIXTURE = Path("tests/fixtures/autofill/greenhouse_job_boards.html")
UNRELATED_FIXTURE = Path("tests/fixtures/autofill/unrelated.html")
CHALLENGE_FIXTURE = Path("tests/fixtures/autofill/challenge.html")
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


def test_recognizes_greenhouse_fixture() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        assert GreenhouseAdapter().recognize(session.page) is True
    finally:
        session.close()


def test_unrelated_html_is_unsupported_form() -> None:
    session = _open(UNRELATED_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        assert adapter.recognize(session.page) is False
        with pytest.raises(GreenhouseFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()


def test_discovers_labeled_fields() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        fields = GreenhouseAdapter().discover_fields(session.page)
        labels = [item.label.lower() for item in fields]
        assert any("first name" in label for label in labels)
        assert any("resume" in label for label in labels)
        assert any("visa sponsorship" in label for label in labels)
        assert any("favorite ide" in label for label in labels)
        assert any("gender" in label for label in labels)
        first_name = _field(fields, "First Name")
        assert first_name.required is True
        assert first_name.field_type == "text"
        sponsorship = _field(fields, "visa sponsorship")
        assert sponsorship.field_type == "radio"
        assert "Yes" in sponsorship.options
        resume = _field(fields, "Resume")
        assert resume.field_type == "file"
    finally:
        session.close()


def test_fills_text_fields_and_reads_them_back() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        for label in ("First Name", "Last Name", "Email", "Phone", "LinkedIn"):
            discovered = _field(fields, label)
            classified = classify_field(discovered, profile)
            assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
            assert adapter.fill_field(session.page, classified) is True
            assert adapter.read_back(session.page, discovered) == classified.value
        gender = _field(fields, "Gender")
        classified_gender = classify_field(gender, profile)
        assert classified_gender.fill is True
        assert adapter.fill_field(session.page, classified_gender) is True
        assert adapter.read_back(session.page, gender) == "Prefer not to disclose"
    finally:
        session.close()


def test_select_known_option_and_missing_option() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        work_auth = _field(adapter.discover_fields(session.page), "authorized to work")
        classified = classify_field(work_auth, profile)
        assert classified.value is True
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, work_auth) == "Yes"

        missing = ClassifiedField(
            field=work_auth,
            classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            value="Maybe",
            fill=True,
        )
        assert adapter.fill_field(session.page, missing) is False
        assert adapter.read_back(session.page, work_auth) == "Yes"
    finally:
        session.close()


def test_radio_sponsorship_and_unmapped_checkbox() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        sponsorship = _field(fields, "visa sponsorship")
        classified = classify_field(sponsorship, profile)
        assert classified.value is False
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, sponsorship) == "No"

        newsletter = _field(fields, "newsletter")
        assert newsletter.field_type == "checkbox"
        assert adapter.read_back(session.page, newsletter) == "false"
        classified_news = classify_field(newsletter, profile)
        assert classified_news.fill is True
        assert classified_news.value is False
        assert adapter.fill_field(session.page, classified_news) is True
        assert adapter.read_back(session.page, newsletter) == "false"
    finally:
        session.close()


def test_resume_upload_read_back() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _field(adapter.discover_fields(session.page), "Resume")
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
    finally:
        session.close()


def test_read_back_leaves_unknown_required_empty() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        unknown = _field(fields, "favorite IDE")
        classified = classify_field(unknown, profile)
        assert classified.classification is FieldClassification.UNKNOWN_REQUIRED
        assert adapter.fill_field(session.page, classified) is False
        assert adapter.read_back(session.page, unknown) is None
        assert adapter.invalid_required_empty(session.page, unknown) is True
    finally:
        session.close()


def test_challenge_page_does_not_look_like_application() -> None:
    session = _open(CHALLENGE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        assert adapter.recognize(session.page) is False
        assert adapter.detect_challenge(session.page) is not None
    finally:
        session.close()


def test_submit_control_is_not_clicked() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        assert not hasattr(adapter, "submit")
        fields = adapter.discover_fields(session.page)
        for discovered in fields:
            classified = classify_field(discovered, profile)
            if classified.fill:
                adapter.fill_field(session.page, classified)
        resume = _field(fields, "Resume")
        adapter.upload_resume(session.page, RESUME_FIXTURE, resume)
        clicked = session.page.evaluate("window.__submitClicked")
        submitted = session.page.evaluate("window.__formSubmitted")
        assert clicked is False
        assert submitted is False
    finally:
        session.close()


def test_job_boards_form_is_not_blocked_by_recaptcha_iframe() -> None:
    session = _open(JOB_BOARDS_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        adapter.prepare_page(session.page)
        assert adapter.recognize(session.page) is True
        assert adapter.detect_challenge(session.page) is None
    finally:
        session.close()


def test_job_boards_discovers_attach_resume_and_skips_decoys() -> None:
    session = _open(JOB_BOARDS_FIXTURE)
    try:
        fields = GreenhouseAdapter().discover_fields(session.page)
        labels = [item.label for item in fields]
        assert "" not in labels
        resume = next(item for item in fields if item.element_id == "resume")
        assert resume.label == "Attach"
        assert resume.field_type == "file"
        country = _field(fields, "Country")
        assert country.element_id == "country"
        assert country.field_type == "combobox"
        assert labels.count("Location (City)*") == 1
        assert labels.count("Country*") == 1
    finally:
        session.close()


def test_job_boards_fills_identity_country_location_phone_and_resume() -> None:
    session = _open(JOB_BOARDS_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        for label in ("First Name", "Last Name", "Email", "Phone", "Country", "Location (City)"):
            discovered = _field(fields, label)
            classified = classify_field(discovered, profile)
            assert classified.fill is True
            assert adapter.fill_field(session.page, classified) is True
            read_back = adapter.read_back(session.page, discovered)
            assert read_back
            if label == "Country":
                assert "Germany" in read_back
            if label == "Location (City)":
                assert "Berlin" in read_back
        phone_country = session.page.locator(".iti__selected-country").get_attribute("aria-label") or ""
        assert "Germany" in phone_country
        resume = next(item for item in fields if item.element_id == "resume")
        assert classify_field(resume, profile).kind.value == "resume"
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
        newsletter = _field(fields, "Email me about")
        classified_news = classify_field(newsletter, profile)
        assert classified_news.fill is True
        assert adapter.fill_field(session.page, classified_news) is True
        read_news = adapter.read_back(session.page, newsletter) or ""
        assert read_news.lower().startswith("no") or read_news.lower() == "no"
        assert session.page.evaluate("window.__submitClicked") is False
        assert session.page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()
