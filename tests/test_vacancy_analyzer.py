from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyExtraction,
)
from app.skills_profile_loader import CandidateSkillsProfile
from app.vacancy_analyzer import VacancyAnalyzer


class FakeLLMClient:
    def __init__(self) -> None:
        self.called_with: dict[str, str] | None = None

    def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
        self.called_with = {"prompt": prompt, "vacancy": vacancy}
        return VacancyExtraction(
            mandatory_skills=["Java", "Spring Boot"],
            optional_skills=["Redis"],
            minimum_experience_years=6,
            seniority="Senior",
            responsibilities=["Design services", "Code review"],
            employment_conditions=["Full-time", "  Full-time  "],
            location_restrictions=["EU timezone overlap"],
            uncertainties=[
                "Неясно, обязателен ли офис",
                "неясно, обязателен ли офис",
                "не указаны часы пересечения по таймзоне",
                "",
            ],
            role_type="Java Backend Engineer",
            short_summary="Продуктовая backend-вакансия на Java.",
        )


def test_vacancy_analyzer_uses_loaders_and_client() -> None:
    fake_client = FakeLLMClient()
    analyzer = VacancyAnalyzer(
        llm_client=fake_client,
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=["redis"],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "redis": 2},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("VACANCY_CONTENT")

    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.summary == "Продуктовая backend-вакансия на Java."
    assert result.matched_points == ["java", "spring boot"]
    assert result.gaps == []
    assert result.match_percentage == 96.9
    assert result.matched_score == 19.0
    assert result.total_possible_score == 19.6
    assert result.nuances == [
        "full-time",
        "eu timezone overlap",
        "не указаны часы пересечения по таймзоне",
    ]
    assert "design services" not in result.nuances
    assert "code review" not in result.nuances
    assert result.recommended_resume == RecommendedResume.JAVA_BACKEND
    assert result.recommended_cover_template == RecommendedCoverTemplate.GENERIC
    assert fake_client.called_with == {
        "prompt": "PROMPT_CONTENT",
        "vacancy": "VACANCY_CONTENT",
    }


def test_missing_mandatory_skills_appear_only_in_gaps() -> None:
    class MandatoryGapLLMClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["Redis"],
                optional_skills=["WebFlux"],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=["Design services"],
                employment_conditions=["Проектный формат"],
                location_restrictions=[],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Краткое описание.",
            )

    analyzer = VacancyAnalyzer(
        llm_client=MandatoryGapLLMClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java"],
            practical_skills=[],
            absent_skills=["redis", "spring webflux"],
            aliases={"spring webflux": ["webflux"]},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "redis": 2, "spring webflux": 2},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("VACANCY_CONTENT")

    assert result.gaps == ["redis"]
    assert "spring webflux" not in result.gaps
    assert "spring webflux" not in result.nuances


def test_gaps_are_sorted_by_weight_descending() -> None:
    class WeightedGapLLMClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql", "redis"],
                optional_skills=["camunda"],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=WeightedGapLLMClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"spring boot": 9, "kafka": 7, "postgresql": 6, "redis": 2, "camunda": 1},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("VACANCY_CONTENT")

    assert result.gaps == ["spring boot", "kafka", "postgresql"]
