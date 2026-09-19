from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.company_watch.models import (
    GreenhouseBoardCatalog,
    GreenhouseBoardCatalogEntry,
    TargetCompaniesConfig,
    TargetCompany,
)

DEFAULT_TARGET_COMPANIES_PATH = Path("config/target_companies.yaml")

# Sibling file (next to whichever target-companies config is loaded) that holds
# a compact catalog of verified public Greenhouse board tokens. See
# config/greenhouse_target_boards.yaml for provenance/verification notes.
GREENHOUSE_BOARD_CATALOG_FILENAME = "greenhouse_target_boards.yaml"

_GREENHOUSE_CATALOG_DEFAULTS: dict[str, object] = {
    "priority": "A",
    "language": "english",
    "relocation_status": "mixed_check_per_role",
    "watcher_type": "greenhouse",
    "ats": "greenhouse",
}

# Matches the cheap backend/JVM title policy already used for hand-maintained
# Greenhouse companies in target_companies.yaml.
_GREENHOUSE_CATALOG_ROLE_TITLE_KEYWORDS: tuple[str, ...] = (
    "backend",
    "back-end",
    "java",
    "jvm",
    "kotlin",
    "server",
    "platform engineer",
    "software engineer",
)


class TargetCompaniesConfigLoadError(Exception):
    """Raised when target companies YAML cannot be loaded."""


def load_target_companies_config(path: str | Path) -> TargetCompaniesConfig:
    config_path = Path(path)
    explicit_config = _load_explicit_target_companies_config(config_path)

    catalog_path = config_path.parent / GREENHOUSE_BOARD_CATALOG_FILENAME
    if not catalog_path.exists():
        return explicit_config

    catalog_companies = _load_greenhouse_board_catalog(catalog_path)
    explicit_keys: set[str] = set()
    for company in explicit_config.companies:
        explicit_keys.update(_explicit_company_keys(company))

    merged_companies = list(explicit_config.companies)
    for catalog_company in catalog_companies:
        if _explicit_company_keys(catalog_company) & explicit_keys:
            continue
        merged_companies.append(catalog_company)
    return TargetCompaniesConfig(companies=merged_companies)


def _load_explicit_target_companies_config(config_path: Path) -> TargetCompaniesConfig:
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


def _load_greenhouse_board_catalog(catalog_path: Path) -> list[TargetCompany]:
    try:
        raw_content = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Cannot read Greenhouse board catalog: {catalog_path}"
        ) from exc

    if not raw_content.strip():
        raise TargetCompaniesConfigLoadError(
            f"Greenhouse board catalog is empty: {catalog_path}"
        )

    try:
        payload = yaml.safe_load(raw_content)
    except yaml.YAMLError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Greenhouse board catalog is not valid YAML: {catalog_path}\n{exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise TargetCompaniesConfigLoadError(
            f"Greenhouse board catalog must be a mapping with a 'boards' list: {catalog_path}"
        )

    try:
        catalog = GreenhouseBoardCatalog.model_validate(payload)
    except ValidationError as exc:
        raise TargetCompaniesConfigLoadError(
            f"Greenhouse board catalog is invalid: {catalog_path}\n{exc}"
        ) from exc

    seen: dict[str, str] = {}
    companies: list[TargetCompany] = []
    for entry in catalog.boards:
        label = f"{entry.name} ({entry.slug})"
        entry_keys = {
            _normalize_catalog_key(entry.slug),
            _normalize_catalog_key(entry.name),
        }
        for key in entry_keys:
            existing = seen.get(key)
            if existing is not None:
                raise TargetCompaniesConfigLoadError(
                    "Greenhouse board catalog has a duplicate entry: "
                    f"'{label}' conflicts with '{existing}': {catalog_path}"
                )
            seen[key] = label
        companies.append(_catalog_entry_to_target_company(entry))
    return companies


def _catalog_entry_to_target_company(entry: GreenhouseBoardCatalogEntry) -> TargetCompany:
    keywords = list(_GREENHOUSE_CATALOG_ROLE_TITLE_KEYWORDS)
    return TargetCompany(
        name=entry.name,
        job_board_url=f"https://job-boards.greenhouse.io/{entry.slug}",
        role_title_keywords=keywords,
        role_keywords=list(keywords),
        **_GREENHOUSE_CATALOG_DEFAULTS,
    )


def _normalize_catalog_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _explicit_company_keys(company: TargetCompany) -> set[str]:
    keys = {_normalize_catalog_key(company.name)}
    board_url = company.job_board_url or ""
    match = re.search(r"greenhouse\.io/([^/?#]+)", board_url, re.IGNORECASE)
    if match:
        keys.add(_normalize_catalog_key(match.group(1)))
    return keys
