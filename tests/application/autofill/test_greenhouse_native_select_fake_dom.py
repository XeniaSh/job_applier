"""Deterministic (non-browser) coverage for GreenhouseAdapter's native
<select> fill_field + read_back path.

The Playwright-backed fixtures in test_greenhouse_wolt_selects.py exercise
the same two question shapes end to end, but require a real Chromium
process and are skipped when one isn't available in the sandbox. This file
fakes only the Playwright `Locator`/`Page` surface the adapter's native
select code (`_fill_select`, `react_controls.read_selected_label`,
`react_controls.read_selected_chips`) actually calls -- `evaluate`,
`select_option`, `get_attribute`, `input_value` -- so the same fill/read
behavior is verified without a browser, and without mocking the adapter
methods themselves.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill import service
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.candidate_profile import CandidateProfile

_PRIVACY_ACK_OPTION = (
    "I understand that my personal data will be processed in accordance "
    "with Wolt’s recruitment privacy statement."
)


class _FakeSelectLocator:
    """Models a native HTML <select> element well enough for the adapter's
    select_option/evaluate/input_value calls, tracking a selected index the
    way a real DOM select would.
    """

    def __init__(self, options: list[str]) -> None:
        self.options = list(options)
        self.selected_index = 0

    def select_option(self, *, label: str | None = None, value: str | None = None, timeout: int | None = None) -> None:
        _ = timeout
        wanted = label if label is not None else value
        for index, text in enumerate(self.options):
            if text == wanted:
                self.selected_index = index
                return
        raise PlaywrightError(f"No option matching {wanted!r}")

    def input_value(self, timeout: int | None = None) -> str:
        _ = timeout
        return self.options[self.selected_index]

    def evaluate(self, script: str, arg: object = None) -> object:
        _ = arg
        if "selectedOptions" in script:
            text = self.options[self.selected_index]
            return [text] if text else []
        if "tagName.toLowerCase() === 'select'" in script:
            return True
        if "el.tagName.toLowerCase()" in script:
            return "select"
        if "selectedIndex" in script:
            return self.options[self.selected_index]
        if "el.options" in script and "textContent" in script:
            return list(self.options)
        return None

    def get_attribute(self, name: str) -> str | None:
        return None

    def click(self, timeout: int | None = None, force: bool = False) -> None:
        _ = timeout, force

    def is_visible(self) -> bool:
        return True

    def locator(self, selector: str) -> "_EmptyLocator":
        _ = selector
        return _EmptyLocator()


class _StuckSelectLocator(_FakeSelectLocator):
    """Models the live-site symptom where Playwright's `select_option` call
    completes without raising -- so the adapter believes the interaction
    succeeded -- but the underlying DOM never commits the new selection
    (e.g. a framework-controlled select that resets itself). `selected_index`
    intentionally never advances past the placeholder.
    """

    def select_option(self, *, label: str | None = None, value: str | None = None, timeout: int | None = None) -> None:
        _ = label, value, timeout


class _EmptyLocator:
    """Stands in for an ancestor lookup that must find nothing, matching a
    real Playwright Locator with zero matches.
    """

    def count(self) -> int:
        return 0


class _FakePage:
    """Only implements `.locator(selector)` for the `[id="..."]` selectors
    `GreenhouseAdapter._field_locator` builds from a `DiscoveredField`'s
    `element_id` -- the only page-level call the fill/read paths under test
    make.
    """

    def __init__(self, locators_by_id: dict[str, _FakeSelectLocator]) -> None:
        self._locators_by_id = locators_by_id

    def locator(self, selector: str) -> _FakeSelectLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if not match or match.group(1) not in self._locators_by_id:
            raise AssertionError(f"Unexpected locator lookup in fake page: {selector!r}")
        return self._locators_by_id[match.group(1)]


def _profile(**identity_overrides: object) -> CandidateProfile:
    identity: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": "+15555550100",
    }
    identity.update(identity_overrides)
    return CandidateProfile.model_validate(
        {
            "identity": identity,
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def _profile_willing_to_relocate(**identity_overrides: object) -> CandidateProfile:
    identity: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": "+15555550100",
    }
    identity.update(identity_overrides)
    return CandidateProfile.model_validate(
        {
            "identity": identity,
            "application_policy": {"relocation": {"willing": True}},
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def test_wolt_privacy_select_fills_and_reads_back_via_fake_native_select() -> None:
    field = DiscoveredField(
        label="Wolt Recruitment Privacy Statement",
        name="job_application_answers_attributes_0_text_value",
        field_type="select",
        options=["Please select", _PRIVACY_ACK_OPTION],
        required=True,
        element_id="wolt_privacy",
    )
    classified = classify_field(field, _profile())
    assert classified.kind is QuestionKind.PRIVACY_CONSENT
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
    assert classified.fill is True

    locator = _FakeSelectLocator(field.options)
    page = _FakePage({"wolt_privacy": locator})
    adapter = GreenhouseAdapter()

    assert adapter.fill_field(page, classified) is True
    assert locator.selected_index == field.options.index(_PRIVACY_ACK_OPTION)
    readback = adapter.read_back(page, field)
    assert readback is not None
    assert "personal data" in readback.lower()


def test_wolt_relocation_select_already_located_via_fake_native_select() -> None:
    options = [
        "Please select",
        "I'm already located in a hiring region",
        "I would need to relocate",
        "I'm looking for a remote job",
    ]
    field = DiscoveredField(
        label="Are you currently located in Helsinki, or would you need to relocate?",
        name="job_application_answers_attributes_1_text_value",
        field_type="select",
        options=options,
        required=True,
        element_id="wolt_relocate_single",
    )
    classified = classify_field(
        field,
        _profile(current_location="Helsinki, Finland", country="Finland"),
    )
    assert classified.kind is QuestionKind.RELOCATION
    assert classified.fill is True
    assert classified.value == "I'm already located in a hiring region"

    locator = _FakeSelectLocator(options)
    page = _FakePage({"wolt_relocate_single": locator})
    adapter = GreenhouseAdapter()

    assert adapter.fill_field(page, classified) is True
    readback = adapter.read_back(page, field)
    assert readback is not None
    assert "already located" in readback.lower()


def test_wolt_relocation_select_multi_city_would_relocate_via_fake_native_select() -> None:
    options = [
        "Please select",
        "I'm already located in a hiring region",
        "I would need to relocate",
        "I'm looking for a remote job",
    ]
    field = DiscoveredField(
        label="Are you currently located in Helsinki or Stockholm or would you need to relocate?",
        name="job_application_answers_attributes_2_text_value",
        field_type="select",
        options=options,
        required=True,
        element_id="wolt_relocate_multi",
    )
    classified = classify_field(
        field,
        _profile_willing_to_relocate(current_location="Berlin, Germany", country="Germany"),
    )
    assert classified.kind is QuestionKind.RELOCATION
    assert classified.fill is True
    assert classified.value == "I would need to relocate"

    locator = _FakeSelectLocator(options)
    page = _FakePage({"wolt_relocate_multi": locator})
    adapter = GreenhouseAdapter()

    assert adapter.fill_field(page, classified) is True
    readback = adapter.read_back(page, field)
    assert readback is not None
    assert "need to relocate" in readback.lower()


def test_wolt_relocation_select_multi_city_unresolved_without_explicit_residence() -> None:
    """Relocation willingness alone cannot answer "are you located in X or Y":

    without a known current residence, `resides_in_any` returns None, so the
    field must stay unresolved rather than guessing "would relocate".
    """
    options = [
        "Please select",
        "I'm already located in a hiring region",
        "I would need to relocate",
        "I'm looking for a remote job",
    ]
    field = DiscoveredField(
        label="Are you currently located in Helsinki or Stockholm or would you need to relocate?",
        name="job_application_answers_attributes_2_text_value",
        field_type="select",
        options=options,
        required=True,
        element_id="wolt_relocate_multi",
    )
    classified = classify_field(field, _profile_willing_to_relocate())
    assert classified.kind is QuestionKind.RELOCATION
    assert classified.fill is False
    assert classified.value is None


def test_wolt_relocation_select_stuck_value_fails_confirmation_via_fill_and_confirm() -> None:
    """Regression for the live-site symptom where Playwright's
    `select_option` call completes without raising, `_fill_select`'s second
    (unverified) attempt reports success, yet the underlying DOM never
    commits the new selection. `_fill_and_confirm` must not trust that
    reported success: it re-reads the control and requires the read-back to
    actually match, so a required field whose selection never persists must
    come back unresolved rather than silently "filled".
    """
    options = [
        "Please select",
        "I'm already located in a hiring region",
        "I would need to relocate",
        "I'm looking for a remote job",
    ]
    field = DiscoveredField(
        label="Are you currently located in Helsinki, or would you need to relocate?",
        name="job_application_answers_attributes_1_text_value",
        field_type="select",
        options=options,
        required=True,
        element_id="wolt_relocate_single",
    )
    classified = classify_field(
        field,
        _profile(current_location="Helsinki, Finland", country="Finland"),
    )
    assert classified.kind is QuestionKind.RELOCATION
    assert classified.fill is True
    assert classified.value == "I'm already located in a hiring region"

    locator = _StuckSelectLocator(options)
    page = _FakePage({"wolt_relocate_single": locator})
    adapter = GreenhouseAdapter()

    assert adapter.fill_field(page, classified) is True
    assert locator.selected_index == 0

    assert service._fill_and_confirm(adapter, page, classified) is False
    assert locator.selected_index == 0
