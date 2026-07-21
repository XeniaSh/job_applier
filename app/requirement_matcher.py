from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Decision, VacancyExtraction
from app.skills_profile_loader import CandidateSkillsProfile

OPTIONAL_WEIGHT_MULTIPLIER = 0.3
DEFAULT_SKILL_WEIGHT = 1
JVM_EVIDENCE_TERMS = (
    "java",
    "kotlin",
    "jvm",
    "spring",
    "spring boot",
    "micronaut",
    "quarkus",
    "jakarta ee",
)
CONFLICTING_STACK_TERMS = (
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
    "dotnet",
    ".net",
    "c#",
    "ruby",
    "rails",
    "php",
    "laravel",
    "frontend",
    "react",
    "angular",
    "vue",
    "mobile",
    "android",
    "ios",
    "qa",
    "tester",
    "devops",
    "sre",
    "data science",
    "data scientist",
    "machine learning",
    "ml",
    "embedded",
)
BACKEND_SIGNAL_TERMS = (
    "backend",
    "back-end",
    "back end",
    "microservice",
    "microservices",
    "platform",
    "api",
    "server-side",
)


def _normalize_skill_name(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("/", " ")
    normalized = re.sub(r"[^a-z0-9+\s-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split()).lower()


def _clean_text_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = _normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _contains_skill(text: str, skill: str) -> bool:
    pattern = rf"(?<![a-z0-9+]){re.escape(skill)}(?![a-z0-9+])"
    return re.search(pattern, text) is not None


def _build_alias_lookup(profile: CandidateSkillsProfile) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in profile.aliases.items():
        canonical_normalized = _normalize_skill_name(canonical)
        if not canonical_normalized:
            continue
        lookup[canonical_normalized] = canonical_normalized
        for alias in aliases:
            alias_normalized = _normalize_skill_name(alias)
            if alias_normalized:
                lookup[alias_normalized] = canonical_normalized
    return lookup


def _canonicalize(raw_skill: str, aliases: dict[str, str]) -> str:
    normalized = _normalize_skill_name(raw_skill)
    return aliases.get(normalized, normalized)


def _build_skill_vocabulary(
    profile: CandidateSkillsProfile,
    aliases: dict[str, str],
) -> dict[str, str]:
    vocabulary: dict[str, str] = {}
    all_terms = [
        *profile.strong_skills,
        *profile.practical_skills,
        *profile.absent_skills,
        *profile.aliases.keys(),
        *profile.skill_weights.keys(),
    ]
    for alias_terms in profile.aliases.values():
        all_terms.extend(alias_terms)

    for term in all_terms:
        normalized_term = _normalize_skill_name(term)
        if not normalized_term:
            continue
        vocabulary[normalized_term] = _canonicalize(term, aliases)
    return vocabulary


def _is_atomic_skill_candidate(skill: str) -> bool:
    words = skill.split()
    if not words or len(words) > 4:
        return False
    disallowed = {
        "year",
        "years",
        "experience",
        "senior",
        "middle",
        "junior",
        "lead",
        "backend",
        "frontend",
        "development",
        "responsibilities",
        "timezone",
        "relocation",
        "remote",
        "hybrid",
        "onsite",
    }
    return not any(word in disallowed for word in words)


def _extract_atomic_skills(raw_skills: list[str], vocabulary: dict[str, str]) -> list[str]:
    extracted: list[str] = []
    seen: set[str] = set()
    ordered_variants = sorted(vocabulary.keys(), key=len, reverse=True)

    for raw_skill in raw_skills:
        normalized_item = _normalize_skill_name(raw_skill)
        if not normalized_item:
            continue

        matched_in_item: list[str] = []
        for variant in ordered_variants:
            if _contains_skill(normalized_item, variant):
                matched_in_item.append(vocabulary[variant])

        if matched_in_item:
            for skill in matched_in_item:
                if skill and skill not in seen:
                    seen.add(skill)
                    extracted.append(skill)
            continue

        parts = re.split(r",|;|/|\band\b|\bor\b", normalized_item)
        for part in parts:
            candidate = _normalize_skill_name(part)
            if not candidate:
                continue
            canonical = vocabulary.get(candidate, candidate)
            if not _is_atomic_skill_candidate(canonical):
                continue
            if canonical in seen:
                continue
            seen.add(canonical)
            extracted.append(canonical)

    return extracted


def _filter_uncertainties(values: list[str]) -> list[str]:
    allowed_patterns = (
        "неясно",
        "неясн",
        "неясен",
        "не указ",
        "непонят",
        "не определ",
        "требует уточнен",
        "возможно",
        "unclear",
        "uncertain",
        "not clear",
    )
    allowed_topics = (
        "mandatory",
        "обязатель",
        "optional",
        "географ",
        "location",
        "remote",
        "удален",
        "timezone",
        "таймзон",
        "часы",
        "график",
        "working hours",
        "employment",
        "тип занятости",
        "контракт",
        "legal",
        "правов",
        "eligibility",
    )
    banned_topics = (
        "salary",
        "зарплат",
        "benefit",
        "льгот",
        "responsibilit",
        "обязанност",
        "senior",
        "junior",
        "middle",
        "experience",
        "опыт",
        "architecture",
        "архитектур",
        "code review",
        "ревью",
        "collaboration",
        "взаимодейств",
        "high-load",
        "high load",
        "высоконагруж",
    )
    cleaned = _clean_text_list(values)
    filtered: list[str] = []
    for item in cleaned:
        if any(topic in item for topic in banned_topics):
            continue
        if any(pattern in item for pattern in allowed_patterns) and any(
            topic in item for topic in allowed_topics
        ):
            filtered.append(item)
    return filtered


def _weight_for_skill(
    skill: str,
    skill_weights: dict[str, int],
) -> int:
    return max(skill_weights.get(skill, DEFAULT_SKILL_WEIGHT), DEFAULT_SKILL_WEIGHT)


def _decision_rank(decision: Decision) -> int:
    if decision == Decision.IGNORE:
        return 0
    if decision == Decision.POTENTIAL_MATCH:
        return 1
    return 2


def _apply_cap(decision: Decision, cap: Decision) -> Decision:
    return cap if _decision_rank(decision) > _decision_rank(cap) else decision


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def sanitize_extraction(
    extraction: VacancyExtraction,
    candidate_skills: CandidateSkillsProfile,
) -> VacancyExtraction:
    aliases = _build_alias_lookup(candidate_skills)
    vocabulary = _build_skill_vocabulary(candidate_skills, aliases)
    minimum_experience_years = extraction.minimum_experience_years
    if minimum_experience_years is not None and minimum_experience_years < 0:
        minimum_experience_years = None

    return VacancyExtraction(
        mandatory_skills=_extract_atomic_skills(extraction.mandatory_skills, vocabulary),
        optional_skills=_extract_atomic_skills(extraction.optional_skills, vocabulary),
        minimum_experience_years=minimum_experience_years,
        seniority=_normalize_text(extraction.seniority) if extraction.seniority else None,
        responsibilities=_clean_text_list(extraction.responsibilities),
        employment_conditions=_clean_text_list(extraction.employment_conditions),
        location_restrictions=_clean_text_list(extraction.location_restrictions),
        uncertainties=_filter_uncertainties(extraction.uncertainties),
        role_type=extraction.role_type,
        short_summary=extraction.short_summary,
    )


@dataclass(frozen=True)
class DeterministicMatchResult:
    decision: Decision
    matched_mandatory: list[str]
    matched_optional: list[str]
    missing_mandatory: list[str]
    optional_missing: list[str]
    mandatory_missing_weights: dict[str, int]
    optional_missing_weights: dict[str, int]
    employment_conditions: list[str]
    location_restrictions: list[str]
    uncertainties: list[str]
    role_type: str
    short_summary: str
    match_percentage: float | None
    matched_score: float
    total_possible_score: float


def compare_requirements(
    extraction: VacancyExtraction,
    candidate_skills: CandidateSkillsProfile,
    vacancy_title: str | None = None,
) -> DeterministicMatchResult:
    extraction = sanitize_extraction(extraction=extraction, candidate_skills=candidate_skills)
    aliases = _build_alias_lookup(candidate_skills)
    canonical_weights = {
        _canonicalize(skill, aliases): max(int(weight), DEFAULT_SKILL_WEIGHT)
        for skill, weight in candidate_skills.skill_weights.items()
    }
    core_skills = [_canonicalize(skill, aliases) for skill in candidate_skills.core_skills]

    present_skills = {
        _canonicalize(skill, aliases)
        for skill in [*candidate_skills.strong_skills, *candidate_skills.practical_skills]
    }

    matched_mandatory: list[str] = []
    matched_optional: list[str] = []
    missing_mandatory: list[str] = []
    optional_missing: list[str] = []
    mandatory_missing_weights: dict[str, int] = {}
    optional_missing_weights: dict[str, int] = {}
    seen_mandatory: set[str] = set()
    seen_optional: set[str] = set()

    total_possible_score = 0.0
    matched_score = 0.0

    for skill in extraction.mandatory_skills:
        canonical_skill = _canonicalize(skill, aliases)
        if not canonical_skill or canonical_skill in seen_mandatory:
            continue
        seen_mandatory.add(canonical_skill)
        weight = _weight_for_skill(canonical_skill, canonical_weights)
        total_possible_score += weight
        if canonical_skill in present_skills:
            matched_mandatory.append(canonical_skill)
            matched_score += weight
        else:
            missing_mandatory.append(canonical_skill)
            mandatory_missing_weights[canonical_skill] = weight

    for skill in extraction.optional_skills:
        canonical_skill = _canonicalize(skill, aliases)
        if not canonical_skill or canonical_skill in seen_optional:
            continue
        seen_optional.add(canonical_skill)
        weight = _weight_for_skill(canonical_skill, canonical_weights)
        contribution = weight * OPTIONAL_WEIGHT_MULTIPLIER
        total_possible_score += contribution
        if canonical_skill in present_skills:
            matched_optional.append(canonical_skill)
            matched_score += contribution
        else:
            optional_missing.append(canonical_skill)
            optional_missing_weights[canonical_skill] = weight

    match_percentage: float | None
    if total_possible_score <= 0:
        match_percentage = None
        decision = Decision.POTENTIAL_MATCH
    else:
        match_percentage = round((matched_score / total_possible_score) * 100, 1)
        if match_percentage >= 85:
            decision = Decision.STRONG_MATCH
        elif match_percentage >= 65:
            decision = Decision.POTENTIAL_MATCH
        else:
            decision = Decision.IGNORE

    matched_all = set(matched_mandatory) | set(matched_optional)
    role_text = _normalize_text(extraction.role_type)
    skills_text = _normalize_text(" ".join([*extraction.mandatory_skills, *extraction.optional_skills]))
    summary_text = _normalize_text(extraction.short_summary)
    title_text = _normalize_text(vacancy_title or "")
    evidence_text = f"{title_text} {role_text} {skills_text} {summary_text}"
    has_jvm_evidence = _contains_any(evidence_text, JVM_EVIDENCE_TERMS)
    has_conflicting_stack = _contains_any(evidence_text, CONFLICTING_STACK_TERMS)
    has_backend_signal = _contains_any(evidence_text, BACKEND_SIGNAL_TERMS)

    # Strong requires explicit positive JVM evidence.
    if decision == Decision.STRONG_MATCH and not has_jvm_evidence:
        decision = Decision.POTENTIAL_MATCH
    # Missing Java/JVM evidence is uncertainty, not rejection.
    if decision == Decision.IGNORE and not has_conflicting_stack and (has_backend_signal or not has_jvm_evidence):
        decision = Decision.POTENTIAL_MATCH

    if "spring boot" in seen_mandatory and "spring boot" not in matched_all:
        decision = _apply_cap(decision, Decision.POTENTIAL_MATCH)
    if core_skills:
        missing_core = [skill for skill in core_skills if skill in seen_mandatory and skill not in matched_all]
        if len(missing_core) == len([skill for skill in core_skills if skill in seen_mandatory]):
            decision = _apply_cap(decision, Decision.POTENTIAL_MATCH)

    conditions_text = " ".join([*extraction.employment_conditions, *extraction.uncertainties])
    location_text = " ".join(extraction.location_restrictions)
    all_constraint_text = _normalize_text(f"{conditions_text} {location_text}")

    if "frontend" in role_text and "backend" not in role_text:
        decision = Decision.IGNORE
    python_first = "python" in role_text or "python" in " ".join(extraction.mandatory_skills)
    meaningful_jvm = any(skill in (set(extraction.mandatory_skills) | set(extraction.optional_skills)) for skill in ("java", "kotlin", "spring boot", "jvm"))
    if python_first and not meaningful_jvm:
        decision = Decision.IGNORE
    if has_conflicting_stack and not has_jvm_evidence:
        decision = Decision.IGNORE
    if any(token in all_constraint_text for token in ("unpaid", "equity-only", "equity only", "без оплаты", "без зарплаты")):
        decision = Decision.IGNORE
    if any(token in role_text for token in ("cto", "technical co-founder", "tech co-founder", "co-founder")) and any(
        token in all_constraint_text for token in ("without salary", "без зарплаты", "equity", "unpaid")
    ):
        decision = Decision.IGNORE
    if any(token in all_constraint_text for token in ("incompatible location", "несовместим")):
        decision = Decision.IGNORE
    if any(token in role_text for token in ("model evaluation", "model training", "ai evaluator")) and "backend" not in role_text:
        decision = Decision.IGNORE

    if extraction.minimum_experience_years is not None and candidate_skills.experience_years is not None:
        gap = extraction.minimum_experience_years - candidate_skills.experience_years
        if gap >= 2:
            decision = Decision.IGNORE
        elif gap == 1:
            decision = _apply_cap(decision, Decision.POTENTIAL_MATCH)

    remote_geo_unclear = any(
        marker in " ".join(extraction.uncertainties)
        for marker in ("remote geography", "география удаленной", "страна удаленной", "удаленной работы")
    )
    timezone_conflict = any(
        marker in all_constraint_text
        for marker in (
            "timezone conflict",
            "конфликт таймзон",
            "time zone conflict",
            "часы пересечения",
            "таймзоне",
            "timezone overlap",
        )
    )
    if remote_geo_unclear or timezone_conflict:
        decision = _apply_cap(decision, Decision.POTENTIAL_MATCH)
    if any(marker in all_constraint_text for marker in ("incompatible country", "страна не подходит", "country restriction incompatible")):
        decision = Decision.IGNORE

    return DeterministicMatchResult(
        decision=decision,
        matched_mandatory=matched_mandatory,
        matched_optional=matched_optional,
        missing_mandatory=missing_mandatory,
        optional_missing=optional_missing,
        mandatory_missing_weights=mandatory_missing_weights,
        optional_missing_weights=optional_missing_weights,
        employment_conditions=list(extraction.employment_conditions),
        location_restrictions=list(extraction.location_restrictions),
        uncertainties=list(extraction.uncertainties),
        role_type=extraction.role_type,
        short_summary=extraction.short_summary,
        match_percentage=match_percentage,
        matched_score=round(matched_score, 3),
        total_possible_score=round(total_possible_score, 3),
    )
