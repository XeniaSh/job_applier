"""Lazy production wiring: PrepareApplicationService + AutofillService.

Telegram and CLI callback tests inject a fake PrepareApplicationService
instead of importing this module. Diagnostic `autofill SOURCE EXTERNAL_ID`
does not use this path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.application.autofill.models import AutofillResult
from app.application.prepare_application import ApplicationLifecycleLookup, PrepareApplicationService

logger = logging.getLogger(__name__)


def build_prepare_application_service(
    lifecycle: ApplicationLifecycleLookup,
    *,
    on_ready: Callable[[AutofillResult], None] | None = None,
    wait_for_review: Callable[[], None] | None = None,
) -> PrepareApplicationService:
    """Wire the production AutofillService.

    `wait_for_review` overrides the default terminal-`input()` browser
    handoff. The Telegram-triggered path must pass one (see
    `app.application.autofill.review_session`); leaving it unset keeps the
    original foreground behavior for any other caller.
    """
    from app.application.autofill.answers import ApplicationAnswerGenerator
    from app.application.autofill.cover_letter import AutofillCoverLetterProvider
    from app.application.autofill.resolver import DefaultVacancyResolver
    from app.application.autofill.service import AutofillService

    llm_client = _optional_llm_client()
    candidate_name, cover_letter_language, cover_letter_pdf_font_path = _cover_letter_document_settings()
    autofill_kwargs: dict[str, object] = {}
    if wait_for_review is not None:
        autofill_kwargs["wait_for_review"] = wait_for_review
    autofill = AutofillService(
        resolver=DefaultVacancyResolver(),
        on_ready=on_ready,
        answer_generator=ApplicationAnswerGenerator(llm_client) if llm_client else None,
        cover_letter_provider=AutofillCoverLetterProvider(llm_client) if llm_client else None,
        auto_submit_enabled=_auto_submit_enabled(),
        candidate_name=candidate_name,
        cover_letter_language=cover_letter_language,
        cover_letter_pdf_font_path=cover_letter_pdf_font_path,
        **autofill_kwargs,
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


def _auto_submit_enabled() -> bool:
    """Defaults to disabled whenever config cannot be loaded, never fails open."""
    try:
        from app.config import Settings

        return Settings().auto_submit_enabled
    except Exception:
        return False


def _cover_letter_document_settings() -> tuple[str, str, Path | None]:
    """The existing configured candidate name, preferred language, and
    Unicode PDF font path -- reused as-is from `app.config.Settings` for the
    Ashby cover-letter file upload's PDF rendering, never a new config
    surface. Defaults to the same empty/English fallback as every other
    caller whenever settings cannot be loaded.
    """
    try:
        from app.config import Settings

        settings = Settings()
        return settings.candidate_name, settings.candidate_preferred_language, settings.cover_letter_pdf_font_path
    except Exception:
        return "", "en", None
