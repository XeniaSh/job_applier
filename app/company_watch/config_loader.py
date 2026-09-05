from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.company_watch.models import TargetCompaniesConfig

DEFAULT_TARGET_COMPANIES_PATH = Path("config/target_companies.yaml")


class TargetCompaniesConfigLoadError(Exception):
    """Raised when target companies YAML cannot be loaded."""


def load_target_companies_config(path: str | Path) -> TargetCompaniesConfig:
    config_path = Path(path)
    try:
        raw_content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Target companies config not found: {config_path}"
        ) from exc
    except OSError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Cannot read target companies config: {config_path}"
        ) from exc

    if not raw_content.strip():
        raise TargetCompaniesConfigLoadError(
            f"Target companies config is empty: {config_path}"
        )

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Target companies config is not valid YAML: {config_path}\n{exc}"
        ) from exc

    if payload is None:
        raise TargetCompaniesConfigLoadError(
            f"Target companies config is empty: {config_path}"
        )
    if not isinstance(payload, dict):
        raise TargetCompaniesConfigLoadError(
            f"Target companies config must be a mapping with a 'companies' list: {config_path}"
        )

    try:
        return TargetCompaniesConfig.model_validate(payload)
    except ValidationError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Target companies config is invalid: {config_path}\n{exc}"
        ) from exc
