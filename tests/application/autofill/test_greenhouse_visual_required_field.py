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

FIXTURE = Path("tests/fixtures/autofill/greenhouse_visual_required_textarea.html")
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
            "application_files": {"default_resume": str(RESUME_FIXTURE)},
            "application_policy": {"relocation": {"willing": True}},
        }
    )


def _open() -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(FIXTURE)
    return session


def _field(fields: list[DiscoveredField], label_substring: str) -> DiscoveredField:
    needle = label_substring.lower()
    for item in fields:
        if needle in item.label.lower():
            return item
    raise AssertionError(f"Field not found: {label_substring} in {[item.label for item in fields]}")


def test_visually_marked_asterisk_textarea_is_discovered_as_required() -> None:
    """The control has no `required`/`aria-required` attribute -- only a
    visible trailing "*" in its label -- and must still be discovered as
    required. Regression for the visa/relocation elaboration textarea that
    was visibly required, empty, but never counted as unresolved-required.
    """
    session = _open()
    try:
        field = _field(GreenhouseAdapter().discover_fields(session.page), "visa and/or relocation support")
        assert field.field_type == "textarea"
        assert field.required is True
    finally:
        session.close()


def test_visually_required_elaboration_textarea_stays_unresolved_required_never_true() -> None:
    """Generic relocation willingness alone must never turn this combined
    visa+relocation free-text elaboration field into a filled "True" value.
    """
    session = _open()
    try:
        field = _field(GreenhouseAdapter().discover_fields(session.page), "visa and/or relocation support")
        classified = classify_field(field, _profile())
        assert classified.kind is QuestionKind.VISA_SPONSORSHIP
        assert classified.fill is False
        assert classified.value is None
        assert classified.classification is FieldClassification.UNKNOWN_REQUIRED
    finally:
        session.close()
