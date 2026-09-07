from __future__ import annotations

from dataclasses import dataclass

from app.company_watch.prefilter import title_contains_keyword
from app.company_watch.seniority import (
    SENIORITY_LEAD_MANAGER,
    SENIORITY_SENIOR,
    SENIORITY_STAFF_PLUS,
    classify_seniority,
)

# Title/seniority weights for LLM-budget selection only.
# These do not change requirement_matcher scoring or hard filters.
JAVA_TITLE_BOOST = 50
BACKEND_TITLE_BOOST = 40
JVM_TITLE_BOOST = 20
KOTLIN_TITLE_BOOST = 20
SOFTWARE_ENGINEER_TITLE_BOOST = 10
SENIOR_BOOST = 10
STAFF_PLUS_PENALTY = 10
COMPILER_PENALTY = 80
KERNEL_PENALTY = 80
RUNTIME_PENALTY = 40
FRONTEND_PENALTY = 80
FULL_STACK_PENALTY = 40
DATA_PENALTY = 40
ML_PENALTY = 40
QA_PENALTY = 80
LEAD_MANAGER_PENALTY = 80


@dataclass(frozen=True)
class PreanalysisRank:
    score: int
    seniority: str


def rank_title_for_analysis(title: str) -> PreanalysisRank:
    seniority = classify_seniority(title).label
    score = 0
    if title_contains_keyword(title, "java"):
        score += JAVA_TITLE_BOOST
    if title_contains_keyword(title, "backend") or title_contains_keyword(title, "back-end"):
        score += BACKEND_TITLE_BOOST
    if title_contains_keyword(title, "jvm"):
        score += JVM_TITLE_BOOST
    if title_contains_keyword(title, "kotlin"):
        score += KOTLIN_TITLE_BOOST
    if title_contains_keyword(title, "software engineer"):
        score += SOFTWARE_ENGINEER_TITLE_BOOST
    if seniority == SENIORITY_SENIOR:
        score += SENIOR_BOOST
    if seniority == SENIORITY_STAFF_PLUS:
        score -= STAFF_PLUS_PENALTY
    if seniority == SENIORITY_LEAD_MANAGER:
        score -= LEAD_MANAGER_PENALTY
    if title_contains_keyword(title, "compiler"):
        score -= COMPILER_PENALTY
    if title_contains_keyword(title, "kernel"):
        score -= KERNEL_PENALTY
    if title_contains_keyword(title, "runtime"):
        score -= RUNTIME_PENALTY
    if title_contains_keyword(title, "frontend") or title_contains_keyword(title, "front-end"):
        score -= FRONTEND_PENALTY
    if title_contains_keyword(title, "full stack") or title_contains_keyword(title, "full-stack"):
        score -= FULL_STACK_PENALTY
    if title_contains_keyword(title, "data engineer") or title_contains_keyword(title, "data platform"):
        score -= DATA_PENALTY
    if title_contains_keyword(title, "machine learning") or title_contains_keyword(title, "ml"):
        score -= ML_PENALTY
    if title_contains_keyword(title, "qa") or title_contains_keyword(title, "test automation"):
        score -= QA_PENALTY
    return PreanalysisRank(score=score, seniority=seniority)
