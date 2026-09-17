from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.models import AutofillStatus
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

NATIVE_YEARS_FIXTURE = Path("tests/fixtures/autofill/greenhouse_years_native_input.html")
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
            },
            "employment": {
                "professional_tech_stack": ["Kotlin"],
                "technology_years": [{"technology": "Kotlin", "years": 2}],
            },
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


def test_native_number_years_field_fills_and_reads_back() -> None:
    session = _open(NATIVE_YEARS_FIXTURE)
    try:
        adapter = GreenhouseAdapter()
        profile = _profile()
        field = _field(adapter.discover_fields(session.page), "Kotlin")
        assert field.field_type == "number"
        classified = classify_field(field, profile)
        assert classified.kind is QuestionKind.YEARS_EXPERIENCE
        assert classified.value == "2"
        assert classified.fill is True
        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, field) == "2"
    finally:
        session.close()


class _FixtureResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = NATIVE_YEARS_FIXTURE.resolve().as_uri()
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Backend Engineer",
            company="Example Corp",
            url=url,
            application_url=url,
        )


def test_service_fills_native_years_field_with_no_warning_or_unresolved() -> None:
    service = AutofillService(
        resolver=_FixtureResolver(),
        profile_loader=_profile,
        browser_factory=lambda: BrowserSession(headed=False, keep_open=False),
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any("kotlin" in item.label.lower() for item in result.filled_fields)
    assert result.unresolved_required_fields == []
    assert not any("read-back failed" in warning.lower() for warning in result.warnings)
