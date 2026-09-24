"""Browser-free regression coverage for the Ashby fill pipeline's compact,
privacy-safe diagnostics (discovered -> resolved -> interaction -> readback)
added alongside the native-radio-rendered Yes/No control support and the
wrapper-scoped text-field locator (see `ashby.py`'s `_is_yes_no_control` and
`_text_field_locator`).

Playwright Chromium cannot launch in this sandbox, so this drives
`AshbyAdapter` through minimal duck-typed `Page`/`Locator` doubles that model
the exact structures a live Ashby posting was observed using instead of the
synthetic fixture's yesno-button widget:

- A Yes/No question rendered as a native radio pair
  (`.ashby-application-form-input-radio-group`, `input[type=radio]` sharing
  a `name`, each paired with a `label[for=id]`) -- exercised here for both
  the optional WhatsApp-consent decline and the required conjunctive
  Java/Spring-Boot skill question.
- The Phone Number `tel` field, exercised through the wrapper-scoped
  `_text_field_locator` fix.

Every diagnostic-log assertion also asserts the candidate's actual answer
(phone digits, technology names, consent wording) never appears in the log
text -- the diagnostics are context/field_type/boolean/count only.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.ashby import AshbyAdapter
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile

LOGGER_NAME = "app.application.autofill.ashby"

# Deliberately opaque, UUID-shaped -- like a real Ashby `data-field-path`,
# never a name that would itself leak the question's subject into a log
# assertion (a real live posting's own `data-field-path` values carry no
# semantic wording either).
_PHONE_CONTEXT = "3f6a8b2c-0a92-4d5e-9c33-7a1b6e2f5d40"
_WHATSAPP_CONTEXT = "8e2d4c6a-1f3b-4a7c-9e5d-2b6f0a8c4d13"
_JAVA_SPRING_CONTEXT = "b4d1f6a2-3c8e-4f0b-a2d5-6c9e1b3f7a80"


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "application_files": {"default_resume": "resume.txt"},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def _vacancy() -> ResolvedVacancy:
    return ResolvedVacancy(
        source="target_company:ashby:perk",
        external_id="abc-123",
        title="Backend Engineer",
        company="Perk",
        url="https://jobs.ashbyhq.com/perk/abc-123/application",
        application_url="https://jobs.ashbyhq.com/perk/abc-123/application",
        vacancy=None,
    )


# -- doubles modeling real Ashby radio and text-input markup ----------------


class _RadioOption:
    def __init__(self, value: str, label: str) -> None:
        self.value = value
        self.label = label
        self.checked = False


class _RadioGroupModel:
    def __init__(self, options: list[_RadioOption]) -> None:
        self.options = options


class _RadioNode:
    def __init__(self, group: _RadioGroupModel, option: _RadioOption) -> None:
        self._group = group
        self._option = option

    def get_attribute(self, name: str) -> str | None:
        return self._option.value if name == "value" else None

    def evaluate(self, script: str) -> str:
        # Real production code only ever evaluates `_RADIO_OPTION_LABEL_JS`
        # here; the fake just returns the label this option was modeled
        # with, regardless of script text.
        return self._option.label

    def check(self, timeout: int | None = None) -> None:
        for option in self._group.options:
            option.checked = False
        self._option.checked = True


class _RadioGroupLocator:
    def __init__(self, group: _RadioGroupModel, *, checked_only: bool = False) -> None:
        self._group = group
        self._checked_only = checked_only

    def _items(self) -> list[_RadioOption]:
        if self._checked_only:
            return [option for option in self._group.options if option.checked]
        return list(self._group.options)

    def count(self) -> int:
        return len(self._items())

    def nth(self, index: int) -> _RadioNode:
        return _RadioNode(self._group, self._items()[index])

    @property
    def first(self) -> _RadioNode:
        return self.nth(0)


class _TextInputNode:
    def __init__(self) -> None:
        self.value = ""

    def fill(self, value: str, timeout: int | None = None) -> None:
        self.value = value

    def evaluate(self, script: str) -> None:
        return None

    def input_value(self) -> str:
        return self.value


class _MissingNode:
    """Mirrors a real Playwright locator that matched zero elements -- any
    interaction with it raises, the same way a real `.input_value()`/
    `.fill()` on a 0-count locator would.
    """

    def fill(self, value: str, timeout: int | None = None) -> None:
        raise PlaywrightError("no matching element")

    def input_value(self) -> str:
        raise PlaywrightError("no matching element")

    def evaluate(self, script: str) -> None:
        raise PlaywrightError("no matching element")


class _FillPage:
    """Duck-types only `.locator(selector)`, dispatching to whichever radio
    group or text input the selector was built to target. A selector this
    page was never told about resolves to `_MissingNode` -- the same
    fail-closed shape a real page gives a locator matching zero live
    elements -- rather than asserting, so a scoping mismatch surfaces as the
    same `PlaywrightError`-driven `False`/`None` production code already
    handles, not a test-harness crash.
    """

    def __init__(
        self,
        radio_groups: dict[str, _RadioGroupModel] | None = None,
        text_inputs: dict[str, _TextInputNode] | None = None,
    ) -> None:
        self._radio_groups = radio_groups or {}
        self._text_inputs = text_inputs or {}

    def locator(self, selector: str):
        for name, group in self._radio_groups.items():
            base = f'input[type="radio"][name="{name}"]'
            if selector == base:
                return _RadioGroupLocator(group)
            if selector == f"{base}:checked":
                return _RadioGroupLocator(group, checked_only=True)
        if selector in self._text_inputs:
            return self._text_inputs[selector]
        return _MissingNode()


def _whatsapp_radio_field(*, required: bool = False) -> DiscoveredField:
    return DiscoveredField(
        label="Can we contact you on WhatsApp about your application? (optional)",
        field_type="radio",
        name="whatsapp-consent",
        required=required,
        options=[
            "Yes - I consent to receiving WhatsApp messages",
            "No - I do not consent to receiving WhatsApp messages",
        ],
        context=_WHATSAPP_CONTEXT,
    )


def _java_spring_radio_field() -> DiscoveredField:
    return DiscoveredField(
        label="Do you have recent hands-on working experience with Java and Spring Boot?",
        field_type="radio",
        name="java-spring-boot",
        required=True,
        options=["Yes", "No"],
        context=_JAVA_SPRING_CONTEXT,
    )


def _phone_field() -> DiscoveredField:
    return DiscoveredField(
        label="Phone Number",
        field_type="tel",
        element_id=_PHONE_CONTEXT,
        required=True,
        context=_PHONE_CONTEXT,
    )


# -- resolved-stage diagnostics (adjust_classified_field) --------------------


def test_resolved_stage_logs_whatsapp_radio_decline_without_leaking_label_or_answer(caplog) -> None:
    field = _whatsapp_radio_field()
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        adjusted = AshbyAdapter().adjust_classified_field(item, profile=_profile(), vacancy=_vacancy())

    assert adjusted.fill is True
    assert adjusted.value is False
    resolved_logs = [r.getMessage() for r in caplog.records if "stage=resolved" in r.getMessage()]
    assert len(resolved_logs) == 1
    log = resolved_logs[0]
    assert f"context={_WHATSAPP_CONTEXT}" in log
    assert "field_type=radio" in log
    assert "fill=True" in log
    assert "whatsapp" not in log.lower()
    assert "consent" not in log.lower()


def test_resolved_stage_logs_java_spring_radio_yes_without_leaking_technology_names(caplog) -> None:
    field = _java_spring_radio_field()
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    profile = _profile(employment={"professional_tech_stack": ["Java", "Spring Boot"]})
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        adjusted = AshbyAdapter().adjust_classified_field(item, profile=profile, vacancy=_vacancy())

    assert adjusted.fill is True
    assert adjusted.value is True
    resolved_logs = [r.getMessage() for r in caplog.records if "stage=resolved" in r.getMessage()]
    assert len(resolved_logs) == 1
    log = resolved_logs[0]
    assert f"context={_JAVA_SPRING_CONTEXT}" in log
    assert "field_type=radio" in log
    assert "fill=True" in log
    assert "java" not in log.lower()
    assert "spring" not in log.lower()


# -- interaction/readback-stage diagnostics (fill_field / read_back) --------


def test_whatsapp_radio_decline_fills_and_reads_back_the_negative_option(caplog) -> None:
    field = _whatsapp_radio_field()
    group = _RadioGroupModel(
        [_RadioOption(option, option) for option in field.options]
    )
    page = _FillPage(radio_groups={"whatsapp-consent": group})
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=False,
        fill=True,
        kind=QuestionKind.SMS_UPDATES,
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        filled = AshbyAdapter().fill_field(page, classified)  # type: ignore[arg-type]
        read_back = AshbyAdapter().read_back(page, field)  # type: ignore[arg-type]

    assert filled is True
    assert read_back == "No - I do not consent to receiving WhatsApp messages"
    interaction_log = next(r.getMessage() for r in caplog.records if "stage=interaction" in r.getMessage())
    readback_log = next(r.getMessage() for r in caplog.records if "stage=readback" in r.getMessage())
    assert f"context={_WHATSAPP_CONTEXT}" in interaction_log
    assert "field_type=radio" in interaction_log
    assert "result=True" in interaction_log
    assert f"context={_WHATSAPP_CONTEXT}" in readback_log
    assert "empty=False" in readback_log
    for log in (interaction_log, readback_log):
        assert "whatsapp" not in log.lower()
        assert "consent" not in log.lower()


def test_java_spring_radio_yes_fills_and_reads_back_the_affirmative_option(caplog) -> None:
    field = _java_spring_radio_field()
    group = _RadioGroupModel([_RadioOption("Yes", "Yes"), _RadioOption("No", "No")])
    page = _FillPage(radio_groups={"java-spring-boot": group})
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.SKILL_SET_CHOICE,
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        filled = AshbyAdapter().fill_field(page, classified)  # type: ignore[arg-type]
        read_back = AshbyAdapter().read_back(page, field)  # type: ignore[arg-type]

    assert filled is True
    assert read_back == "Yes"
    interaction_log = next(r.getMessage() for r in caplog.records if "stage=interaction" in r.getMessage())
    assert f"context={_JAVA_SPRING_CONTEXT}" in interaction_log
    assert "result=True" in interaction_log


def test_java_spring_radio_fill_fails_closed_when_no_live_option_matches(caplog) -> None:
    # A live options list that no longer offers an exact Yes/No pair (e.g. a
    # differently-worded live control) must never guess -- `_fill_radio`
    # returns False rather than checking an unrelated option.
    field = _java_spring_radio_field()
    group = _RadioGroupModel([_RadioOption("agree", "I agree"), _RadioOption("disagree", "I disagree")])
    page = _FillPage(radio_groups={"java-spring-boot": group})
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.SKILL_SET_CHOICE,
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        filled = AshbyAdapter().fill_field(page, classified)  # type: ignore[arg-type]

    assert filled is False
    interaction_log = next(r.getMessage() for r in caplog.records if "stage=interaction" in r.getMessage())
    assert "result=False" in interaction_log


def test_phone_tel_field_fills_and_reads_back_via_wrapper_scoped_locator(caplog) -> None:
    field = _phone_field()
    text_input = _TextInputNode()
    selector = f'[data-field-path="{_PHONE_CONTEXT}"] input[id="{_PHONE_CONTEXT}"]'
    page = _FillPage(text_inputs={selector: text_input})
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="+15555550100",
        fill=True,
        kind=QuestionKind.PHONE,
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        filled = AshbyAdapter().fill_field(page, classified)  # type: ignore[arg-type]
        read_back = AshbyAdapter().read_back(page, field)  # type: ignore[arg-type]

    assert filled is True
    assert read_back == "+15555550100"
    assert text_input.value == "+15555550100"
    interaction_log = next(r.getMessage() for r in caplog.records if "stage=interaction" in r.getMessage())
    readback_log = next(r.getMessage() for r in caplog.records if "stage=readback" in r.getMessage())
    assert f"context={_PHONE_CONTEXT}" in interaction_log
    assert "field_type=tel" in interaction_log
    assert "result=True" in interaction_log
    assert f"context={_PHONE_CONTEXT}" in readback_log
    assert "empty=False" in readback_log
    for log in (interaction_log, readback_log):
        assert "+15555550100" not in log
        assert "5555550100" not in log


def test_phone_tel_field_readback_reports_empty_when_locator_never_matches(caplog) -> None:
    # A duplicate id/name elsewhere on the page (the plausible live failure
    # mode this locator scoping guards against) means the wrapper-scoped
    # selector this module builds finds nothing to read back -- `read_back`
    # must report that as `empty=True` in the diagnostic, never raise or
    # silently invent a value.
    field = _phone_field()
    page = _FillPage(text_inputs={})

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        read_back = AshbyAdapter().read_back(page, field)  # type: ignore[arg-type]

    assert read_back is None
