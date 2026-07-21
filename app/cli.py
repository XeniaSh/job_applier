from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import inspect
import logging
import os
import re
import threading
import time
from typing import Callable

import typer
from pydantic import ValidationError

from app.collectors.hh_client import HHClient
from app.collectors.hh_collector import DEFAULT_HH_QUERIES, HHCollector, HHCollectReport
from app.collectors.greenhouse_collector import GreenhouseCollectionError, GreenhouseCollector
from app.collectors.email_imap_client import (
    EmailAuthenticationError,
    EmailConnectionError,
    EmailIMAPClient,
)
from app.collectors.linkedin_email_collector import (
    LinkedInEmailCollectReport,
    LinkedInEmailCollector,
    LinkedInProcessedVacancy,
    LinkedInSyncDiagnostics,
)
from app.collectors.linkedin_email_parser import extract_email_text_parts, parse_linkedin_email
from app.collectors.title_filter import should_accept_title
from app.collectors.vacancy_collector import Collector, CollectorResult, NormalizedVacancy, VacancyCollector, vacancy_identity
from app.application.preparation_service import (
    ApplicationPreparationError,
    PreparedApplication,
    PreparationService,
    resolve_resume_path,
)
from app.application.resume_cache_service import KNOWN_RESUME_NAMES, ResumeCacheService
from app.config import Settings
from app.formatter import format_evaluation_ru
from app.llm_client import CoverLetterValidationError, LLMClient, LLMRequestError, LLMResponseError
from app.models import Decision, VacancyEvaluation
from app.prompt_loader import PromptLoadError, load_analysis_prompt
from app.skills_profile_loader import SkillsProfileLoadError, load_candidate_skills
from app.storage.seen_jobs import SeenJobsStorage
from app.storage.imap_checkpoint import ImapCheckpointStorage
from app.storage.telegram_delivery import (
    ALLOWED_STATUSES,
    STATUS_APPLIED,
    STATUS_PREPARE_REQUESTED,
    STATUS_PREPARING,
    STATUS_PREPARED,
    STATUS_PREPARATION_FAILED,
    STATUS_SKIPPED,
    TelegramDeliveryStorage,
)
from app.telegram.client import (
    TelegramClient,
    TelegramMessageNotModifiedError,
    TelegramRequestError,
    build_archived_buttons,
    build_loading_buttons,
    build_loading_text,
    build_prepare_failed_buttons,
    build_prepared_application_buttons,
    build_ready_text,
    map_source_to_code,
    parse_callback_data,
    validate_linkedin_job_url,
)
from app.telegram.formatter import format_archived_vacancy_html, format_preparation_failed_html
from app.telegram.models import (
    ApplicationHistoryRecord,
    TelegramDeliveryRecord,
    TelegramResumeCacheRecord,
    TelegramVacancyCard,
)
from app.vacancy_analyzer import VacancyAnalyzer

app = typer.Typer(help="Personal job vacancy analyzer.")
logger = logging.getLogger(__name__)


def _load_vacancy_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Не удалось прочитать файл вакансии: {path}") from exc

    if not text.strip():
        raise ValueError("Файл вакансии пустой. Добавьте описание вакансии в UTF-8.")
    return text


def build_analyzer(settings: Settings) -> VacancyAnalyzer:
    llm_client = LLMClient(
        api_url=settings.llm_api_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    return VacancyAnalyzer(
        llm_client=llm_client,
        skills_loader=load_candidate_skills,
        prompt_loader=load_analysis_prompt,
    )


@app.command("review")
def review(
    vacancy_file: Path = typer.Argument(..., help="Path to UTF-8 vacancy file."),
    json_output: bool = typer.Option(False, "--json", help="Print raw validated JSON."),
) -> None:
    try:
        vacancy_text = _load_vacancy_text(vacancy_file)
    except ValueError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    analyzer = build_analyzer(settings)
    try:
        result = analyzer.analyze(vacancy_text)
    except (SkillsProfileLoadError, PromptLoadError) as exc:
        typer.secho(f"Ошибка загрузки файлов: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    except LLMRequestError as exc:
        typer.secho(f"Ошибка LLM запроса: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    except LLMResponseError as exc:
        typer.secho(f"Ошибка валидации ответа LLM: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    typer.echo(format_evaluation_ru(result))


@app.command("collect-hh")
def collect_hh(
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum new vacancies to analyze."),
    query: list[str] | None = typer.Option(None, "--query", help="Override default HH queries."),
    include_ignore: bool = typer.Option(False, "--include-ignore", help="Print IGNORE results too."),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    analyzer = build_analyzer(settings)
    hh_client = HHClient(user_agent=settings.hh_user_agent)
    seen_jobs = SeenJobsStorage()
    collector = HHCollector(hh_client=hh_client, analyzer=analyzer, seen_jobs=seen_jobs)

    selected_queries = list(query) if query else list(DEFAULT_HH_QUERIES)
    report = collector.collect_and_analyze(queries=selected_queries, limit=limit)

    if report.successful_searches == 0:
        typer.secho("Не удалось выполнить поиск вакансий HH.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _print_processed_results(report=report, include_ignore=include_ignore)
    _print_summary(report)


def _print_processed_results(report: HHCollectReport, include_ignore: bool) -> None:
    for item in report.processed:
        decision = item.evaluation.decision.value
        if decision not in {"STRONG_MATCH", "POTENTIAL_MATCH", "IGNORE"}:
            continue
        if decision == "IGNORE" and not include_ignore:
            continue

        stack_value = (
            f"{item.evaluation.match_percentage:.1f}%"
            if item.evaluation.match_percentage is not None
            else "n/a"
        )
        typer.echo(
            "\n".join(
                [
                    f"Решение: {decision}",
                    f"Вакансия: {item.title}",
                    f"Компания: {item.company}",
                    f"URL: {item.url}",
                    f"Совпадение по стеку: {stack_value}",
                    _format_short_list("Пробелы", item.evaluation.gaps, limit=3),
                    _format_short_list("Нюансы", item.evaluation.nuances, limit=3),
                    f"Рекомендуемое резюме: {item.evaluation.recommended_resume.value}",
                    "",
                ]
            ).strip()
        )


def _format_short_list(title: str, values: list[str], limit: int) -> str:
    cleaned = []
    seen = set()
    for value in values:
        normalized = " ".join(value.strip().split()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= limit:
            break
    if not cleaned:
        return f"{title}: нет"
    rendered = [f"{title}:"]
    rendered.extend([f"- {item}" for item in cleaned])
    return "\n".join(rendered)


def _print_summary(report: HHCollectReport) -> None:
    typer.echo(
        "\n".join(
            [
                f"Найдено новых вакансий: {report.new_found}",
                f"Проанализировано: {report.analyzed}",
                f"Сильных совпадений: {report.strong_matches}",
                f"Потенциальных совпадений: {report.potential_matches}",
                f"Пропущено: {report.ignored}",
                f"Ошибок: {report.errors + report.search_errors}",
            ]
        )
    )


def _build_linkedin_email_collector(
    *,
    settings: Settings,
    analyzer: VacancyAnalyzer,
    seen_jobs: SeenJobsStorage,
    email_client: EmailIMAPClient,
    bootstrap_lookback_days: int | None = None,
) -> LinkedInEmailCollector:
    checkpoint_storage = ImapCheckpointStorage()
    kwargs = {
        "email_client": email_client,
        "analyzer": analyzer,
        "seen_jobs": seen_jobs,
    }
    signature = inspect.signature(LinkedInEmailCollector)
    parameters = signature.parameters
    if "checkpoint_storage" in parameters:
        kwargs["checkpoint_storage"] = checkpoint_storage
    if "incremental_enabled" in parameters:
        kwargs["incremental_enabled"] = settings.linkedin_email_incremental_enabled
    if "bootstrap_message_limit" in parameters:
        kwargs["bootstrap_message_limit"] = settings.linkedin_email_bootstrap_message_limit
    if "bootstrap_lookback_days" in parameters:
        kwargs["bootstrap_lookback_days"] = bootstrap_lookback_days or settings.linkedin_email_bootstrap_lookback_days
    if "batch_size" in parameters:
        kwargs["batch_size"] = settings.linkedin_email_batch_size
    return LinkedInEmailCollector(**kwargs)


def _linkedin_account_key(username: str) -> str:
    digest = hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@app.command("collect-linkedin-email")
def collect_linkedin_email(
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum new vacancies to analyze."),
    include_ignore: bool = typer.Option(False, "--include-ignore", help="Print IGNORE results too."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse emails and print metadata without LLM analysis and without mark seen.",
    ),
    rescan: bool = typer.Option(False, "--rescan", help="Run bounded historical scan without changing checkpoint."),
    reset_imap_checkpoint: bool = typer.Option(
        False,
        "--reset-imap-checkpoint",
        help="Reset saved IMAP UID checkpoint before collection.",
    ),
    since_days: int | None = typer.Option(
        None,
        "--since-days",
        min=1,
        help="Override bounded lookback window for bootstrap/rescan.",
    ),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    analyzer = build_analyzer(settings)
    seen_jobs = SeenJobsStorage()
    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=settings.linkedin_email_mark_as_read,
    )
    collector = _build_linkedin_email_collector(
        settings=settings,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        email_client=email_client,
        bootstrap_lookback_days=since_days,
    )
    if reset_imap_checkpoint:
        collector.reset_checkpoint()

    try:
        report = collector.collect_and_analyze(
            limit=limit,
            dry_run=dry_run,
            skip_seen=True,
            mark_seen=True,
            analyze_in_dry_run=False,
            rescan=rescan,
        )
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к почте LinkedIn alerts.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if not dry_run:
        _sync_application_history(report.processed)
    diagnostics_method = getattr(collector, "last_sync_diagnostics", None)
    if callable(diagnostics_method):
        _print_linkedin_sync_diagnostics(diagnostics_method(), verbose=False)
    _print_linkedin_results(report=report, include_ignore=include_ignore, dry_run=dry_run)
    _print_linkedin_summary(report)


@app.command("collect-greenhouse")
def collect_greenhouse(
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum new vacancies to analyze."),
    board: list[str] | None = typer.Option(None, "--board", help="Board slug or full board URL."),
    include_ignore: bool = typer.Option(False, "--include-ignore", help="Print IGNORE results too."),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    selected_boards = list(board) if board else list(settings.greenhouse_boards)
    if not selected_boards:
        typer.secho(
            "GREENHOUSE_BOARDS не задан. Укажите --board или GREENHOUSE_BOARDS в .env.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    analyzer = build_analyzer(settings)
    seen_jobs = SeenJobsStorage()
    collector = GreenhouseCollector(boards=selected_boards)
    try:
        collected = collector.collect()
    except GreenhouseCollectionError as exc:
        typer.secho(f"Ошибка Greenhouse collection: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    report, _per_source, _analysis = _analyze_collected_vacancies(
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        vacancies=collected,
        limit=limit,
        skip_seen=True,
        mark_seen=True,
    )
    _sync_application_history(report.processed)
    _print_linkedin_results(report=report, include_ignore=include_ignore, dry_run=False)
    _print_linkedin_summary(report)


@app.command("reset-imap-checkpoint")
def reset_imap_checkpoint(
    folder: str | None = typer.Option(None, "--folder", help="Mailbox folder (defaults to configured folder)."),
) -> None:
    settings = Settings()
    checkpoint_storage = ImapCheckpointStorage()
    removed = checkpoint_storage.reset(
        source=LinkedInEmailCollector.SOURCE,
        account_key=_linkedin_account_key(settings.linkedin_email_username),
        folder=folder or settings.linkedin_email_folder,
    )
    if removed:
        typer.echo("IMAP checkpoint reset.")
    else:
        typer.echo("IMAP checkpoint not found.")


@app.command("send-linkedin-telegram")
def send_linkedin_telegram(
    limit: int = typer.Option(20, "--limit", min=1),
    include_strong: bool = typer.Option(True, "--include-strong/--no-include-strong"),
    include_potential: bool = typer.Option(True, "--include-potential/--no-include-potential"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose"),
    backfill: bool = typer.Option(False, "--backfill"),
    rescan: bool = typer.Option(False, "--rescan", help="Run bounded historical scan without changing checkpoint."),
    reset_imap_checkpoint: bool = typer.Option(
        False,
        "--reset-imap-checkpoint",
        help="Reset saved IMAP UID checkpoint before collection.",
    ),
    since_days: int | None = typer.Option(
        None,
        "--since-days",
        min=1,
        help="Override bounded lookback window for bootstrap/rescan.",
    ),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    if not dry_run:
        _require_telegram_settings(settings)
        telegram_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    else:
        telegram_client = None

    analyzer = build_analyzer(settings)
    seen_jobs = SeenJobsStorage()
    deliveries = TelegramDeliveryStorage()
    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=settings.linkedin_email_mark_as_read,
    )
    collector = _build_linkedin_email_collector(
        settings=settings,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        email_client=email_client,
        bootstrap_lookback_days=since_days,
    )
    if reset_imap_checkpoint:
        collector.reset_checkpoint()

    try:
        report = collector.collect_and_analyze(
            limit=limit,
            dry_run=dry_run,
            skip_seen=(not dry_run and not backfill),
            analyze_in_dry_run=dry_run,
            mark_seen=False,
            rescan=rescan,
        )
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к почте LinkedIn alerts.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if not dry_run:
        _sync_application_history(report.processed)
    diagnostics_method = getattr(collector, "last_sync_diagnostics", None)
    if callable(diagnostics_method):
        _print_linkedin_sync_diagnostics(diagnostics_method(), verbose=verbose)
    for item in report.processed:
        if item.evaluation is None:
            if verbose and item.skipped_by_prefilter:
                typer.echo(f"SKIP TITLE_FILTER {item.title}")
            continue
        seen_info = seen_jobs.is_seen("linkedin-email", item.external_id)
        delivered_info = deliveries.was_sent("linkedin-email", item.external_id, settings.telegram_chat_id)
        if seen_info:
            report.already_seen += 1
            if dry_run and verbose:
                typer.echo(f"INFO ALREADY_SEEN {item.title}")
        if delivered_info:
            report.already_delivered += 1
            if dry_run and verbose:
                typer.echo(f"INFO ALREADY_DELIVERED {item.title}")

        decision = item.evaluation.decision.value
        if decision == "STRONG_MATCH" and not include_strong:
            if verbose:
                typer.echo(f"SKIP {decision} {item.title}")
            continue
        if decision == "POTENTIAL_MATCH" and not include_potential:
            if verbose:
                typer.echo(f"SKIP {decision} {item.title}")
            continue
        if decision not in {"STRONG_MATCH", "POTENTIAL_MATCH"}:
            if verbose:
                typer.echo(f"SKIP {decision} {item.title}")
            continue

        try:
            card = TelegramVacancyCard(
                source=map_source_to_code("linkedin-email"),
                external_id=item.external_id,
                decision=decision,
                title=item.title,
                company=item.company,
                location=item.location,
                url=item.url,
                match_percentage=item.evaluation.match_percentage,
                gaps=item.evaluation.gaps,
                nuances=item.evaluation.nuances,
                recommended_resume=item.evaluation.recommended_resume.value,
                content_completeness=item.content_completeness,
            )
            from app.telegram.formatter import format_telegram_card_html

            formatted_card = format_telegram_card_html(card)
        except ValueError as exc:
            report.send_errors += 1
            logger.error("Telegram card prepare failed for job %s: %s", item.external_id, exc)
            continue

        report.prepared_cards += 1

        if dry_run:
            if verbose:
                typer.echo(f"WOULD_SEND {decision} {item.title}")
            typer.echo("--------------------------------")
            typer.echo(formatted_card)
            continue

        if delivered_info:
            if verbose:
                typer.echo(f"SKIP ALREADY_DELIVERED {item.title}")
            continue

        try:
            message_ref = telegram_client.send_vacancy_card(card)  # type: ignore[union-attr]
        except (TelegramRequestError, ValueError) as exc:
            report.send_errors += 1
            logger.error("Telegram send failed for job %s: %s", item.external_id, exc)
            continue

        deliveries.save_sent(
            source="linkedin-email",
            external_id=item.external_id,
            chat_id=settings.telegram_chat_id,
            message_id=message_ref.message_id,
        )
        deliveries.mark_history_status(
            source="linkedin-email",
            external_id=item.external_id,
            status="SENT",
            timestamp_field="sent_at",
        )
        report.sent += 1

    typer.echo(f"Найдено писем: {report.emails_found}")
    typer.echo(f"Извлечено вакансий: {report.vacancies_extracted}")
    typer.echo(f"Уникальных вакансий: {report.unique_vacancies}")
    typer.echo(f"Уже в seen_jobs: {report.already_seen}")
    typer.echo(f"Проанализировано: {report.analyzed}")
    typer.echo(f"Подготовлено карточек: {report.prepared_cards}")
    typer.echo(f"Отправлено в Telegram: {report.sent}")
    typer.echo(f"Уже отправлялись: {report.already_delivered}")
    typer.echo(f"Ошибок отправки: {report.send_errors}")


@app.command("prepare-telegram-applications")
def prepare_telegram_applications(
    limit: int = typer.Option(10, "--limit", min=1),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    if not dry_run:
        _require_telegram_settings(settings)
        telegram_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    else:
        telegram_client = None

    analyzer = build_analyzer(settings)
    llm_client = LLMClient(
        api_url=settings.llm_api_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=False,
    )
    service = PreparationService(
        analyzer=analyzer,
        llm_client=llm_client,
        email_client=email_client,
        resumes_dir=settings.resumes_dir,
        preferred_language=settings.candidate_preferred_language,
        grammatical_gender=settings.candidate_grammatical_gender,
    )
    storage = TelegramDeliveryStorage()
    result = _prepare_requested_applications(
        settings=settings,
        service=service,
        storage=storage,
        telegram_client=telegram_client,
        limit=limit,
        dry_run=dry_run,
        print_dry_run_items=dry_run,
        timing_logger=None,
    )

    typer.echo(f"В очереди: {result.queue_items}")
    typer.echo(f"Сгенерировано пакетов: {result.generated_packages}")
    typer.echo(f"Подготовлено успешно: {result.prepared_successfully}")
    typer.echo(f"Отправлено в Telegram: {result.telegram_sent}")
    typer.echo(f"Ошибок: {result.errors_count}")
    typer.echo(f"PDF отправлено из кэша: {result.pdf_cached}")
    typer.echo(f"PDF загружено заново: {result.pdf_uploaded}")
    typer.echo(f"PDF отсутствует: {result.pdf_missing}")
    typer.echo(f"Ошибок PDF: {result.pdf_errors}")


@app.command("run")
def run_pipeline(
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    _require_telegram_settings(settings)

    lock = _JobApplierLock(Path("data/job_applier.lock"))
    if not lock.acquire():
        typer.echo("Job Applier is already running.")
        raise typer.Exit(code=1)

    analyzer = build_analyzer(settings)
    llm_client = LLMClient(
        api_url=settings.llm_api_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=settings.linkedin_email_mark_as_read,
    )
    seen_jobs = SeenJobsStorage()
    deliveries = TelegramDeliveryStorage()
    linkedin_collector = _build_linkedin_email_collector(
        settings=settings,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        email_client=email_client,
    )
    runtime_collectors: list[RuntimeCollector] = [
        RuntimeCollector(
            name="linkedin-email",
            collect_fn=linkedin_collector.collect,
            diagnostics_fn=getattr(linkedin_collector, "last_sync_diagnostics", None),
        )
    ]
    if settings.greenhouse_boards:
        greenhouse_collector = GreenhouseCollector(boards=settings.greenhouse_boards)
        runtime_collectors.append(RuntimeCollector(name="greenhouse", collect_fn=greenhouse_collector.collect))
    collectors: list[Collector] = runtime_collectors
    telegram_client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    callback_resume_cache = ResumeCacheService(
        resumes_dir=settings.resumes_dir,
        storage=deliveries,
        telegram_client=telegram_client,
    )
    preparation_service = PreparationService(
        analyzer=analyzer,
        llm_client=llm_client,
        email_client=email_client,
        resumes_dir=settings.resumes_dir,
        preferred_language=settings.candidate_preferred_language,
        grammatical_gender=settings.candidate_grammatical_gender,
    )

    interval = max(1, int(settings.pipeline_interval_seconds))
    poll_interval = max(1, int(settings.telegram_poll_interval_seconds))
    next_cycle_monotonic = 0.0
    offset_raw = deliveries.get_state("telegram_update_offset")
    offset = int(offset_raw) if offset_raw and offset_raw.isdigit() else None
    prepare_stop_event = threading.Event()
    prepare_wakeup_event = threading.Event()

    def _prepare_worker() -> None:
        worker_storage = TelegramDeliveryStorage()
        worker_resume_cache = ResumeCacheService(
            resumes_dir=settings.resumes_dir,
            storage=worker_storage,
            telegram_client=telegram_client,
        )
        while not prepare_stop_event.is_set():
            try:
                priority_keys = _drain_prepare_priorities(storage=worker_storage)
                if priority_keys and verbose:
                    for source, external_id in priority_keys:
                        _run_log(f"Prepare priority requested {source}:{external_id}")
                result = _prepare_requested_applications(
                    settings=settings,
                    service=preparation_service,
                    storage=worker_storage,
                    telegram_client=telegram_client,
                    limit=20,
                    dry_run=False,
                    print_dry_run_items=False,
                    resume_cache_service=worker_resume_cache,
                    timing_logger=_run_log if verbose else None,
                    priority_vacancy_keys=priority_keys,
                )
                if priority_keys and verbose:
                    _run_log(f"Prepare queue continue pending={max(0, result.queue_items - len(priority_keys))}")
                if result.generated_packages == 0 and result.errors_count == 0:
                    prepare_wakeup_event.wait(timeout=0.5)
                    prepare_wakeup_event.clear()
            except Exception as exc:  # noqa: BLE001
                _run_log(f"Prepare worker error: {exc}")
                prepare_wakeup_event.wait(timeout=0.5)
                prepare_wakeup_event.clear()

    prepare_thread = threading.Thread(target=_prepare_worker, name="prepare-worker", daemon=True)
    prepare_thread.start()

    typer.echo("Job Applier started.")
    typer.echo("Press Ctrl+C to stop.")
    try:
        while True:
            now = time.monotonic()
            if now >= next_cycle_monotonic:
                try:
                    cycle_start = time.monotonic()
                    collect_start = time.monotonic()
                    pipeline_result = _collect_pipeline_items(
                        collectors=collectors,
                        safe_error_formatter=lambda source, exc: _format_collector_error(
                            source=source,
                            exc=exc,
                            imap_host=settings.linkedin_email_imap_host,
                            secrets=_runtime_secrets(settings),
                        ),
                    )
                    collect_ms = max(0, int((time.monotonic() - collect_start) * 1000))
                    analyze_start = time.monotonic()
                    _analyze_pipeline_items(
                        analyzer=analyzer,
                        seen_jobs=seen_jobs,
                        pipeline=pipeline_result,
                        limit=20,
                        skip_seen=True,
                        mark_seen=True,
                    )
                    analyze_ms = max(0, int((time.monotonic() - analyze_start) * 1000))
                    telegram_start = time.monotonic()
                    _deliver_pipeline_items(
                        pipeline=pipeline_result,
                        deliveries=deliveries,
                        telegram_client=telegram_client,
                        chat_id=settings.telegram_chat_id,
                    )
                    telegram_ms = max(0, int((time.monotonic() - telegram_start) * 1000))
                    cycle_ms = max(0, int((time.monotonic() - cycle_start) * 1000))

                    pipeline_result.validate_accounting()
                    diagnostics_by_source = {
                        collector.name: collector.diagnostics()
                        for collector in runtime_collectors
                    }
                    source_names = _ordered_sources(pipeline_result.sources())
                    for source_name in source_names:
                        if pipeline_result.has_collect_error(source_name):
                            failed_message = pipeline_result.collect_error_message(source_name)
                            _run_log(f"{source_name}: failed — {failed_message}")
                            continue
                        _run_log(
                            f"{source_name}: extracted={pipeline_result.extracted(source_name)} "
                            f"unique={pipeline_result.unique(source_name)} "
                            f"new={pipeline_result.new(source_name)} "
                            f"already_seen={pipeline_result.already_seen(source_name)} "
                            f"invalid_identity={pipeline_result.invalid_identity(source_name)} "
                            f"prefiltered={pipeline_result.title_filtered(source_name)} "
                            f"errors={pipeline_result.errors_before_analysis(source_name)}"
                        )
                        source_diag = diagnostics_by_source.get(source_name)
                        if source_diag is not None:
                            if source_diag.sync_mode == "incremental" and source_diag.messages_fetched == 0:
                                _run_log(
                                    f"{source_name}: checked new_messages=0 checkpoint={source_diag.checkpoint_after or source_diag.checkpoint_before or 0}"
                                )
                            elif verbose:
                                _run_log(
                                    f"{source_name}: mode={source_diag.sync_mode} "
                                    f"checkpoint_before={source_diag.checkpoint_before or 0} "
                                    f"checkpoint_after={source_diag.checkpoint_after or 0} "
                                    f"highest_uid_seen={source_diag.highest_uid_seen or 0} "
                                    f"searched_uids={source_diag.searched_uids} "
                                    f"fetch_attempted={source_diag.fetch_attempted} "
                                    f"fetch_succeeded={source_diag.fetch_succeeded} "
                                    f"decode_succeeded={source_diag.decode_succeeded} "
                                    f"rejected_sender={source_diag.rejected_sender} "
                                    f"rejected_subject={source_diag.rejected_subject} "
                                    f"parse_errors={source_diag.parse_errors} "
                                    f"messages_parsed={source_diag.messages_parsed} "
                                    f"matched={source_diag.messages_matched} "
                                    f"fetched={source_diag.messages_fetched} "
                                    f"extracted={source_diag.vacancies_extracted} "
                                    f"search_criteria={source_diag.search_criteria or 'n/a'} "
                                    f"checkpoint_advanced={source_diag.checkpoint_advanced} "
                                    f"uidvalidity_changed={source_diag.uidvalidity_changed}"
                                )
                                if source_diag.classification_counts:
                                    counts_line = " ".join(
                                        f"{reason}={count}"
                                        for reason, count in sorted(source_diag.classification_counts.items())
                                    )
                                    _run_log(f"{source_name}: classification_counts {counts_line}")
                                for event in source_diag.classification_events:
                                    _run_log(f"{source_name}: {event}")
                                for event in source_diag.rejection_events:
                                    _run_log(f"{source_name}: {event}")
                                if source_diag.timings_ms:
                                    _run_log(
                                        f"{source_name} timings: "
                                        f"connect={source_diag.timings_ms.get('connect', 0)}ms "
                                        f"select={source_diag.timings_ms.get('select', 0)}ms "
                                        f"search={source_diag.timings_ms.get('search', 0)}ms "
                                        f"fetch={source_diag.timings_ms.get('fetch', 0)}ms "
                                        f"parse={source_diag.timings_ms.get('parse', 0)}ms "
                                        f"checkpoint={source_diag.timings_ms.get('checkpoint', 0)}ms"
                                    )
                    if len(source_names) > 1:
                        _run_log(f"Merged: unique={pipeline_result.merged_unique()}")
                    _run_log(
                        "Analysis: "
                        f"analyzed={pipeline_result.analyzed_total()} "
                        f"strong={pipeline_result.strong_total()} "
                        f"potential={pipeline_result.potential_total()} "
                        f"ignore={pipeline_result.ignore_total()} "
                        f"title_filtered={pipeline_result.title_filtered_total()} "
                        f"errors={pipeline_result.processing_errors_total()}"
                    )
                    _run_log(
                        "Telegram: "
                        f"eligible={pipeline_result.eligible()} "
                        f"already_delivered={pipeline_result.already_delivered()} "
                        f"sent={pipeline_result.sent()} "
                        f"errors={pipeline_result.telegram_errors()}"
                    )
                    no_work_reason = pipeline_result.no_work_reason()
                    if pipeline_result.analyzed_total() == 0 and no_work_reason:
                        _run_log(f"No vacancies analyzed: {no_work_reason}")
                    _run_log(
                        f"Timing: collect={collect_ms}ms analyze={analyze_ms}ms "
                        f"telegram={telegram_ms}ms cycle={cycle_ms}ms"
                    )
                    if verbose:
                        for outcome in _pipeline_verbose_outcomes(pipeline_result):
                            _run_log(outcome)
                except (LLMRequestError, LLMResponseError) as exc:
                    _run_log(f"Pipeline cycle failed: {exc}")
                except Exception as exc:  # noqa: BLE001
                    _run_log(f"Pipeline cycle failed: {exc}")
                next_cycle_monotonic = time.monotonic() + interval

            try:
                offset, prepare_requests = _poll_telegram_actions_once(
                    client=telegram_client,
                    storage=deliveries,
                    configured_chat_id=str(settings.telegram_chat_id),
                    offset=offset,
                    timeout=poll_interval,
                    resumes_dir=settings.resumes_dir,
                    resume_cache_service=callback_resume_cache,
                    timing_logger=_run_log if verbose else None,
                )
                if prepare_requests > 0:
                    _run_log("Prepare request received")
                    prepare_wakeup_event.set()
            except TelegramRequestError as exc:
                details = _format_telegram_error(exc, secrets=_runtime_secrets(settings))
                _run_log(f"Telegram poll failed:\n  {details}")
                time.sleep(poll_interval)
                continue
    except KeyboardInterrupt:
        typer.echo("Job Applier stopped.")
    finally:
        prepare_stop_event.set()
        prepare_wakeup_event.set()
        prepare_thread.join(timeout=5.0)
        lock.release()


@app.command("poll-telegram-actions")
def poll_telegram_actions(
    once: bool = typer.Option(False, "--once"),
    timeout: int = typer.Option(25, "--timeout", min=1, max=60),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    _require_telegram_settings(settings)

    client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    storage = TelegramDeliveryStorage()
    offset_raw = storage.get_state("telegram_update_offset")
    offset = int(offset_raw) if offset_raw and offset_raw.isdigit() else None

    try:
        while True:
            try:
                offset, _ = _poll_telegram_actions_once(
                    client=client,
                    storage=storage,
                    configured_chat_id=str(settings.telegram_chat_id),
                    offset=offset,
                    timeout=timeout,
                    resumes_dir=settings.resumes_dir,
                    timing_logger=None,
                )
            except TelegramRequestError as exc:
                typer.secho(f"Ошибка Telegram polling: {exc}", err=True, fg=typer.colors.RED)
                if once:
                    raise typer.Exit(code=1) from exc
                continue
            if once:
                break
    except KeyboardInterrupt:
        typer.echo("Остановка polling по Ctrl+C")


@app.command("telegram-chat-id")
def telegram_chat_id() -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    if not settings.telegram_bot_token:
        typer.secho("TELEGRAM_BOT_TOKEN не задан.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)

    client = TelegramClient(settings.telegram_bot_token, chat_id="0")
    try:
        updates = client.get_updates(offset=None, timeout=1)
    except TelegramRequestError as exc:
        typer.secho(f"Ошибка Telegram: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    seen_chat_ids: set[str] = set()
    rows: list[str] = []
    for update in updates:
        message = update.get("message") or update.get("callback_query", {}).get("message") or {}
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        if chat.get("type") != "private":
            continue
        chat_id = str(chat.get("id", ""))
        if not chat_id or chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        display_name = " ".join(
            [str(chat.get("first_name", "")).strip(), str(chat.get("last_name", "")).strip()]
        ).strip()
        username = str(chat.get("username", "")).strip()
        suffix = f" (@{username})" if username else ""
        rows.append(f"{chat_id} — {display_name or 'Unknown'}{suffix}")

    if not rows:
        typer.echo("Обновления не найдены. Сначала отправьте любое сообщение вашему боту.")
        return
    typer.echo("Найдены чаты:")
    for row in rows:
        typer.echo(row)


@app.command("telegram-debug")
def telegram_debug(
    status: str | None = typer.Option(None, "--status"),
    source: str | None = typer.Option(None, "--source"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    normalized_status = _normalize_status_or_exit(status)
    storage = TelegramDeliveryStorage()
    try:
        rows = storage.list_deliveries(
            status=normalized_status,
            source=source.strip() if source else None,
            limit=limit,
        )
    except ValueError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    if not rows:
        typer.echo("Telegram delivery records not found.")
        return
    _print_delivery_debug_table(rows)


@app.command("application-history")
def application_history(
    status: str | None = typer.Option(None, "--status"),
    source: str | None = typer.Option(None, "--source"),
    company: str | None = typer.Option(None, "--company"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    rows = TelegramDeliveryStorage().list_application_history(
        status=status.strip() if status else None,
        source=source.strip() if source else None,
        company=company.strip() if company else None,
        limit=limit,
    )
    if not rows:
        typer.echo("Application history is empty.")
        return
    _print_application_history_table(rows)


@app.command("application-stats")
def application_stats(
    days: int = typer.Option(30, "--days", min=1),
    source: str | None = typer.Option(None, "--source"),
) -> None:
    stats = TelegramDeliveryStorage().get_application_stats(
        days=days,
        source=source.strip() if source else None,
    )
    sent = stats["sent"]
    prepared = stats["prepared"]
    applied = stats["applied"]
    sent_to_prepared = _safe_percent(prepared, sent)
    prepared_to_applied = _safe_percent(applied, prepared)
    typer.echo(f"Period: last {days} days\n")
    typer.echo(f"Found: {stats['found']}")
    typer.echo(f"Sent to Telegram: {sent}")
    typer.echo(f"Prepare requested: {stats['prepare_requested']}")
    typer.echo(f"Prepared: {prepared}")
    typer.echo(f"Applied: {applied}")
    typer.echo(f"Skipped: {stats['skipped']}\n")
    typer.echo("Conversion:")
    typer.echo(f"Sent -> Prepared: {sent_to_prepared:.1f}%")
    typer.echo(f"Prepared -> Applied: {prepared_to_applied:.1f}%\n")
    typer.echo("Top companies:")
    if stats["top_companies"]:
        for name, count in stats["top_companies"]:
            typer.echo(f"{name}: {count}")
    else:
        typer.echo("n/a")
    typer.echo("\nTop recommended resumes:")
    if stats["top_resumes"]:
        for name, count in stats["top_resumes"]:
            typer.echo(f"{name}: {count}")
    else:
        typer.echo("n/a")


@app.command("telegram-reset")
def telegram_reset(
    external_id: str = typer.Argument(...),
    source: str = typer.Option("linkedin-email", "--source"),
    status: str = typer.Option(STATUS_PREPARE_REQUESTED, "--status"),
) -> None:
    normalized_status = _normalize_status_or_exit(status)
    normalized_source = source.strip()
    normalized_external_id = external_id.strip()
    storage = TelegramDeliveryStorage()
    current = storage.get_delivery(normalized_source, normalized_external_id)
    if current is None:
        typer.secho(
            f"Delivery record not found: {normalized_source}:{normalized_external_id}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    try:
        storage.set_status(normalized_source, normalized_external_id, normalized_status)
    except ValueError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    except KeyError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Updated {normalized_source}:{normalized_external_id}")
    typer.echo(f"{current.status} -> {normalized_status}")


@app.command("telegram-delete-delivery")
def telegram_delete_delivery(
    external_id: str = typer.Argument(...),
    source: str = typer.Option("linkedin-email", "--source"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    normalized_source = source.strip()
    normalized_external_id = external_id.strip()
    if not yes:
        confirmed = typer.confirm(f"Delete delivery {normalized_source}:{normalized_external_id}?", default=False)
        if not confirmed:
            typer.echo("Cancelled.")
            raise typer.Exit(code=1)
    storage = TelegramDeliveryStorage()
    deleted = storage.delete_delivery(normalized_source, normalized_external_id)
    if not deleted:
        typer.secho(
            f"Delivery record not found: {normalized_source}:{normalized_external_id}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Deleted delivery {normalized_source}:{normalized_external_id}")


@app.command("telegram-cache-resumes")
def telegram_cache_resumes(
    resume: list[str] | None = typer.Option(None, "--resume"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(f"Ошибка конфигурации: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc
    _require_telegram_settings(settings)

    storage = TelegramDeliveryStorage()
    client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    cache_service = ResumeCacheService(
        resumes_dir=settings.resumes_dir,
        storage=storage,
        telegram_client=client,
    )

    selected = list(resume) if resume else list(KNOWN_RESUME_NAMES)
    normalized_selected: list[str] = []
    for item in selected:
        normalized_selected.append(_normalize_resume_name_or_exit(item))

    cached = 0
    uploaded = 0
    missing = 0
    errors = 0
    for resume_name in normalized_selected:
        try:
            result = cache_service.get_or_upload(
                resume_name=resume_name,
                chat_id=settings.telegram_chat_id,
                force_upload=force,
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            typer.echo(f"{resume_name}: error ({exc})")
            continue

        if result.missing:
            missing += 1
            typer.echo(f"{resume_name}: missing")
        elif result.cache_hit:
            cached += 1
            typer.echo(f"{resume_name}: cached")
        elif result.uploaded:
            uploaded += 1
            typer.echo(f"{resume_name}: uploaded")
        else:
            errors += 1
            typer.echo(f"{resume_name}: error (unknown state)")

    typer.echo(f"Cached: {cached}")
    typer.echo(f"Uploaded: {uploaded}")
    typer.echo(f"Missing: {missing}")
    typer.echo(f"Errors: {errors}")


@app.command("telegram-resume-cache")
def telegram_resume_cache() -> None:
    rows = TelegramDeliveryStorage().list_resume_cache()
    if not rows:
        typer.echo("Telegram resume cache is empty.")
        return
    _print_resume_cache_table(rows)


@app.command("telegram-clear-resume-cache")
def telegram_clear_resume_cache(
    resume_name: str | None = typer.Argument(None),
    all: bool = typer.Option(False, "--all"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    if not all and not resume_name:
        typer.secho("Provide RESUME_NAME or --all.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)
    storage = TelegramDeliveryStorage()
    if all:
        targets = [row.resume_name for row in storage.list_resume_cache()]
        if not yes:
            confirmed = typer.confirm(f"Clear all resume cache records ({len(targets)})?", default=False)
            if not confirmed:
                typer.echo("Cancelled.")
                raise typer.Exit(code=1)
        deleted = 0
        for item in targets:
            if storage.delete_resume_cache(item):
                deleted += 1
        typer.echo(f"Deleted resume cache rows: {deleted}")
        return

    normalized_name = _normalize_resume_name_or_exit(resume_name or "")
    if not yes:
        confirmed = typer.confirm(f"Delete resume cache {normalized_name}?", default=False)
        if not confirmed:
            typer.echo("Cancelled.")
            raise typer.Exit(code=1)
    deleted = storage.delete_resume_cache(normalized_name)
    if not deleted:
        typer.secho(f"Resume cache not found: {normalized_name}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.echo(f"Deleted resume cache: {normalized_name}")


def _print_linkedin_results(
    report: LinkedInEmailCollectReport,
    include_ignore: bool,
    dry_run: bool,
) -> None:
    for item in report.processed:
        if dry_run:
            typer.echo(
                "\n".join(
                    [
                        "Режим: DRY-RUN",
                        f"Вакансия: {item.title}",
                        f"Компания: {item.company or 'n/a'}",
                        f"Локация: {item.location or 'n/a'}",
                        f"URL: {item.url}",
                        f"Content completeness: {item.content_completeness}",
                        "",
                    ]
                ).strip()
            )
            continue

        if item.evaluation is None:
            continue
        decision = item.evaluation.decision.value
        if decision == "IGNORE" and not include_ignore:
            continue
        if decision not in {"STRONG_MATCH", "POTENTIAL_MATCH", "IGNORE"}:
            continue

        stack_value = (
            f"{item.evaluation.match_percentage:.1f}%"
            if item.evaluation.match_percentage is not None
            else "n/a"
        )
        typer.echo(
            "\n".join(
                [
                    f"Решение: {decision}",
                    f"Вакансия: {item.title}",
                    f"Компания: {item.company or 'n/a'}",
                    f"Локация: {item.location or 'n/a'}",
                    f"LinkedIn URL: {item.url}",
                    f"Совпадение по стеку: {stack_value}",
                    _format_short_list("Пробелы", item.evaluation.gaps, limit=3),
                    _format_short_list("Нюансы", item.evaluation.nuances, limit=3),
                    f"Рекомендуемое резюме: {item.evaluation.recommended_resume.value}",
                    f"Content completeness: {item.content_completeness}",
                    "",
                ]
            ).strip()
        )


def _print_linkedin_summary(report: LinkedInEmailCollectReport) -> None:
    typer.echo(
        "\n".join(
            [
                f"Найдено писем: {report.emails_found}",
                f"Извлечено вакансий: {report.vacancies_extracted}",
                f"Уникальных вакансий: {report.unique_vacancies}",
                f"Новых вакансий: {report.new_vacancies}",
                f"Уже в seen_jobs: {report.already_seen}",
                f"Проанализировано: {report.analyzed}",
                f"Сильных совпадений: {report.strong_matches}",
                f"Потенциальных совпадений: {report.potential_matches}",
                f"Отфильтровано по заголовку: {report.prefiltered}",
                f"Пропущено: {report.ignored}",
                f"Ошибок: {report.errors}",
            ]
        )
    )


def _print_linkedin_sync_diagnostics(diagnostics: LinkedInSyncDiagnostics, *, verbose: bool) -> None:
    checkpoint_value = diagnostics.checkpoint_after or diagnostics.checkpoint_before or 0
    typer.echo(
        "LinkedIn IMAP sync: "
        f"mode={diagnostics.sync_mode} "
        f"matched={diagnostics.messages_matched} "
        f"fetched={diagnostics.messages_fetched} "
        f"extracted={diagnostics.vacancies_extracted} "
        f"checkpoint={checkpoint_value}"
    )
    if verbose:
        typer.echo(
            "LinkedIn IMAP details: "
            f"checkpoint_before={diagnostics.checkpoint_before or 0} "
            f"checkpoint_after={diagnostics.checkpoint_after or 0} "
            f"highest_uid_seen={diagnostics.highest_uid_seen or 0} "
            f"searched_uids={diagnostics.searched_uids} "
            f"fetch_attempted={diagnostics.fetch_attempted} "
            f"fetch_succeeded={diagnostics.fetch_succeeded} "
            f"decode_succeeded={diagnostics.decode_succeeded} "
            f"rejected_sender={diagnostics.rejected_sender} "
            f"rejected_subject={diagnostics.rejected_subject} "
            f"parse_errors={diagnostics.parse_errors} "
            f"messages_parsed={diagnostics.messages_parsed} "
            f"search_criteria={diagnostics.search_criteria or 'n/a'} "
            f"checkpoint_advanced={diagnostics.checkpoint_advanced} "
            f"uidvalidity_changed={diagnostics.uidvalidity_changed}"
        )
        if diagnostics.classification_counts:
            typer.echo(
                "LinkedIn IMAP classification counts: "
                + " ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(diagnostics.classification_counts.items())
                )
            )
        for event in diagnostics.classification_events:
            typer.echo(f"LinkedIn IMAP message: {event}")
        for event in diagnostics.rejection_events:
            typer.echo(f"LinkedIn IMAP message: {event}")
        typer.echo(
            "LinkedIn IMAP timings: "
            f"connect={diagnostics.timings_ms.get('connect', 0)}ms "
            f"select={diagnostics.timings_ms.get('select', 0)}ms "
            f"search={diagnostics.timings_ms.get('search', 0)}ms "
            f"fetch={diagnostics.timings_ms.get('fetch', 0)}ms "
            f"parse={diagnostics.timings_ms.get('parse', 0)}ms "
            f"checkpoint={diagnostics.timings_ms.get('checkpoint', 0)}ms"
        )


def _collect_pipeline_items(
    *,
    collectors: list[Collector],
    safe_error_formatter: Callable[[str, Exception], str],
) -> PipelineResult:
    items: list[PipelineItem] = []
    for collector in collectors:
        try:
            result = collector.collect()
        except Exception as exc:  # noqa: BLE001
            items.append(
                PipelineItem(
                    source=collector.name,
                    error=safe_error_formatter(collector.name, exc),
                    error_stage="collect",
                )
            )
            continue

        for vacancy in result.vacancies:
            identity = vacancy_identity(vacancy)
            storage_key = _identity_storage_key(identity=identity, vacancy=vacancy)
            items.append(
                PipelineItem(
                    source=result.source or collector.name,
                    vacancy=vacancy,
                    identity=identity,
                    storage_source=storage_key[0] if storage_key else None,
                    storage_external_id=storage_key[1] if storage_key else None,
                )
            )

    seen_keys: set[str] = set()
    for item in items:
        if item.vacancy is None:
            continue
        if item.identity:
            key = item.identity
        else:
            key = f"missing:{item.source}:{len(seen_keys)}"
        if key in seen_keys:
            item.duplicate = True
            continue
        seen_keys.add(key)
    return PipelineResult(items=items)


def _analyze_pipeline_items(
    *,
    analyzer: VacancyAnalyzer,
    seen_jobs: SeenJobsStorage,
    pipeline: PipelineResult,
    limit: int,
    skip_seen: bool,
    mark_seen: bool,
) -> None:
    for item in pipeline.items:
        if item.vacancy is None or item.duplicate:
            continue
        vacancy = item.vacancy
        if item.storage_source is None or item.storage_external_id is None:
            item.invalid_identity = True
            item.preanalysis_outcome = "invalid_identity"
            continue

        try:
            item.already_seen = seen_jobs.is_seen(item.storage_source, item.storage_external_id)
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
            item.error_stage = "seen"
            item.preanalysis_outcome = "error"
            continue

        if item.already_seen and skip_seen:
            item.preanalysis_outcome = "already_seen"
            continue

        if not should_accept_title(vacancy.title):
            item.title_filtered = True
            item.preanalysis_outcome = "prefiltered"
            if mark_seen:
                seen_jobs.mark_seen(item.storage_source, item.storage_external_id)
            continue

        item.preanalysis_outcome = "new"

    analyzable: list[PipelineItem] = [item for item in pipeline.items if item.preanalysis_outcome == "new"]
    for item in analyzable[:limit]:
        item.considered = True
        vacancy = item.vacancy
        if vacancy is None:
            continue
        try:
            evaluation = analyzer.analyze(vacancy.to_analysis_text(), content_completeness="FULL")
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)
            item.error_stage = "analyze"
            logger.error("%s vacancy %s failed: %s", vacancy.source, vacancy.external_id, exc)
            continue

        item.analysis_result = evaluation
        if mark_seen:
            seen_jobs.mark_seen(item.storage_source or vacancy.source, item.storage_external_id or vacancy.external_id)
        decision = evaluation.decision.value
        item.telegram_eligible = decision in {"STRONG_MATCH", "POTENTIAL_MATCH"}


def _deliver_pipeline_items(
    *,
    pipeline: PipelineResult,
    deliveries: TelegramDeliveryStorage,
    telegram_client: TelegramClient,
    chat_id: str,
) -> None:
    for item in pipeline.items:
        if item.vacancy is None:
            continue
        if item.title_filtered:
            _upsert_history_item(
                LinkedInProcessedVacancy(
                    external_id=item.vacancy.external_id,
                    title=item.vacancy.title,
                    company=item.vacancy.company,
                    location=item.vacancy.location,
                    url=item.vacancy.url,
                    content_completeness="FULL",
                    evaluation=None,
                    skipped_by_prefilter=True,
                    source=item.source,
                )
            )
            continue
        if item.analysis_result is None:
            continue

        _upsert_history_item(
            LinkedInProcessedVacancy(
                external_id=item.vacancy.external_id,
                title=item.vacancy.title,
                company=item.vacancy.company,
                location=item.vacancy.location,
                url=item.vacancy.url,
                content_completeness="FULL",
                evaluation=item.analysis_result,
                source=item.source,
            )
        )

        if not item.telegram_eligible:
            continue
        delivery_source = item.storage_source or item.source
        delivery_external_id = item.storage_external_id or item.vacancy.external_id
        already_delivered = deliveries.was_sent(delivery_source, delivery_external_id, chat_id)
        if already_delivered:
            item.telegram_already_delivered = True
            continue
        card = TelegramVacancyCard(
            source=map_source_to_code(item.source),
            external_id=item.vacancy.external_id,
            decision=item.analysis_result.decision.value,
            title=item.vacancy.title,
            company=item.vacancy.company,
            location=item.vacancy.location,
            url=item.vacancy.url,
            match_percentage=item.analysis_result.match_percentage,
            gaps=item.analysis_result.gaps,
            nuances=item.analysis_result.nuances,
            recommended_resume=item.analysis_result.recommended_resume.value,
            content_completeness="FULL",
        )
        try:
            message_ref = telegram_client.send_vacancy_card(card)
        except (TelegramRequestError, ValueError) as exc:
            item.error = str(exc)
            item.error_stage = "telegram"
            logger.error("Telegram send failed for job %s: %s", item.vacancy.external_id, exc)
            continue
        deliveries.save_sent(
            source=delivery_source,
            external_id=delivery_external_id,
            chat_id=chat_id,
            message_id=message_ref.message_id,
        )
        deliveries.mark_history_status(
            source=delivery_source,
            external_id=delivery_external_id,
            status="SENT",
            timestamp_field="sent_at",
        )
        item.telegram_delivered = True


def _pipeline_verbose_outcomes(pipeline: PipelineResult) -> list[str]:
    outcomes: list[str] = []
    for item in pipeline.items:
        if item.vacancy is None or item.duplicate:
            continue
        title = item.vacancy.title
        identity = item.identity or "<missing_identity>"
        if item.preanalysis_outcome == "already_seen":
            outcomes.append(f"ALREADY_SEEN {identity} {title}")
            continue
        if item.preanalysis_outcome == "invalid_identity":
            outcomes.append(f"INVALID_IDENTITY {title}")
            continue
        if item.preanalysis_outcome == "prefiltered":
            outcomes.append(f"PREFILTERED {identity} {title}")
            continue
        if item.preanalysis_outcome == "error":
            outcomes.append(f"ERROR {identity} {item.error or 'preanalysis_error'}")
            continue
        if item.preanalysis_outcome == "new":
            outcomes.append(f"NEW {identity} {title}")
        if item.analysis_result is None:
            continue
        if item.analysis_result.decision == Decision.STRONG_MATCH:
            outcomes.append(f"STRONG {identity} {title}")
        elif item.analysis_result.decision == Decision.POTENTIAL_MATCH:
            outcomes.append(f"POTENTIAL {identity} {title}")
        else:
            outcomes.append(f"IGNORE {identity} {title}")

        if item.telegram_already_delivered:
            outcomes.append(f"ALREADY_DELIVERED {identity} {title}")
        elif item.telegram_delivered:
            outcomes.append(f"SENT {identity} {title}")
    return outcomes


def _ordered_sources(sources: list[str]) -> list[str]:
    preferred = ["linkedin-email", "greenhouse"]
    ranked = [source for source in preferred if source in sources]
    ranked.extend(sorted(source for source in sources if source not in preferred))
    return ranked


def _identity_storage_key(*, identity: str | None, vacancy: NormalizedVacancy) -> tuple[str, str] | None:
    if identity is None:
        return None
    if identity.startswith("url:"):
        return ("url", identity[4:])
    if identity.startswith("fp:"):
        return ("fp", identity[3:])
    if ":" not in identity:
        return None
    source, external_id = identity.split(":", 1)
    source = source.strip()
    external_id = external_id.strip()
    if not source or not external_id:
        return None
    return source, external_id


def _collect_from_collectors(
    *,
    collectors: list[VacancyCollector],
    safe_error_formatter,
) -> CollectCycleReport:
    merged: list[NormalizedVacancy] = []
    seen_keys: set[tuple[str, str, str] | str] = set()
    per_source: dict[str, SourceCycleCounters] = {}

    for collector in collectors:
        source = getattr(collector, "SOURCE", collector.__class__.__name__.lower())
        counters = per_source.setdefault(source, SourceCycleCounters(source=source))
        try:
            extracted, items = _collect_source_items(collector)
            if not getattr(collector, "SOURCE", None) and items:
                inferred_source = items[0].source
                if inferred_source != source:
                    per_source.pop(source, None)
                    source = inferred_source
                    counters = per_source.setdefault(source, SourceCycleCounters(source=source))
            counters.extracted = extracted
            counters.unique = len(items)
        except Exception as exc:  # noqa: BLE001
            counters.failed_message = safe_error_formatter(source, exc)
            counters.errors += 1
            logger.error("Collector %s failed: %s", source, counters.failed_message)
            continue

        for item in items:
            key = item.dedupe_key()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(item)

    return CollectCycleReport(
        merged=merged,
        merged_unique=len(merged),
        per_source=per_source,
    )


def _collect_source_items(collector: VacancyCollector) -> tuple[int, list[NormalizedVacancy]]:
    if hasattr(collector, "_collect_unique_vacancies"):
        items, _emails, _parse_errors, extracted = collector._collect_unique_vacancies()  # noqa: SLF001
        normalized = [
            NormalizedVacancy(
                source=collector.SOURCE,
                external_id=item.external_id,
                title=item.title,
                company=item.company,
                location=item.location,
                employment=None,
                description=item.to_analysis_text(),
                url=item.url,
                published_at=item.received_at.isoformat() if item.received_at else None,
            )
            for item in items
        ]
        return extracted, normalized
    items = collector.collect()
    return len(items), items


def _analyze_collected_vacancies(
    *,
    analyzer: VacancyAnalyzer,
    seen_jobs: SeenJobsStorage,
    vacancies: list[NormalizedVacancy],
    limit: int,
    skip_seen: bool,
    mark_seen: bool,
) -> tuple[LinkedInEmailCollectReport, dict[str, SourceCycleCounters], AnalysisCycleReport]:
    report = LinkedInEmailCollectReport()
    limited = vacancies[:limit]
    report.unique_vacancies = len(limited)
    report.vacancies_extracted = len(vacancies)
    per_source: dict[str, SourceCycleCounters] = {}
    verbose_outcomes: list[str] = []

    for vacancy in limited:
        source_counters = per_source.setdefault(vacancy.source, SourceCycleCounters(source=vacancy.source))
        is_seen = seen_jobs.is_seen(vacancy.source, vacancy.external_id)
        if is_seen:
            report.already_seen += 1
            source_counters.already_seen += 1
            verbose_outcomes.append(f"ALREADY_SEEN {vacancy.title}")
            if skip_seen:
                continue
        report.new_vacancies += 1
        source_counters.new += 1

        if not should_accept_title(vacancy.title):
            report.prefiltered += 1
            source_counters.prefiltered += 1
            verbose_outcomes.append(f"TITLE_FILTER {vacancy.title}")
            report.processed.append(
                LinkedInProcessedVacancy(
                    external_id=vacancy.external_id,
                    title=vacancy.title,
                    company=vacancy.company,
                    location=vacancy.location,
                    url=vacancy.url,
                    content_completeness="FULL",
                    evaluation=None,
                    skipped_by_prefilter=True,
                    source=vacancy.source,
                )
            )
            if mark_seen:
                seen_jobs.mark_seen(vacancy.source, vacancy.external_id)
            continue

        try:
            evaluation = analyzer.analyze(vacancy.to_analysis_text(), content_completeness="FULL")
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            source_counters.errors += 1
            logger.error("%s vacancy %s failed: %s", vacancy.source, vacancy.external_id, exc)
            continue

        if mark_seen:
            seen_jobs.mark_seen(vacancy.source, vacancy.external_id)
        report.analyzed += 1
        source_counters.analyzed += 1
        if evaluation.decision == Decision.STRONG_MATCH:
            report.strong_matches += 1
            source_counters.strong += 1
            verbose_outcomes.append(f"STRONG {vacancy.title}")
        elif evaluation.decision == Decision.POTENTIAL_MATCH:
            report.potential_matches += 1
            source_counters.potential += 1
            verbose_outcomes.append(f"POTENTIAL {vacancy.title}")
        else:
            report.ignored += 1
            source_counters.ignore += 1
            verbose_outcomes.append(f"IGNORE {vacancy.title}")
        report.processed.append(
            LinkedInProcessedVacancy(
                external_id=vacancy.external_id,
                title=vacancy.title,
                company=vacancy.company,
                location=vacancy.location,
                url=vacancy.url,
                content_completeness="FULL",
                evaluation=evaluation,
                source=vacancy.source,
            )
        )

    no_work_reason = _determine_no_work_reason(
        merged_unique=len(limited),
        new=report.new_vacancies,
        already_seen=report.already_seen,
        prefiltered=report.prefiltered,
        analyzed=report.analyzed,
        errors=report.errors,
    )
    return report, per_source, AnalysisCycleReport(
        processed=report.processed,
        analyzed=report.analyzed,
        strong=report.strong_matches,
        potential=report.potential_matches,
        ignore=report.ignored,
        title_filtered=report.prefiltered,
        errors=report.errors,
        no_work_reason=no_work_reason,
        verbose_events=verbose_outcomes,
    )


def _merge_source_counters(
    *,
    collected: dict[str, SourceCycleCounters],
    analyzed: dict[str, SourceCycleCounters],
    known_sources: list[str],
) -> dict[str, SourceCycleCounters]:
    merged: dict[str, SourceCycleCounters] = {}
    for name in known_sources:
        if name in collected or name in analyzed:
            merged[name] = SourceCycleCounters(source=name)

    for source, counters in collected.items():
        target = merged.setdefault(source, SourceCycleCounters(source=source))
        target.extracted = counters.extracted
        target.unique = counters.unique
        target.errors += counters.errors
        target.failed_message = counters.failed_message

    for source, counters in analyzed.items():
        target = merged.setdefault(source, SourceCycleCounters(source=source))
        target.new += counters.new
        target.already_seen += counters.already_seen
        target.prefiltered += counters.prefiltered
        target.analyzed += counters.analyzed
        target.strong += counters.strong
        target.potential += counters.potential
        target.ignore += counters.ignore
        target.errors += counters.errors
    return merged


def _determine_no_work_reason(
    *,
    merged_unique: int,
    new: int,
    already_seen: int,
    prefiltered: int,
    analyzed: int,
    errors: int,
) -> str | None:
    if analyzed > 0:
        return None
    if errors > 0:
        return f"processing errors={errors}."
    if merged_unique == 0:
        return "collector returned no new vacancies."
    if new == 0 and already_seen == merged_unique:
        return "all unique vacancies were already seen."
    if new > 0 and prefiltered == new:
        return "all candidates were removed by title filter."
    return "see cycle counters."


def _runtime_secrets(settings: Settings) -> list[str]:
    return [
        settings.llm_api_key,
        settings.linkedin_email_password,
        settings.telegram_bot_token,
    ]


def _sanitize_text(value: str, *, secrets: list[str]) -> str:
    sanitized = value
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "***")
    return sanitized


def _format_collector_error(*, source: str, exc: Exception, imap_host: str, secrets: list[str]) -> str:
    if source == "linkedin-email":
        if isinstance(exc, EmailAuthenticationError):
            return "IMAP authentication failure."
        if isinstance(exc, EmailConnectionError):
            message = str(exc).lower()
            if "select mailbox" in message or "folder" in message:
                return "IMAP mailbox selection failure."
            if "timeout" in message:
                return f"IMAP connection timeout to {imap_host}."
            if "ssl" in message or "connection failed" in message:
                return f"IMAP SSL/network failure to {imap_host}."
            return f"IMAP connection failed to {imap_host}."
    return _sanitize_text(str(exc), secrets=secrets) or "collector failed."


def _format_telegram_error(exc: Exception, *, secrets: list[str]) -> str:
    if isinstance(exc, TelegramRequestError):
        parts: list[str] = []
        if exc.method:
            parts.append(f"method={exc.method}")
        if exc.http_status is not None:
            parts.append(f"HTTP {exc.http_status}")
        if exc.error_code is not None:
            parts.append(f"error_code={exc.error_code}")
        if exc.description:
            safe_description = _sanitize_text(exc.description, secrets=secrets)
            parts.append(f'description="{safe_description}"')
        if parts:
            return "\n  ".join(parts)
    message = _sanitize_text(str(exc), secrets=secrets).lower()
    if "http 409" in message:
        return "HTTP 409 conflict — another getUpdates poller may be running."
    if "http 401" in message:
        return "HTTP 401 unauthorized."
    if "timeout" in message:
        return "timeout."
    http_match = re.search(r"http\s+(\d{3})", message)
    if http_match:
        return f"HTTP {http_match.group(1)}."
    if "request failed" in message:
        return "network failure."
    return _sanitize_text(str(exc), secrets=secrets)


def _is_non_retryable_callback_ack_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramRequestError):
        return False
    if exc.method != "answerCallbackQuery":
        return False
    if exc.http_status != 400:
        return False
    description = (exc.description or "").casefold()
    phrases = (
        "query is too old",
        "response timeout expired",
        "query id is invalid",
    )
    return any(phrase in description for phrase in phrases)


@app.command("list-imap-folders")
def list_imap_folders() -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=settings.linkedin_email_mark_as_read,
    )

    try:
        folders = email_client.list_mailboxes()
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к IMAP.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo("Available IMAP folders:\n")
    for folder in folders:
        typer.echo(folder)


@app.command("preview-linkedin-email")
def preview_linkedin_email(
    limit_emails: int = typer.Option(3, "--limit-emails", min=1),
    limit_vacancies: int = typer.Option(20, "--limit-vacancies", min=1),
    save_html: bool = typer.Option(False, "--save-html"),
) -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.secho(
            f"Отсутствует обязательная конфигурация LLM: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2) from exc

    email_client = EmailIMAPClient(
        host=settings.linkedin_email_imap_host,
        port=settings.linkedin_email_imap_port,
        username=settings.linkedin_email_username,
        password=settings.linkedin_email_password,
        folder=settings.linkedin_email_folder,
        search_days=settings.linkedin_email_search_days,
        mark_as_read=False,
    )

    try:
        messages = email_client.fetch_linkedin_messages()
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к IMAP.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    limited_messages = messages[:limit_emails]
    typer.echo("--------------------------------------------------\n")
    typer.echo(f"Found {len(limited_messages)} LinkedIn Job Alert emails\n")

    processed_emails = 0
    vacancies_extracted = 0
    duplicate_job_ids = 0
    parsing_errors = 0
    printed_vacancies = 0
    structured_cards = 0
    fallback_urls = 0
    seen_job_ids: set[str] = set()
    debug_dir = Path("data/debug")

    for email_index, raw_message in enumerate(limited_messages, start=1):
        processed_emails += 1
        typer.echo(f"Email #{email_index}\n")
        typer.echo("Subject:")
        typer.echo(raw_message.subject or "n/a")
        typer.echo("\nFrom:")
        typer.echo(raw_message.from_address or "n/a")
        typer.echo("\nReceived:")
        typer.echo(_format_received(raw_message.received_at))

        if save_html:
            html_content, _ = extract_email_text_parts(raw_message.email_message)
            if html_content.strip():
                _save_debug_html(debug_dir=debug_dir, html_content=html_content)

        try:
            vacancies = parse_linkedin_email(raw_message)
        except Exception as exc:  # noqa: BLE001
            parsing_errors += 1
            typer.echo(f"\nWarning: failed to parse email ({exc})")
            typer.echo("\n--------------------------------------------------\n")
            continue

        vacancies_extracted += len(vacancies)
        typer.echo(f"\nVacancies found: {len(vacancies)}\n")

        for item in vacancies:
            if printed_vacancies >= limit_vacancies:
                break
            if item.external_id in seen_job_ids:
                duplicate_job_ids += 1
                continue
            seen_job_ids.add(item.external_id)
            printed_vacancies += 1
            typer.echo("--------------------------------\n")
            typer.echo(f"{printed_vacancies}.\n")
            typer.echo("Job ID:")
            typer.echo(item.external_id)
            typer.echo("\nTitle:")
            typer.echo(item.title or "n/a")
            typer.echo("\nCompany:")
            typer.echo(item.company or "n/a")
            typer.echo("\nLocation:")
            typer.echo(item.location or "n/a")
            typer.echo("\nURL:")
            typer.echo(item.url or "n/a")
            typer.echo("\nCompleteness:")
            typer.echo(item.content_completeness.value)
            typer.echo("\nParser source:")
            typer.echo(item.parser_source.value)
            typer.echo("")
            if item.parser_source.value == "STRUCTURED_CARD":
                structured_cards += 1
            else:
                fallback_urls += 1

        typer.echo("--------------------------------------------------\n")
        if printed_vacancies >= limit_vacancies:
            break

    typer.echo("Summary\n")
    typer.echo(f"Emails processed: {processed_emails}")
    typer.echo(f"Vacancies extracted: {vacancies_extracted}")
    typer.echo(f"Duplicate job IDs: {duplicate_job_ids}")
    typer.echo(f"Structured cards: {structured_cards}")
    typer.echo(f"Fallback URLs: {fallback_urls}")
    typer.echo(f"Parsing errors: {parsing_errors}")


def _process_callback_update(
    *,
    update: dict,
    client: TelegramClient,
    storage: TelegramDeliveryStorage,
    configured_chat_id: str,
    resumes_dir: Path | None = None,
    resume_cache_service: ResumeCacheService | None = None,
    timing_logger: Callable[[str], None] | None = None,
) -> None:
    received_at = time.monotonic()
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return

    callback_id = str(callback.get("id", ""))
    callback_data = str(callback.get("data", ""))
    message = callback.get("message", {})
    if not isinstance(message, dict):
        return
    chat = message.get("chat", {})
    if not isinstance(chat, dict):
        return
    callback_chat_id = str(chat.get("id", ""))

    if callback_chat_id != str(configured_chat_id):
        if callback_id:
            client.answer_callback_query(callback_id, text="Действие недоступно для этого чата")
        return

    try:
        action, source, external_id = parse_callback_data(callback_data)
    except ValueError:
        if callback_id:
            client.answer_callback_query(callback_id, text="Некорректное действие")
        return

    message_id = int(message.get("message_id", 0))
    title, company, url = _resolve_card_context(
        storage=storage,
        message=message,
        source=source,
        external_id=external_id,
    )

    get_delivery = getattr(storage, "get_delivery", None)
    current_status = None
    if callable(get_delivery):
        current = get_delivery(source, external_id)
        current_status = current.status if current is not None else None

    answered = False
    answer_at: float | None = None
    answer_state = "no"

    if timing_logger is not None:
        timing_logger(f"Callback received {callback_data}")

    def answer_once(text: str | None) -> None:
        nonlocal answered, answer_at, answer_state
        if answered or not callback_id:
            return
        try:
            client.answer_callback_query(callback_id, text=text)
            answered = True
            answer_at = time.monotonic()
            answer_state = "yes"
        except TelegramRequestError as exc:
            if _is_non_retryable_callback_ack_error(exc):
                answered = True
                answer_at = time.monotonic()
                answer_state = "expired"
                if timing_logger is not None:
                    timing_logger(f"Callback acknowledgement expired {source}:{external_id}; update consumed")
                return
            raise

    try:
        if action == "skip":
            if current_status == STATUS_SKIPPED:
                answer_once("Вакансия уже пропущена")
                return
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_SKIPPED,
                history_status=STATUS_SKIPPED,
                timestamp_field="skipped_at",
            )
            answer_once("Вакансия пропущена")
            _edit_archived_card(
                client=client,
                chat_id=configured_chat_id,
                message_id=message_id,
                url=url,
                title=title,
                company=company,
                applied=False,
            )
            _cleanup_aux_messages(
                storage=storage,
                client=client,
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
            )
        elif action == "applied":
            if current_status == STATUS_APPLIED:
                answer_once("Отклик уже отмечен")
                return
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_APPLIED,
                history_status=STATUS_APPLIED,
                timestamp_field="applied_at",
            )
            answer_once("Отклик отмечен как отправленный")
            _edit_archived_card(
                client=client,
                chat_id=configured_chat_id,
                message_id=message_id,
                url=url,
                title=title,
                company=company,
                applied=True,
            )
            _cleanup_aux_messages(
                storage=storage,
                client=client,
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
            )
        elif action == "prepare":
            if current_status in {STATUS_PREPARE_REQUESTED, STATUS_PREPARING}:
                answer_once("Уже в обработке")
                if message_id > 0 and url:
                    try:
                        client.edit_message_text(
                            chat_id=configured_chat_id,
                            message_id=message_id,
                            text=build_loading_text(title=title, company=company),
                            buttons=build_loading_buttons(url),
                        )
                    except (TelegramRequestError, TelegramMessageNotModifiedError, ValueError):
                        _ = None
                return
            if current_status == STATUS_PREPARED:
                answer_once("Отклик уже готов")
                return
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_PREPARE_REQUESTED,
                history_status=STATUS_PREPARE_REQUESTED,
                timestamp_field=None,
            )
            answer_once("Добавлено в очередь на подготовку отклика")
            if message_id > 0 and url:
                try:
                    edit_start = time.monotonic()
                    client.edit_message_text(
                        chat_id=configured_chat_id,
                        message_id=message_id,
                        text=build_loading_text(title=title, company=company),
                        buttons=build_loading_buttons(url),
                    )
                    if timing_logger is not None:
                        timing_logger(
                            f"Callback {action}:{source}:{external_id} editMessageText +{int((time.monotonic() - edit_start) * 1000)}ms"
                        )
                except TelegramMessageNotModifiedError:
                    _ = None
            _enqueue_prepare_priority(storage=storage, source=source, external_id=external_id)
        elif action == "copy":
            get_preparation = getattr(storage, "get_preparation", None)
            prep = get_preparation(source, external_id) if callable(get_preparation) else None
            if prep is None or prep.status != STATUS_PREPARED or not prep.cover_letter:
                answer_once("Отклик еще не готов")
                return
            existing_cover_id = getattr(prep, "cover_letter_message_id", None)
            if isinstance(existing_cover_id, int) and existing_cover_id > 0:
                answer_once("Cover letter already sent below this vacancy.")
                return
            sent_ref = client.send_text_message(
                prep.cover_letter,
                chat_id=configured_chat_id,
                reply_to_message_id=message_id if message_id > 0 else None,
            )
            set_aux = getattr(storage, "set_preparation_aux_message_id", None)
            if callable(set_aux):
                set_aux(
                    source=source,
                    external_id=external_id,
                    cover_letter_message_id=sent_ref.message_id,
                )
            answer_once("Cover letter sent")
        else:  # action == "resume"
            get_preparation = getattr(storage, "get_preparation", None)
            prep = get_preparation(source, external_id) if callable(get_preparation) else None
            if prep is None or prep.status != STATUS_PREPARED or not prep.resume_name:
                answer_once("Resume PDF not found.")
                return
            existing_resume_id = getattr(prep, "resume_message_id", None)
            if isinstance(existing_resume_id, int) and existing_resume_id > 0:
                answer_once("Resume already sent below this vacancy.")
                return
            cache = resume_cache_service
            if cache is None:
                if resumes_dir is None:
                    answer_once("Resume PDF not found.")
                    return
                cache = ResumeCacheService(
                    resumes_dir=resumes_dir,
                    storage=storage,
                    telegram_client=client,
                )
            resume_result = cache.get_or_upload(
                resume_name=prep.resume_name,
                chat_id=configured_chat_id,
            )
            if resume_result.missing:
                answer_once("Resume PDF not found.")
                return
            caption = _build_resume_caption(
                resume_name=prep.resume_name,
                title=title,
                company=company,
            )
            sent_doc = None
            if resume_result.telegram_file_id:
                try:
                    sent_doc = client.send_document_by_file_id(
                        chat_id=configured_chat_id,
                        file_id=resume_result.telegram_file_id,
                        caption=caption,
                        reply_to_message_id=message_id if message_id > 0 else None,
                    )
                except TelegramRequestError:
                    sent_doc = None
            if sent_doc is None:
                if resumes_dir is None:
                    answer_once("Resume PDF not found.")
                    return
                resume_path, _resume_error = resolve_resume_path(resumes_dir, prep.resume_name)
                if resume_path is None:
                    answer_once("Resume PDF not found.")
                    return
                sent_doc = client.send_document(
                    file_path=str(resume_path),
                    caption=caption,
                    chat_id=configured_chat_id,
                    reply_to_message_id=message_id if message_id > 0 else None,
                )
                stat = resume_path.stat()
                save_cache = getattr(storage, "save_resume_cache", None)
                if callable(save_cache):
                    save_cache(
                        resume_name=prep.resume_name,
                        file_path=str(resume_path),
                        file_mtime_ns=int(stat.st_mtime_ns),
                        file_size=int(stat.st_size),
                        telegram_file_id=sent_doc.file_id,
                        telegram_file_unique_id=sent_doc.file_unique_id,
                    )
            set_aux = getattr(storage, "set_preparation_aux_message_id", None)
            if callable(set_aux):
                set_aux(
                    source=source,
                    external_id=external_id,
                    resume_message_id=sent_doc.message_id,
                )
            answer_once("Resume sent")
    except (ValueError, KeyError, TelegramRequestError, OSError):
        if not answered:
            answer_once("Не удалось обновить статус")
        return
    finally:
        if timing_logger is not None:
            ack_ms = int((answer_at - received_at) * 1000) if answer_at is not None else -1
            total_ms = int((time.monotonic() - received_at) * 1000)
            timing_logger(
                f"Callback {action}:{source}:{external_id} ack_ms={ack_ms} total_ms={total_ms} answered={answer_state}"
            )

    if action in {"copy", "resume", "prepare"}:
        return


def _edit_archived_card(
    *,
    client: TelegramClient,
    chat_id: str,
    message_id: int,
    url: str | None,
    title: str,
    company: str | None,
    applied: bool,
) -> None:
    if message_id <= 0:
        return
    buttons = build_archived_buttons(url) if url else []
    try:
        client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=format_archived_vacancy_html(applied=applied, title=title, company=company),
            buttons=buttons,
        )
    except TelegramMessageNotModifiedError:
        _ = None


def _build_resume_caption(*, resume_name: str, title: str, company: str | None) -> str:
    company_part = company or "n/a"
    return f"Resume · {resume_name.replace('-', ' ').title()}\n{title} · {company_part}"


def _cleanup_aux_messages(
    *,
    storage: TelegramDeliveryStorage,
    client: TelegramClient,
    source: str,
    external_id: str,
    chat_id: str,
) -> None:
    get_preparation = getattr(storage, "get_preparation", None)
    prep = get_preparation(source, external_id) if callable(get_preparation) else None
    if prep is None:
        return
    resume_message_id = getattr(prep, "resume_message_id", None)
    cover_message_id = getattr(prep, "cover_letter_message_id", None)
    for message_id in [resume_message_id, cover_message_id]:
        if not isinstance(message_id, int) or message_id <= 0:
            continue
        try:
            client.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramRequestError as exc:
            logger.warning("Auxiliary Telegram message cleanup failed for %s:%s: %s", source, external_id, exc)
    clear_aux = getattr(storage, "clear_preparation_aux_message_ids", None)
    if callable(clear_aux):
        clear_aux(source=source, external_id=external_id)


def _resolve_card_context(
    *,
    storage: TelegramDeliveryStorage,
    message: dict,
    source: str,
    external_id: str,
) -> tuple[str, str | None, str | None]:
    title: str | None = None
    company: str | None = None
    url: str | None = None

    get_history = getattr(storage, "get_history_title_company_url", None)
    if callable(get_history):
        title, company, url = get_history(source, external_id)

    get_preparation = getattr(storage, "get_preparation", None)
    prep = get_preparation(source, external_id) if callable(get_preparation) else None
    if prep is not None:
        prep_title = getattr(prep, "vacancy_title", None)
        prep_company = getattr(prep, "vacancy_company", None)
        prep_url = getattr(prep, "vacancy_url", None)
        if not title and prep_title:
            title = prep_title
        if not company and prep_company:
            company = prep_company
        if not url and prep_url:
            url = prep_url

    if not url:
        url = _extract_url_button(message)

    if not title:
        parsed_title, parsed_company = _extract_title_company_from_message(message)
        title = parsed_title or f"Vacancy {external_id}"
        if not company:
            company = parsed_company
    return title, company, url


def _extract_title_company_from_message(message: dict) -> tuple[str | None, str | None]:
    raw_text = message.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, None
    plain = re.sub(r"<[^>]+>", "", raw_text)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    if not lines:
        return None, None
    filtered = [
        line
        for line in lines
        if line
        and not line.startswith(("✅", "❌", "⏳"))
        and "Preparing application" not in line
        and "Application Ready" not in line
    ]
    if not filtered:
        return None, None
    title = filtered[0]
    company = filtered[1] if len(filtered) > 1 else None
    return title, company


def _extract_url_button(message: dict) -> str | None:
    markup = message.get("reply_markup", {})
    if not isinstance(markup, dict):
        return None
    keyboard = markup.get("inline_keyboard", [])
    if not isinstance(keyboard, list):
        return None
    for row in keyboard:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            url = button.get("url")
            if isinstance(url, str):
                try:
                    return validate_linkedin_job_url(url)
                except ValueError:
                    continue
    return None


def _require_telegram_settings(settings: Settings) -> None:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return
    typer.secho(
        "Для этой команды требуются TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.",
        err=True,
        fg=typer.colors.RED,
    )
    raise typer.Exit(code=2)


def _print_prepared_dry_run(prepared: PreparedApplication) -> None:
    typer.echo(f"PREPARED {prepared.title}")
    typer.echo("Resume:")
    typer.echo(
        f"{prepared.recommended_resume} ({'found' if prepared.resume_path else 'PDF not found'})"
    )
    typer.echo(f"Language: {prepared.language}")
    typer.echo("")
    typer.echo("Cover letter:")
    typer.echo(prepared.cover_letter)
    typer.echo("")


def _format_received(received_at) -> str:
    if received_at is None:
        return "n/a"
    converted = received_at.astimezone(timezone.utc)
    return converted.strftime("%Y-%m-%d %H:%M UTC")


def _save_debug_html(debug_dir: Path, html_content: str) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _format_received_filename()
    candidate = debug_dir / f"linkedin_email_{timestamp}.html"
    counter = 1
    while candidate.exists():
        candidate = debug_dir / f"linkedin_email_{timestamp}_{counter}.html"
        counter += 1
    candidate.write_text(html_content, encoding="utf-8")


def _format_received_filename() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _normalize_status_or_exit(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in ALLOWED_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_STATUSES))
        typer.secho(f"Unknown status: {value}. Allowed: {allowed}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)
    return normalized


def _print_delivery_debug_table(rows: list[TelegramDeliveryRecord]) -> None:
    headers = ("external_id", "source", "status", "chat_id", "message_id", "sent_at")
    rendered_rows = [
        (
            record.external_id,
            record.source,
            record.status,
            record.chat_id,
            str(record.message_id),
            record.sent_at,
        )
        for record in rows
    ]
    widths = [len(header) for header in headers]
    for row in rendered_rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    header_line = "  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    typer.echo(header_line)
    typer.echo("  ".join("-" * width for width in widths))
    for row in rendered_rows:
        typer.echo("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def _sync_application_history(items: list[LinkedInProcessedVacancy]) -> None:
    for item in items:
        _upsert_history_item(item)


def _upsert_history_item(item: LinkedInProcessedVacancy) -> None:
    evaluation = item.evaluation
    TelegramDeliveryStorage().upsert_application_history(
        source=item.source,
        external_id=item.external_id,
        title=item.title,
        company=item.company,
        location=item.location,
        url=item.url,
        decision=evaluation.decision.value if evaluation else None,
        recommended_resume=evaluation.recommended_resume.value if evaluation else None,
    )


def _safe_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _print_application_history_table(rows: list[ApplicationHistoryRecord]) -> None:
    headers = ("date", "status", "title", "company", "source", "external_id")
    rendered_rows = [
        (
            row.display_date,
            row.current_status,
            row.title or "n/a",
            row.company or "n/a",
            row.source,
            row.external_id,
        )
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in rendered_rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    typer.echo("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    typer.echo("  ".join("-" * width for width in widths))
    for row in rendered_rows:
        typer.echo("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def _normalize_resume_name_or_exit(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in KNOWN_RESUME_NAMES:
        allowed = ", ".join(KNOWN_RESUME_NAMES)
        typer.secho(
            f"Unknown resume identifier: {value}. Allowed: {allowed}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)
    return normalized


def _print_resume_cache_table(rows: list[TelegramResumeCacheRecord]) -> None:
    headers = ("resume_name", "file_path", "file_size", "cached_at", "file_id")
    rendered_rows = [
        (
            row.resume_name,
            row.file_path,
            str(row.file_size),
            row.cached_at,
            _preview_file_id(row.telegram_file_id),
        )
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in rendered_rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    typer.echo("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    typer.echo("  ".join("-" * width for width in widths))
    for row in rendered_rows:
        typer.echo("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def _preview_file_id(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 12:
        return cleaned
    return f"{cleaned[:12]}..."


@dataclass(frozen=True)
class PreparationRunResult:
    queue_items: int
    generated_packages: int
    prepared_successfully: int
    telegram_sent: int
    errors_count: int
    pdf_cached: int
    pdf_uploaded: int
    pdf_missing: int
    pdf_errors: int


@dataclass(frozen=True)
class _PrepareOneResult:
    generated_packages: int = 0
    prepared_successfully: int = 0
    telegram_sent: int = 0
    errors_count: int = 0
    pdf_cached: int = 0
    pdf_uploaded: int = 0
    pdf_missing: int = 0
    pdf_errors: int = 0


@dataclass
class PipelineItem:
    source: str
    vacancy: NormalizedVacancy | None = None
    identity: str | None = None
    storage_source: str | None = None
    storage_external_id: str | None = None
    duplicate: bool = False
    considered: bool = False
    preanalysis_outcome: str | None = None
    already_seen: bool = False
    invalid_identity: bool = False
    title_filtered: bool = False
    analysis_result: VacancyEvaluation | None = None
    telegram_eligible: bool = False
    telegram_delivered: bool = False
    telegram_already_delivered: bool = False
    error: str | None = None
    error_stage: str | None = None

    @property
    def title(self) -> str:
        if self.vacancy is None:
            return ""
        return self.vacancy.title


@dataclass
class PipelineResult:
    items: list[PipelineItem]

    def _source_items(self, source: str) -> list[PipelineItem]:
        return [item for item in self.items if item.source == source]

    def sources(self) -> list[str]:
        return sorted({item.source for item in self.items})

    def extracted(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.vacancy is not None)

    def unique(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.vacancy is not None and not item.duplicate)

    def new(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.preanalysis_outcome == "new")

    def already_seen(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.preanalysis_outcome == "already_seen")

    def invalid_identity(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.preanalysis_outcome == "invalid_identity")

    def title_filtered(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.preanalysis_outcome == "prefiltered")

    def errors_before_analysis(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.preanalysis_outcome == "error")

    def analyzed(self, source: str) -> int:
        return sum(1 for item in self._source_items(source) if item.analysis_result is not None)

    def strong(self, source: str) -> int:
        return sum(
            1
            for item in self._source_items(source)
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.STRONG_MATCH
        )

    def potential(self, source: str) -> int:
        return sum(
            1
            for item in self._source_items(source)
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.POTENTIAL_MATCH
        )

    def ignore(self, source: str) -> int:
        return sum(
            1
            for item in self._source_items(source)
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.IGNORE
        )

    def eligible(self) -> int:
        return sum(1 for item in self.items if item.telegram_eligible)

    def sent(self) -> int:
        return sum(1 for item in self.items if item.telegram_delivered)

    def already_delivered(self) -> int:
        return sum(1 for item in self.items if item.telegram_already_delivered)

    def telegram_errors(self) -> int:
        return sum(1 for item in self.items if item.error_stage == "telegram")

    def stage_errors(self, source: str, stage: str) -> int:
        return sum(1 for item in self._source_items(source) if item.error_stage == stage)

    def has_collect_error(self, source: str) -> bool:
        return any(item.error_stage == "collect" for item in self._source_items(source))

    def collect_error_message(self, source: str) -> str | None:
        for item in self._source_items(source):
            if item.error_stage == "collect":
                return item.error
        return None

    def merged_unique(self) -> int:
        return sum(1 for item in self.items if item.vacancy is not None and not item.duplicate)

    def analyzed_total(self) -> int:
        return sum(1 for item in self.items if item.analysis_result is not None)

    def strong_total(self) -> int:
        return sum(
            1
            for item in self.items
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.STRONG_MATCH
        )

    def potential_total(self) -> int:
        return sum(
            1
            for item in self.items
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.POTENTIAL_MATCH
        )

    def ignore_total(self) -> int:
        return sum(
            1
            for item in self.items
            if item.analysis_result is not None
            and getattr(item.analysis_result, "decision", None) == Decision.IGNORE
        )

    def title_filtered_total(self) -> int:
        return sum(1 for item in self.items if item.preanalysis_outcome == "prefiltered")

    def invalid_identity_total(self) -> int:
        return sum(1 for item in self.items if item.preanalysis_outcome == "invalid_identity")

    def processing_errors_total(self) -> int:
        return sum(1 for item in self.items if item.preanalysis_outcome == "error" or item.error_stage == "analyze")

    def no_work_reason(self) -> str | None:
        analyzed = self.analyzed_total()
        if analyzed > 0:
            return None
        errors = self.processing_errors_total()
        merged_unique = self.merged_unique()
        already_seen = sum(1 for item in self.items if item.preanalysis_outcome == "already_seen")
        invalid_identity = self.invalid_identity_total()
        prefiltered = self.title_filtered_total()
        new_count = sum(1 for item in self.items if item.preanalysis_outcome == "new")
        accounted = already_seen + invalid_identity + prefiltered + new_count + sum(
            1 for item in self.items if item.preanalysis_outcome == "error"
        )
        title_filtered = self.title_filtered_total()
        if errors > 0:
            return f"processing errors={errors}."
        if merged_unique == 0:
            return "collector returned no new vacancies."
        if accounted != merged_unique:
            return f"Pipeline accounting error: unique={merged_unique} accounted={accounted} missing={merged_unique - accounted}."
        if already_seen == merged_unique:
            return "all unique vacancies were already seen."
        if invalid_identity > 0 and already_seen + invalid_identity == merged_unique:
            return f"{invalid_identity} vacancies had no usable identity."
        if title_filtered == merged_unique - already_seen - invalid_identity:
            return "all candidates were removed by title filter."
        if new_count == 0:
            return f"new=0 already_seen={already_seen} prefiltered={title_filtered}."
        return "see cycle counters."

    def validate_accounting(self) -> None:
        for source in self.sources():
            unique = self.unique(source)
            already_seen = self.already_seen(source)
            invalid_identity = self.invalid_identity(source)
            prefiltered = self.title_filtered(source)
            new_count = self.new(source)
            errors = self.errors_before_analysis(source)
            assert unique == (already_seen + invalid_identity + prefiltered + new_count + errors), (
                f"Unbalanced accounting for {source}: unique={unique}, "
                f"already_seen={already_seen}, invalid_identity={invalid_identity}, "
                f"prefiltered={prefiltered}, new={new_count}, errors_before_analysis={errors}"
            )


@dataclass(frozen=True)
class RuntimeCollector:
    name: str
    collect_fn: Callable[[], list[NormalizedVacancy]]
    diagnostics_fn: Callable[[], LinkedInSyncDiagnostics] | None = None

    def collect(self) -> CollectorResult:
        return CollectorResult(source=self.name, vacancies=self.collect_fn())

    def diagnostics(self) -> LinkedInSyncDiagnostics | None:
        if self.diagnostics_fn is None:
            return None
        return self.diagnostics_fn()


@dataclass
class SourceCycleCounters:
    source: str
    extracted: int = 0
    unique: int = 0
    new: int = 0
    already_seen: int = 0
    prefiltered: int = 0
    analyzed: int = 0
    strong: int = 0
    potential: int = 0
    ignore: int = 0
    errors: int = 0
    failed_message: str | None = None


@dataclass
class CollectCycleReport:
    merged: list[NormalizedVacancy]
    merged_unique: int
    per_source: dict[str, SourceCycleCounters]


@dataclass(frozen=True)
class AnalysisCycleReport:
    processed: list[LinkedInProcessedVacancy]
    analyzed: int
    strong: int
    potential: int
    ignore: int
    title_filtered: int
    errors: int
    no_work_reason: str | None
    verbose_events: list[str]


@dataclass(frozen=True)
class TelegramCycleReport:
    eligible: int
    already_delivered: int
    sent: int
    send_errors: int
    verbose_events: list[str]


class _JobApplierLock:
    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, str(os.getpid()).encode("utf-8"))
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                _ = None
            self._fd = None
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            _ = None


def _run_log(message: str) -> None:
    stamp = _format_log_time()
    typer.echo(f"[{stamp}] {message}")


def _format_log_time() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _enqueue_prepare_priority(*, storage: TelegramDeliveryStorage, source: str, external_id: str) -> None:
    get_state = getattr(storage, "get_state", None)
    set_state = getattr(storage, "set_state", None)
    if not callable(get_state) or not callable(set_state):
        return
    raw = get_state("prepare_priority_queue")
    items: list[str] = []
    if isinstance(raw, str) and raw.strip():
        items = [entry for entry in raw.split("\n") if entry.strip()]
    key = f"{source}:{external_id}"
    if key not in items:
        items.append(key)
    set_state("prepare_priority_queue", "\n".join(items))


def _drain_prepare_priorities(*, storage: TelegramDeliveryStorage) -> list[tuple[str, str]]:
    get_state = getattr(storage, "get_state", None)
    set_state = getattr(storage, "set_state", None)
    if not callable(get_state) or not callable(set_state):
        return []
    raw = get_state("prepare_priority_queue")
    if not isinstance(raw, str) or not raw.strip():
        return []
    pairs: list[tuple[str, str]] = []
    for entry in raw.split("\n"):
        value = entry.strip()
        if not value or ":" not in value:
            continue
        source, external_id = value.split(":", 1)
        if source and external_id:
            pairs.append((source, external_id))
    set_state("prepare_priority_queue", "")
    return pairs


def _poll_telegram_actions_once(
    *,
    client: TelegramClient,
    storage: TelegramDeliveryStorage,
    configured_chat_id: str,
    offset: int | None,
    timeout: int,
    resumes_dir: Path | None = None,
    resume_cache_service: ResumeCacheService | None = None,
    timing_logger: Callable[[str], None] | None = None,
) -> tuple[int | None, int]:
    updates = client.get_updates(offset=offset, timeout=timeout)
    next_offset = offset
    prepare_requests = 0
    for update in updates:
        update_id = int(update.get("update_id", 0))
        try:
            callback = update.get("callback_query", {})
            callback_data = callback.get("data", "") if isinstance(callback, dict) else ""
            if isinstance(callback_data, str):
                try:
                    action, _source, _external_id = parse_callback_data(callback_data)
                    if action == "prepare":
                        prepare_requests += 1
                except ValueError:
                    _ = None
            _process_callback_update(
                update=update,
                client=client,
                storage=storage,
                configured_chat_id=configured_chat_id,
                resumes_dir=resumes_dir,
                resume_cache_service=resume_cache_service,
                timing_logger=timing_logger,
            )
        except Exception as exc:  # noqa: BLE001
            if timing_logger is not None:
                safe = _format_telegram_error(exc, secrets=[])
                timing_logger(f"Callback processing failed update_id={update_id}: {safe}")
        finally:
            next_offset = max(next_offset or 0, update_id + 1)
    if updates and next_offset is not None:
        storage.set_state("telegram_update_offset", str(next_offset))
    return next_offset, prepare_requests


def _prepare_requested_applications(
    *,
    settings: Settings,
    service: PreparationService,
    storage: TelegramDeliveryStorage,
    telegram_client: TelegramClient | None,
    limit: int,
    dry_run: bool,
    print_dry_run_items: bool,
    resume_cache_service: ResumeCacheService | None = None,
    timing_logger: Callable[[str], None] | None = None,
    priority_vacancy_keys: list[tuple[str, str]] | None = None,
) -> PreparationRunResult:
    queue = storage.list_by_status(
        chat_id=settings.telegram_chat_id if settings.telegram_chat_id else "0",
        status=STATUS_PREPARE_REQUESTED,
        limit=limit,
    )
    queue_items = len(queue)
    ordered_keys: list[tuple[str, str]] = []
    seen_keys: set[tuple[str, str]] = set()
    for key in priority_vacancy_keys or []:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)
    for key in queue:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)
    generated_packages = 0
    prepared_successfully = 0
    telegram_sent = 0
    errors_count = 0
    pdf_cached = 0
    pdf_uploaded = 0
    pdf_missing = 0
    pdf_errors = 0

    _ = resume_cache_service

    for source, external_id in ordered_keys:
        one = _prepare_one_application(
            source=source,
            external_id=external_id,
            settings=settings,
            service=service,
            storage=storage,
            telegram_client=telegram_client,
            dry_run=dry_run,
            print_dry_run_items=print_dry_run_items,
            timing_logger=timing_logger,
        )
        generated_packages += one.generated_packages
        prepared_successfully += one.prepared_successfully
        telegram_sent += one.telegram_sent
        errors_count += one.errors_count
        pdf_cached += one.pdf_cached
        pdf_uploaded += one.pdf_uploaded
        pdf_missing += one.pdf_missing
        pdf_errors += one.pdf_errors

    return PreparationRunResult(
        queue_items=queue_items,
        generated_packages=generated_packages,
        prepared_successfully=prepared_successfully,
        telegram_sent=telegram_sent,
        errors_count=errors_count,
        pdf_cached=pdf_cached,
        pdf_uploaded=pdf_uploaded,
        pdf_missing=pdf_missing,
        pdf_errors=pdf_errors,
    )


def _prepare_one_application(
    *,
    source: str,
    external_id: str,
    settings: Settings,
    service: PreparationService,
    storage: TelegramDeliveryStorage,
    telegram_client: TelegramClient | None,
    dry_run: bool,
    print_dry_run_items: bool,
    timing_logger: Callable[[str], None] | None,
) -> _PrepareOneResult:
    if not dry_run:
        get_delivery = getattr(storage, "get_delivery", None)
        claim = getattr(storage, "claim_for_preparation", None)
        current_status = None
        if callable(get_delivery):
            delivery = get_delivery(source, external_id)
            current_status = delivery.status if delivery is not None else None
        if current_status in {STATUS_PREPARED, STATUS_APPLIED, STATUS_SKIPPED, STATUS_PREPARING}:
            if timing_logger is not None:
                timing_logger(f"Prepare skipped claim {source}:{external_id} status={current_status}")
            return _PrepareOneResult()
        if callable(claim):
            claimed = bool(
                claim(
                    source=source,
                    external_id=external_id,
                    chat_id=settings.telegram_chat_id,
                )
            )
            if not claimed:
                refreshed = None
                if callable(get_delivery):
                    row = get_delivery(source, external_id)
                    refreshed = row.status if row is not None else None
                if timing_logger is not None:
                    timing_logger(f"Prepare skipped claim {source}:{external_id} status={refreshed or 'unknown'}")
                return _PrepareOneResult()
            if timing_logger is not None:
                timing_logger(f"Prepare claimed {source}:{external_id}")

    item_started_at = time.monotonic()
    if timing_logger is not None:
        timing_logger(f"Prepare start {source}:{external_id}")
    generation_started_at = time.monotonic()
    try:
        prepared = service.prepare(source=source, external_id=external_id)
        if timing_logger is not None:
            timing_logger(
                f"Prepare generated {source}:{external_id} +{int((time.monotonic() - generation_started_at) * 1000)}ms"
            )
    except (
        ApplicationPreparationError,
        LLMRequestError,
        LLMResponseError,
        CoverLetterValidationError,
        PromptLoadError,
    ) as exc:
        if timing_logger is not None:
            timing_logger(
                f"Prepare failed {source}:{external_id} +{int((time.monotonic() - generation_started_at) * 1000)}ms"
            )
        if not dry_run:
            message_ref = storage.get_message_ref(
                source=source,
                external_id=external_id,
                chat_id=settings.telegram_chat_id,
            )
            storage.update_status(
                source=source,
                external_id=external_id,
                chat_id=settings.telegram_chat_id,
                status=STATUS_PREPARATION_FAILED,
            )
            storage.save_preparation(
                source=source,
                external_id=external_id,
                status=STATUS_PREPARATION_FAILED,
                resume_name=None,
                language=None,
                error_message=str(exc),
                cover_letter=None,
                vacancy_title=None,
                vacancy_company=None,
                vacancy_url=None,
            )
            storage.mark_history_status(
                source=source,
                external_id=external_id,
                status=STATUS_PREPARATION_FAILED,
                timestamp_field=None,
            )
            if message_ref is not None:
                try:
                    title, company, url = _resolve_card_context(
                        storage=storage,
                        message={"text": "", "reply_markup": {}},
                        source=source,
                        external_id=external_id,
                    )
                    if url:
                        telegram_client.edit_message_text(  # type: ignore[union-attr]
                            chat_id=message_ref[0],
                            message_id=message_ref[1],
                            text=format_preparation_failed_html(title=title, company=company),
                            buttons=build_prepare_failed_buttons(source=source, external_id=external_id, url=url),
                        )
                except (TelegramRequestError, TelegramMessageNotModifiedError, ValueError):
                    logger.warning("Primary vacancy message update failed for preparation error: %s:%s", source, external_id)
        elif print_dry_run_items:
            typer.echo(f"FAILED {source}:{external_id} {exc}")
        return _PrepareOneResult(errors_count=1)

    generated_packages = 1
    if dry_run:
        missing = 1 if prepared.resume_path is None else 0
        if print_dry_run_items:
            _print_prepared_dry_run(prepared)
        return _PrepareOneResult(generated_packages=generated_packages, pdf_missing=missing)

    message_ref = storage.get_message_ref(
        source=source,
        external_id=external_id,
        chat_id=settings.telegram_chat_id,
    )
    if message_ref is None:
        storage.update_status(
            source=source,
            external_id=external_id,
            chat_id=settings.telegram_chat_id,
            status=STATUS_PREPARATION_FAILED,
        )
        storage.save_preparation(
            source=source,
            external_id=external_id,
            status=STATUS_PREPARATION_FAILED,
            resume_name=prepared.recommended_resume,
            language=prepared.language,
            error_message="Original Telegram card is missing.",
            cover_letter=prepared.cover_letter,
            vacancy_title=prepared.title,
            vacancy_company=prepared.company,
            vacancy_url=prepared.url,
        )
        storage.mark_history_status(
            source=source,
            external_id=external_id,
            status=STATUS_PREPARATION_FAILED,
            timestamp_field=None,
        )
        return _PrepareOneResult(generated_packages=generated_packages, errors_count=1)

    try:
        edit_started_at = time.monotonic()
        telegram_client.edit_message_text(  # type: ignore[union-attr]
            chat_id=message_ref[0],
            message_id=message_ref[1],
            text=build_ready_text(
                title=prepared.title,
                company=prepared.company,
                recommended_resume=prepared.recommended_resume,
            ),
            buttons=build_prepared_application_buttons(
                source=prepared.source,
                external_id=prepared.external_id,
                url=prepared.url,
            ),
        )
        if timing_logger is not None:
            timing_logger(
                f"Prepare editMessageText {source}:{external_id} +{int((time.monotonic() - edit_started_at) * 1000)}ms"
            )
    except TelegramMessageNotModifiedError:
        _ = None
    except (TelegramRequestError, ValueError) as exc:
        storage.update_status(
            source=source,
            external_id=external_id,
            chat_id=settings.telegram_chat_id,
            status=STATUS_PREPARATION_FAILED,
        )
        storage.save_preparation(
            source=source,
            external_id=external_id,
            status=STATUS_PREPARATION_FAILED,
            resume_name=prepared.recommended_resume,
            language=prepared.language,
            error_message=str(exc),
            cover_letter=prepared.cover_letter,
            vacancy_title=prepared.title,
            vacancy_company=prepared.company,
            vacancy_url=prepared.url,
        )
        storage.mark_history_status(
            source=source,
            external_id=external_id,
            status=STATUS_PREPARATION_FAILED,
            timestamp_field=None,
        )
        return _PrepareOneResult(generated_packages=generated_packages, errors_count=1)

    storage.update_status(
        source=source,
        external_id=external_id,
        chat_id=settings.telegram_chat_id,
        status=STATUS_PREPARED,
    )
    storage.save_preparation(
        source=source,
        external_id=external_id,
        status=STATUS_PREPARED,
        resume_name=prepared.recommended_resume,
        language=prepared.language,
        error_message=None,
        cover_letter=prepared.cover_letter,
        vacancy_title=prepared.title,
        vacancy_company=prepared.company,
        vacancy_url=prepared.url,
    )
    storage.mark_history_status(
        source=source,
        external_id=external_id,
        status=STATUS_PREPARED,
        timestamp_field="prepared_at",
    )
    if timing_logger is not None:
        timing_logger(
            f"Prepare done {source}:{external_id} total={int((time.monotonic() - item_started_at) * 1000)}ms"
        )
    return _PrepareOneResult(
        generated_packages=generated_packages,
        prepared_successfully=1,
        telegram_sent=1,
    )


def _send_processed_to_telegram(
    *,
    processed: list[LinkedInProcessedVacancy],
    deliveries: TelegramDeliveryStorage,
    telegram_client: TelegramClient,
    chat_id: str,
    verbose: bool,
) -> tuple[int, int]:
    report = _send_processed_to_telegram_detailed(
        processed=processed,
        deliveries=deliveries,
        telegram_client=telegram_client,
        chat_id=chat_id,
        verbose=verbose,
    )
    return report.sent, report.already_delivered


def _send_processed_to_telegram_detailed(
    *,
    processed: list[LinkedInProcessedVacancy],
    deliveries: TelegramDeliveryStorage,
    telegram_client: TelegramClient,
    chat_id: str,
    verbose: bool,
) -> TelegramCycleReport:
    eligible = 0
    sent = 0
    already_sent = 0
    send_errors = 0
    verbose_events: list[str] = []
    for item in processed:
        _upsert_history_item(item)
        if item.evaluation is None:
            continue
        decision = item.evaluation.decision.value
        if decision not in {"STRONG_MATCH", "POTENTIAL_MATCH"}:
            continue
        eligible += 1
        already_delivered = deliveries.was_sent(item.source, item.external_id, chat_id)
        if already_delivered:
            already_sent += 1
            if verbose:
                verbose_events.append(f"ALREADY_DELIVERED {item.title}")
            continue
        card = TelegramVacancyCard(
            source=map_source_to_code(item.source),
            external_id=item.external_id,
            decision=decision,
            title=item.title,
            company=item.company,
            location=item.location,
            url=item.url,
            match_percentage=item.evaluation.match_percentage,
            gaps=item.evaluation.gaps,
            nuances=item.evaluation.nuances,
            recommended_resume=item.evaluation.recommended_resume.value,
            content_completeness=item.content_completeness,
        )
        try:
            message_ref = telegram_client.send_vacancy_card(card)
            deliveries.save_sent(
                source=item.source,
                external_id=item.external_id,
                chat_id=chat_id,
                message_id=message_ref.message_id,
            )
            deliveries.mark_history_status(
                source=item.source,
                external_id=item.external_id,
                status="SENT",
                timestamp_field="sent_at",
            )
            sent += 1
            if verbose:
                verbose_events.append(f"SENT {item.title}")
        except (TelegramRequestError, ValueError) as exc:
            send_errors += 1
            logger.error("Telegram send failed for job %s: %s", item.external_id, exc)
    return TelegramCycleReport(
        eligible=eligible,
        already_delivered=already_sent,
        sent=sent,
        send_errors=send_errors,
        verbose_events=verbose_events,
    )
