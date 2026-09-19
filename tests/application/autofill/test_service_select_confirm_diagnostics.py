"""Focused coverage for the shared select fill-and-confirm boundary in
`app.application.autofill.service`:

- `_readback_matches` must allow the same fuzzy substring match for AGE that
  GENDER and RELOCATION already get -- AGE was added in the same change that
  introduced the age-decline select flow, but was never added to this
  allowlist, so a real-site read-back that isn't a byte-for-byte match (but
  is clearly the same option) would be wrongly treated as unconfirmed.
- `_fill_and_confirm`'s bounded diagnostic, logged only for the three select
  kinds this covers, only as sanitized structural facts.

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


def test_fill_and_confirm_diagnostic_scoped_to_the_three_select_kinds(caplog) -> None:
    """GENDER already works and is out of scope for this fix -- the bounded
    diagnostic must not fire for it, to keep the new logging targeted.
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
    assert not any(r.getMessage().startswith("select_choice_unconfirmed") for r in caplog.records)
