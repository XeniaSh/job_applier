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

FIXTURE = Path("tests/fixtures/autofill/greenhouse_wolt_selects.html")
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
        "application_files": {"default_resume": str(RESUME_FIXTURE)},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


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


def test_wolt_privacy_select_is_required_acknowledgement_filled_and_read_back() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "Wolt Recruitment Privacy Statement")
        assert field.required is True
        classified = classify_field(field, _profile())
        assert classified.kind is QuestionKind.PRIVACY_CONSENT
        assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
        assert classified.fill is True
        assert adapter.fill_field(session.page, classified) is True
        readback = adapter.read_back(session.page, classified.field)
        assert readback is not None
        assert "personal data" in readback.lower()
    finally:
        session.close()

def test_wolt_relocation_select_already_located_when_residence_matches() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "located in Helsinki, or would you need to relocate")
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "Helsinki, Finland",
                "country": "Finland",
            }),
        )
        assert classified.kind is QuestionKind.RELOCATION
        assert classified.fill is True
        assert classified.value == "I'm already located in a hiring region"
        assert adapter.fill_field(session.page, classified) is True
        readback = adapter.read_back(session.page, classified.field)
        assert readback is not None
        assert "already located" in readback.lower()
    finally:
        session.close()


def test_wolt_relocation_select_multi_city_would_relocate_when_willing() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "located in Helsinki or Stockholm")
        classified = classify_field(
            field,
            _profile(application_policy={"relocation": {"willing": True}}),
        )
        assert classified.kind is QuestionKind.RELOCATION
        assert classified.fill is True
        assert classified.value == "I would need to relocate"
        assert adapter.fill_field(session.page, classified) is True
        readback = adapter.read_back(session.page, classified.field)
        assert readback is not None
        assert "need to relocate" in readback.lower()
    finally:
        session.close()
