from pathlib import Path

from typer.testing import CliRunner

from app.cli import app


def test_empty_vacancy_input(tmp_path: Path) -> None:
    vacancy_file = tmp_path / "vacancy.txt"
    vacancy_file.write_text("   \n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["review", str(vacancy_file)])

    assert result.exit_code != 0
    assert "Файл вакансии пустой" in result.output


def test_missing_environment_variables(tmp_path: Path, monkeypatch) -> None:
    vacancy_file = tmp_path / "vacancy.txt"
    vacancy_file.write_text("Java backend vacancy", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_API_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["review", str(vacancy_file)])

    assert result.exit_code != 0
    assert "Отсутствует обязательная конфигурация LLM" in result.output
