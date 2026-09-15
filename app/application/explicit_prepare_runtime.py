"""Lazy production wiring: PrepareApplicationService + AutofillService.

Telegram and CLI callback tests inject a fake PrepareApplicationService
instead of importing this module. Diagnostic `autofill SOURCE EXTERNAL_ID`
does not use this path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.application.autofill.models import AutofillResult
from app.application.prepare_application import ApplicationLifecycleLookup, PrepareApplicationService

logger = logging.getLogger(__name__)


def build_prepare_application_service(
    lifecycle: ApplicationLifecycleLookup,
    *,
    on_ready: Callable[[AutofillResult], None] | None = None,
) -> PrepareApplicationService:
    from app.application.autofill.answers import ApplicationAnswerGenerator
    from app.application.autofill.cover_letter import AutofillCoverLetterProvider
    from app.application.autofill.resolver import DefaultVacancyResolver
    from app.application.autofill.service import AutofillService

    llm_client = _optional_llm_client()
    autofill = AutofillService(
        resolver=DefaultVacancyResolver(),
        on_ready=on_ready,
        answer_generator=ApplicationAnswerGenerator(llm_client) if llm_client else None,
        cover_letter_provider=AutofillCoverLetterProvider(llm_client) if llm_client else None,
    )
    return PrepareApplicationService(autofill, lifecycle)


def _optional_llm_client():
    try:
        from app.config import Settings
        from app.llm_client import LLMClient

        settings = Settings()
        return LLMClient(
            api_url=settings.llm_api_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
    except Exception:
        logger.info("Autofill LLM client unavailable; generated answers and cover letter skipped.")
        return None
