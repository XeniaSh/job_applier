from __future__ import annotations

import logging

from app.application.autofill.logging import log_autofill_result, redact_pii
from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)


def test_redact_email_and_phone() -> None:
    text = redact_pii("Contact ada.example@example.test or +15555550100")
    assert "ada.example@example.test" not in text
    assert "+15555550100" not in text
    assert "[redacted-email]" in text
    assert "[redacted-phone]" in text


def test_log_autofill_result_does_not_include_pii(caplog) -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="6886113",
        application_url="https://job-boards.greenhouse.io/agoda/jobs/6886113",
        status=AutofillStatus.READY_FOR_REVIEW,
        filled_fields=[
            AutofillFieldResult(
                label="Email",
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            )
        ],
        warnings=["Could not use ada.example@example.test"],
    )
    with caplog.at_level(logging.INFO):
        log_autofill_result(result)
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "target_company:greenhouse:agoda" in combined
    assert "6886113" in combined
    assert "job-boards.greenhouse.io" in combined
    assert "Email" in combined
    assert "SUPPORTED_DETERMINISTIC" in combined
    assert "ada.example@example.test" not in combined
