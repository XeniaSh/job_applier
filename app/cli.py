from pathlib import Path
from datetime import timezone

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
from app.config import Settings
from app.formatter import format_evaluation_ru
from app.llm_client import LLMClient, LLMRequestError, LLMResponseError
from app.prompt_loader import PromptLoadError, load_analysis_prompt
from app.skills_profile_loader import SkillsProfileLoadError, load_candidate_skills
from app.storage.seen_jobs import SeenJobsStorage
from app.vacancy_analyzer import VacancyAnalyzer

app = typer.Typer(help="Personal job vacancy analyzer.")


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
        report = collector.collect_and_analyze(limit=limit, dry_run=dry_run)
    except (EmailConnectionError, EmailAuthenticationError) as exc:
        _ = exc
        typer.secho("Ошибка подключения к почте LinkedIn alerts.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    _print_linkedin_results(report=report, include_ignore=include_ignore, dry_run=dry_run)
    _print_linkedin_summary(report)


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
                f"Новых вакансий: {report.new_vacancies}",
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
