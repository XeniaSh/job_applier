from pathlib import Path
from datetime import timezone
from dataclasses import dataclass
import logging
import os
import time

import typer
from pydantic import ValidationError

from app.collectors.hh_client import HHClient
from app.collectors.hh_collector import DEFAULT_HH_QUERIES, HHCollector, HHCollectReport
from app.collectors.email_imap_client import (
    EmailAuthenticationError,
    EmailConnectionError,
    EmailIMAPClient,
)
from app.collectors.linkedin_email_collector import (
    LinkedInEmailCollectReport,
    LinkedInEmailCollector,
)
from app.collectors.linkedin_email_parser import extract_email_text_parts, parse_linkedin_email
from app.application.preparation_service import (
    ApplicationPreparationError,
    PreparedApplication,
    PreparationService,
)
from app.application.resume_cache_service import KNOWN_RESUME_NAMES, ResumeCacheService
from app.config import Settings
from app.formatter import format_evaluation_ru
from app.llm_client import CoverLetterValidationError, LLMClient, LLMRequestError, LLMResponseError
from app.prompt_loader import PromptLoadError, load_analysis_prompt
from app.skills_profile_loader import SkillsProfileLoadError, load_candidate_skills
from app.storage.seen_jobs import SeenJobsStorage
from app.storage.telegram_delivery import (
    ALLOWED_STATUSES,
    STATUS_APPLIED,
    STATUS_PREPARE_REQUESTED,
    STATUS_PREPARED,
    STATUS_PREPARATION_FAILED,
    STATUS_SKIPPED,
    TelegramDeliveryStorage,
)
from app.telegram.client import (
    TelegramClient,
    TelegramRequestError,
    map_source_to_code,
    parse_callback_data,
    validate_linkedin_job_url,
)
from app.telegram.models import (
    ApplicationHistoryRecord,
    TelegramDeliveryRecord,
    TelegramInlineButton,
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


@app.command("collect-linkedin-email")
def collect_linkedin_email(
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum new vacancies to analyze."),
    include_ignore: bool = typer.Option(False, "--include-ignore", help="Print IGNORE results too."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse emails and print metadata without LLM analysis and without mark seen.",
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
    collector = LinkedInEmailCollector(
        email_client=email_client,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
    )

    try:
        report = collector.collect_and_analyze(
            limit=limit,
            dry_run=dry_run,
            skip_seen=True,
            mark_seen=True,
            analyze_in_dry_run=False,
        )
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к почте LinkedIn alerts.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    _sync_application_history(report.processed)
    _print_linkedin_results(report=report, include_ignore=include_ignore, dry_run=dry_run)
    _print_linkedin_summary(report)


@app.command("send-linkedin-telegram")
def send_linkedin_telegram(
    limit: int = typer.Option(20, "--limit", min=1),
    include_strong: bool = typer.Option(True, "--include-strong/--no-include-strong"),
    include_potential: bool = typer.Option(True, "--include-potential/--no-include-potential"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose"),
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
    collector = LinkedInEmailCollector(
        email_client=email_client,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
    )

    try:
        report = collector.collect_and_analyze(
            limit=limit,
            dry_run=dry_run,
            skip_seen=False,
            analyze_in_dry_run=dry_run,
            mark_seen=False,
        )
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к почте LinkedIn alerts.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    _sync_application_history(report.processed)
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
    collector = LinkedInEmailCollector(
        email_client=email_client,
        analyzer=analyzer,
        seen_jobs=seen_jobs,
    )
    telegram_client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
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

    typer.echo("Job Applier started.")
    typer.echo("Press Ctrl+C to stop.")
    try:
        while True:
            now = time.monotonic()
            if now >= next_cycle_monotonic:
                try:
                    report = collector.collect_and_analyze(
                        limit=20,
                        dry_run=False,
                        skip_seen=True,
                        mark_seen=True,
                        analyze_in_dry_run=False,
                    )
                    if report.new_vacancies > 0:
                        _run_log(f"LinkedIn: {report.new_vacancies} new vacancies")
                    sent = _send_processed_to_telegram(
                        processed=report.processed,
                        deliveries=deliveries,
                        telegram_client=telegram_client,
                        chat_id=settings.telegram_chat_id,
                        verbose=verbose,
                    )
                    if sent > 0:
                        _run_log(f"Telegram: {sent} cards sent")
                except (EmailConnectionError, EmailAuthenticationError, LLMRequestError, LLMResponseError) as exc:
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
                )
                if prepare_requests > 0:
                    _run_log("Prepare request received")
            except TelegramRequestError as exc:
                _run_log(f"Telegram poll failed: {exc}")
                time.sleep(poll_interval)
                continue

            if prepare_requests > 0:
                result = _prepare_requested_applications(
                    settings=settings,
                    service=preparation_service,
                    storage=deliveries,
                    telegram_client=telegram_client,
                    limit=20,
                    dry_run=False,
                    print_dry_run_items=False,
                )
                if result.generated_packages > 0:
                    _run_log("Application generated")
                if result.pdf_cached > 0 or result.pdf_uploaded > 0:
                    _run_log("Resume sent")
                if result.pdf_errors > 0:
                    _run_log(f"PDF warnings: {result.pdf_errors}")
                if result.errors_count > 0:
                    _run_log(f"Preparation errors: {result.errors_count}")
    except KeyboardInterrupt:
        typer.echo("Job Applier stopped.")
    finally:
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
) -> None:
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

    try:
        if action == "skip":
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_SKIPPED,
                history_status=STATUS_SKIPPED,
                timestamp_field="skipped_at",
            )
            if callback_id:
                client.answer_callback_query(callback_id, text="Вакансия пропущена")
        elif action == "applied":
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_APPLIED,
                history_status=STATUS_APPLIED,
                timestamp_field="applied_at",
            )
            if callback_id:
                client.answer_callback_query(callback_id, text="Отклик отмечен как отправленный")
        else:
            storage.update_delivery_and_history(
                source=source,
                external_id=external_id,
                chat_id=configured_chat_id,
                delivery_status=STATUS_PREPARE_REQUESTED,
                history_status=STATUS_PREPARE_REQUESTED,
                timestamp_field=None,
            )
            if callback_id:
                client.answer_callback_query(
                    callback_id,
                    text="Добавлено в очередь на подготовку отклика",
                )
    except (ValueError, KeyError):
        if callback_id:
            client.answer_callback_query(callback_id, text="Не удалось обновить статус")
        return

    message_id = int(message.get("message_id", 0))
    if message_id <= 0:
        return
    url_button = _extract_url_button(message)
    if url_button is None:
        return
    client.edit_message_reply_markup(
        chat_id=configured_chat_id,
        message_id=message_id,
        buttons=[[TelegramInlineButton(text="🔗 Open LinkedIn", url=url_button)]],
    )


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
        source="linkedin-email",
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
    stamp = time.strftime("%H:%M")
    typer.echo(f"[{stamp}] {message}")


def _poll_telegram_actions_once(
    *,
    client: TelegramClient,
    storage: TelegramDeliveryStorage,
    configured_chat_id: str,
    offset: int | None,
    timeout: int,
) -> tuple[int | None, int]:
    updates = client.get_updates(offset=offset, timeout=timeout)
    next_offset = offset
    prepare_requests = 0
    for update in updates:
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
        )
        update_id = int(update.get("update_id", 0))
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
) -> PreparationRunResult:
    queue = storage.list_by_status(
        chat_id=settings.telegram_chat_id if settings.telegram_chat_id else "0",
        status=STATUS_PREPARE_REQUESTED,
        limit=limit,
    )

    queue_items = len(queue)
    generated_packages = 0
    prepared_successfully = 0
    telegram_sent = 0
    errors_count = 0
    pdf_cached = 0
    pdf_uploaded = 0
    pdf_missing = 0
    pdf_errors = 0

    cache_service = resume_cache_service
    if not dry_run and cache_service is None and telegram_client is not None:
        cache_service = ResumeCacheService(
            resumes_dir=settings.resumes_dir,
            storage=storage,
            telegram_client=telegram_client,
        )

    for source, external_id in queue:
        try:
            prepared = service.prepare(source=source, external_id=external_id)
        except (
            ApplicationPreparationError,
            LLMRequestError,
            LLMResponseError,
            CoverLetterValidationError,
            PromptLoadError,
        ) as exc:
            errors_count += 1
            if not dry_run:
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
                )
                storage.mark_history_status(
                    source=source,
                    external_id=external_id,
                    status=STATUS_PREPARATION_FAILED,
                    timestamp_field=None,
                )
                try:
                    telegram_client.send_text_message(  # type: ignore[union-attr]
                        f"❌ Не удалось подготовить отклик: {str(exc)}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif print_dry_run_items:
                typer.echo(f"FAILED {source}:{external_id} {exc}")
            continue

        generated_packages += 1

        if dry_run:
            if prepared.resume_path is None:
                pdf_missing += 1
            if print_dry_run_items:
                _print_prepared_dry_run(prepared)
            continue

        try:
            telegram_client.send_prepared_application(  # type: ignore[union-attr]
                source=prepared.source,
                external_id=prepared.external_id,
                title=prepared.title,
                company=prepared.company,
                language=prepared.language,
                recommended_resume=prepared.recommended_resume,
                cover_letter=prepared.cover_letter,
                warnings=prepared.warnings,
                url=prepared.url,
            )
        except (TelegramRequestError, ValueError) as exc:
            errors_count += 1
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
            )
            storage.mark_history_status(
                source=source,
                external_id=external_id,
                status=STATUS_PREPARATION_FAILED,
                timestamp_field=None,
            )
            continue

        prepared_successfully += 1
        telegram_sent += 1
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
        )
        storage.mark_history_status(
            source=source,
            external_id=external_id,
            status=STATUS_PREPARED,
            timestamp_field="prepared_at",
        )
        try:
            telegram_client.send_text_message("✅ Отклик подготовлен")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

        if cache_service is None:
            pdf_errors += 1
            logger.warning("Resume cache service is unavailable for %s:%s", source, external_id)
            continue

        try:
            resume_result = cache_service.get_or_upload(
                resume_name=prepared.recommended_resume,
                chat_id=settings.telegram_chat_id,
            )
            if resume_result.missing or resume_result.telegram_file_id is None:
                pdf_missing += 1
                continue

            if resume_result.cache_hit:
                telegram_client.send_document_by_file_id(  # type: ignore[union-attr]
                    chat_id=settings.telegram_chat_id,
                    file_id=resume_result.telegram_file_id,
                    caption=f"Резюме для отклика: {prepared.recommended_resume}",
                )
                pdf_cached += 1
            elif resume_result.uploaded:
                pdf_uploaded += 1
        except (TelegramRequestError, ValueError, OSError) as exc:
            pdf_errors += 1
            logger.warning("Resume delivery warning for %s:%s: %s", source, external_id, exc)

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


def _send_processed_to_telegram(
    *,
    processed: list[LinkedInProcessedVacancy],
    deliveries: TelegramDeliveryStorage,
    telegram_client: TelegramClient,
    chat_id: str,
    verbose: bool,
) -> int:
    sent = 0
    for item in processed:
        _upsert_history_item(item)
        if item.evaluation is None:
            continue
        decision = item.evaluation.decision.value
        if decision not in {"STRONG_MATCH", "POTENTIAL_MATCH"}:
            continue
        already_delivered = deliveries.was_sent("linkedin-email", item.external_id, chat_id)
        if already_delivered:
            continue
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
        try:
            message_ref = telegram_client.send_vacancy_card(card)
            deliveries.save_sent(
                source="linkedin-email",
                external_id=item.external_id,
                chat_id=chat_id,
                message_id=message_ref.message_id,
            )
            deliveries.mark_history_status(
                source="linkedin-email",
                external_id=item.external_id,
                status="SENT",
                timestamp_field="sent_at",
            )
            sent += 1
            if verbose:
                _run_log(f"Telegram delivered {item.external_id}")
        except (TelegramRequestError, ValueError) as exc:
            logger.error("Telegram send failed for job %s: %s", item.external_id, exc)
    return sent
