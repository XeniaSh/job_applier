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
    RESIDENCE_COUNTRY_UNCONFIRMED_TEXT,
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


def test_resume_resolution_failed_names_variant_and_file_for_followup() -> None:
    """A resume resolution failure must tell the user which variant was
    selected (and its filename) so they know exactly what to fix/supply --
    never just the generic "no usable resume could be resolved" text alone.
    It must point at the resume_profiles.yaml configuration, never a CLI
    flag -- Telegram has no `--resume-variant` option.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=["Resume for profile 'ai_backend' is unusable: ..."],
        failure_reason=AutofillFailureReason.RESUME_RESOLUTION_FAILED,
        resume_variant="ai_backend",
        resume_file="ai-backend.pdf",
    )
    text = format_application_prepare_failed_text(result)
    assert "ai_backend" in text
    assert "ai-backend.pdf" in text
    assert "resume_profiles.yaml" in text
    assert "--resume-variant" not in text


def test_resume_resolution_failed_without_variant_still_points_at_config() -> None:
    """Even when no variant could be determined at all (e.g. the
    resume_profiles.yaml file itself failed to load), the message must still
    point the user at that configuration file.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=["Resume profiles configuration could not be loaded."],
        failure_reason=AutofillFailureReason.RESUME_RESOLUTION_FAILED,
    )
    text = format_application_prepare_failed_text(result)
    assert "resume_profiles.yaml" in text
    assert "--resume-variant" not in text


def test_resume_resolution_failed_surfaces_location_uncertainty() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=["Resume variant 'java_backend_nz' is not configured."],
        failure_reason=AutofillFailureReason.RESUME_RESOLUTION_FAILED,
        resume_variant="java_backend_nz",
    ).model_copy(update={"resume_location_warning": "Vacancy location is missing."})
    text = format_application_prepare_failed_text(result)
    assert "Vacancy location is missing." in text


def test_browser_setup_failed_after_successful_resume_selection_surfaces_variant() -> None:
    """A resume variant resolved successfully before a *later* browser setup
    failure must still be surfaced -- the uncertainty text is not limited to
    RESUME_RESOLUTION_FAILED. See `AutofillRunner._attach_resume_selection`,
    called unconditionally after the whole try/except in `service.py`.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.FAILED,
        warnings=["Failed to start Playwright Chromium: internal detail"],
        failure_reason=AutofillFailureReason.BROWSER_SETUP_FAILED,
        resume_variant="ai_backend_nz",
        resume_file="ai-backend-nz.pdf",
    ).model_copy(update={"resume_location_warning": "Vacancy location is missing."})
    text = format_application_prepare_failed_text(result)
    assert "browser" in text.lower()
    assert "ai_backend_nz" in text
    assert "ai-backend-nz.pdf" in text
    assert "Vacancy location is missing." in text
    # Only the resolution-failure path points at resume_profiles.yaml.
    assert "resume_profiles.yaml" not in text


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


def test_needs_manual_intervention_resume_failure_is_not_mislabeled_as_challenge() -> None:
    """A required resume attachment that could not be confirmed is not a
    security challenge; the completed text must not claim one was detected.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=[
            "Resume attachment for 'Resume/CV' could not be confirmed at the end of "
            "Prepare; please attach it manually before submitting."
        ],
        resume_attachment_incomplete=True,
    )
    text = format_application_prepare_completed_text(result)
    assert "Manual review needed." in text
    assert "security" not in text.lower()
    assert "challenge" not in text.lower()
    assert "resume" in text.lower()


def test_needs_manual_intervention_resume_failure_names_variant_and_file() -> None:
    """The reason text must name the selected variant/basename so the user
    knows exactly which resume to supply manually -- not just a generic
    "attach it manually" message.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Resume was not attached for 'Resume/CV'."],
        resume_attachment_incomplete=True,
        resume_variant="ai_backend",
        resume_file="ai-backend.pdf",
    )
    text = format_application_prepare_completed_text(result)
    assert "ai_backend" in text
    assert "ai-backend.pdf" in text


def test_needs_manual_intervention_resume_failure_heading_states_incomplete() -> None:
    """A required resume attachment that could not be confirmed must not be
    reported under the unqualified "Preparation completed." heading --
    the preparation is incomplete until that attachment is fixed manually.
    """
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=["Resume was not attached for 'Resume/CV'."],
        resume_attachment_incomplete=True,
    )
    text = format_application_prepare_completed_text(result)
    assert text.startswith("Preparation incomplete")
    assert "Preparation completed." not in text


def test_resume_location_warning_is_surfaced_in_completed_text() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
    ).model_copy(
        update={
            "resume_variant": "java_backend_nz",
            "resume_file": "java-backend-nz.pdf",
            "resume_location_warning": "Vacancy location is missing, so the New Zealand resume "
            "variant could not be confirmed.",
        }
    )
    text = format_application_prepare_completed_text(result)
    assert "Vacancy location is missing" in text


def test_needs_manual_intervention_generic_reason_without_warnings() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=[],
    )
    text = format_application_prepare_completed_text(result)
    assert "security" not in text.lower()
    assert "challenge" not in text.lower()
    assert "manual action is required" in text.lower()


def test_resume_variant_and_file_are_reported_when_present() -> None:
    result = stage1_autofill_result(
        source="s",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
    ).model_copy(update={"resume_variant": "ai_backend_nz", "resume_file": "ai-backend-nz.pdf"})
    text = format_application_prepare_completed_text(result)
    assert "Resume variant: ai_backend_nz (ai-backend-nz.pdf)" in text


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


def test_residence_country_unconfirmed_warning_adds_prominent_line() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        warnings=[
            "Residence country selection for 'Country of residence' was not "
            "confirmed: any visible selection must be checked and corrected "
            "manually."
        ],
    )
    text = format_application_prepare_completed_text(result)
    assert RESIDENCE_COUNTRY_UNCONFIRMED_TEXT in text
    assert "Country of residence" not in text
    assert "Brazil" not in text


def test_without_residence_country_warning_no_extra_line_is_added() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        warnings=["Read-back failed for 'Some other field'."],
    )
    text = format_application_prepare_completed_text(result)
    assert RESIDENCE_COUNTRY_UNCONFIRMED_TEXT not in text


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
