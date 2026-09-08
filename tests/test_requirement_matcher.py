from app.models import Decision, VacancyExtraction
from app.requirement_matcher import compare_requirements
from app.skills_profile_loader import CandidateSkillsProfile


def _skills_profile(*, experience_years: int = 6) -> CandidateSkillsProfile:
    return CandidateSkillsProfile(
        strong_skills=["java", "spring boot", "kafka", "postgresql", "microservices", "rest api"],
        practical_skills=["kotlin", "docker", "kubernetes", "concurrency", "sql optimization"],
        absent_skills=["redis", "spring webflux", "camunda"],
        experience_years=experience_years,
        aliases={"spring webflux": ["webflux"], "postgresql": ["postgres"]},
        core_skills=["java", "spring boot"],
        skill_weights={
            "java": 10,
            "spring boot": 9,
            "microservices": 8,
            "kafka": 7,
            "postgresql": 6,
            "rest api": 6,
            "kotlin": 5,
            "docker": 4,
            "kubernetes": 4,
            "concurrency": 4,
            "sql optimization": 3,
            "distributed systems": 3,
            "jvm": 3,
            "performance tuning": 3,
            "redis": 2,
            "spring webflux": 2,
            "camunda": 1,
        },
    )


def _extraction(
    *,
    mandatory_skills: list[str],
    optional_skills: list[str] | None = None,
    minimum_experience_years: int | None = None,
    role_type: str = "Java Backend Engineer",
    short_summary: str = "Тест",
    responsibilities: list[str] | None = None,
    employment_conditions: list[str] | None = None,
    location_restrictions: list[str] | None = None,
    uncertainties: list[str] | None = None,
) -> VacancyExtraction:
    return VacancyExtraction(
        mandatory_skills=mandatory_skills,
        optional_skills=optional_skills or [],
        minimum_experience_years=minimum_experience_years,
        seniority=None,
        responsibilities=responsibilities or [],
        employment_conditions=employment_conditions or [],
        location_restrictions=location_restrictions or [],
        uncertainties=uncertainties or [],
        role_type=role_type,
        short_summary=short_summary,
    )


def test_missing_redis_and_webflux_still_strong_match() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka", "postgresql", "microservices"],
            optional_skills=["redis", "webflux"],
        ),
        candidate_skills=_skills_profile(),
    )

    assert result.decision == Decision.STRONG_MATCH
    assert result.match_percentage == 97.1


def test_missing_java_is_potential_without_conflicting_stack() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "kafka"]),
        candidate_skills=_skills_profile(experience_years=6).model_copy(
            update={"strong_skills": ["spring boot", "kafka", "postgresql"]}
        ),
    )
    assert result.decision == Decision.POTENTIAL_MATCH


def test_missing_mandatory_spring_boot_caps_potential() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "spring boot", "kafka", "postgresql"]),
        candidate_skills=_skills_profile().model_copy(
            update={"strong_skills": ["java", "kafka", "postgresql"]}
        ),
    )
    assert result.decision == Decision.POTENTIAL_MATCH


def test_score_90_plus_is_strong_match() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "spring boot", "kafka", "postgresql", "docker"]),
        candidate_skills=_skills_profile(),
    )
    assert result.match_percentage == 100.0
    assert result.decision == Decision.STRONG_MATCH


def test_score_70_is_potential_match() -> None:
    profile = _skills_profile().model_copy(
        update={
            "strong_skills": ["java"],
            "practical_skills": [],
            "skill_weights": {"java": 7, "kafka": 3},
            "core_skills": ["java", "spring boot"],
        }
    )
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "kafka"]),
        candidate_skills=profile,
    )
    assert result.match_percentage == 70.0
    assert result.decision == Decision.POTENTIAL_MATCH


def test_score_50_is_potential_without_conflicting_stack() -> None:
    profile = _skills_profile().model_copy(
        update={
            "strong_skills": ["java"],
            "practical_skills": [],
            "skill_weights": {"java": 1, "kafka": 1},
            "core_skills": ["java", "spring boot"],
        }
    )
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "kafka"]),
        candidate_skills=profile,
    )
    assert result.match_percentage == 50.0
    assert result.decision == Decision.POTENTIAL_MATCH


def test_optional_skills_contribute_only_30_percent() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java"], optional_skills=["kafka"]),
        candidate_skills=_skills_profile().model_copy(update={"strong_skills": ["java"]}),
    )
    assert result.total_possible_score == 12.1
    assert result.matched_score == 10.0
    assert result.match_percentage == 82.6


def test_unknown_skill_uses_default_weight_one() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "unknown skill"]),
        candidate_skills=_skills_profile().model_copy(update={"strong_skills": ["java"]}),
    )
    assert result.total_possible_score == 11.0
    assert result.match_percentage == 90.9


def test_one_year_experience_shortfall_caps_potential() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            minimum_experience_years=7,
        ),
        candidate_skills=_skills_profile(experience_years=6),
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.experience_gap_years == 1
    assert result.experience_gap_capped is True


def test_two_year_experience_shortfall_caps_potential() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            minimum_experience_years=8,
        ),
        candidate_skills=_skills_profile(experience_years=6),
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage == 100.0
    assert result.experience_gap_years == 2
    assert result.experience_gap_capped is True


def test_agoda_company_name_is_not_conflicting_go_stack() -> None:
    profile = _skills_profile().model_copy(
        update={
            "strong_skills": ["java"],
            "practical_skills": [],
            "skill_weights": {"java": 1, "kafka": 1},
            "core_skills": ["java", "spring boot"],
        }
    )
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "kafka"],
            role_type="backend engineer",
            short_summary="Backend systems at Agoda with relocation to Bangkok",
        ),
        candidate_skills=profile,
        vacancy_title="Back End Staff Software Engineer",
    )
    assert result.match_percentage == 50.0
    assert result.decision == Decision.POTENTIAL_MATCH


def test_go_backend_without_jvm_is_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["go"],
            role_type="Go backend engineer",
            short_summary="Build backend services in Go",
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Go backend engineer",
    )
    assert result.decision == Decision.IGNORE


def test_golang_backend_without_jvm_is_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["golang"],
            role_type="Golang backend engineer",
            short_summary="Build backend services in Golang",
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Golang backend engineer",
    )
    assert result.decision == Decision.IGNORE


def test_mlflow_is_not_conflicting_ml_stack() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            short_summary="Use MLflow to track experiment metadata",
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Java Backend Engineer",
    )
    assert result.decision == Decision.STRONG_MATCH


def test_full_time_only_is_nuance_only() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            employment_conditions=["full-time only"],
        ),
        candidate_skills=_skills_profile(),
    )
    assert result.match_percentage == 100.0
    assert result.decision == Decision.STRONG_MATCH


def test_unclear_remote_geography_caps_potential() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            uncertainties=["неясна география удаленной работы"],
        ),
        candidate_skills=_skills_profile(),
    )
    assert result.decision == Decision.POTENTIAL_MATCH


def test_incompatible_location_is_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            location_restrictions=["incompatible country restriction"],
        ),
        candidate_skills=_skills_profile(),
    )
    assert result.decision == Decision.IGNORE


def test_missing_redis_cannot_independently_force_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "spring boot", "kafka"], optional_skills=["redis"]),
        candidate_skills=_skills_profile(),
    )
    assert result.decision == Decision.STRONG_MATCH


def test_java_backend_title_stays_strong_match() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "spring boot", "kafka"]),
        candidate_skills=_skills_profile(),
        vacancy_title="Senior Java Backend Engineer",
    )
    assert result.decision == Decision.STRONG_MATCH


def test_compiler_title_is_not_strong_from_java_kotlin_alone() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "kotlin"],
            role_type="Senior Compiler Developer",
            short_summary="Work on Kotlin compiler core.",
            responsibilities=["Implement compiler IR", "Improve codegen"],
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Senior Compiler Developer (Kotlin Compiler - Core)",
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage == 100.0


def test_jvm_runtime_title_without_backend_signals_gets_domain_penalty() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java"],
            role_type="JVM Runtime Engineer",
            short_summary="Work on JVM runtime internals.",
            responsibilities=["Tune garbage collection", "Maintain runtime internals"],
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="JVM Runtime Engineer",
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage == 100.0


def test_distributed_systems_title_is_not_domain_mismatch() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            role_type="Senior Java Engineer",
            short_summary="Build distributed Java services.",
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Senior Java Engineer - Distributed Systems",
    )
    assert result.decision == Decision.STRONG_MATCH


def test_payments_title_is_not_domain_mismatch() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            role_type="Java Software Engineer",
            short_summary="Payments product engineering.",
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Java Software Engineer - Payments",
    )
    assert result.decision == Decision.STRONG_MATCH


def test_compiler_title_can_stay_strong_when_description_has_backend_work() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "kotlin"],
            role_type="Senior Compiler Developer",
            short_summary="Compiler work plus backend services.",
            responsibilities=["Design backend microservices", "Own compiler tooling"],
        ),
        candidate_skills=_skills_profile(),
        vacancy_title="Senior Compiler Developer (Kotlin Compiler - Core)",
    )
    assert result.decision == Decision.STRONG_MATCH


def test_repeated_scoring_produces_identical_output() -> None:
    extraction = _extraction(
        mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
        optional_skills=["redis", "webflux"],
        minimum_experience_years=6,
    )
    first = compare_requirements(extraction=extraction, candidate_skills=_skills_profile())
    second = compare_requirements(extraction=extraction, candidate_skills=_skills_profile())
    assert first == second


def test_agoda_like_java_kotlin_experience_stretch_is_potential() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "kotlin"],
            minimum_experience_years=10,
            role_type="backend engineer",
            short_summary="Back End Staff Software Engineer at Agoda",
        ),
        candidate_skills=_skills_profile(experience_years=6),
        vacancy_title="Back End Staff Software Engineer",
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage == 100.0
    assert result.experience_gap_years == 4
    assert result.experience_gap_capped is True
