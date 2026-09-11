from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.questions import QuestionKind
from app.application.autofill.service import AutofillService
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile

CUSTOM_FIXTURE = Path("tests/fixtures/autofill/greenhouse_custom_questions.html")
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
            "professional_links": {
                "linkedin": "https://www.linkedin.com/in/ada-example-test",
                "github": "https://github.com/ada-example-test",
                "website": "https://ada.example.test",
            },
            "employment": {
                "years_of_experience": 7,
                "highest_academic_level": "Master's",
                "postgraduate_studies_completed": True,
                "doctorate_awarded": False,
                "professional_tech_stack": ["Java", "Kotlin", "Spring Boot", "PostgreSQL"],
                "preferred_fields_of_interest": ["Backend"],
                "open_to_relocation": True,
                "relocation_destinations": ["Bangkok", "Thailand"],
            },
            "employee_relationship": {"has_relationship": False},
            "application_policy": {"relocation": {"willing": True}},
            "application_consent": {"privacy_data_processing": True},
            "application_files": {"default_resume": str(RESUME_FIXTURE)},
        }
    )


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


def test_website_is_not_filled_from_github_when_website_missing() -> None:
    session = _open(CUSTOM_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        profile.professional_links.website = None
        website = _field(adapter.discover_fields(session.page), "Website / Blog / Other")
        classified = classify_field(website, profile)
        assert classified.kind is QuestionKind.WEBSITE
        assert classified.fill is False
        assert adapter.fill_field(session.page, classified) is False
        assert adapter.read_back(session.page, website) in {None, ""}
        github = _field(adapter.discover_fields(session.page), "Github")
        github_classified = classify_field(github, profile)
        assert github_classified.fill is True
        assert adapter.fill_field(session.page, github_classified) is True
        assert adapter.read_back(session.page, github) == profile.professional_links.github
        assert adapter.read_back(session.page, website) in {None, ""}
    finally:
        session.close()


def test_custom_select_multiselect_relocation_and_relationship() -> None:
    session = _open(CUSTOM_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        fields = adapter.discover_fields(session.page)
        years = classify_field(_field(fields, "overall years"), profile)
        assert years.fill is True
        assert adapter.fill_field(session.page, years) is True
        assert "6-8" in (adapter.read_back(session.page, years.field) or "")

        academic = classify_field(_field(fields, "Highest academic"), profile)
        assert academic.value == "MASTERS"
        assert adapter.fill_field(session.page, academic) is True
        assert adapter.read_back(session.page, academic.field) == "Master's Degree"

        stack = classify_field(_field(fields, "technologies"), profile)
        assert stack.kind is QuestionKind.TECH_STACK
        assert stack.max_choices == 3
        assert adapter.fill_field(session.page, stack) is True
        read_stack = adapter.read_back(session.page, stack.field) or ""
        assert "Java" in read_stack
        assert "Kotlin" in read_stack
        assert "Python" not in read_stack
        assert read_stack.count(",") <= 2

        interest = classify_field(_field(fields, "field of interest"), profile)
        assert adapter.fill_field(session.page, interest) is True
        assert adapter.read_back(session.page, interest.field) == "Backend"

        relocation = classify_field(_field(fields, "relocate to Bangkok"), profile)
        assert adapter.fill_field(session.page, relocation) is True
        assert adapter.read_back(session.page, relocation.field) == "Yes"

        relationship = classify_field(_field(fields, "personal relationship"), profile)
        assert adapter.fill_field(session.page, relationship) is True
        assert adapter.read_back(session.page, relationship.field) == "No"

        how = _field(fields, "how do you know")
        name = _field(fields, "employee's name")
        assert classify_field(how, profile).fill is False
        assert classify_field(name, profile).fill is False
        assert adapter.read_back(session.page, how) in {None, ""}
        assert adapter.read_back(session.page, name) in {None, ""}

        privacy = classify_field(_field(fields, "personal data"), profile)
        assert privacy.fill is True
        assert adapter.fill_field(session.page, privacy) is True
        assert adapter.read_back(session.page, privacy.field) == "true"
        assert session.page.evaluate("window.__submitClicked") is False
    finally:
        session.close()


def test_cover_letter_enter_manually_inserts_text_and_reads_back() -> None:
    session = _open(CUSTOM_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        text = "I am a Java backend engineer with around seven years of experience."
        assert adapter.fill_cover_letter(session.page, text) is True
        editor = session.page.locator("#cover_letter_text")
        assert editor.input_value() == text
        assert editor.get_attribute("data-react-value") == text
        cover = next(
            item
            for item in adapter.discover_fields(session.page)
            if item.field_type == "cover_letter" or "cover letter" in item.label.lower()
        )
        assert adapter.read_back(session.page, cover) == text
        assert session.page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()


class _FixtureResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = CUSTOM_FIXTURE.resolve().as_uri()
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Backend Engineer",
            company="Example Corp",
            url=url,
            application_url=url,
        )


class _FakeAnswers:
    def generate(self, field: DiscoveredField, profile: CandidateProfile, vacancy: object = None, **kwargs: object) -> str | None:
        _ = profile, vacancy, kwargs
        if "technical background" in field.label.lower():
            return "I have built production Java services."
        if "why do you want" in field.label.lower():
            return "The role matches my backend experience."
        return None


class _FakeCoverLetter:
    def generate(self, vacancy: object, profile: CandidateProfile) -> str | None:
        _ = vacancy, profile
        return "I am a Java Backend Engineer with around seven years of experience developing services."


def test_service_fills_generated_and_cover_letter_without_submit() -> None:
    session_holder: dict[str, BrowserSession] = {}

    def factory() -> BrowserSession:
        session = BrowserSession(headed=False, keep_open=True)
        session_holder["session"] = session
        return session

    def wait() -> None:
        page = session_holder["session"].page
        assert page.evaluate("window.__submitClicked") is False
        assert page.evaluate("window.__formSubmitted") is False
        assert "seven years" in page.locator("#cover_letter_text").input_value()

    service = AutofillService(
        resolver=_FixtureResolver(),
        profile_loader=_profile,
        browser_factory=factory,
        wait_for_review=wait,
        answer_generator=_FakeAnswers(),  # type: ignore[arg-type]
        cover_letter_provider=_FakeCoverLetter(),
    )
    result = service.run("target_company:greenhouse:agoda", "7044713", keep_open=True)
    assert result.submit_performed is False
    labels = [item.label.lower() for item in result.filled_fields]
    generated_labels = [item.label.lower() for item in result.generated_fields]
    unresolved = [
        item.label.lower()
        for item in result.unresolved_required_fields + result.unresolved_optional_fields
    ]
    assert any("cover letter" in label for label in labels)
    assert any("technical background" in label for label in labels)
    assert generated_labels
    assert any("salary" in label for label in unresolved)
