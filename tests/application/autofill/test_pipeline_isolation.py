from __future__ import annotations

from pathlib import Path


def test_collectors_do_not_import_autofill() -> None:
    for path in Path("app/collectors").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "autofill" not in text, path


def test_cli_module_does_not_import_playwright() -> None:
    text = Path("app/cli.py").read_text(encoding="utf-8")
    assert "playwright" not in text
    assert "AutofillService" not in text


def test_company_watch_does_not_import_autofill() -> None:
    for path in Path("app/company_watch").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "autofill" not in text, path


def test_autofill_layer_does_not_gate_on_recommendation() -> None:
    for path in (
        Path("app/application/autofill/service.py"),
        Path("app/application/autofill/cli.py"),
        Path("app/application/autofill/greenhouse.py"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "recommend_application" not in text, path
        assert "RECOMMENDATION_SKIP" not in text, path
        assert "allows_autonomous_application_workflow" not in text, path
