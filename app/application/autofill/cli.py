from __future__ import annotations

import logging
from pathlib import Path

import typer

from app.application.autofill.models import AutofillResult, AutofillStatus
from app.application.autofill.summary import render_autofill_summary
from app.resume_policy import RESUME_VARIANT_IDS
from app.resume_profiles import ResumeProfilesLoadError

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
        resume_variant: str | None = typer.Option(
            None,
            "--resume-variant",
            help=f"Force one resume variant instead of the deterministic policy. One of: {', '.join(RESUME_VARIANT_IDS)}.",
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
            candidate_name, cover_letter_language, cover_letter_pdf_font_path = _cover_letter_document_settings()
            service = AutofillService(
                resolver=DefaultVacancyResolver(),
                on_ready=_show,
                answer_generator=ApplicationAnswerGenerator(llm_client) if llm_client else None,
                cover_letter_provider=AutofillCoverLetterProvider(llm_client) if llm_client else None,
                auto_submit_enabled=_auto_submit_enabled(),
                candidate_name=candidate_name,
                cover_letter_language=cover_letter_language,
                cover_letter_pdf_font_path=cover_letter_pdf_font_path,
                resume_profiles=_resume_profiles(),
                resume_variant_override=resume_variant,
            )
            result = service.run(source, external_id, keep_open=keep_open)
        except VacancyResolveError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        except ResumeProfilesLoadError as exc:
            typer.echo(f"Resume profiles configuration is invalid: {exc}", err=True)
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


def _auto_submit_enabled() -> bool:
    """Defaults to disabled whenever config cannot be loaded, never fails open."""
    try:
        from app.config import Settings

        return Settings().auto_submit_enabled
    except Exception:
        return False


def _resume_profiles():
    """The same configured `resume_profiles.yaml` (via `Settings.resume_profiles_path`)
    used by the production Telegram/LinkedIn paths, so this diagnostic command
    exercises the real resume-variant selection instead of the legacy
    candidate-profile default. Deliberately not wrapped in a try/except: an
    invalid or missing config must fail this command visibly, never silently
    substitute the legacy candidate-profile default resume (which would
    bypass the selected variant, its PDF validation, and `--resume-variant`).
    """
    from app.config import Settings
    from app.resume_profiles import load_resume_profiles

    settings = Settings()
    return load_resume_profiles(settings.resume_profiles_path)
