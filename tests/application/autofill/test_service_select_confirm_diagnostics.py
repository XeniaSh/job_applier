"""Focused coverage for the shared select fill-and-confirm boundary in
`app.application.autofill.service`:

- `_readback_matches` must allow the same fuzzy substring match for AGE that
  GENDER and RELOCATION already get -- AGE was added in the same change that
  introduced the age-decline select flow, but was never added to this
  allowlist, so a real-site read-back that isn't a byte-for-byte match (but
  is clearly the same option) would be wrongly treated as unconfirmed.
- `_fill_and_confirm`'s bounded diagnostic, logged only for the select kinds
  this covers, only as sanitized structural facts.

None of this touches a real browser: `_fill_and_confirm` only calls
`adapter.fill_field` / `adapter.read_back`, so a minimal fake adapter is
enough to exercise the real decision boundary the service uses.
"""

from __future__ import annotations

import logging

from app.application.autofill import service
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind

_LOGGER_NAME = "app.application.autofill.service"


class _FakeAdapter:
    """Only implements what `_fill_and_confirm` calls. Always reports the
    fill as attempted (`fill_field` -> True); the interesting behavior is
    driven entirely by the fixed `read_back` value, mirroring how a real
    adapter's `select_option` call can succeed without raising while the
    live DOM's actual selected option is what read-back later reports.
    """

    def __init__(self, read_back_value: str | None) -> None:
        self._read_back_value = read_back_value
        self.fill_calls = 0

    def fill_field(self, page: object, classified: ClassifiedField) -> bool:
        _ = page, classified
        self.fill_calls += 1
        return True

    def read_back(self, page: object, field: object) -> str | None:
        _ = page, field
        return self._read_back_value


def _age_field() -> DiscoveredField:
    return DiscoveredField(
        label="What's your age?",
        field_type="select",
        required=True,
        options=["Please select", "I don't wish to answer"],
    )


def _relocation_field() -> DiscoveredField:
    return DiscoveredField(
        label="Are you currently located in Berlin, Helsinki, or would you need to relocate?",
        field_type="select",
        required=True,
        options=[
            "Please select",
            "I'm already located in a hiring region",
            "I would need to relocate",
            "I'm looking for a remote job",
        ],
    )


def _gender_field() -> DiscoveredField:
    return DiscoveredField(
        label="Gender",
        field_type="select",
        required=True,
        options=["Please select", "Prefer not to disclose"],
    )


def _country_field() -> DiscoveredField:
    return DiscoveredField(
        label="Country*",
        field_type="combobox",
        required=True,
        options=["Uzbekistan +998"],
    )


def _country_item(value: str = "Uzbekistan") -> ClassifiedField:
    return ClassifiedField(
        field=_country_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.COUNTRY,
    )


def _school_field() -> DiscoveredField:
    return DiscoveredField(
        label="School *",
        field_type="combobox",
        required=True,
        options=["Aalto University"],
    )


def _school_item(value: str = "Aalto University") -> ClassifiedField:
    return ClassifiedField(
        field=_school_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.SCHOOL,
    )


def _privacy_field() -> DiscoveredField:
    return DiscoveredField(
        label="I acknowledge that my data will be processed as described in the Privacy Policy",
        field_type="select",
        required=True,
        options=[
            "Please select",
            "I understand that my personal data will be processed in accordance with the Privacy Policy",
        ],
    )


def _privacy_item(value: bool = True) -> ClassifiedField:
    return ClassifiedField(
        field=_privacy_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.PRIVACY_CONSENT,
    )


def test_readback_matches_allows_fuzzy_match_for_age_decline() -> None:
    field = _age_field()
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="I don't wish to answer",
        fill=True,
        kind=QuestionKind.AGE,
    )
    # Not a byte-for-byte match (trailing punctuation), but clearly the same
    # option -- previously excluded from the fuzzy-match kind allowlist even
    # though GENDER (its closest sibling) already got this treatment.
    assert service._readback_matches(item, "I don't wish to answer.") is True


def test_readback_matches_still_rejects_unrelated_age_readback() -> None:
    field = _age_field()
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="I don't wish to answer",
        fill=True,
        kind=QuestionKind.AGE,
    )
    assert service._readback_matches(item, "Please select") is False


def test_readback_matches_rejects_bare_dial_code_for_country() -> None:
    """A bare dialing code alone (e.g. "+998") must never confirm a COUNTRY
    fill on its own -- it carries no semantic country information and could
    just as easily belong to an unrelated phone-country-code widget. Only a
    read-back that actually names the requested country (e.g. "Uzbekistan
    +998") may confirm."""
    item = _country_item("Uzbekistan")
    assert service._readback_matches(item, "+998") is False


def test_readback_matches_accepts_semantic_country_with_dial_code_suffix() -> None:
    item = _country_item("Uzbekistan")
    assert service._readback_matches(item, "Uzbekistan +998") is True


def test_readback_matches_accepts_school_equivalent_modulo_formatting() -> None:
    item = _school_item("Aalto University")
    assert service._readback_matches(item, "aalto, university.") is True
    assert service._readback_matches(item, "  AALTO   UNIVERSITY  ") is True


def test_readback_matches_rejects_partial_prefix_or_unrelated_school_readback() -> None:
    """A readback that is only a substring/superset, a bare prefix, or a
    different institution entirely must never confirm the fill -- SCHOOL is
    deliberately never part of the fuzzy substring-match kind allowlist the
    other menu-choice kinds share.
    """
    item = _school_item("Aalto University")
    assert service._readback_matches(item, "Aalto University of Applied Sciences") is False
    assert service._readback_matches(item, "Aalto") is False
    assert service._readback_matches(item, "University of Helsinki") is False


def test_fill_and_confirm_confirms_age_decline_on_first_attempt_with_fuzzy_readback(
    caplog,
) -> None:
    field = _age_field()
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="I don't wish to answer",
        fill=True,
        kind=QuestionKind.AGE,
    )
    adapter = _FakeAdapter(read_back_value="I don't wish to answer.")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)
    assert confirmed is True
    assert adapter.fill_calls == 1
    assert not any(r.getMessage().startswith("select_choice_unconfirmed") for r in caplog.records)


def test_fill_and_confirm_logs_bounded_diagnostic_for_unconfirmed_relocation_select(
    caplog,
) -> None:
    field = _relocation_field()
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="I would need to relocate",
        fill=True,
        kind=QuestionKind.RELOCATION,
    )
    # The live select never advances past its placeholder -- the "stuck
    # select" symptom: fill_field keeps reporting success, but read-back
    # shows the option never actually committed.
    adapter = _FakeAdapter(read_back_value="Please select")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)

    assert confirmed is False
    assert adapter.fill_calls == 2

    diagnostics = [r.getMessage() for r in caplog.records if r.getMessage().startswith("select_choice_unconfirmed")]
    assert len(diagnostics) == 1
    message = diagnostics[0]
    assert "kind=relocation" in message
    assert "required=True" in message
    assert "option_count=4" in message
    assert "selected_index=0" in message
    assert "fill_reported_ok=True" in message
    assert "readback_checked=True" in message
    # Never the candidate's resolved value or any option text -- only the
    # field's own question label and structural facts.
    assert "I would need to relocate" not in message
    assert "hiring region" not in message
    assert "remote job" not in message


def test_fill_and_confirm_diagnostic_scoped_to_the_menu_choice_kinds(caplog) -> None:
    """Only the bounded set of menu-choice kinds gets this diagnostic --
    demonstrated here with a kind outside that set (ACADEMIC_LEVEL already
    has its own semantic-canonical comparator in `_readback_matches` and
    isn't part of this mechanism).
    """
    field = DiscoveredField(
        label="Highest level of education",
        field_type="select",
        required=True,
        options=["Please select", "Master's Degree"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="MASTERS",
        fill=True,
        kind=QuestionKind.ACADEMIC_LEVEL,
    )
    adapter = _FakeAdapter(read_back_value="Please select")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)

    assert confirmed is False
    assert not any(r.getMessage().startswith("select_choice_unconfirmed") for r in caplog.records)


def test_fill_and_confirm_logs_bounded_diagnostic_for_unconfirmed_gender_select(caplog) -> None:
    """GENDER was originally left out of `_SELECT_CHOICE_DIAGNOSTIC_KINDS`
    on the (mistaken) assumption that it "already works". In fact its known
    failure mode -- a discovery-time placeholder value that never matches
    the live-selected option's real wording -- is fixed separately by
    `_with_confirmed_choice` (see the fake-locator coverage in
    test_react_controls_custom_select_fake_dom.py), but that fix cannot
    rule out other real-site React-select DOM shapes this module cannot
    observe directly. GENDER is included here as a safety net so any
    remaining read-back failure for it is now visible too.
    """
    field = _gender_field()
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Prefer not to disclose",
        fill=True,
        kind=QuestionKind.GENDER,
    )
    adapter = _FakeAdapter(read_back_value="Please select")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)

    assert confirmed is False
    diagnostics = [r.getMessage() for r in caplog.records if r.getMessage().startswith("select_choice_unconfirmed")]
    assert len(diagnostics) == 1
    message = diagnostics[0]
    assert "kind=gender" in message
    assert "control_tag=unknown" in message
    assert "branch=unknown" in message
    # Never the candidate's resolved value or any option text.
    assert "Prefer not to disclose" not in message


def test_readback_matches_confirms_privacy_ack_via_i_understand_with_privacy_cue() -> None:
    item = _privacy_item()
    # A required privacy acknowledgement resolved to boolean True has no
    # native option text at discovery time; the live custom React-select can
    # read back its actual "I understand ..." wording instead of a literal
    # yes/acknowledge/confirm string.
    assert (
        service._readback_matches(
            item,
            "I understand that my personal data will be processed in accordance with the Privacy Policy",
        )
        is True
    )


def test_readback_matches_rejects_unrelated_i_understand_text_for_privacy_ack() -> None:
    item = _privacy_item()
    # "I understand" alone is not a privacy acknowledgement -- it must carry
    # a privacy/data-processing cue, otherwise an unrelated confirmation
    # (e.g. acknowledging job requirements) would be wrongly accepted.
    assert service._readback_matches(item, "I understand the job responsibilities") is False


def test_fill_and_confirm_confirms_privacy_ack_on_first_attempt_with_i_understand_readback(
    caplog,
) -> None:
    item = _privacy_item()
    adapter = _FakeAdapter(
        read_back_value=(
            "I understand that my personal data will be processed in accordance with the Privacy Policy"
        )
    )
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)
    assert confirmed is True
    assert adapter.fill_calls == 1
    assert not any(r.getMessage().startswith("select_choice_unconfirmed") for r in caplog.records)


def test_fill_and_confirm_logs_bounded_diagnostic_when_privacy_ack_readback_is_unrelated(
    caplog,
) -> None:
    item = _privacy_item()
    adapter = _FakeAdapter(read_back_value="Please select")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, object(), item)

    assert confirmed is False
    diagnostics = [r.getMessage() for r in caplog.records if r.getMessage().startswith("select_choice_unconfirmed")]
    assert len(diagnostics) == 1
    message = diagnostics[0]
    assert "kind=privacy_consent" in message
    # Never the candidate's resolved value or any option text.
    assert "I understand" not in message
