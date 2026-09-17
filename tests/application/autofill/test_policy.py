from __future__ import annotations

import copy

from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillResult,
    AutofillStatus,
    FieldClassification,
)
from app.application.autofill.policy import (
    AutoSubmitDecision,
    AutoSubmitReasonCode,
    evaluate_auto_submit_policy,
)

_BASE = dict(
    source="target_company:greenhouse:agoda",
    external_id="1",
    application_url="https://example.test/jobs/1",
)


def _field(
    label: str,
    classification: FieldClassification,
    *,
    required: bool = False,
    generated: bool = False,
) -> AutofillFieldResult:
    return AutofillFieldResult(
        label=label,
        classification=classification,
        required=required,
        generated=generated,
    )


def test_ready_result_with_no_blockers_is_auto_submit_safe() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_optional_fields=[
            _field("Twitter", FieldClassification.UNKNOWN_OPTIONAL)
        ],
        sensitive_fields=[_field("Gender", FieldClassification.SENSITIVE_OPTIONAL)],
        unsupported_fields=[
            _field("Doodle", FieldClassification.UNSUPPORTED, required=False)
        ],
        warnings=["Cover letter was left unresolved for 'Why us'."],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.AUTO_SUBMIT_SAFE
    assert decision.reason_codes == ()
    assert decision.reasons == ()


def test_non_ready_status_needs_review() -> None:
    for status in (AutofillStatus.NEEDS_MANUAL_INTERVENTION, AutofillStatus.FAILED):
        result = AutofillResult(**_BASE, status=status)
        decision = evaluate_auto_submit_policy(result)
        assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
        assert AutoSubmitReasonCode.NON_READY_STATUS in decision.reason_codes


def test_unresolved_required_field_needs_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            _field("Phone", FieldClassification.UNKNOWN_REQUIRED, required=True)
        ],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.UNRESOLVED_REQUIRED_FIELDS in decision.reason_codes


def test_generated_fields_need_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        generated_fields=[
            _field(
                "Why us",
                FieldClassification.SUPPORTED_DETERMINISTIC,
                generated=True,
            )
        ],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.GENERATED_FIELDS_PRESENT in decision.reason_codes


def test_unsupported_required_field_needs_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unsupported_fields=[
            _field("Signature", FieldClassification.UNSUPPORTED, required=True)
        ],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.UNSUPPORTED_REQUIRED_FIELDS in decision.reason_codes


def test_unsupported_optional_field_alone_is_safe() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unsupported_fields=[
            _field("Doodle", FieldClassification.UNSUPPORTED, required=False)
        ],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.AUTO_SUBMIT_SAFE


def test_unresolved_optional_and_sensitive_fields_alone_are_safe() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_optional_fields=[
            _field("Twitter", FieldClassification.UNKNOWN_OPTIONAL)
        ],
        sensitive_fields=[_field("Gender", FieldClassification.SENSITIVE_OPTIONAL)],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.AUTO_SUBMIT_SAFE


def test_captcha_warning_needs_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Security challenge detected: captcha"],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.SECURITY_CHALLENGE_DETECTED in decision.reason_codes


def test_validation_error_warning_needs_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        warnings=["Cover letter was left unresolved: validation rejected both attempts"],
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.VALIDATION_ERROR_DETECTED in decision.reason_codes


def test_already_submitted_needs_review() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        submit_performed=True,
    )

    decision = evaluate_auto_submit_policy(result)

    assert decision.decision is AutoSubmitDecision.NEEDS_REVIEW
    assert AutoSubmitReasonCode.ALREADY_SUBMITTED in decision.reason_codes


def test_policy_result_is_immutable() -> None:
    result = AutofillResult(**_BASE, status=AutofillStatus.READY_FOR_REVIEW)
    decision = evaluate_auto_submit_policy(result)

    try:
        decision.decision = AutoSubmitDecision.NEEDS_REVIEW  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised


def test_evaluation_does_not_mutate_or_submit() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            _field("Phone", FieldClassification.UNKNOWN_REQUIRED, required=True)
        ],
    )
    before = copy.deepcopy(result)

    evaluate_auto_submit_policy(result)

    assert result == before
    assert result.submit_performed is False


def test_deterministic_for_same_input() -> None:
    result = AutofillResult(
        **_BASE,
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            _field("Phone", FieldClassification.UNKNOWN_REQUIRED, required=True)
        ],
        generated_fields=[
            _field("Why us", FieldClassification.SUPPORTED_DETERMINISTIC, generated=True)
        ],
    )

    first = evaluate_auto_submit_policy(result)
    second = evaluate_auto_submit_policy(result)

    assert first == second
