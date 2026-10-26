from __future__ import annotations

import functools
import http.server
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import (
    GreenhouseAdapter,
    GreenhouseFormError,
    _resume_is_attached,
    _resume_widget_status,
)
from app.application.autofill.models import FieldClassification
from app.application.candidate_profile import CandidateProfile

GREENHOUSE_FIXTURE = Path("tests/fixtures/autofill/greenhouse_application.html")
JOB_BOARDS_FIXTURE = Path("tests/fixtures/autofill/greenhouse_job_boards.html")
RESUME_BUTTON_FIXTURE = Path("tests/fixtures/autofill/greenhouse_resume_button.html")
RESUME_TEXTONLY_FIXTURE = Path("tests/fixtures/autofill/greenhouse_resume_textonly.html")
MODERN_WIDGET_FIXTURE = Path("tests/fixtures/autofill/greenhouse_resume_modern_widget.html")
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


@contextmanager
def _local_http_server(directory: Path):
    """Serve `directory` over 127.0.0.1 so tests can exercise the non-file:// upload path.

    The old resume-upload path clicked an adjacent button only for HTTP(S)
    pages; file: fixture tests never exercised that behavior.
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


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
        assert classified.value in {False, "No"}
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


def test_verify_resume_attached_confirms_a_real_upload() -> None:
    """`verify_resume_attached` -- the final, no-reupload re-check `AutofillService`
    runs after every other field has been filled -- must confirm a resume
    that genuinely uploaded, mirroring Lever/Ashby's own `verify_resume_attached`.
    """
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _field(adapter.discover_fields(session.page), "Resume")
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert adapter.verify_resume_attached(session.page, resume, RESUME_FIXTURE.name) is True
    finally:
        session.close()


def test_verify_resume_attached_fails_closed_when_never_uploaded() -> None:
    session = _open(GREENHOUSE_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _field(adapter.discover_fields(session.page), "Resume")
        assert adapter.verify_resume_attached(session.page, resume, RESUME_FIXTURE.name) is False
    finally:
        session.close()


def test_resume_body_text_decoy_is_never_mistaken_for_attachment() -> None:
    """Regression: `_resume_is_attached` must never fall back to scanning
    the page's visible body text for the filename. A page that merely
    echoes a filename in unrelated copy (e.g. an upload-rejection toast, or
    a mention of a previous applicant's file) must stay unattached when the
    resume input's own `FileList` is empty.
    """
    from app.application.autofill.greenhouse import _resume_is_attached

    session = _open(RESUME_TEXTONLY_FIXTURE)
    try:
        field = DiscoveredField(label="Resume", field_type="file", element_id="resume")
        assert _resume_is_attached(session.page, field, "resume.pdf") is False
    finally:
        session.close()


def test_resume_upload_over_http_with_no_adjacent_button() -> None:
    """Regression: a plain HTTP(S) form with a bare `#resume` file input and

    no wrapping dropzone button used to fail silently. `GREENHOUSE_FIXTURE`'s
    resume field is a plain `<input type="file" id="resume">` with a
    `<label>`, not a `<button>` — served via `file:` (every other test in
    this module) that goes through the direct-set-input branch and always
    passed; served over real HTTP it used to hit the button-only branch,
    find no button, and leave the resume unattached. This proves the new
    direct-input fallback attaches a synthetic file over an HTTP(S) transport.
    """
    with _local_http_server(GREENHOUSE_FIXTURE.parent) as base_url:
        session = BrowserSession(headed=False, keep_open=False)
        session.open(f"{base_url}/{GREENHOUSE_FIXTURE.name}")
        try:
            adapter = GreenhouseAdapter()
            resume = _field(adapter.discover_fields(session.page), "Resume")
            assert resume.field_type == "file"
            assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
            assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
        finally:
            session.close()


def test_resume_upload_over_http_ignores_adjacent_unrelated_button() -> None:
    with _local_http_server(RESUME_BUTTON_FIXTURE.parent) as base_url:
        session = BrowserSession(headed=False, keep_open=False)
        session.open(f"{base_url}/{RESUME_BUTTON_FIXTURE.name}")
        try:
            adapter = GreenhouseAdapter()
            resume = _field(adapter.discover_fields(session.page), "Resume")
            assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
            assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
            assert session.page.locator("#portfolio").evaluate("el => el.files.length") == 0
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


# --- Modern (current) Greenhouse file-upload widget -----------------------
#
# `MODERN_WIDGET_FIXTURE` reproduces, in markup and lifecycle, the
# `[role="group"][aria-labelledby="upload-label-{id}"]` widget confirmed from
# Greenhouse's own public embed pages (job-boards.greenhouse.io/embed/job_app)
# and bundled `entry.client-*.js`: the native `<input type="file">` is
# removed on a completed upload, replaced by a `.file-upload__filename`
# readout; a `[role="progressbar"]` appears while uploading; an
# `upload-label--error` class and `{id}-error` paragraph appear on rejection.
# These tests exercise the adapter's real DOM-reading code (`upload_resume`,
# `_resume_is_attached`, `_resume_widget_status`, `read_back`,
# `verify_resume_attached`, `resume_file_attached`) against those actual
# transitions in a real browser -- not a Python fake standing in for a
# chosen boolean.


def _modern_resume_field(fields: list[DiscoveredField]) -> DiscoveredField:
    return next(item for item in fields if item.element_id == "resume")


def test_modern_widget_upload_removes_input_and_still_confirms_attachment() -> None:
    """The core regression: a genuinely successful upload removes `#resume`
    entirely (see the fixture's `ghArmNativeInput`), so a verifier that only
    ever reads a (by-then-gone) input's `.files` must not report this as a
    false negative.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert session.page.locator("#resume").count() == 0
        assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
        assert adapter.verify_resume_attached(session.page, resume, RESUME_FIXTURE.name) is True
        assert adapter.resume_file_attached(session.page, resume) is True
    finally:
        session.close()


def test_modern_widget_pending_upload_is_not_yet_attached() -> None:
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate("window.__ghSetPending('resume')")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        assert adapter.resume_file_attached(session.page, resume) is False
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["pending"] is True
        assert status["completedFilename"] is None
    finally:
        session.close()


def test_modern_widget_delayed_completion_after_pending_is_confirmed() -> None:
    """A slow upload: pending at the first read, completed by the time the
    bounded re-check runs -- mirrors the real network round-trip this widget
    waits on.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate("window.__ghSetPending('resume')")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        session.page.evaluate(f"window.__ghSetCompleted({RESUME_FIXTURE.name!r})")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is True
        assert adapter.verify_resume_attached(session.page, resume, RESUME_FIXTURE.name) is True
    finally:
        session.close()


def test_modern_widget_rejection_echoing_filename_is_not_attached() -> None:
    """An upload-rejection error may legitimately echo the attempted
    filename back in its own message -- that must never be mistaken for a
    real attachment (mirrors `test_resume_body_text_decoy_is_never_mistaken_for_attachment`).
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(
            f"window.__ghSetError('resume', {('Could not upload ' + RESUME_FIXTURE.name)!r})"
        )
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["error"] is True
        assert status["completedFilename"] is None
    finally:
        session.close()


def test_modern_widget_wrong_filename_is_not_attached() -> None:
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate("window.__ghSetCompleted('unexpected-other-file.pdf')")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
    finally:
        session.close()


def test_modern_widget_filename_match_is_exact_not_substring() -> None:
    """A completed filename that *contains* the expected basename as a
    substring (or vice versa) must never match -- only an exact basename
    counts. Also proves special characters round-trip through the readout
    untouched.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate("window.__ghSetCompleted('not_resume.pdf')")
        assert _resume_is_attached(session.page, resume, "resume.pdf") is False

        special_name = "Ada Example — Résumé (final) #2.pdf"
        session.page.evaluate("(name) => window.__ghSetCompleted(name)", special_name)
        assert _resume_is_attached(session.page, resume, special_name) is True
        assert _resume_is_attached(session.page, resume, "Résumé (final) #2.pdf") is False
    finally:
        session.close()


def test_modern_widget_scopes_strictly_to_its_own_group_not_cover_letter() -> None:
    """The Cover Letter field renders the identical `.file-upload__filename`
    markup under its own id -- a resume check must never read that as its
    own attachment, and vice versa.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(f"window.__ghSetCompleted({RESUME_FIXTURE.name!r}, 'cover_letter')")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["nativeInputPresent"] is True
        assert status["completedFilename"] is None
    finally:
        session.close()


def test_modern_widget_explicit_missing_distinct_id_never_falls_back_to_generic_resume_group() -> None:
    """A field that identifies a distinct, missing id (`resume_other`) must
    stay unattached even while the page's own default "resume" group is
    genuinely showing a completed, matching filename -- never a silent
    fallback to that other control's evidence.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        session.page.evaluate(f"window.__ghSetCompleted({RESUME_FIXTURE.name!r})")
        other_field = DiscoveredField(
            label="Resume/CV", field_type="file", required=True, element_id="resume_other"
        )
        status, observation_failed = _resume_widget_status(session.page, other_field)
        assert status is None
        assert observation_failed is False
        assert _resume_is_attached(session.page, other_field, RESUME_FIXTURE.name) is False
    finally:
        session.close()


def test_modern_widget_removal_after_success_fails_closed_with_no_reupload() -> None:
    """A resume verified earlier in the run can be cleared later (e.g. the
    candidate clicks the widget's own "Remove file" control) -- the final
    re-check must catch this and must never re-upload to "fix" it.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        session.page.evaluate("window.__ghRestoreInput('resume')")
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        assert adapter.verify_resume_attached(session.page, resume, RESUME_FIXTURE.name) is False
        # The native input is back (idle, unselected) -- confirms this is a
        # genuine removal, not a page/DOM lookup failure.
        assert session.page.locator("#resume").count() == 1
    finally:
        session.close()


def test_modern_widget_legacy_fixture_still_uses_native_files_only() -> None:
    """`GREENHOUSE_FIXTURE` has no modern widget group at all -- confirms the
    fallback path (`_resume_widget_status` returns None) still relies solely
    on the native input's own `.files`, unchanged from before this fix.
    """
    session = _open(GREENHOUSE_FIXTURE)
    try:
        resume = _field(GreenhouseAdapter().discover_fields(session.page), "Resume")
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert status is None
        assert observation_failed is False
    finally:
        session.close()


def test_modern_widget_discovers_required_resume_and_optional_cover_letter_group() -> None:
    """`aria-required` lives on the wrapping `role="group"`, never on the
    `<input type="file">` itself -- `_discover_control` must consult that
    group for the Resume/CV control (mirrors the Cover Letter required-file
    case) and must not invent a requirement for a group that says optional.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        fields = GreenhouseAdapter().discover_fields(session.page)
        resume = _modern_resume_field(fields)
        assert resume.required is True
        cover_letter = next(item for item in fields if item.element_id == "cover_letter")
        assert cover_letter.required is False
    finally:
        session.close()


def test_modern_widget_missing_remove_control_is_not_attached() -> None:
    """A `.file-upload__filename` readout without its own working "Remove
    file" button is never a real completed state -- only a readout that
    carries both the filename slot and the remove control counts.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(
            """(name) => {
                const wrapper = document.getElementById('resume-wrapper');
                wrapper.innerHTML =
                    '<div class="file-upload__filename"><svg></svg>' +
                    '<p class="body body__secondary"></p></div>';
                wrapper.querySelector('p').textContent = name;
            }""",
            RESUME_FIXTURE.name,
        )
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["completedFilename"] is None
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        assert adapter.read_back(session.page, resume) is None
    finally:
        session.close()


def test_modern_widget_hidden_stale_completed_state_is_not_attached() -> None:
    """A completed filename readout that is no longer visible (e.g. a stale
    node CSS hid without removing) must never count, even though the
    filename text and remove control are still technically present in the
    DOM.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(f"window.__ghSetCompleted({RESUME_FIXTURE.name!r})")
        session.page.evaluate(
            "document.querySelector('#resume-wrapper .file-upload__filename').style.display = 'none'"
        )
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["completedFilename"] is None
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
    finally:
        session.close()


def test_modern_widget_stale_completed_filename_coexisting_with_pending_is_not_attached() -> None:
    """Regression: a contrived (but DOM-observable) in-between state where a
    visible, fully-formed completed readout (filename slot + Remove file
    control) coexists with a progressbar for the same group. The readout
    alone used to be enough for `_resume_is_attached` to report True; it must
    now fail closed whenever `pending` (or `error`) is also reported,
    because the earlier `completedFilename` could be stale while a fresh
    upload is in flight.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(
            """(name) => {
                const wrapper = document.getElementById('resume-wrapper');
                wrapper.innerHTML =
                    '<div class="file-upload__filename"><svg></svg><p class="body body__secondary"></p>' +
                    '<button type="button" aria-label="Remove file">x</button></div>' +
                    '<div role="progressbar" aria-valuenow="40"></div>';
                wrapper.querySelector('p').textContent = name;
            }""",
            RESUME_FIXTURE.name,
        )
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["completedFilename"] == RESUME_FIXTURE.name
        assert status["pending"] is True
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is False
        assert adapter.read_back(session.page, resume) is None
    finally:
        session.close()


def test_modern_widget_retained_native_input_alongside_success_ui_still_attaches() -> None:
    """Some widget variants briefly retain the (now-inert) native input node
    alongside the completed filename readout instead of removing it outright
    -- `nativeInputPresent` reflects that, but it must never by itself block
    a genuine, visible completed readout with its own working Remove file
    control from counting as attached.
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate(
            """(name) => {
                const wrapper = document.getElementById('resume-wrapper');
                wrapper.innerHTML =
                    '<input id="resume" class="visually-hidden" type="file" accept=".pdf,.doc,.docx,.txt,.rtf">' +
                    '<div class="file-upload__filename"><svg></svg><p class="body body__secondary"></p>' +
                    '<button type="button" aria-label="Remove file">x</button></div>';
                wrapper.querySelector('p').textContent = name;
            }""",
            RESUME_FIXTURE.name,
        )
        status, observation_failed = _resume_widget_status(session.page, resume)
        assert observation_failed is False
        assert status is not None
        assert status["nativeInputPresent"] is True
        assert status["completedFilename"] == RESUME_FIXTURE.name
        assert _resume_is_attached(session.page, resume, RESUME_FIXTURE.name) is True
    finally:
        session.close()


def test_modern_widget_real_delayed_transition_resolves_via_polling_without_second_write() -> None:
    """A genuine async upload: the widget goes `pending` immediately on
    selection and only reaches `completedFilename` after a real `setTimeout`
    (`__ghArmDelayedUpload`), mirroring an actual network round-trip rather
    than a Python-side state swap. `upload_resume` must keep polling long
    enough to observe that real transition -- not sample twice at one fixed
    delay -- and must never issue a second `_set_resume_files` write to
    "retry" it (tracked here via the fixture's own change-event counter,
    which increments once per native `set_input_files` call).
    """
    session = _open(MODERN_WIDGET_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        resume = _modern_resume_field(adapter.discover_fields(session.page))
        session.page.evaluate("window.__ghArmDelayedUpload('resume', 800)")
        assert adapter.upload_resume(session.page, RESUME_FIXTURE, resume) is True
        assert session.page.evaluate("window.__ghChangeCount") == 1
        assert adapter.read_back(session.page, resume) == RESUME_FIXTURE.name
        assert session.page.evaluate("window.__submitClicked") is False
    finally:
        session.close()
