"""Unit tests for Telegram-facing prepare/autofill result formatting.

Focus: a FAILED or NEEDS_MANUAL_INTERVENTION AutofillResult must map to a
concise, safe, actionable Telegram message instead of a bare "Preparation
failed." or raw exception text.
"""

from __future__ import annotations

from app.application.autofill.models import AutofillFailureReason, AutofillStatus, stage1_autofill_result
from app.application.autofill.review_session import ReviewSessionCloseOutcome
from app.telegram.application_prepare import (
    FAILED_TEXT,
    format_application_prepare_failed_text,
    format_application_prepare_completed_text,
    format_review_done_response_text,
    format_review_done_terminal_text,
)


def _failed(reason: AutofillFailureReason | None, *, warnings: list[str] | None = None):
    return stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=warnings or [],
        failure_reason=reason,
    )


def test_no_result_falls_back_to_generic_text() -> None:
    assert format_application_prepare_failed_text(None) == FAILED_TEXT
    assert format_application_prepare_failed_text() == FAILED_TEXT


def test_unsupported_form_produces_actionable_text() -> None:
    result = _failed(AutofillFailureReason.UNSUPPORTED_FORM, warnings=["UNSUPPORTED_FORM"])
    text = format_application_prepare_failed_text(result)
    assert text != FAILED_TEXT
    assert "not supported" in text.lower()
    assert "UNSUPPORTED_FORM" not in text


def test_browser_setup_failure_preserves_safe_reason_without_raw_exception() -> None:
    result = _failed(
        AutofillFailureReason.BROWSER_SETUP_FAILED,
        warnings=["Failed to start Playwright Chromium: Some internal traceback detail xyz123"],
    )
    text = format_application_prepare_failed_text(result)
    assert "browser" in text.lower()
    assert "traceback" not in text.lower()
    assert "xyz123" not in text


def test_unexpected_error_does_not_leak_raw_exception_text() -> None:
    result = _failed(
        AutofillFailureReason.UNEXPECTED_ERROR,
        warnings=["Unexpected autofill error: ValueError('sensitive detail: secret-token-abc')"],
    )
    text = format_application_prepare_failed_text(result)
    assert "secret-token-abc" not in text
    assert "ValueError" not in text
    assert "unexpected error" in text.lower()


def test_unrecognized_reason_falls_back_to_generic_text() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=["some warning"],
    )
    assert format_application_prepare_failed_text(result) == FAILED_TEXT


def test_needs_manual_intervention_reports_captcha_challenge() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Security challenge detected: captcha"],
    )
    text = format_application_prepare_completed_text(result)
    assert "Manual review needed." in text
    assert "CAPTCHA" in text
    assert "Submit was not performed." in text


def test_needs_manual_intervention_reports_cloudflare_challenge() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Security challenge detected: cloudflare_challenge"],
    )
    text = format_application_prepare_completed_text(result)
    assert "Cloudflare" in text


def test_needs_manual_intervention_unknown_challenge_token_is_generic_but_safe() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Security challenge detected: some_new_unmapped_token"],
    )
    text = format_application_prepare_completed_text(result)
    assert "some_new_unmapped_token" not in text
    assert "security" in text.lower()


def test_ready_for_review_unresolved_field_summary_is_preserved() -> None:
    from app.application.autofill.models import AutofillFieldResult, FieldClassification

    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            AutofillFieldResult(
                label="Expected salary",
                classification=FieldClassification.UNKNOWN_REQUIRED,
                required=True,
            )
        ],
    )
    text = format_application_prepare_completed_text(result)
    assert "Unresolved required fields: 1" in text
    assert "Manual review needed." in text
    assert "Submit was not performed." in text


def test_review_done_response_text_for_each_outcome() -> None:
    closed = format_review_done_response_text(ReviewSessionCloseOutcome.CLOSED)
    already = format_review_done_response_text(ReviewSessionCloseOutcome.ALREADY_CLOSED)
    not_found = format_review_done_response_text(ReviewSessionCloseOutcome.NOT_FOUND)
    assert closed != already != not_found
    assert closed and already and not_found


def test_review_done_terminal_text_preserves_summary_and_states_unconfirmed() -> None:
    original = "Preparation completed.\nFilled fields: 5\nManual review needed."
    text = format_review_done_terminal_text(original, ReviewSessionCloseOutcome.CLOSED)
    assert "Preparation completed.\nFilled fields: 5" in text
    assert "Manual review needed." not in text
    assert "Manual review finished." in text
    assert "Review finished." in text
    assert "Submission was not verified" in text
    assert "Applied" in text
    assert "Closing the browser now." not in text


def test_review_done_terminal_text_never_implies_submit_succeeded() -> None:
    original = "Preparation completed.\nFilled fields: 3"
    for outcome in ReviewSessionCloseOutcome:
        text = format_review_done_terminal_text(original, outcome)
        assert "application submitted" not in text.lower()
        assert "successfully applied" not in text.lower()
        assert "confirm it manually" in text.lower()
        assert format_review_done_terminal_text(text, outcome) == text
