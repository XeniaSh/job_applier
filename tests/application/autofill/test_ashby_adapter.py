from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.application.autofill.ashby import AshbyAdapter, AshbyFormError, is_ashby_application_page
from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.candidate_profile import CandidateProfile

ASHBY_FIXTURE = Path("tests/fixtures/autofill/ashby_application.html")
ASHBY_OVERVIEW_FIXTURE = Path("tests/fixtures/autofill/ashby_overview.html")
UNRELATED_FIXTURE = Path("tests/fixtures/autofill/unrelated.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(), reason="Playwright Chromium is not installed"
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


def test_recognizes_ashby_fixture() -> None:
    session = _open(ASHBY_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is True
    assert is_ashby_application_page(session.page) is True
    session.close()


def test_rejects_overview_page() -> None:
    session = _open(ASHBY_OVERVIEW_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_rejects_unrelated_page() -> None:
    session = _open(UNRELATED_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_rejects_survey_only_form_container_without_main_application_panel() -> None:
    # Ashby wraps its EEOC/demographic survey section in the very same
    # `.ashby-application-form-container` class as the real application
    # panel (see the shared fixture's own sibling survey container). A page
    # that only has that survey container -- no tabpanel with both a submit
    # button and name/email systemfields -- must not be recognized.
    session = BrowserSession(headed=False, keep_open=False)
    session.start()
    session.page.set_content(
        """
        <div class="ashby-survey-form-container">
          <div class="ashby-application-form-container">
            <fieldset data-field-path="_systemfield_eeoc_gender">
              <div class="ashby-application-form-question-title">Gender</div>
              <div class="ashby-application-form-input-radio-group">
                <label><input type="radio" name="eeoc-gender" value="Man" /> Man</label>
                <label><input type="radio" name="eeoc-gender" value="Woman" /> Woman</label>
              </div>
            </fieldset>
          </div>
        </div>
        """
    )
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_detects_visible_active_captcha_even_on_a_recognized_application_page() -> None:
    session = _open(ASHBY_FIXTURE)
    session.page.evaluate(
        """() => {
            const iframe = document.createElement('iframe');
            // A self-contained data: URI keeps this test offline -- only the
            // "recaptcha" substring in the src (matched case-insensitively)
            // and the iframe's visible layout box matter here, not that
            // anything actually loads from google.com.
            iframe.src = 'data:text/html;charset=utf-8,recaptcha-challenge';
            iframe.style.width = '300px';
            iframe.style.height = '80px';
            document.body.appendChild(iframe);
        }"""
    )
    assert AshbyAdapter().detect_challenge(session.page) == "captcha"
    session.close()


def test_discover_fields_raises_on_unrecognized_page() -> None:
    session = _open(UNRELATED_FIXTURE)
    with pytest.raises(AshbyFormError):
        AshbyAdapter().discover_fields(session.page)
    session.close()


def test_discover_fields_ignores_data_field_path_outside_application_panel() -> None:
    # The fixture has a decoy `[data-field-path]` element as a sibling of
    # the `#form` tabpanel (representing a different tab/section of the same
    # page) -- discovery must be scoped to the recognized panel so it is
    # never surfaced or drivable.
    session = _open(ASHBY_FIXTURE)
    fields = AshbyAdapter().discover_fields(session.page)
    assert not any(item.context == "decoy-outside-panel-uuid" for item in fields)
    assert not any("should never be discovered" in item.label.lower() for item in fields)
    session.close()


def test_offscreen_recaptcha_badge_iframe_is_not_a_blocking_challenge() -> None:
    # The fixture's badge iframe is positioned off-screen rather than
    # visibility:hidden -- the same way the real widget behaves -- so it can
    # report `is_visible() == True`. It must still be excluded, by ancestry
    # (`.grecaptcha-badge`), not by visibility.
    session = _open(ASHBY_FIXTURE)
    badge_iframe = session.page.locator(".grecaptcha-badge iframe[title='recaptcha']")
    assert badge_iframe.count() == 1
    assert badge_iframe.is_visible() is True
    assert AshbyAdapter().detect_challenge(session.page) is None
    session.close()


def test_fills_name_email_linkedin_and_uploads_resume() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    classified = [classify_field(item, profile) for item in fields]

    name = next(item for item in classified if item.kind is QuestionKind.FULL_NAME)
    email = next(item for item in classified if item.kind is QuestionKind.EMAIL)
    linkedin = next(item for item in classified if item.kind is QuestionKind.LINKEDIN)
    resume = next(item for item in classified if item.kind is QuestionKind.RESUME)

    assert name.fill is True
    assert email.fill is True
    assert linkedin.fill is True
    assert resume.fill is True

    assert adapter.fill_field(session.page, name) is True
    assert adapter.fill_field(session.page, email) is True
    assert adapter.fill_field(session.page, linkedin) is True
    assert adapter.upload_resume(session.page, Path(profile.application_files.default_resume), resume.field) is True

    assert adapter.read_back(session.page, name.field) == "Ada Example"
    assert adapter.read_back(session.page, email.field) == "ada.example@example.test"
    assert adapter.read_back(session.page, linkedin.field) == "https://www.linkedin.com/in/ada-example-test"
    assert adapter.read_back(session.page, resume.field) == "resume.txt"

    session.close()


def test_never_uploads_to_the_autofill_uploader_input() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    resume = next(classify_field(item, profile) for item in fields if item.field_type == "file")
    adapter.upload_resume(session.page, Path(profile.application_files.default_resume), resume.field)

    uploader_files = session.page.evaluate(
        "() => { const el = document.querySelector('.ashby-application-form-autofill-uploader input[type=file]');"
        " return el ? el.files.length : -1; }"
    )
    assert uploader_files == 0
    session.close()


def test_submit_button_is_never_clicked_during_a_full_fill_pass() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    session.page.evaluate(
        "() => { window.__submitClicked = false;"
        " document.querySelector('.ashby-application-form-submit-button')"
        ".addEventListener('click', () => { window.__submitClicked = true; }); }"
    )
    fields = adapter.discover_fields(session.page)
    for item in [classify_field(field, profile) for field in fields]:
        if item.kind is QuestionKind.RESUME and item.fill:
            adapter.upload_resume(session.page, Path(profile.application_files.default_resume), item.field)
        elif item.fill:
            adapter.fill_field(session.page, item)

    assert session.page.evaluate("() => window.__submitClicked") is False
    session.close()


def test_autocomplete_comboboxes_are_unsupported_not_typed_into() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)

    source_field = _field(fields, "Where did you hear")
    location_field = _field(fields, "Current Location")
    assert source_field.field_type == "unknown"
    assert location_field.field_type == "unknown"

    source_classified = classify_field(source_field, profile)
    location_classified = classify_field(location_field, profile)
    assert source_classified.classification is FieldClassification.UNSUPPORTED
    assert location_classified.classification is FieldClassification.UNSUPPORTED
    assert source_classified.fill is False
    assert location_classified.fill is False
    session.close()


def test_employee_relationship_checkbox_group_is_not_discovered() -> None:
    session = _open(ASHBY_FIXTURE)
    fields = AshbyAdapter().discover_fields(session.page)
    assert not any("related to any current employees" in item.label.lower() for item in fields)
    session.close()


def test_consent_checkbox_is_discovered_but_never_auto_checked() -> None:
    # The live consent checkbox's own id/name carry no stable semantic
    # meaning (id is an Ashby-generated UUID, name is the literal "I agree"
    # label text) -- only the wrapper's data-field-path (`context`)
    # reliably identifies it.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    consent = next(item for item in fields if item.context == "_systemfield_data_consent_ack")
    assert consent.field_type == "checkbox"
    assert consent.name == "I agree"
    assert consent.required is False

    forced = ClassifiedField(
        field=consent,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.NEWSLETTER,
    )
    assert adapter.fill_field(session.page, forced) is False
    assert adapter.read_back(session.page, consent) == "false"
    session.close()


def test_which_location_radio_fills_and_reads_back_when_explicitly_resolved() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Which location are you applying for")
    assert location.field_type == "radio"
    assert location.required is True

    classified = ClassifiedField(
        field=location,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Boston",
        fill=True,
        kind=QuestionKind.LOCATED_IN,
    )
    assert adapter.fill_field(session.page, classified) is True
    assert adapter.read_back(session.page, location) == "Boston"
    session.close()


def test_which_location_radio_fill_fails_closed_on_unmatched_prefix() -> None:
    # "Bo" is a prefix of the live "Boston" option but not an exact match --
    # must never guess. The ambiguous-match case (more than one live option
    # matching) is a synthetic scenario not observed on this snapshot's
    # single-option field, covered browser-free instead by
    # `_matching_radio_indices` in test_ashby_helpers.py.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Which location are you applying for")

    prefix_classified = ClassifiedField(
        field=location,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Bo",
        fill=True,
        kind=QuestionKind.LOCATED_IN,
    )
    assert adapter.fill_field(session.page, prefix_classified) is False
    assert adapter.read_back(session.page, location) is None
    session.close()


def test_which_location_radio_stays_unresolved_by_default() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = classify_field(_field(fields, "Which location are you applying for"), profile)
    assert location.fill is False
    session.close()


def test_legal_authorization_yesno_fills_and_reads_back_yes_and_no() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    legal = _field(fields, "legal authorisation")
    assert legal.field_type == "yesno"
    assert legal.required is True
    assert set(legal.options) == {"Yes", "No"}

    classified_yes = ClassifiedField(
        field=legal,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.WORK_AUTHORIZATION,
    )
    assert adapter.fill_field(session.page, classified_yes) is True
    assert adapter.read_back(session.page, legal) == "Yes"

    classified_no = replace(classified_yes, value=False)
    assert adapter.fill_field(session.page, classified_no) is True
    assert adapter.read_back(session.page, legal) == "No"
    session.close()


def test_legal_authorization_yesno_stays_unresolved_by_default() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    legal = classify_field(_field(fields, "legal authorisation"), profile)
    # This exact phrasing ("legal authorisation to work in...") is not one
    # of the shared classifier's recognized work-authorization patterns, so
    # it falls through to the generic unresolved-required bucket -- it must
    # stay unresolved/manual, never guessed.
    assert legal.fill is False
    session.close()


def test_office_arrangement_yesno_stays_unresolved_by_default() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    office = classify_field(_field(fields, "3 days per week"), profile)
    # No explicit office-work preference is set on this profile, so the
    # shared mapping must leave it unresolved rather than guess.
    assert office.fill is False
    session.close()


def test_eeoc_gender_survey_group_is_optional_and_never_guessed() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    gender = _field(fields, "Gender")
    assert gender.field_type == "radio"
    assert gender.required is False

    classified = classify_field(gender, profile)
    assert classified.fill is False
    session.close()
