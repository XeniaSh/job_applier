from collections.abc import Callable
from typing import Protocol

from app.models import (
    Decision,
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

    def analyze(self, vacancy: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        skills = self._skills_loader()
        prompt = self._prompt_loader()
        extraction = self._llm_client.extract_vacancy(prompt=prompt, vacancy=vacancy)
        comparison = compare_requirements(extraction=extraction, candidate_skills=skills)

        decision = comparison.decision
        gaps = _select_gaps_for_output(
            missing_mandatory=comparison.missing_mandatory,
            mandatory_missing_weights=comparison.mandatory_missing_weights,
            optional_missing=comparison.optional_missing,
            optional_missing_weights=comparison.optional_missing_weights,
        )
        match_percentage = comparison.match_percentage
        matched_score = comparison.matched_score
        total_possible_score = comparison.total_possible_score
        explicit_skill_count = _count_explicit_skills(extraction)
        evidence_sufficient = True

        completeness = content_completeness.upper().strip()
        location_nuance, location_cap = _build_location_nuance(
            location_values=comparison.location_restrictions,
            uncertainty_values=comparison.uncertainties,
            vacancy_text=vacancy,
        )
        if location_cap is not None:
            decision = _cap_decision(decision, location_cap)

        lead_nuance = _build_seniority_nuance(vacancy_text=vacancy, extraction=extraction)
        is_incomplete = completeness in {"PARTIAL", "MINIMAL"}

        if is_incomplete:
            nuances = _clean_and_limit(
                [
                    "Описание вакансии неполное — требуется открыть LinkedIn",
                    *( [location_nuance] if location_nuance else []),
                    *( [lead_nuance] if lead_nuance else []),
                ],
                limit=3,
            )
            gaps = []
            evidence_sufficient = completeness == "PARTIAL" and explicit_skill_count >= 3

            if completeness == "MINIMAL":
                decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
                match_percentage = None
                evidence_sufficient = False
            elif completeness == "PARTIAL":
                if explicit_skill_count < 3:
                    decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
                    match_percentage = None
                elif decision == Decision.STRONG_MATCH and not _is_partial_strong_allowed(
                    vacancy_text=vacancy,
                    extraction=extraction,
                ):
                    decision = Decision.POTENTIAL_MATCH
            if lead_nuance and not _candidate_targets_lead_roles(skills):
                decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
        else:
            nuances = _clean_and_limit(
                [
                    *comparison.employment_conditions,
                    *( [location_nuance] if location_nuance else []),
                    *comparison.uncertainties,
                    *( [lead_nuance] if lead_nuance else []),
                ],
                limit=3,
            )

        return VacancyEvaluation(
            decision=decision,
            summary=extraction.short_summary,
            matched_points=_clean_and_limit(comparison.matched_mandatory, limit=5),
            gaps=gaps,
            nuances=nuances,
            match_percentage=match_percentage,
            matched_score=matched_score,
            total_possible_score=total_possible_score,
            explicit_skill_count=explicit_skill_count,
            evidence_sufficient=evidence_sufficient,
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


def _is_partial_strong_allowed(vacancy_text: str, extraction: VacancyExtraction) -> bool:
    text = vacancy_text.lower()
    has_language_core = any(token in text for token in ("java", "kotlin", "jvm"))
    has_backend_context = any(token in text for token in ("backend", "back end", "spring"))
    if not (has_language_core and has_backend_context):
        return False

    location_conflict_markers = ("incompatible", "несовместим", "country restriction")
    if any(
        marker in " ".join(extraction.location_restrictions).lower()
        for marker in location_conflict_markers
    ):
        return False

    hard_negative_role_markers = (
        "frontend",
        "react",
        "angular",
        "qa",
        "tester",
        "support",
        "analyst",
        "devops",
        "python",
        "php",
        ".net",
        "mobile",
        "data scientist",
        "ml engineer",
    )
    role_text = extraction.role_type.lower()
    if any(marker in role_text for marker in hard_negative_role_markers):
        return False

    return True


def _count_explicit_skills(extraction: VacancyExtraction) -> int:
    return len(_clean_and_limit([*extraction.mandatory_skills, *extraction.optional_skills], limit=100))


def _candidate_targets_lead_roles(profile: CandidateSkillsProfile) -> bool:
    markers = ("lead", "tech lead", "principal", "staff", "architect", "head of engineering", "manager")
    all_terms = [*profile.strong_skills, *profile.practical_skills, *profile.core_skills]
    text = " ".join(all_terms).lower()
    return any(marker in text for marker in markers)


def _build_seniority_nuance(vacancy_text: str, extraction: VacancyExtraction) -> str | None:
    indicators = ("lead", "tech lead", "principal", "staff", "architect", "head of engineering", "manager")
    text = " ".join(
        [
            vacancy_text.lower(),
            extraction.role_type.lower(),
            (extraction.seniority or "").lower(),
        ]
    )
    if any(marker in text for marker in indicators):
        return "Роль уровня Lead — стоит проверить ожидания по управлению и архитектурной ответственности"
    return None


def _build_location_nuance(
    location_values: list[str],
    uncertainty_values: list[str],
    vacancy_text: str,
) -> tuple[str | None, Decision | None]:
    raw_location = " ".join(location_values).lower()
    uncertainties = " ".join(uncertainty_values).lower()
    vacancy = vacancy_text.lower()
    combined = " ".join([raw_location, uncertainties, vacancy])

    if not combined.strip():
        return None, None
    if any(marker in combined for marker in ("worldwide remote", "remote worldwide", "work from anywhere", "anywhere in the world")):
        return None, None

    residency_required = any(
        marker in combined
        for marker in (
            "must reside in philippines",
            "reside in philippines",
            "philippines residents only",
            "only philippines",
            "based in philippines only",
        )
    )
    if residency_required:
        return (
            "Указано требование проживания на Филиппинах — для текущего профиля это ограничение критично",
            Decision.IGNORE,
        )

    if "philippines" in combined:
        if any(marker in combined for marker in ("remote", "удален", "relocation", "on-site", "office", "hybrid")):
            return (
                "Вакансия ориентирована на кандидатов на Филиппинах — нужно проверить возможность работы из другой страны",
                Decision.POTENTIAL_MATCH,
            )
        return (
            "Вакансия ориентирована на кандидатов на Филиппинах — нужно проверить возможность работы из другой страны",
            Decision.POTENTIAL_MATCH,
        )

    if any(marker in combined for marker in ("manila", "national capital region")):
        return (
            "Указана локация Manila; удалённый международный формат не подтверждён",
            Decision.POTENTIAL_MATCH,
        )

    remote_geo_unclear = any(
        marker in combined
        for marker in (
            "remote geography",
            "география удаленной",
            "страна удаленной",
            "удаленной работы",
            "unclear remote",
        )
    )
    if remote_geo_unclear:
        return (
            "Неясна география удалённой работы — нужно уточнить доступность международного формата",
            Decision.POTENTIAL_MATCH,
        )

    return None, None


def _cap_decision(current: Decision, cap: Decision) -> Decision:
    order = {
        Decision.IGNORE: 0,
        Decision.POTENTIAL_MATCH: 1,
        Decision.STRONG_MATCH: 2,
    }
    return cap if order[current] > order[cap] else current
