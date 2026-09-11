from __future__ import annotations

from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)
from app.application.autofill.summary import render_autofill_summary


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
