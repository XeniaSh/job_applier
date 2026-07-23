from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_RELOCATION_BLOCK_PATH = Path("profiles/cover_letter_relocation_new_zealand.md")


@dataclass(frozen=True)
class CoverLetterProfile:
    """Selected cover-letter profile for optional post-generation blocks."""

    name: str
    label: str
    blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelocationCountryTarget:
    """Extensible relocation target. Add Australia/Canada entries here later."""

    country: str
    keywords: tuple[str, ...]
    block_path: Path


# Future: append Australia / Canada targets without changing call sites.
RELOCATION_TARGETS: tuple[RelocationCountryTarget, ...] = (
    RelocationCountryTarget(
        country="New Zealand",
        keywords=(
            "new zealand",
            "auckland",
            "wellington",
            "christchurch",
        ),
        block_path=DEFAULT_RELOCATION_BLOCK_PATH,
    ),
)


def resolve_cover_letter_profile(
    *,
    location: str | None = None,
    country: str | None = None,
    vacancy_text: str | None = None,
) -> CoverLetterProfile:
    """
    Resolve cover letter profile from vacancy geography.

    Currently only New Zealand enables the relocation profile.
    """
    effective_location = (location or "").strip() or _extract_location_from_vacancy_text(vacancy_text)
    haystack = _normalize_haystack(location=effective_location, country=country, vacancy_text=None)
    for target in RELOCATION_TARGETS:
        if _matches_target(target, country=country, haystack=haystack):
            block = _load_relocation_block(target.block_path)
            return CoverLetterProfile(
                name="relocation",
                label=f"relocation ({target.country})",
                blocks=(block,),
            )
    return CoverLetterProfile(name="default", label="default")


def apply_cover_letter_profile(cover_letter: str, profile: CoverLetterProfile) -> str:
    """Append optional profile blocks after the default cover letter body."""
    body = " ".join((cover_letter or "").strip().split())
    if not profile.blocks:
        return body
    parts = [body] if body else []
    for block in profile.blocks:
        cleaned = _normalize_block(block)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts).strip()


def _extract_location_from_vacancy_text(vacancy_text: str | None) -> str | None:
    if not vacancy_text:
        return None
    for line in vacancy_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("location:"):
            value = stripped.split(":", 1)[1].strip()
            return value or None
    return None


def _matches_target(
    target: RelocationCountryTarget,
    *,
    country: str | None,
    haystack: str,
) -> bool:
    if country and country.strip().casefold() == target.country.casefold():
        return True
    return any(keyword in haystack for keyword in target.keywords)


def _normalize_haystack(
    *,
    location: str | None,
    country: str | None,
    vacancy_text: str | None,
) -> str:
    chunks = [location or "", country or "", vacancy_text or ""]
    return " ".join(chunks).casefold()


def _normalize_block(text: str) -> str:
    paragraphs = []
    for chunk in text.replace("\r\n", "\n").strip().split("\n\n"):
        paragraph = " ".join(chunk.split())
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


@lru_cache(maxsize=8)
def _load_relocation_block(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    cleaned = _normalize_block(content)
    if not cleaned:
        raise ValueError(f"Cover letter relocation block is empty: {path}")
    return cleaned
