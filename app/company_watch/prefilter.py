from __future__ import annotations

import re

from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.models import TargetCompany

DEFAULT_EXCLUDE_TITLE_KEYWORDS: tuple[str, ...] = (
    "sales",
    "account executive",
    "customer success",
    "marketing",
    "finance",
    "legal",
    "hr",
    "recruiter",
    "talent",
    "people",
    "product manager",
    "project manager",
    "program manager",
    "engineering manager",
    "director",
    "head of",
    "vp",
    "graduate",
    "junior",
    "intern",
    "support",
    "solutions architect",
    "solution architect",
    "consultant",
    "frontend",
    "front-end",
)


def _normalized_phrase(text: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return f" {collapsed.strip()} " if collapsed.strip() else ""


def title_contains_keyword(title: str, keyword: str) -> bool:
    needle = _normalized_phrase(keyword)
    haystack = _normalized_phrase(title)
    return bool(needle) and needle in haystack


def any_title_keyword_matches(title: str, keywords: list[str] | tuple[str, ...]) -> bool:
    cleaned = [item.strip() for item in keywords if item.strip()]
    return any(title_contains_keyword(title, keyword) for keyword in cleaned)


def include_title_keywords(company: TargetCompany) -> list[str]:
    if company.role_title_keywords:
        return list(company.role_title_keywords)
    return list(company.role_keywords)


def exclude_title_keywords(company: TargetCompany) -> list[str]:
    combined: list[str] = []
    seen: set[str] = set()
    for keyword in (*DEFAULT_EXCLUDE_TITLE_KEYWORDS, *company.exclude_title_keywords):
        normalized = _normalized_phrase(keyword)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        combined.append(keyword)
    return combined


def passes_role_prefilter(vacancy: NormalizedVacancy, company: TargetCompany) -> bool:
    title = vacancy.title
    if any_title_keyword_matches(title, exclude_title_keywords(company)):
        return False
    include = include_title_keywords(company)
    if not include:
        return True
    return any_title_keyword_matches(title, include)
