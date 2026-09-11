from __future__ import annotations

import logging
import re

from app.application.autofill.models import AutofillResult

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def redact_pii(value: str) -> str:
    redacted = _EMAIL_RE.sub("[redacted-email]", value)
    return _PHONE_RE.sub("[redacted-phone]", redacted)


def log_autofill_result(result: AutofillResult) -> None:
    logger.info(
        "autofill status=%s source=%s external_id=%s resume_uploaded=%s submit_performed=%s",
        result.status.value,
        result.source,
        result.external_id,
        result.resume_uploaded,
        result.submit_performed,
    )
    if result.application_url:
        logger.info("autofill domain=%s", _domain(result.application_url))
    for field in (
        result.filled_fields
        + result.unresolved_required_fields
        + result.unresolved_optional_fields
        + result.sensitive_fields
        + result.unsupported_fields
    ):
        logger.info(
            "autofill field label=%s class=%s type=%s required=%s",
            field.label,
            field.classification.value,
            field.field_type,
            field.required,
        )
    for warning in result.warnings:
        logger.warning("autofill warning=%s", redact_pii(warning))


def _domain(url: str) -> str:
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]
