from pathlib import Path

import typer
from pydantic import ValidationError

from app.config import Settings
from app.formatter import format_evaluation_ru
from app.llm_client import LLMClient, LLMRequestError, LLMResponseError
from app.prompt_loader import PromptLoadError, load_analysis_prompt
from app.skills_profile_loader import SkillsProfileLoadError, load_candidate_skills
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
