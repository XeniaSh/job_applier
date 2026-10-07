"""Browser-free coverage for `LeverAdapter.adjust_classified_field`'s
handling of the application-location select
(`<select name="opportunityLocationId">`, "Which location are you applying
for?").

Live regression: on Binance's Lever posting
(4ad6cbc1-8919-43c9-9221-6fc616305347), the shared classifier's generic
"location" wording match (`questions._is_identity_location`) mapped this
control to `profile.identity.current_location` -- a candidate-residence
fact absent from every real option (the real options are this posting's
own offices, e.g. "Taiwan, Taipei"). `LeverAdapter._fill_select`'s two
un-timed `select_option` attempts would then each stall against
Playwright's default 30s timeout.

These tests exercise the real `classify_field` / `LeverAdapter.
adjust_classified_field` / `LeverAdapter.fill_field` / `LeverAdapter.
read_back` code paths against fake Playwright doubles -- no browser, no
mocking of the adapter methods under test -- and prove: (1) the shared
classifier alone does misclassify this control from current residence;
(2) `adjust_classified_field` forces that case to `UNSUPPORTED` so
`AutofillService`'s existing unsupported-field handling would skip it
before any fill/retry and move on to the next field (reaching
`READY_FOR_REVIEW`/manual review, never a Playwright wait); (3) an
explicit, exact-matching `question_overrides` answer fills and reads back
through the real native `select_option`/readback path, with a bounded
secondary timeout applied only to this one control; (4) partial,
placeholder, ambiguous-casing-only, and boolean overrides all fail closed
the same way; (5) an unrelated Lever select is unaffected and keeps
Playwright's own default (untimed) behavior.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile

_OPTIONS = [
    "Select...",
    "Asia",
    "UAE, Dubai",
    "Taiwan, Taipei",
    "Japan, Tokyo",
    "South East Asia",
    "Argentina, Buenos Aires",
    "Latin America",
    "Mexico, Mexico City",
]

_VACANCY = ResolvedVacancy(
    source="target_company:lever:binance",
    external_id="4ad6cbc1-8919-43c9-9221-6fc616305347",
    title="Backend Engineer",
    company="Binance",
    url="https://jobs.lever.co/binance/4ad6cbc1-8919-43c9-9221-6fc616305347",
    application_url="https://jobs.lever.co/binance/4ad6cbc1-8919-43c9-9221-6fc616305347/apply",
)


def _location_field(**overrides: object) -> DiscoveredField:
    payload: dict[str, object] = {
        "label": "Which location are you applying for?",
        "name": "opportunityLocationId",
        "field_type": "select",
        "options": _OPTIONS,
        "required": False,
    }
    payload.update(overrides)
    return DiscoveredField(**payload)


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
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def _adjusted(field: DiscoveredField, profile: CandidateProfile):
    classified = classify_field(field, profile)
    return classified, LeverAdapter().adjust_classified_field(classified, profile=profile, vacancy=_VACANCY)


class _UntouchedPage:
    """Raises on any attribute access, proving `fill_field` never resolves a
    locator or otherwise touches the page -- the only way to structurally
    guarantee no Playwright wait (and so no 30s stall) can occur.
    """

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"fill_field touched page.{name}")


def test_shared_classifier_alone_misclassifies_from_current_residence() -> None:
    """Proves the regression this guard exists for: without the Lever
    adjustment, the shared classifier's generic "location" wording match
    fills this control from `profile.identity.current_location`, a value
    absent from every real option.
    """
    classified = classify_field(_location_field(), _profile())
    assert classified.kind is QuestionKind.LOCATION
    assert classified.classification is FieldClassification.SUPPORTED_DETERMINISTIC
    assert classified.fill is True
    assert classified.value == "Berlin, Germany"


def test_without_override_adjustment_is_unsupported_and_fill_never_touches_page() -> None:
    classified, adjusted = _adjusted(_location_field(), _profile())
    assert classified.fill is True  # the shared-classifier misclassification above
    assert adjusted.classification is FieldClassification.UNSUPPORTED
    assert adjusted.fill is False
    assert adjusted.value is None

    adapter = LeverAdapter()
    assert adapter.fill_field(_UntouchedPage(), adjusted) is False


class _FakeSelectLocator:
    """Models a native HTML <select> well enough for `_fill_select`/
    `read_back`, recording the `timeout` Playwright actually received on
    each `select_option` call so the bounded secondary timeout can be
    verified without a browser.
    """

    def __init__(self, options: list[str]) -> None:
        self.options = list(options)
        self.selected_index = 0
        self.select_option_timeouts: list[int | None] = []

    def select_option(
        self, *, label: str | None = None, value: str | None = None, timeout: int | None = None
    ) -> None:
        self.select_option_timeouts.append(timeout)
        wanted = label if label is not None else value
        for index, text in enumerate(self.options):
            if text == wanted:
                self.selected_index = index
                return
        raise PlaywrightError(f"No option matching {wanted!r}")

    def evaluate(self, script: str, arg: object = None) -> object:
        _ = arg
        if "selectedIndex" in script:
            return self.options[self.selected_index]
        return None

    def get_attribute(self, name: str) -> str | None:
        return None


class _FakePage:
    """Resolve the native select's name selector, as in the live form."""

    def __init__(self, locators_by_selector: dict[str, _FakeSelectLocator]) -> None:
        self._locators_by_selector = locators_by_selector

    def locator(self, selector: str) -> _FakeSelectLocator:
        return self._locators_by_selector[selector]


def test_exact_override_match_fills_and_reads_back_with_bounded_secondary_timeout() -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["which location are you applying"], "answer": "Taiwan, Taipei"},
            ],
        }
    )
    field = _location_field()
    _classified, adjusted = _adjusted(field, profile)
    assert adjusted.classification is FieldClassification.SUPPORTED_DETERMINISTIC
    assert adjusted.fill is True
    assert adjusted.value == "Taiwan, Taipei"

    locator = _FakeSelectLocator(_OPTIONS)
    page = _FakePage({'[name="opportunityLocationId"]': locator})
    adapter = LeverAdapter()

    assert adapter.fill_field(page, adjusted) is True
    assert locator.selected_index == _OPTIONS.index("Taiwan, Taipei")
    assert locator.select_option_timeouts == [5_000]
    assert adapter.read_back(page, field) == "Taiwan, Taipei"


def test_override_match_is_case_and_punctuation_insensitive() -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["which location are you applying"], "answer": "  argentina,   buenos-aires "},
            ],
        }
    )
    _classified, adjusted = _adjusted(_location_field(), profile)
    assert adjusted.fill is True
    assert adjusted.value == "Argentina, Buenos Aires"


@pytest.mark.parametrize(
    "answer",
    [
        "Taipei",  # partial/substring of a real option -- fuzzy matching must never apply here
        "Remote",  # not among the discovered options at all
        "Select...",  # the control's own placeholder, never a real choice
        True,  # a bare boolean override is not a location label
    ],
)
def test_invalid_override_fails_closed_and_fill_never_touches_page(answer: object) -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["which location are you applying"], "answer": answer},
            ],
        }
    )
    _classified, adjusted = _adjusted(_location_field(), profile)
    assert adjusted.classification is FieldClassification.UNSUPPORTED
    assert adjusted.fill is False
    assert adjusted.value is None

    adapter = LeverAdapter()
    assert adapter.fill_field(_UntouchedPage(), adjusted) is False


def test_ambiguous_override_match_fails_closed() -> None:
    """An override that normalizes equal to more than one real option must
    never pick one arbitrarily."""
    field = _location_field(options=["Select...", "Buenos Aires", "buenos-aires"])
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["which location are you applying"], "answer": "Buenos Aires"},
            ],
        }
    )
    _classified, adjusted = _adjusted(field, profile)
    assert adjusted.classification is FieldClassification.UNSUPPORTED
    assert adjusted.fill is False
    assert adjusted.value is None


def test_ambiguous_identical_label_duplicate_options_fail_closed() -> None:
    """Two distinct native `<option>` elements that happen to render the same
    label text (e.g. two different offices both posted as "Buenos Aires")
    must count as two matches, not one -- an ambiguous choice must never be
    accepted just because the labels are identical strings.
    """
    field = _location_field(options=["Select...", "Buenos Aires", "Buenos Aires"])
    profile = _profile(
        application_policy={
            "question_overrides": [
                {"question_contains": ["which location are you applying"], "answer": "Buenos Aires"},
            ],
        }
    )
    _classified, adjusted = _adjusted(field, profile)
    assert adjusted.classification is FieldClassification.UNSUPPORTED
    assert adjusted.fill is False
    assert adjusted.value is None


def test_unrelated_lever_select_is_unaffected_by_adjustment() -> None:
    """A different Lever select (here, the stock SMS-updates opt-in
    question) must pass through `adjust_classified_field` unchanged and
    keep Playwright's own default (untimed) `select_option` behavior.
    """
    field = DiscoveredField(
        label="Do you allow us to provide you SMS updates of your interview process?",
        name="smsUpdates",
        element_id="smsUpdates",
        field_type="select",
        options=["Select...", "Yes", "No"],
        required=False,
    )
    classified, adjusted = _adjusted(field, _profile())
    assert adjusted is classified
    assert adjusted.kind is QuestionKind.SMS_UPDATES
    assert adjusted.fill is True

    locator = _FakeSelectLocator(field.options)
    page = _FakePage({'[id="smsUpdates"]': locator})
    adapter = LeverAdapter()

    assert adapter.fill_field(page, adjusted) is True
    assert locator.select_option_timeouts == [None]
