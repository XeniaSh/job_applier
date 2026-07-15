from collections.abc import Callable
from typing import Protocol

from app.models import (
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
    VacancyExtraction,
)
from app.requirement_matcher import compare_requirements
from app.skills_profile_loader import CandidateSkillsProfile


class LLMAnalyzerClient(Protocol):
    def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
        ...


class VacancyAnalyzer:
    def __init__(
        self,
        llm_client: LLMAnalyzerClient,
        skills_loader: Callable[[], CandidateSkillsProfile],
        prompt_loader: Callable[[], str],
    ) -> None:
        self._llm_client = llm_client
        self._skills_loader = skills_loader
        self._prompt_loader = prompt_loader

    def analyze(self, vacancy: str) -> VacancyEvaluation:
        skills = self._skills_loader()
        prompt = self._prompt_loader()
        extraction = self._llm_client.extract_vacancy(prompt=prompt, vacancy=vacancy)
        comparison = compare_requirements(extraction=extraction, candidate_skills=skills)

        nuances = _clean_and_limit(
            [
                *comparison.employment_conditions,
                *comparison.location_restrictions,
                *comparison.uncertainties,
            ],
            limit=3,
        )

        return VacancyEvaluation(
            decision=comparison.decision,
            summary=extraction.short_summary,
            matched_points=_clean_and_limit(comparison.matched_mandatory, limit=5),
            gaps=_select_gaps_for_output(
                missing_mandatory=comparison.missing_mandatory,
                mandatory_missing_weights=comparison.mandatory_missing_weights,
                optional_missing=comparison.optional_missing,
                optional_missing_weights=comparison.optional_missing_weights,
            ),
            nuances=nuances,
            match_percentage=comparison.match_percentage,
            matched_score=comparison.matched_score,
            total_possible_score=comparison.total_possible_score,
            recommended_resume=_recommend_resume(extraction.role_type),
            recommended_cover_template=_recommend_cover_template(extraction.role_type, extraction.short_summary),
        )


def _clean_and_limit(values: list[str], limit: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = " ".join(item.strip().split()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= limit:
            break
    return cleaned


def _select_gaps_for_output(
    missing_mandatory: list[str],
    mandatory_missing_weights: dict[str, int],
    optional_missing: list[str],
    optional_missing_weights: dict[str, int],
) -> list[str]:
    ranked_mandatory = sorted(
        _clean_and_limit(missing_mandatory, limit=100),
        key=lambda skill: (-mandatory_missing_weights.get(skill, 1), skill),
    )
    if ranked_mandatory:
        return ranked_mandatory[:3]

    useful_optional = [
        skill
        for skill in _clean_and_limit(optional_missing, limit=100)
        if optional_missing_weights.get(skill, 1) >= 4
    ]
    ranked_optional = sorted(
        useful_optional,
        key=lambda skill: (-optional_missing_weights.get(skill, 1), skill),
    )
    return ranked_optional[:3]


def _recommend_resume(role_type: str) -> RecommendedResume:
    role = role_type.lower()
    if "kotlin" in role:
        return RecommendedResume.KOTLIN_BACKEND
    if "ai" in role:
        return RecommendedResume.AI_ADJACENT_BACKEND
    if "fintech" in role:
        return RecommendedResume.FINTECH_BACKEND
    return RecommendedResume.JAVA_BACKEND


def _recommend_cover_template(role_type: str, summary: str) -> RecommendedCoverTemplate:
    context = f"{role_type} {summary}".lower()
    if "ai" in context:
        return RecommendedCoverTemplate.AI_ADJACENT
    if "fintech" in context:
        return RecommendedCoverTemplate.FINTECH
    if "agency" in context:
        return RecommendedCoverTemplate.AGENCY
    if "product" in context:
        return RecommendedCoverTemplate.PRODUCT
    return RecommendedCoverTemplate.GENERIC
