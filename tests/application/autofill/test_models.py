from __future__ import annotations

from app.application.autofill.models import (
    AutofillResult,
    AutofillStatus,
    FieldClassification,
    Stage1AutofillResult,
    stage1_autofill_result,
)


def test_stage1_factory_always_sets_submit_performed_false() -> None:
    result = stage1_autofill_result(
        source="target_company:greenhouse:agoda",
        external_id="6886113",
        status=AutofillStatus.READY_FOR_REVIEW,
        application_url="https://job-boards.greenhouse.io/agoda/jobs/6886113",
        submit_performed=True,
        resume_uploaded=True,
    )
    assert result.submit_performed is False
    assert result.resume_uploaded is True
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.source == "target_company:greenhouse:agoda"
    assert result.external_id == "6886113"


def test_result_includes_required_collections() -> None:
    result = AutofillResult(
        source="target_company:greenhouse:agoda",
        external_id="1",
        application_url="https://example.test/jobs/1",
        status=AutofillStatus.FAILED,
    )
    assert result.filled_fields == []
    assert result.unresolved_required_fields == []
    assert result.unresolved_optional_fields == []
    assert result.sensitive_fields == []
    assert result.unsupported_fields == []
    assert result.generated_fields == []
    assert result.warnings == []
    assert result.submit_performed is False


def test_field_classification_values() -> None:
    assert FieldClassification.SUPPORTED_DETERMINISTIC.value == "SUPPORTED_DETERMINISTIC"
    assert FieldClassification.UNKNOWN_REQUIRED.value == "UNKNOWN_REQUIRED"
    assert FieldClassification.UNKNOWN_OPTIONAL.value == "UNKNOWN_OPTIONAL"
    assert FieldClassification.SENSITIVE_OPTIONAL.value == "SENSITIVE_OPTIONAL"
    assert FieldClassification.UNSUPPORTED.value == "UNSUPPORTED"


def test_stage1_model_coerces_submit_to_false() -> None:
    result = Stage1AutofillResult(
        source="target_company:greenhouse:agoda",
        external_id="1",
        status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
        submit_performed=True,
    )
    assert result.submit_performed is False
