from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter, LeverFormError, is_lever_job_detail_page
from app.application.autofill.models import FieldClassification
from app.application.candidate_profile import CandidateProfile

LEVER_FIXTURE = Path("tests/fixtures/autofill/lever_application.html")
LEVER_LABEL_FOR_RADIOS_FIXTURE = Path("tests/fixtures/autofill/lever_application_label_for_radios.html")
LEVER_JOB_DETAIL_FIXTURE = Path("tests/fixtures/autofill/lever_job_detail.html")
LEVER_JOB_DETAIL_DEAD_END_FIXTURE = Path("tests/fixtures/autofill/lever_job_detail_dead_end.html")
LEVER_JOB_DETAIL_REAL_FIXTURE = Path("tests/fixtures/autofill/lever_job_detail_real.html")
LEVER_JOB_DETAIL_WRONG_JOB_FIXTURE = Path("tests/fixtures/autofill/lever_job_detail_wrong_job.html")
LEVER_JOB_DETAIL_LEGACY_WRONG_JOB_FIXTURE = Path(
    "tests/fixtures/autofill/lever_job_detail_legacy_wrong_job.html"
)
LEVER_JOB_DETAIL_EXTERNAL_LINK_FIXTURE = Path("tests/fixtures/autofill/lever_job_detail_external_link.html")
LEVER_COMPANY_LISTING_FIXTURE = Path("tests/fixtures/autofill/lever_company_listing.html")
UNRELATED_FIXTURE = Path("tests/fixtures/autofill/unrelated.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

# Real hosted job-detail URL shape, mirroring an actual observed Qonto posting.
LEVER_JOB_PATH = "/qonto/d36db188-ab16-43b6-86a8-47ed9cdd29b1"
LEVER_JOB_URL = f"https://jobs.lever.co{LEVER_JOB_PATH}"
LEVER_JOB_APPLY_PATH = f"{LEVER_JOB_PATH}/apply"

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


def _open_mocked_lever_url(url: str, routes: dict[str, Path]) -> BrowserSession:
    """Open `url` against real `jobs.lever.co` host without a network call.

    `routes` maps a path (e.g. `LEVER_JOB_PATH`) to a local fixture file
    served for that path, so detection logic that inspects `page.url` and
    resolved anchor `href`s sees a genuine `jobs.lever.co` origin.
    """
    session = BrowserSession(headed=False, keep_open=False)
    session.start()

    def handler(route, request) -> None:  # noqa: ANN001
        path = urlsplit(request.url).path
        fixture = routes.get(path)
        if fixture is None:
            route.fulfill(status=404, content_type="text/plain", body="not found")
            return
        route.fulfill(status=200, content_type="text/html", body=fixture.read_text())

    session.page.route("https://jobs.lever.co/**", handler)
    session.page.goto(url, wait_until="domcontentloaded")
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


def test_label_for_radio_group_discovers_one_field_with_visible_options() -> None:
    """A five-option `label[for=id]` radio group (not wrapped) must still
    discover as exactly one semantic field, with visible option text -- not
    the group's raw, non-human `value` attributes.
    """
    session = _open(LEVER_LABEL_FOR_RADIOS_FIXTURE)
    try:
        fields = LeverAdapter().discover_fields(session.page)
        sponsorship_fields = [item for item in fields if item.name == "cards[sponsorship]"]
        assert len(sponsorship_fields) == 1
        sponsorship = sponsorship_fields[0]
        assert sponsorship.field_type == "radio"
        assert sponsorship.required is True
        assert sponsorship.options == [
            "Yes - I need a visa and I would like to relocate",
            "Yes - I need a visa but I have already relocated to one of your locations",
            "No - I already have a visa or a European nationality so I can relocate",
            "No - I do not want to relocate",
            "No - I already have a visa or a European nationality and I already live in one of your locations",
        ]
        assert "opt0" not in sponsorship.options
    finally:
        session.close()


def test_label_for_radio_sponsorship_fills_and_reads_back_first_yes_option() -> None:
    """Explicit sponsorship-needed + wants-to-relocate facts must select the
    "need a visa and would like to relocate" option, filled and verified
    exactly once for the whole group -- not by iterating every option.
    """
    session = _open(LEVER_LABEL_FOR_RADIOS_FIXTURE)
    try:
        adapter = LeverAdapter()
        profile = _profile(
            work_eligibility={"requires_visa_sponsorship": True},
            application_policy={"relocation": {"willing": True}},
        )
        fields = adapter.discover_fields(session.page)
        sponsorship = _field(fields, "visa sponsorship")
        classified = classify_field(sponsorship, profile)
        assert classified.value == "Yes - I need a visa and I would like to relocate"
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, sponsorship) == "Yes - I need a visa and I would like to relocate"
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


def test_recognizes_job_detail_page_before_navigation() -> None:
    session = _open(LEVER_JOB_DETAIL_FIXTURE)
    try:
        adapter = LeverAdapter()
        assert is_lever_job_detail_page(session.page) is True
        # The job-detail page itself is not the application form yet.
        assert adapter.recognize(session.page) is False
    finally:
        session.close()


def test_prepare_page_navigates_from_job_detail_to_application_form() -> None:
    session = _open(LEVER_JOB_DETAIL_FIXTURE)
    try:
        adapter = LeverAdapter()
        adapter.prepare_page(session.page)
        assert adapter.recognize(session.page) is True
        assert is_lever_job_detail_page(session.page) is False

        fields = adapter.discover_fields(session.page)
        full_name = _field(fields, "Full name")
        profile = _profile()
        classified = classify_field(full_name, profile)
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, full_name) == classified.value

        page = session.page
        assert page.evaluate("window.__submitClicked") is False
        assert page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()


def test_prepare_page_fails_closed_when_apply_action_has_no_form() -> None:
    session = _open(LEVER_JOB_DETAIL_DEAD_END_FIXTURE)
    try:
        adapter = LeverAdapter()
        adapter.prepare_page(session.page)
        assert adapter.recognize(session.page) is False
        with pytest.raises(LeverFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()


def test_recognizes_real_qonto_shaped_job_detail_page() -> None:
    """Real Qonto markup has no `data-qa`: only class/styling and a same-job
    `/apply` href. Detection must still recognize this as a job-detail page.
    """
    session = _open_mocked_lever_url(
        LEVER_JOB_URL,
        {LEVER_JOB_PATH: LEVER_JOB_DETAIL_REAL_FIXTURE, LEVER_JOB_APPLY_PATH: LEVER_FIXTURE},
    )
    try:
        assert is_lever_job_detail_page(session.page) is True
        assert LeverAdapter().recognize(session.page) is False
    finally:
        session.close()


def test_prepare_page_navigates_from_real_job_detail_to_application_form() -> None:
    session = _open_mocked_lever_url(
        LEVER_JOB_URL,
        {LEVER_JOB_PATH: LEVER_JOB_DETAIL_REAL_FIXTURE, LEVER_JOB_APPLY_PATH: LEVER_FIXTURE},
    )
    try:
        adapter = LeverAdapter()
        adapter.prepare_page(session.page)
        assert adapter.recognize(session.page) is True
        assert is_lever_job_detail_page(session.page) is False

        fields = adapter.discover_fields(session.page)
        full_name = _field(fields, "Full name")
        profile = _profile()
        classified = classify_field(full_name, profile)
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, full_name) == classified.value

        page = session.page
        assert page.evaluate("window.__submitClicked") is False
        assert page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()


def test_rejects_unrelated_lever_company_listing_page() -> None:
    """A Lever careers/listing page (no single job path) must never be treated
    as a job-detail page, even though it links to real postings.
    """
    session = _open_mocked_lever_url(
        "https://jobs.lever.co/qonto",
        {"/qonto": LEVER_COMPANY_LISTING_FIXTURE},
    )
    try:
        assert is_lever_job_detail_page(session.page) is False
        assert LeverAdapter().recognize(session.page) is False
    finally:
        session.close()


def test_rejects_wrong_job_apply_link() -> None:
    """An apply-shaped anchor pointing at a *different* posting-id must not be
    treated as this job's Apply action, and the adapter must fail closed.
    """
    session = _open_mocked_lever_url(
        LEVER_JOB_URL,
        {LEVER_JOB_PATH: LEVER_JOB_DETAIL_WRONG_JOB_FIXTURE},
    )
    try:
        adapter = LeverAdapter()
        assert is_lever_job_detail_page(session.page) is False
        adapter.prepare_page(session.page)
        assert adapter.recognize(session.page) is False
        with pytest.raises(LeverFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()


def test_rejects_wrong_job_legacy_apply_link() -> None:
    """A legacy `data-qa` apply anchor pointing at a *different* posting-id
    must be rejected the same way as the modern (no `data-qa`) shape -- the
    `data-qa` attribute is not itself proof the href is safe to follow.
    """
    session = _open_mocked_lever_url(
        LEVER_JOB_URL,
        {LEVER_JOB_PATH: LEVER_JOB_DETAIL_LEGACY_WRONG_JOB_FIXTURE},
    )
    try:
        adapter = LeverAdapter()
        assert is_lever_job_detail_page(session.page) is False
        adapter.prepare_page(session.page)
        assert session.page.url == LEVER_JOB_URL
        assert adapter.recognize(session.page) is False
        with pytest.raises(LeverFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()


def test_rejects_external_apply_link() -> None:
    """An apply-shaped anchor pointing off Lever's own host must never be
    followed, and the adapter must fail closed rather than navigate there.
    """
    session = _open_mocked_lever_url(
        LEVER_JOB_URL,
        {LEVER_JOB_PATH: LEVER_JOB_DETAIL_EXTERNAL_LINK_FIXTURE},
    )
    try:
        adapter = LeverAdapter()
        assert is_lever_job_detail_page(session.page) is False
        adapter.prepare_page(session.page)
        assert session.page.url == LEVER_JOB_URL
        assert adapter.recognize(session.page) is False
        with pytest.raises(LeverFormError, match="UNSUPPORTED_FORM"):
            adapter.discover_fields(session.page)
    finally:
        session.close()
