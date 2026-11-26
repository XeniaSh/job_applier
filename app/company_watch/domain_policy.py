"""Conservative employer/project domain exclusions, independent of ATS or fit."""
from pathlib import Path
import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["speculative_trading", "crypto_exchange", "gambling", "defense", "adult_entertainment"]
DEFAULT_DOMAIN_POLICY_PATH = Path("config/discovery_policy.yaml")


class DomainPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded_domains: list[Domain] = Field(default_factory=list)
    employer_domains: dict[str, list[Domain]] = Field(default_factory=dict)
    source_domains: dict[str, list[Domain]] = Field(default_factory=dict)


def load_domain_policy(path: Path = DEFAULT_DOMAIN_POLICY_PATH) -> DomainPolicy:
    # Old/custom configurations need no new file. Malformed present policy must
    # fail visibly rather than silently dropping its restrictions.
    if not path.exists():
        return DomainPolicy()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("Invalid discovery policy YAML") from exc
    return DomainPolicy.model_validate(payload)


_SELF_DESCRIPTION = (
    r"(?:we are|we operate|we run|we build|we develop|our company is|our product is|our platform is|our client is) "
    r"(?:an? |the )?(?:[\w-]+ ){0,2}"
)
_DOMAINS = {
    "speculative_trading": r"(?:forex broker|cfd broker|proprietary trading firm|speculative trading (?:firm|company))",
    "crypto_exchange": r"(?:cryptocurrency exchange|crypto exchange)",
    "gambling": r"(?:online casino|gambling platform|sportsbook|sports betting platform)",
    "defense": r"(?:defense contractor|defence contractor|weapons manufacturer|weapons systems)",
    "adult_entertainment": r"(?:adult entertainment (?:platform|company)|pornographic platform)",
}
_RUSSIAN_DOMAINS = {
    "speculative_trading": r"(?:форекс[- ]брокер|cfd[- ]брокер)",
    "crypto_exchange": r"(?:криптовалютная биржа|криптобиржа)",
    "gambling": r"(?:онлайн[- ]казино|букмекерская компания|гемблинг[- ]платформа)",
    "defense": r"(?:производитель оружия|оборонный подрядчик)",
    "adult_entertainment": r"(?:порнографическая платформа|платформа adult entertainment)",
}
_SELF_PATTERNS = {
    key: re.compile(
        _SELF_DESCRIPTION + value + r"\b|(?:мы|наша компания|наш клиент)\s*[-—–]\s*"
        + _RUSSIAN_DOMAINS[key] + r"\b", re.I,
    ) for key, value in _DOMAINS.items()
}
_NEGATION = re.compile(r"\b(?:not|never|no|non|не|без)\b", re.I)


def excluded_domain_reason(*, company: str = "", source: str = "", text: str = "",
                           policy: DomainPolicy) -> str | None:
    excluded = set(policy.excluded_domains)
    if not excluded:
        return None
    # Exact case-insensitive identity, no "bank", "crypto", "game", customer
    # list or incidental keyword exclusion. Source mappings cover renamed cards.
    name = " ".join(company.casefold().split())
    domains = set()
    for key, values in policy.employer_domains.items():
        if " ".join(key.casefold().split()) == name:
            domains.update(values)
    for key, values in policy.source_domains.items():
        if key.casefold() == source.casefold():
            domains.update(values)
    for sentence in re.split(r"[.!?\n]+", text):
        for domain, pattern in _SELF_PATTERNS.items():
            matches = pattern.finditer(" ".join(sentence.split()))
            if any(not _NEGATION.search(match.group()) for match in matches):
                domains.add(domain)
    hits = sorted(domains & excluded)
    return "excluded employer/project domain: " + ", ".join(hits) if hits else None
