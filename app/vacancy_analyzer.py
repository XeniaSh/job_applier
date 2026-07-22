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
        title_text = _extract_title_from_vacancy_text(vacancy) or ""
        extraction = self._llm_client.extract_vacancy(prompt=prompt, vacancy=vacancy)
        comparison = compare_requirements(
            extraction=extraction,
            candidate_skills=skills,
            vacancy_title=title_text,
        )

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
        recommended_resume = _recommend_resume(extraction.role_type)

        completeness = content_completeness.upper().strip()
        title_from_text = _extract_title_from_vacancy_text(vacancy)
        title_text = title_from_text or extraction.role_type
        alert_query = _extract_alert_query(vacancy)
        incomplete_title_class = _classify_incomplete_title(
            title=title_from_text or title_text,
            alert_query=alert_query,
        )
        # Only trust explicit Title: line for seniority upgrades on incomplete cards.
        title_allows_strong = (
            incomplete_title_class == "jvm_explicit_backend" and title_from_text is not None
        )
        has_explicit_jvm_evidence = _has_explicit_jvm_evidence(extraction, title_text=title_text)
        location_nuance, location_cap = _build_location_nuance(
            location_values=comparison.location_restrictions,
            uncertainty_values=comparison.uncertainties,
            vacancy_text=vacancy,
        )
        if location_cap is not None:
            decision = _cap_decision(decision, location_cap)

        lead_nuance, lead_signal = _build_lead_warning(vacancy_text=vacancy)
        is_incomplete = completeness in {"PARTIAL", "MINIMAL"}
        employment_remaining, info_items = _extract_info_items(
            employment_conditions=comparison.employment_conditions,
            vacancy_text=vacancy,
            uncertainties=comparison.uncertainties,
        )

        if is_incomplete:
            incomplete_description_nuance = "Описание вакансии неполное — требуется открыть LinkedIn"
            stack_missing_nuance = "В email-карточке не указан стек — требуется открыть полное описание"
            combined_incomplete_nuance = "В email-карточке нет полного описания и стека — требуется открыть LinkedIn"
            generic_missing_stack = (
                incomplete_title_class == "generic_backend"
                and not has_explicit_jvm_evidence
            )
            if generic_missing_stack:
                incomplete_nuance = combined_incomplete_nuance
            else:
                incomplete_nuance = incomplete_description_nuance
            nuances = _clean_and_limit(
                [
                    incomplete_nuance,
                    *([stack_missing_nuance] if generic_missing_stack else []),
                    *( [location_nuance] if location_nuance else []),
                    *( [lead_nuance] if lead_nuance else []),
                ],
                limit=3,
            )
            gaps = []
            evidence_sufficient = completeness == "PARTIAL" and (
                explicit_skill_count >= 3 or title_allows_strong
            )

            if completeness == "MINIMAL":
                if title_allows_strong and decision != Decision.IGNORE:
                    decision = Decision.STRONG_MATCH
                    evidence_sufficient = True
                else:
                    decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
                    match_percentage = None
                    evidence_sufficient = False
            elif completeness == "PARTIAL":
                if title_allows_strong and decision != Decision.IGNORE:
                    decision = Decision.STRONG_MATCH
                elif explicit_skill_count < 3:
                    decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
                    match_percentage = None
                elif decision == Decision.STRONG_MATCH and not _is_partial_strong_allowed(
                    vacancy_text=vacancy,
                    extraction=extraction,
                ):
                    decision = Decision.POTENTIAL_MATCH
            if (
                generic_missing_stack
                and incomplete_title_class != "hard_negative"
            ):
                decision = Decision.POTENTIAL_MATCH
                match_percentage = None
                gaps = []
                recommended_resume = RecommendedResume.JAVA_BACKEND
            if incomplete_title_class == "hard_negative":
                decision = Decision.IGNORE
            if lead_nuance and not _candidate_targets_lead_roles(skills):
                decision = _cap_decision(decision, Decision.POTENTIAL_MATCH)
        else:
            nuances = _clean_and_limit(
                [
                    *employment_remaining,
                    *( [location_nuance] if location_nuance else []),
                    *comparison.uncertainties,
                    *( [lead_nuance] if lead_nuance else []),
                ],
                limit=3,
            )
            # Remove work-mode / salary lines that were already classified as info.
            nuances = [item for item in nuances if not _is_info_metadata_text(item)]
        decision_reason = _build_decision_reason(
            decision=decision,
            extraction=extraction,
            comparison=comparison,
            location_nuance=location_nuance,
            lead_nuance=lead_nuance,
            explicit_skill_count=explicit_skill_count,
            title_text=title_text,
        )
        if (
            is_incomplete
            and incomplete_title_class == "jvm_explicit_backend"
            and decision == Decision.STRONG_MATCH
        ):
            decision_reason = (
                "Explicit Java stack is already present in the trusted title."
            )
        warning_signals = _build_warning_signals(
            nuances=nuances,
            lead_signal=lead_signal,
            location_nuance=location_nuance,
        )

        return VacancyEvaluation(
            decision=decision,
            summary=extraction.short_summary,
            decision_reason=decision_reason,
            matched_points=_clean_and_limit(comparison.matched_mandatory, limit=5),
            gaps=gaps,
            nuances=nuances,
            info_items=_dedupe_preserve_case(info_items, limit=5),
            match_percentage=match_percentage,
            matched_score=matched_score,
            total_possible_score=total_possible_score,
            explicit_skill_count=explicit_skill_count,
            evidence_sufficient=evidence_sufficient,
            recommended_resume=recommended_resume,
            recommended_cover_template=_recommend_cover_template(extraction.role_type, extraction.short_summary),
            warning_signals=warning_signals,
        )


def _build_decision_reason(
    *,
    decision: Decision,
    extraction: VacancyExtraction,
    comparison,
    location_nuance: str | None,
    lead_nuance: str | None,
    explicit_skill_count: int,
    title_text: str,
) -> str:
    role = extraction.role_type.strip() or "role"
    mandatory = set(comparison.missing_mandatory)
    location_text = " ".join(comparison.location_restrictions).lower()
    uncertainty_text = " ".join(comparison.uncertainties).lower()
    all_constraints = f"{location_text} {uncertainty_text}"
    role_text = role.lower()
    evidence_text = " ".join(
        [
            title_text.lower(),
            role_text,
            extraction.short_summary.lower(),
            " ".join(extraction.mandatory_skills).lower(),
            " ".join(extraction.optional_skills).lower(),
        ]
    )
    has_jvm_evidence = any(
        token in evidence_text
        for token in ("java", "kotlin", "jvm", "spring", "spring boot", "micronaut", "quarkus", "jakarta ee")
    )
    has_conflicting_stack = any(
        token in evidence_text
        for token in (
            "python",
            "django",
            "flask",
            "fastapi",
            "go",
            "golang",
            "node",
            "node.js",
            "typescript",
            "javascript",
            ".net",
            "dotnet",
            "php",
            "ruby",
            "frontend",
            "react",
            "angular",
            "mobile",
            "qa",
            "devops",
            "data scientist",
            "machine learning",
            "ml",
            "embedded",
        )
    )
    if decision == Decision.STRONG_MATCH:
        if comparison.matched_mandatory:
            top = ", ".join(comparison.matched_mandatory[:3])
            return f"Core requirements are matched ({top}) with strong backend alignment."
        return "Core backend requirements are matched with strong alignment."
    if decision == Decision.POTENTIAL_MATCH:
        if location_nuance:
            return "Role appears relevant but location/remote constraints require confirmation."
        if lead_nuance:
            return "Role is relevant but lead-level expectations need manual verification."
        if not has_jvm_evidence and "backend" in role_text and not has_conflicting_stack:
            return "Backend role but the email summary does not specify the technology stack."
        if explicit_skill_count < 3 and not any(token in title_text.lower() for token in ("java", "kotlin", "jvm", "spring")):
            return "Backend signal is present, but the email card lacks enough explicit stack evidence."
        if mandatory:
            missing = ", ".join(sorted(mandatory)[:2])
            return f"Role is relevant, but some required skills are missing ({missing})."
        return "Backend role matches partially, but evidence is not strong enough for a full match."
    if any(token in all_constraints for token in ("incompatible", "must reside in philippines", "country restriction")):
        return "Location restriction is incompatible with the candidate profile."
    if has_conflicting_stack:
        return f'Primary stack in the vacancy points to "{role}", not the target Java backend stack.'
    if any(token in role_text for token in ("frontend", "react", "angular", "qa", "tester", "devops", "python")):
        return f'Role focus is "{role}", which is outside the target Java backend scope.'
    if not any(token in (set(extraction.mandatory_skills) | set(extraction.optional_skills)) for token in ("java", "kotlin", "jvm", "spring boot")):
        return "No Java/Kotlin/JVM requirement is explicitly present in the vacancy requirements."
    if mandatory:
        missing = ", ".join(sorted(mandatory)[:3])
        return f"Required skills are missing for this profile ({missing})."
    return "Vacancy requirements do not align with the target Java backend profile."


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


def _dedupe_preserve_case(values: list[str], limit: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = " ".join(item.strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
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
    has_language_core = any(token in text for token in ("java", "kotlin", "jvm", "spring"))
    has_backend_context = any(
        token in text
        for token in ("backend", "back end", "back-end", "spring", "api", "engineer", "developer")
    )
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


def _build_lead_warning(vacancy_text: str) -> tuple[str | None, dict[str, str] | None]:
    markers = (
        " lead ",
        "tech lead",
        "technical leadership",
        "architecture ownership",
        "lead architecture",
        "architect",
        "principal",
        "staff engineer",
        "mentor",
        "mentoring",
        "manage team",
        "people management",
    )
    section = "description"
    for raw_line in vacancy_text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("title:"):
            section = "title"
        elif lowered.startswith("alert query:"):
            section = "alert_query"
        elif lowered.startswith("snippet:"):
            section = "description"
            continue
        elif lowered.startswith("source url:"):
            section = "url"
        if not line:
            continue
        padded = f" {lowered} "
        if not any(marker in padded for marker in markers):
            continue
        return (
            "Роль уровня Lead — стоит проверить ожидания по управлению и архитектурной ответственности",
            {"code": "lead_level", "source": section, "evidence": line[:160]},
        )
    return None, None


def _build_warning_signals(
    *,
    nuances: list[str],
    lead_signal: dict[str, str] | None,
    location_nuance: str | None,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    if lead_signal is not None:
        key = (lead_signal.get("code", ""), lead_signal.get("source", ""), lead_signal.get("evidence", ""))
        seen.add(key)
        signals.append(lead_signal)
    for nuance in nuances:
        text = " ".join(nuance.strip().split())
        if not text:
            continue
        if _is_info_metadata_text(text):
            continue
        lowered = text.lower()
        if "роль уровня lead" in lowered:
            code = "lead_level"
            source = lead_signal.get("source", "vacancy_text") if lead_signal else "vacancy_text"
            evidence = lead_signal.get("evidence", text) if lead_signal else text
        elif location_nuance and text == location_nuance:
            code = "location_constraint"
            source = "vacancy_text"
            evidence = text
        elif "неполное" in lowered or "нет полного описания" in lowered:
            code = "incomplete_description"
            source = "email_summary"
            evidence = text
        elif _is_warning_worthy_text(text):
            code = "nuance"
            source = "heuristic"
            evidence = text
        else:
            continue
        key = (code, source, evidence)
        if key in seen:
            continue
        seen.add(key)
        signals.append({"code": code, "source": source, "evidence": evidence})
    return signals


def _extract_info_items(
    *,
    employment_conditions: list[str],
    vacancy_text: str,
    uncertainties: list[str],
) -> tuple[list[str], list[str]]:
    remaining: list[str] = []
    info_items: list[str] = []
    seen_info: set[str] = set()

    def add_info(label: str, value: str) -> None:
        item = f"{label}: {value}"
        key = item.lower()
        if key in seen_info:
            return
        seen_info.add(key)
        info_items.append(item)

    for raw in employment_conditions:
        text = " ".join(raw.strip().split())
        if not text:
            continue
        lowered = text.lower()
        work_mode = _detect_work_mode(lowered)
        if work_mode == "hybrid":
            add_info("Work mode", "Hybrid")
            continue
        if work_mode == "on-site":
            add_info("Constraints", "On-site")
            continue
        if work_mode == "remote":
            add_info("Work mode", "Remote")
            continue
        salary = _detect_salary(text)
        if salary is not None:
            add_info("Salary", salary)
            continue
        remaining.append(text)

    combined = " ".join([vacancy_text, *uncertainties]).lower()
    if not any(item.lower().startswith("work mode:") for item in info_items):
        mode = _detect_work_mode(combined)
        if mode == "hybrid":
            add_info("Work mode", "Hybrid")
        elif mode == "on-site":
            add_info("Constraints", "On-site")
        elif mode == "remote":
            add_info("Work mode", "Remote")
    if not any(item.lower().startswith("salary:") for item in info_items):
        salary = _detect_salary(vacancy_text)
        if salary is not None:
            add_info("Salary", salary)

    return remaining, info_items


def _detect_work_mode(text: str) -> str | None:
    if "hybrid" in text:
        return "hybrid"
    if any(token in text for token in ("on-site", "onsite", "on site", "in-office", "in office")):
        return "on-site"
    if any(token in text for token in ("remote", "удален")):
        return "remote"
    return None


def _detect_salary(text: str) -> str | None:
    lowered = text.lower()
    if "salary" in lowered or "compensation" in lowered or "₱" in text or "php " in lowered:
        cleaned = " ".join(text.strip().split())
        if cleaned.lower().startswith("salary:"):
            return cleaned.split(":", 1)[1].strip() or cleaned
        return cleaned
    return None


def _is_info_metadata_text(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith("work mode:") or lowered.startswith("constraints:") or lowered.startswith("salary:"):
        return True
    return _detect_work_mode(lowered) is not None and not _is_warning_worthy_text(text)


def _is_warning_worthy_text(text: str) -> bool:
    lowered = text.lower()
    warning_markers = (
        "sponsorship",
        "visa",
        "relocation",
        "relocate",
        "lead",
        "архитектур",
        "управлен",
        "contract",
        "контракт",
        "филиппин",
        "philippines",
        "неполное",
        "нет полного описания",
        "unusual",
        "must reside",
    )
    return any(marker in lowered for marker in warning_markers)


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


def _extract_title_from_vacancy_text(vacancy_text: str) -> str | None:
    for line in vacancy_text.splitlines():
        if line.lower().startswith("title:"):
            value = " ".join(line.split(":", 1)[1].strip().split())
            return value or None
    return None


def _extract_alert_query(vacancy_text: str) -> str | None:
    for line in vacancy_text.splitlines():
        if line.lower().startswith("alert query:"):
            value = " ".join(line.split(":", 1)[1].strip().split())
            return value or None
    return None


def _has_explicit_jvm_evidence(extraction: VacancyExtraction, *, title_text: str = "") -> bool:
    all_skills = [*extraction.mandatory_skills, *extraction.optional_skills]
    text = f"{title_text.lower()} {' '.join(all_skills).lower()}".strip()
    return any(token in text for token in ("java", "kotlin", "jvm", "spring"))


def _classify_incomplete_title(*, title: str, alert_query: str | None) -> str:
    text = title.lower()
    hard_negative_markers = (
        "frontend",
        "front-end",
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
        "dotnet",
        "mobile",
        "ios",
        "android",
        "data science",
        "data scientist",
        "ml",
        "machine learning",
    )
    jvm_explicit_markers = ("java", "kotlin", "jvm", "spring")
    if any(marker in text for marker in hard_negative_markers):
        if any(marker in text for marker in ("backend", "back-end", "back end")) and any(
            marker in text for marker in jvm_explicit_markers
        ):
            return "jvm_explicit_backend"
        return "hard_negative"

    has_backend_marker = any(marker in text for marker in ("backend", "back-end", "back end"))
    has_generic_backend_context = (
        ("software engineer" in text or "software developer" in text)
        and any(marker in text for marker in ("platform", "server-side", "distributed", "microservices", "infrastructure"))
    )
    if any(marker in text for marker in jvm_explicit_markers) and (has_backend_marker or "engineer" in text or "developer" in text):
        return "jvm_explicit_backend"
    if has_backend_marker or has_generic_backend_context:
        return "generic_backend"

    if alert_query:
        weak_context = alert_query.lower()
        if any(marker in weak_context for marker in jvm_explicit_markers) and any(
            marker in text for marker in ("engineer", "developer", "platform")
        ):
            return "generic_backend"
    return "other"
