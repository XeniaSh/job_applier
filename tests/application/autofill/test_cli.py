from __future__ import annotations

from typer.testing import CliRunner

from app.application.autofill.models import AutofillStatus, stage1_autofill_result
from app.cli import app


class _FakeService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def run(self, source: str, external_id: str, *, keep_open: bool = True):
        _ = keep_open
        return stage1_autofill_result(
            source=source,
            external_id=external_id,
            application_url="https://job-boards.greenhouse.io/agoda/jobs/1",
            status=AutofillStatus.READY_FOR_REVIEW,
            resume_uploaded=True,
        )


class _FailedService(_FakeService):
    def run(self, source: str, external_id: str, *, keep_open: bool = True):
        _ = keep_open
        return stage1_autofill_result(
            source=source,
            external_id=external_id,
            status=AutofillStatus.FAILED,
            warnings=["Vacancy 1 was not found for source target_company:greenhouse:agoda."],
        )


def test_autofill_cli_prints_summary(monkeypatch) -> None:
    monkeypatch.setattr("app.application.autofill.service.AutofillService", _FakeService)
    result = CliRunner().invoke(
        app,
        ["autofill", "target_company:greenhouse:agoda", "1", "--no-keep-open"],
    )
    assert result.exit_code == 0
    assert "Autofill completed." in result.output
    assert "NOT PERFORMED" in result.output
    assert "telegram" not in result.output.lower()


def test_autofill_cli_does_not_consult_recommendation(monkeypatch) -> None:
    monkeypatch.setattr("app.application.autofill.service.AutofillService", _FakeService)
    result = CliRunner().invoke(
        app,
        [
            "autofill",
            "target_company:greenhouse:agoda",
            "7044713",
            "--no-keep-open",
        ],
    )
    assert result.exit_code == 0
    assert "Autofill completed." in result.output
    assert "SKIP" not in result.output


def test_autofill_cli_exits_nonzero_on_failure(monkeypatch) -> None:
    monkeypatch.setattr("app.application.autofill.service.AutofillService", _FailedService)
    result = CliRunner().invoke(
        app,
        ["autofill", "target_company:greenhouse:agoda", "missing", "--no-keep-open"],
    )
    assert result.exit_code == 1
    assert "Autofill failed." in result.output
