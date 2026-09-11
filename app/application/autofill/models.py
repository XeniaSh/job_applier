from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AutofillStatus(StrEnum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_MANUAL_INTERVENTION = "NEEDS_MANUAL_INTERVENTION"
    FAILED = "FAILED"


class FieldClassification(StrEnum):
    SUPPORTED_DETERMINISTIC = "SUPPORTED_DETERMINISTIC"
    UNKNOWN_REQUIRED = "UNKNOWN_REQUIRED"
    UNKNOWN_OPTIONAL = "UNKNOWN_OPTIONAL"
    SENSITIVE_OPTIONAL = "SENSITIVE_OPTIONAL"
    UNSUPPORTED = "UNSUPPORTED"


class AutofillFieldResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    classification: FieldClassification
    field_type: str | None = None
    required: bool = False
    name: str | None = None
    generated: bool = False


class AutofillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    external_id: str
    application_url: str | None = None
    status: AutofillStatus
    filled_fields: list[AutofillFieldResult] = Field(default_factory=list)
    unresolved_required_fields: list[AutofillFieldResult] = Field(default_factory=list)
    unresolved_optional_fields: list[AutofillFieldResult] = Field(default_factory=list)
    sensitive_fields: list[AutofillFieldResult] = Field(default_factory=list)
    unsupported_fields: list[AutofillFieldResult] = Field(default_factory=list)
    generated_fields: list[AutofillFieldResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resume_uploaded: bool = False
    cover_letter_filled: bool = False
    submit_performed: bool = False


def stage1_autofill_result(
    *,
    source: str,
    external_id: str,
    status: AutofillStatus,
    application_url: str | None = None,
    filled_fields: list[AutofillFieldResult] | None = None,
    unresolved_required_fields: list[AutofillFieldResult] | None = None,
    unresolved_optional_fields: list[AutofillFieldResult] | None = None,
    sensitive_fields: list[AutofillFieldResult] | None = None,
    unsupported_fields: list[AutofillFieldResult] | None = None,
    generated_fields: list[AutofillFieldResult] | None = None,
    warnings: list[str] | None = None,
    resume_uploaded: bool = False,
    cover_letter_filled: bool = False,
    submit_performed: bool = False,
) -> AutofillResult:
    """Build a Stage 1 result. Submit is always false."""
    _ = submit_performed
    return AutofillResult(
        source=source,
        external_id=external_id,
        application_url=application_url,
        status=status,
        filled_fields=filled_fields or [],
        unresolved_required_fields=unresolved_required_fields or [],
        unresolved_optional_fields=unresolved_optional_fields or [],
        sensitive_fields=sensitive_fields or [],
        unsupported_fields=unsupported_fields or [],
        generated_fields=generated_fields or [],
        warnings=warnings or [],
        resume_uploaded=resume_uploaded,
        cover_letter_filled=cover_letter_filled,
        submit_performed=False,
    )


class Stage1AutofillResult(AutofillResult):
    """AutofillResult that cannot record a submit."""

    @model_validator(mode="after")
    def never_submit(self) -> Stage1AutofillResult:
        if self.submit_performed:
            object.__setattr__(self, "submit_performed", False)
        return self
