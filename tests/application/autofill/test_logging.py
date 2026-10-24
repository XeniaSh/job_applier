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


def test_log_autofill_result_includes_resume_variant_and_file(caplog) -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        resume_uploaded=True,
    ).model_copy(update={"resume_variant": "ai_backend_nz", "resume_file": "ai-backend-nz.pdf"})
    with caplog.at_level(logging.INFO):
        log_autofill_result(result)
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "resume_variant=ai_backend_nz" in combined
    assert "resume_file=ai-backend-nz.pdf" in combined


def test_log_autofill_result_includes_resume_variant_reason(caplog) -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        resume_uploaded=True,
    ).model_copy(
        update={
            "resume_variant": "ai_backend",
            "resume_variant_reason": "Hands-on AI/LLM evidence in role or responsibilities ('llm').",
        }
    )
    with caplog.at_level(logging.INFO):
        log_autofill_result(result)
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "resume_variant_reason=Hands-on AI/LLM evidence in role or responsibilities ('llm')." in combined


def test_log_autofill_result_includes_resume_location_warning(caplog) -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.READY_FOR_REVIEW,
        resume_uploaded=True,
    ).model_copy(update={"resume_variant": "java_backend_nz", "resume_location_warning": "Vacancy location is missing."})
    with caplog.at_level(logging.INFO):
        log_autofill_result(result)
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "resume_location_warning=Vacancy location is missing." in combined
