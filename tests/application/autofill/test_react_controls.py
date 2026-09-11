from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.phone import calling_code_occurs_once, extract_calling_code
from app.application.autofill.questions import QuestionKind
from app.application.autofill.service import AutofillService
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile

REACT_FIXTURE = Path("tests/fixtures/autofill/greenhouse_react_controls.html")
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
            "phone": "+998901234567",
            "current_location": "Tashkent, Uzbekistan",
            "country": "Uzbekistan",
        },
        "professional_links": {"website": "https://ada.example.test"},
        "employment": {
            "years_of_experience": 7,
            "highest_academic_level": "Master's",
            "postgraduate_studies_completed": True,
            "doctorate_awarded": False,
            "professional_tech_stack": ["Java", "Kotlin", "Spring Boot", "PostgreSQL"],
            "open_to_relocation": True,
        },
        "application_files": {"default_resume": str(RESUME_FIXTURE)},
        "employee_relationship": {"has_relationship": False},
        "application_policy": {
            "relocation": {"willing": True},
            "default_no_undeclared_affiliations": True,
            "prior_affiliations": [{"organization": "Deloitte", "associated": False}],
            "newsletter_opt_in": False,
            "sms_interview_updates": False,
            "prefer_not_to_disclose_gender": True,
            "application_source_preference": [
                "Company Website",
                "LinkedIn",
                "Other",
            ],
            "question_overrides": [
                {"question_contains": "engineering blog", "answer": True},
                {"question_contains": ["applied", "past 6 months"], "answer": False},
            ],
        },
        "sensitive": {"gender": "Female"},
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


def test_phone_widget_fills_national_number_without_duplicate_code() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        phone = _field(adapter.discover_fields(session.page), "Phone")
        classified = classify_field(phone, profile)
        assert classified.kind is QuestionKind.PHONE
        assert adapter.fill_field(session.page, classified) is True
        typed = session.page.locator("#phone").input_value()
        assert "998" not in typed
        assert typed.replace(" ", "") == "901234567"
        read_back = adapter.read_back(session.page, phone) or ""
        code = extract_calling_code(read_back)
        assert code == "998"
        assert calling_code_occurs_once(read_back, code)
        assert "901234567" in read_back.replace(" ", "")
        aria = session.page.locator(".iti__selected-country").get_attribute("aria-label") or ""
        assert "Uzbekistan" in aria
    finally:
        session.close()


def test_national_phone_is_filled_as_is_when_already_national() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile(
            identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "901234567",
                "country": "Uzbekistan",
            }
        )
        phone = _field(adapter.discover_fields(session.page), "Phone")
        classified = classify_field(phone, profile)
        assert adapter.fill_field(session.page, classified) is True
        typed = session.page.locator("#phone").input_value()
        assert typed.replace(" ", "") == "901234567"
        read_back = adapter.read_back(session.page, phone) or ""
        assert calling_code_occurs_once(read_back, "998")
    finally:
        session.close()


def test_react_multiselect_keeps_three_sequential_values() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        stack = classify_field(_field(adapter.discover_fields(session.page), "tech stack"), profile)
        assert stack.kind is QuestionKind.TECH_STACK
        assert stack.max_choices == 3
        assert stack.value == ["Java", "Kotlin", "Spring Boot", "PostgreSQL"]
        assert adapter.fill_field(session.page, stack) is True
        read_back = adapter.read_back(session.page, stack.field) or ""
        assert "Java" in read_back
        assert "Kotlin" in read_back
        assert "Spring Boot" in read_back
        chips = session.page.locator(".select__multi-value__label")
        assert chips.count() == 3
        texts = [chips.nth(i).inner_text() for i in range(chips.count())]
        assert "Javascript" not in texts
    finally:
        session.close()


def test_javascript_available_but_not_selected_and_chips_persist() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile(employment={"professional_tech_stack": ["Java", "Kotlin"]})
        stack = classify_field(_field(adapter.discover_fields(session.page), "tech stack"), profile)
        assert stack.value == ["Java", "Kotlin"]
        assert adapter.fill_field(session.page, stack) is True
        chips = session.page.locator(".select__multi-value__label")
        texts = [chips.nth(i).inner_text().strip() for i in range(chips.count())]
        assert texts == ["Java", "Kotlin"] or set(texts) == {"Java", "Kotlin"}
        assert "Javascript" not in texts
        read_back = adapter.read_back(session.page, stack.field) or ""
        assert "Java" in read_back
        assert "Kotlin" in read_back
        assert "Javascript" not in read_back
    finally:
        session.close()


def test_academic_react_select_persists_masters_degree_not_diploma() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile(
            employment={
                "highest_academic_level": "specialist",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": False,
                "professional_tech_stack": ["Java", "Kotlin"],
            }
        )
        academic = classify_field(
            _field(adapter.discover_fields(session.page), "highest academic level"),
            profile,
        )
        assert academic.kind is QuestionKind.ACADEMIC_LEVEL
        assert academic.value == "MASTERS"
        assert adapter.fill_field(session.page, academic) is True
        visible = session.page.locator("#academic-field .select__single-value").inner_text().strip()
        assert visible == "Master's Degree"
        assert visible != "Diploma"
        assert adapter.read_back(session.page, academic.field) == "Master's Degree"
    finally:
        session.close()


def test_academic_react_select_persists_awarded_doctorate() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile(
            employment={
                "highest_academic_level": "PhD",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": True,
                "professional_tech_stack": ["Java"],
            }
        )
        academic = classify_field(
            _field(adapter.discover_fields(session.page), "highest academic level"),
            profile,
        )
        assert academic.value == "DOCTORATE"
        assert adapter.fill_field(session.page, academic) is True
        visible = session.page.locator("#academic-field .select__single-value").inner_text().strip()
        assert visible == "Doctorate Degree"
        assert adapter.read_back(session.page, academic.field) == "Doctorate Degree"
    finally:
        session.close()


def test_gender_country_sms_newsletter_overrides_and_booking() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)

        gender = classify_field(_field(fields, "Gender"), profile)
        assert gender.fill is True
        assert adapter.fill_field(session.page, gender) is True
        assert adapter.read_back(session.page, gender.field) == "Prefer not to disclose"
        assert adapter.read_back(session.page, gender.field) != "Female"

        country = classify_field(_field(fields, "country/region are you currently based"), profile)
        assert country.kind is QuestionKind.COUNTRY
        assert adapter.fill_field(session.page, country) is True
        assert adapter.read_back(session.page, country.field) == "Uzbekistan"

        booking = classify_field(_field(fields, "Booking Holdings"), profile)
        assert booking.kind is QuestionKind.PRIOR_AFFILIATION
        assert adapter.fill_field(session.page, booking) is True
        assert adapter.read_back(session.page, booking.field) == "No"

        sms = classify_field(_field(fields, "TEXT/SMS"), profile)
        assert sms.kind is QuestionKind.SMS_UPDATES
        assert adapter.fill_field(session.page, sms) is True
        assert adapter.read_back(session.page, sms.field) == "No"

        newsletter = classify_field(_field(fields, "other job openings"), profile)
        assert newsletter.kind is QuestionKind.NEWSLETTER
        assert adapter.fill_field(session.page, newsletter) is True
        assert adapter.read_back(session.page, newsletter.field) == "No"

        blog = classify_field(_field(fields, "engineering blog"), profile)
        assert blog.kind is QuestionKind.QUESTION_OVERRIDE
        assert adapter.fill_field(session.page, blog) is True
        assert adapter.read_back(session.page, blog.field) == "Yes"

        applied = classify_field(_field(fields, "past 6 months"), profile)
        assert applied.kind is QuestionKind.QUESTION_OVERRIDE
        assert adapter.fill_field(session.page, applied) is True
        assert adapter.read_back(session.page, applied.field) == "No"
    finally:
        session.close()


def test_boolean_react_select_maps_false_to_visible_no() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        relocation = classify_field(_field(fields, "relocate to Bangkok"), profile)
        assert relocation.kind is QuestionKind.RELOCATION
        assert adapter.fill_field(session.page, relocation) is True
        relocation_label = session.page.locator("#relocation-field .select__single-value").inner_text()
        assert relocation_label.strip() == "Yes"
        assert adapter.read_back(session.page, relocation.field) == "Yes"

        relationship = classify_field(_field(fields, "personal relationship"), profile)
        assert relationship.value in {False, "No"}
        assert adapter.fill_field(session.page, relationship) is True
        relationship_label = session.page.locator("#relationship-field .select__single-value").inner_text()
        assert relationship_label.strip() == "No"
        assert adapter.read_back(session.page, relationship.field) == "No"
        assert session.page.locator("#relationship").input_value() != "false"
    finally:
        session.close()


def test_deloitte_and_source_and_inactive_conditionals() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        deloitte = classify_field(_field(fields, "associated with Deloitte"), profile)
        assert deloitte.kind is QuestionKind.PRIOR_AFFILIATION
        assert adapter.fill_field(session.page, deloitte) is True
        visible = adapter.read_back(session.page, deloitte.field) or ""
        assert visible.startswith("No")

        source = classify_field(_field(fields, "How did you hear"), profile)
        assert source.kind is QuestionKind.APPLICATION_SOURCE
        assert adapter.fill_field(session.page, source) is True
        assert adapter.read_back(session.page, source.field) == "Company Website"

        name = _field(fields, "employee's name")
        classified_name = classify_field(name, profile)
        assert classified_name.inactive_conditional is True
        assert classified_name.fill is False
        assert adapter.read_back(session.page, name) in {None, ""}
        assert session.page.evaluate("window.__submitClicked") is False
    finally:
        session.close()


def test_cover_letter_enter_manually_survives_readback() -> None:
    session = _open(REACT_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        text = "I am a Java backend engineer with around seven years of experience."
        assert adapter.fill_cover_letter(session.page, text) is True
        editor = session.page.locator("#cover_letter_text")
        assert editor.is_visible()
        assert editor.input_value() == text
        assert editor.get_attribute("data-react-value") == text
        cover = next(
            item
            for item in adapter.discover_fields(session.page)
            if item.field_type == "cover_letter" or "cover letter" in item.label.lower()
        )
        assert adapter.read_back(session.page, cover) == text
        assert session.page.locator("#resume_text").input_value() == ""
        assert session.page.locator("#resume_text").is_hidden()
        assert session.page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()


class _FixtureResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = REACT_FIXTURE.resolve().as_uri()
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Backend Engineer",
            company="Example Corp",
            url=url,
            application_url=url,
        )


class _FakeCoverLetter:
    def generate(self, vacancy: object, profile: CandidateProfile) -> str | None:
        _ = vacancy, profile
        return "I am a Java Backend Engineer with around seven years of experience developing services."


def test_service_reports_cover_letter_filled_and_skips_inactive_required() -> None:
    session_holder: dict[str, BrowserSession] = {}

    def factory() -> BrowserSession:
        session = BrowserSession(headed=False, keep_open=True)
        session_holder["session"] = session
        return session

    def wait() -> None:
        assert session_holder["session"].page.evaluate("window.__submitClicked") is False

    service = AutofillService(
        resolver=_FixtureResolver(),
        profile_loader=_profile,
        browser_factory=factory,
        wait_for_review=wait,
        cover_letter_provider=_FakeCoverLetter(),
    )
    result = service.run("target_company:greenhouse:agoda", "7044713", keep_open=True)
    assert result.submit_performed is False
    assert result.cover_letter_filled is True
    unresolved = [item.label.lower() for item in result.unresolved_required_fields]
    assert not any("employee's name" in label for label in unresolved)
    filled = [item.label.lower() for item in result.filled_fields]
    assert any("cover letter" in label for label in filled)
    assert any("tech stack" in label for label in filled)
    assert any("hear about" in label for label in filled)


def test_cover_letter_missing_enter_manually_is_reported() -> None:
    session = _open(Path("tests/fixtures/autofill/greenhouse_cover_letter_no_manual.html"))
    try:
        adapter = GreenhouseAdapter()
        text = "I am a Java backend engineer with around seven years of experience."
        assert adapter.fill_cover_letter(session.page, text) is False
        assert adapter.last_cover_letter_error is not None
        assert "Enter manually was not found" in adapter.last_cover_letter_error
    finally:
        session.close()


def test_cover_letter_react_clear_is_detected() -> None:
    session = _open(Path("tests/fixtures/autofill/greenhouse_cover_letter_clears.html"))
    try:
        adapter = GreenhouseAdapter()
        text = "I am a Java backend engineer with around seven years of experience."
        assert adapter.fill_cover_letter(session.page, text) is False
        assert adapter.last_cover_letter_error is not None
        assert "cleared the editor" in adapter.last_cover_letter_error.lower() or "did not persist" in adapter.last_cover_letter_error.lower()
        editor = session.page.locator("#cover_letter_text")
        assert editor.is_visible()
        assert text not in (editor.input_value() or "")
    finally:
        session.close()
