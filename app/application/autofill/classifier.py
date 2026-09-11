from __future__ import annotations

from dataclasses import dataclass

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind, QuestionValue, map_question
from app.application.candidate_profile import CandidateProfile

_UNSUPPORTED_TYPES = frozenset({"signature", "captcha", "unknown"})


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


def classify_field(field: DiscoveredField, profile: CandidateProfile) -> ClassifiedField:
    if field.field_type in _UNSUPPORTED_TYPES:
        return ClassifiedField(
            field=field,
            classification=FieldClassification.UNSUPPORTED,
            fill=False,
            kind=QuestionKind.UNKNOWN,
        )

    mapped = map_question(field, profile)

    if mapped.kind is QuestionKind.GENDER:
        if mapped.fillable:
            return ClassifiedField(
                field=field,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                value=mapped.value,
                fill=True,
                kind=mapped.kind,
                country=mapped.country,
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
        )

    if mapped.kind is QuestionKind.SENSITIVE:
        return ClassifiedField(
            field=field,
            classification=FieldClassification.SENSITIVE_OPTIONAL,
            value=None,
            fill=False,
            kind=mapped.kind,
            country=mapped.country,
            inactive_conditional=mapped.inactive_conditional,
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
    )
