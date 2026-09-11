from __future__ import annotations

from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)
from app.application.autofill.summary import format_privacy_acknowledgement_report, render_autofill_summary


def test_summary_lists_labels_not_pii_values() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        filled_fields=[
            AutofillFieldResult(
                label="Email",
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            ),
            AutofillFieldResult(
                label="Phone",
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            ),
        ],
        unresolved_required_fields=[
            AutofillFieldResult(
                label="Expected salary",
                classification=FieldClassification.UNKNOWN_REQUIRED,
                required=True,
            )
        ],
        unsupported_fields=[
            AutofillFieldResult(
                label="Custom multi-select question",
                classification=FieldClassification.UNSUPPORTED,
            )
        ],
        resume_uploaded=True,
    )
    text = render_autofill_summary(result)
    assert text.startswith("Autofill completed.")
    assert "- Email" in text
    assert "- Phone" in text
    assert "ada.example@example.test" not in text
    assert "+15555550100" not in text
    assert "Needs review:" in text
    assert "- Expected salary" in text
    assert "Unsupported:" in text
    assert "Generated (needs review):" in text
    assert "Uploaded successfully" in text
    assert "NOT PERFORMED" in text
    assert "Cover letter:" in text
    assert "Not filled" in text


def test_summary_includes_privacy_diagnostic_block() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:adyen",
        external_id="7342887",
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            AutofillFieldResult(
                label="Point of Data Transfer — Acknowledge/Confirm",
                classification=FieldClassification.UNKNOWN_REQUIRED,
                required=True,
            )
        ],
        warnings=[
            format_privacy_acknowledgement_report(
                {
                    "discovered": True,
                    "classified": "required_privacy",
                    "control_type": "hidden input + styled/label-backed checkbox",
                    "interaction_attempted": "click_associated_label",
                    "readback_checked": False,
                    "failure_reason": "checked_state_did_not_persist",
                }
            )
        ],
    )
    text = render_autofill_summary(result)
    assert "privacy acknowledgement:" in text
    assert "discovered: yes" in text
    assert "classified: required_privacy" in text
    assert "readback_checked: false" in text
    assert "failure_reason: checked_state_did_not_persist" in text


def test_summary_skips_blank_labels() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        unresolved_required_fields=[
            AutofillFieldResult(
                label="",
                classification=FieldClassification.UNKNOWN_REQUIRED,
                required=True,
            ),
            AutofillFieldResult(
                label="Country*",
                classification=FieldClassification.UNKNOWN_REQUIRED,
                required=True,
            ),
        ],
    )
    text = render_autofill_summary(result)
    assert "- Country*" in text
    assert "- \n" not in text
    assert text.count("- ") >= 1
