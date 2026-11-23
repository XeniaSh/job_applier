from __future__ import annotations

from dataclasses import dataclass

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.phone import international_phone_digits
from app.application.autofill.questions import QuestionKind, QuestionValue, map_question
from app.application.candidate_profile import CandidateProfile

_UNSUPPORTED_TYPES = frozenset({"signature", "captcha", "unknown"})

# Greenhouse's phone-widget dialing-code country picker
# (`greenhouse._PHONE_DIAL_COUNTRY_FIELD_TYPE`, discovered only via its
# evidenced `.phone-input__country` structural nesting) is never the
# residence/citizenship/work-country question `QuestionKind.COUNTRY` maps
# to -- no residence/citizenship/location profile data can ever resolve it,
# and it is kept out of `map_question` entirely rather than risk it ever
# being matched by a country-shaped kind. It is driven only by the
# configured international phone number (`QuestionKind.PHONE_DIAL_COUNTRY`);
# without one (missing/non-international) it stays unresolved and manual.
# Older evidence (never filled) predates this phone-only path.
_PHONE_DIAL_COUNTRY_TYPE = "phone_dial_country"
_PHONE_DIAL_COUNTRY_UNRESOLVED_REASON = (
    "Phone dialing country code selector (not country of residence/citizenship) "
    "-- requires manual selection (configured phone is missing or not international)."
)


@dataclass(frozen=True)
class ClassifiedField:
    field: DiscoveredField
    classification: FieldClassification
    value: QuestionValue = None
    fill: bool = False
    kind: QuestionKind = QuestionKind.UNKNOWN
    country: str | None = None
    generated: bool = False
    max_choices: int | None = None
    inactive_conditional: bool = False
    unresolved_reason: str | None = None


def classify_field(field: DiscoveredField, profile: CandidateProfile) -> ClassifiedField:
    if field.field_type == _PHONE_DIAL_COUNTRY_TYPE:
        # The value is only ever the configured international phone number
        # (never residence/citizenship/location); the adapter resolves the
        # calling code from the control's own displayed options. A missing,
        # non-international, or malformed number stays manual.
        digits = international_phone_digits(profile.identity.phone)
        if digits is None:
            return ClassifiedField(
                field=field,
                classification=FieldClassification.UNSUPPORTED,
                fill=False,
                kind=QuestionKind.PHONE_DIAL_COUNTRY,
                unresolved_reason=_PHONE_DIAL_COUNTRY_UNRESOLVED_REASON,
            )
        return ClassifiedField(
            field=field,
            classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            value=f"+{digits}",
            fill=True,
            kind=QuestionKind.PHONE_DIAL_COUNTRY,
        )
    if field.field_type in _UNSUPPORTED_TYPES:
        return ClassifiedField(
            field=field,
            classification=FieldClassification.UNSUPPORTED,
            fill=False,
            kind=QuestionKind.UNKNOWN,
        )

    mapped = map_question(field, profile)

    if mapped.kind in (QuestionKind.GENDER, QuestionKind.AGE):
        if mapped.fillable:
            return ClassifiedField(
                field=field,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                value=mapped.value,
                fill=True,
                kind=mapped.kind,
                country=mapped.country,
                unresolved_reason=mapped.unresolved_reason,
            )
        unresolved = (
            FieldClassification.UNKNOWN_REQUIRED
            if field.required
            else FieldClassification.SENSITIVE_OPTIONAL
        )
        return ClassifiedField(
            field=field,
            classification=unresolved,
            value=None,
            fill=False,
            kind=mapped.kind,
            country=mapped.country,
            inactive_conditional=mapped.inactive_conditional,
            unresolved_reason=mapped.unresolved_reason,
        )

    if mapped.kind is QuestionKind.SENSITIVE:
        if mapped.fillable:
            return ClassifiedField(
                field=field,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                value=mapped.value,
                fill=True,
                kind=mapped.kind,
                country=mapped.country,
                unresolved_reason=mapped.unresolved_reason,
            )
        # Fail-closed regardless of `required`: an unresolved sensitive
        # question always stays SENSITIVE_OPTIONAL (never auto-classified as
        # UNKNOWN_REQUIRED) so it always lands in `sensitive_fields`; the
        # service layer additionally surfaces it in
        # `unresolved_required_fields` when the field is required.
        return ClassifiedField(
            field=field,
            classification=FieldClassification.SENSITIVE_OPTIONAL,
            value=None,
            fill=False,
            kind=mapped.kind,
            country=mapped.country,
            inactive_conditional=mapped.inactive_conditional,
            unresolved_reason=mapped.unresolved_reason,
        )

    if mapped.inactive_conditional:
        return ClassifiedField(
            field=field,
            classification=FieldClassification.UNKNOWN_OPTIONAL,
            value=None,
            fill=False,
            kind=mapped.kind,
            country=mapped.country,
            max_choices=mapped.max_choices,
            inactive_conditional=True,
            unresolved_reason=mapped.unresolved_reason,
        )

    if mapped.fillable:
        return ClassifiedField(
            field=field,
            classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            value=mapped.value,
            fill=True,
            kind=mapped.kind,
            country=mapped.country,
            max_choices=mapped.max_choices,
            inactive_conditional=mapped.inactive_conditional,
            unresolved_reason=mapped.unresolved_reason,
        )

    unresolved = (
        FieldClassification.UNKNOWN_REQUIRED
        if field.required
        else FieldClassification.UNKNOWN_OPTIONAL
    )
    return ClassifiedField(
        field=field,
        classification=unresolved,
        value=None,
        fill=False,
        kind=mapped.kind,
        country=mapped.country,
        max_choices=mapped.max_choices,
        inactive_conditional=mapped.inactive_conditional,
        unresolved_reason=mapped.unresolved_reason,
    )
