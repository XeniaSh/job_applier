from pathlib import Path

import typer
from pydantic import ValidationError

from app.collectors.hh_client import HHClient
from app.collectors.hh_collector import DEFAULT_HH_QUERIES, HHCollector, HHCollectReport
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
