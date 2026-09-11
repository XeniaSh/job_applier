from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.application.autofill.resolver import DefaultVacancyResolver, VacancyResolveError

RESOLVER_PATH = Path("app/application/autofill/resolver.py")


def test_unsupported_source_raises_typed_error() -> None:
    resolver = DefaultVacancyResolver()
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        resolver.resolve("linkedin-email", "123")


def test_generic_greenhouse_source_is_unsupported() -> None:
    resolver = DefaultVacancyResolver()
    with pytest.raises(VacancyResolveError, match="Unsupported vacancy source"):
        resolver.resolve("greenhouse", "123")


def test_resolver_module_does_not_import_telegram() -> None:
    tree = ast.parse(RESOLVER_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert all("telegram" not in name for name in imported)
