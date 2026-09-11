from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

GREENHOUSE_FIXTURE = Path("tests/fixtures/autofill/greenhouse_application.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)


class _FixtureResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = GREENHOUSE_FIXTURE.resolve().as_uri()
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Backend Engineer",
            company="Example Corp",
            url=url,
            application_url=url,
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
    )


def test_greenhouse_fixture_end_to_end() -> None:
    session_holder: dict[str, BrowserSession] = {}

    def factory() -> BrowserSession:
        session = BrowserSession(headed=False, keep_open=True)
        session_holder["session"] = session
        return session

    def wait() -> None:
        page = session_holder["session"].page
        assert page.evaluate("window.__submitClicked") is False
        assert page.evaluate("window.__formSubmitted") is False
        selected = page.locator("#gender option:checked").inner_text().strip()
        assert selected == "Prefer not to disclose"

    service = AutofillService(
        resolver=_FixtureResolver(),
        profile_loader=_profile,
        browser_factory=factory,
        wait_for_review=wait,
    )
    result = service.run("target_company:greenhouse:agoda", "6886113", keep_open=True)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert result.resume_uploaded is True
    filled_labels = [item.label.lower() for item in result.filled_fields]
    assert any("first name" in label for label in filled_labels)
    assert any("gender" in item.label.lower() for item in result.filled_fields)
    assert any("favorite ide" in item.label.lower() for item in result.unresolved_required_fields)
    assert all("gender" not in item.label.lower() for item in result.sensitive_fields)
