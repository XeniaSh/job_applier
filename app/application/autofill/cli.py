from __future__ import annotations

import logging

import typer

from app.application.autofill.models import AutofillResult, AutofillStatus
from app.application.autofill.summary import render_autofill_summary

logger = logging.getLogger(__name__)


def register_autofill_command(app: typer.Typer) -> None:
    @app.command("autofill")
    def autofill(
        source: str = typer.Argument(..., help="Vacancy source, e.g. target_company:greenhouse:agoda"),
        external_id: str = typer.Argument(..., help="Stable ATS job id"),
        keep_open: bool = typer.Option(
            True,
            "--keep-open/--no-keep-open",
            help="Leave the headed browser open until Enter is pressed.",
        ),
    ) -> None:
        from app.application.autofill.answers import ApplicationAnswerGenerator
        from app.application.autofill.cover_letter import AutofillCoverLetterProvider
        from app.application.autofill.resolver import DefaultVacancyResolver, VacancyResolveError
        from app.application.autofill.service import AutofillService

        shown = False

        def _show(result: AutofillResult) -> None:
            nonlocal shown
            typer.echo(render_autofill_summary(result))
            shown = True

        try:
            llm_client = _optional_llm_client()
            service = AutofillService(
                resolver=DefaultVacancyResolver(),
                on_ready=_show,
                answer_generator=ApplicationAnswerGenerator(llm_client) if llm_client else None,
                cover_letter_provider=AutofillCoverLetterProvider(llm_client) if llm_client else None,
            )
            result = service.run(source, external_id, keep_open=keep_open)
        except VacancyResolveError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

        if not shown:
            typer.echo(render_autofill_summary(result))
        if result.status is AutofillStatus.FAILED:
            raise typer.Exit(code=1)
        if result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION:
            raise typer.Exit(code=2)


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
