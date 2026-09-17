from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.application.autofill.models import AutofillResult, AutofillStatus

# Case-insensitive substrings used to spot specific blocking conditions inside
# `AutofillResult.warnings`. Kept narrow and tied to warning text actually
# produced today (see service.py's "Security challenge detected: ..." and
# cover_letter.py's "validation rejected both attempts" -> "... was left
# unresolved: validation rejected both attempts"), not a broad interpretation
# of all warnings.
_CHALLENGE_MARKERS = ("captcha", "security challenge")
_VALIDATION_MARKERS = ("validation",)


class AutoSubmitDecision(StrEnum):
    AUTO_SUBMIT_SAFE = "AUTO_SUBMIT_SAFE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AutoSubmitReasonCode(StrEnum):
    NON_READY_STATUS = "NON_READY_STATUS"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    UNRESOLVED_REQUIRED_FIELDS = "UNRESOLVED_REQUIRED_FIELDS"
    GENERATED_FIELDS_PRESENT = "GENERATED_FIELDS_PRESENT"
    UNSUPPORTED_REQUIRED_FIELDS = "UNSUPPORTED_REQUIRED_FIELDS"
    SECURITY_CHALLENGE_DETECTED = "SECURITY_CHALLENGE_DETECTED"
    VALIDATION_ERROR_DETECTED = "VALIDATION_ERROR_DETECTED"


class AutoSubmitPolicyResult(BaseModel):
    """Immutable outcome of `evaluate_auto_submit_policy`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: AutoSubmitDecision
    reason_codes: tuple[AutoSubmitReasonCode, ...] = ()
    reasons: tuple[str, ...] = ()


def evaluate_auto_submit_policy(result: AutofillResult) -> AutoSubmitPolicyResult:
    """Compute the deterministic auto-submit safety decision for `result`.

    Pure function: does not mutate `result`, click Submit, or perform any I/O.
    Returns `AUTO_SUBMIT_SAFE` only when the result is `READY_FOR_REVIEW`, has
    not already been submitted, has no unresolved required fields, no
    generated fields (no permitted auto-submit policy for generated content
    yet), no required field on an unsupported control, and no warning
    indicating a CAPTCHA/security challenge or a validation error. Optional
    unresolved/sensitive fields, optional unsupported fields, and other
    non-blocking warnings do not affect the decision.
    """
    reason_codes: list[AutoSubmitReasonCode] = []
    reasons: list[str] = []

    def flag(code: AutoSubmitReasonCode, message: str) -> None:
        reason_codes.append(code)
        reasons.append(message)

    if result.status is not AutofillStatus.READY_FOR_REVIEW:
        flag(
            AutoSubmitReasonCode.NON_READY_STATUS,
            f"Autofill status is {result.status.value}, not READY_FOR_REVIEW.",
        )

    if result.submit_performed:
        flag(
            AutoSubmitReasonCode.ALREADY_SUBMITTED,
            "submit_performed is already true.",
        )

    if result.unresolved_required_fields:
        flag(
            AutoSubmitReasonCode.UNRESOLVED_REQUIRED_FIELDS,
            f"{len(result.unresolved_required_fields)} required field(s) unresolved.",
        )

    if result.generated_fields:
        flag(
            AutoSubmitReasonCode.GENERATED_FIELDS_PRESENT,
            f"{len(result.generated_fields)} field(s) were generated; "
            "generated content has no permitted auto-submit policy.",
        )

    unsupported_required = [field for field in result.unsupported_fields if field.required]
    if unsupported_required:
        flag(
            AutoSubmitReasonCode.UNSUPPORTED_REQUIRED_FIELDS,
            f"{len(unsupported_required)} required field(s) use an unsupported control.",
        )

    lowered_warnings = [warning.lower() for warning in result.warnings]
    if any(marker in warning for warning in lowered_warnings for marker in _CHALLENGE_MARKERS):
        flag(
            AutoSubmitReasonCode.SECURITY_CHALLENGE_DETECTED,
            "A CAPTCHA or security challenge was detected.",
        )
    if any(marker in warning for warning in lowered_warnings for marker in _VALIDATION_MARKERS):
        flag(
            AutoSubmitReasonCode.VALIDATION_ERROR_DETECTED,
            "A validation error was reported.",
        )

    decision = (
        AutoSubmitDecision.NEEDS_REVIEW if reason_codes else AutoSubmitDecision.AUTO_SUBMIT_SAFE
    )
    return AutoSubmitPolicyResult(
        decision=decision,
        reason_codes=tuple(reason_codes),
        reasons=tuple(reasons),
    )
