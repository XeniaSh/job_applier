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
    employment_conditions: list[str] | None = None,
    location_restrictions: list[str] | None = None,
    uncertainties: list[str] | None = None,
) -> VacancyExtraction:
    return VacancyExtraction(
        mandatory_skills=mandatory_skills,
        optional_skills=optional_skills or [],
        minimum_experience_years=minimum_experience_years,
        seniority=None,
        responsibilities=[],
        employment_conditions=employment_conditions or [],
        location_restrictions=location_restrictions or [],
        uncertainties=uncertainties or [],
        role_type=role_type,
        short_summary="Тест",
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


def test_missing_java_produces_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(mandatory_skills=["java", "kafka"]),
        candidate_skills=_skills_profile(experience_years=6).model_copy(
            update={"strong_skills": ["spring boot", "kafka", "postgresql"]}
        ),
    )
    assert result.decision == Decision.IGNORE


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


def test_score_50_is_ignore() -> None:
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
    assert result.decision == Decision.IGNORE


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


def test_two_year_experience_shortfall_is_ignore() -> None:
    result = compare_requirements(
        extraction=_extraction(
            mandatory_skills=["java", "spring boot", "kafka"],
            minimum_experience_years=8,
        ),
        candidate_skills=_skills_profile(experience_years=6),
    )
    assert result.decision == Decision.IGNORE


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


def test_repeated_scoring_produces_identical_output() -> None:
    extraction = _extraction(
        mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
        optional_skills=["redis", "webflux"],
        minimum_experience_years=6,
    )
    first = compare_requirements(extraction=extraction, candidate_skills=_skills_profile())
    second = compare_requirements(extraction=extraction, candidate_skills=_skills_profile())
    assert first == second
