from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyExtraction,
)
from app.formatter import format_evaluation_ru
from app.skills_profile_loader import CandidateSkillsProfile
from app.vacancy_analyzer import VacancyAnalyzer


class FakeLLMClient:
    def __init__(self) -> None:
        self.called_with: dict[str, str] | None = None

    def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
        self.called_with = {"prompt": prompt, "vacancy": vacancy}
        return VacancyExtraction(
            mandatory_skills=["Java", "Spring Boot", "Kafka"],
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
            practical_skills=["kafka"],
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
    assert result.matched_points == ["java", "spring boot", "kafka"]
    assert result.gaps == []
    assert result.match_percentage == 97.1
    assert result.matched_score == 20.0
    assert result.total_possible_score == 20.6
    assert result.explicit_skill_count == 4
    assert result.evidence_sufficient is True
    assert result.nuances == ["full-time", "не указаны часы пересечения по таймзоне"]
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


def test_minimal_content_cannot_be_strong_match() -> None:
    analyzer = VacancyAnalyzer(
        llm_client=FakeLLMClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("Title: Java Backend Engineer", content_completeness="MINIMAL")

    assert result.decision in {Decision.POTENTIAL_MATCH, Decision.IGNORE}
    assert result.match_percentage is None
    assert result.evidence_sufficient is False
    assert "описание вакансии неполное" in " ".join(result.nuances)


def test_incomplete_content_does_not_create_false_missing_skill_gaps() -> None:
    class MissingStackLLMClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql", "redis"],
                optional_skills=[],
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
        llm_client=MissingStackLLMClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6, "redis": 2},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("Title: Java Backend Engineer", content_completeness="PARTIAL")
    assert result.gaps == []


def test_partial_with_two_skills_cannot_be_strong_and_score_hidden() -> None:
    class TwoSkillsClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot"],
                optional_skills=[],
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
        llm_client=TwoSkillsClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("Title: Java Backend Engineer", content_completeness="PARTIAL")
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage is None
    assert result.explicit_skill_count == 2
    assert result.evidence_sufficient is False


def test_partial_with_four_skills_may_be_strong() -> None:
    class FourSkillsClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=["remote worldwide"],
                location_restrictions=[],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=FourSkillsClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka", "postgresql"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("Title: Java Backend Engineer", content_completeness="PARTIAL")
    assert result.decision == Decision.STRONG_MATCH
    assert result.match_percentage == 100.0
    assert result.explicit_skill_count == 4
    assert result.evidence_sufficient is True


def test_raw_location_strings_are_not_used_as_nuances() -> None:
    class LocationClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=["National Capital Region, Philippines"],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=LocationClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Java Backend role", content_completeness="PARTIAL")
    rendered = " ".join(result.nuances)
    assert "national capital region, philippines" not in rendered
    assert "manila" in rendered or "филиппинах" in rendered


def test_explicit_philippines_residency_requirement_is_ignore() -> None:
    class ResidencyClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=["must reside in Philippines"],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=ResidencyClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Java Backend role", content_completeness="PARTIAL")
    assert result.decision == Decision.IGNORE


def test_worldwide_remote_has_no_location_downgrade() -> None:
    class RemoteClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=["worldwide remote"],
                location_restrictions=[],
                uncertainties=[],
                role_type="Java Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=RemoteClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka", "postgresql"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Java Backend role", content_completeness="PARTIAL")
    assert result.decision == Decision.STRONG_MATCH


def test_lead_title_adds_nuance_and_caps_when_partial() -> None:
    class LeadClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority="Lead",
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Backend Lead (Java/Kotlin)",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=LeadClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka", "postgresql"],
            practical_skills=["kotlin"],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6, "kotlin": 5},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    partial = analyzer.analyze("Backend Lead (Java/Kotlin)", content_completeness="PARTIAL")
    full = analyzer.analyze("Backend Lead (Java/Kotlin)", content_completeness="FULL")
    nuance_text = " ".join(partial.nuances)
    assert "роль уровня lead" in nuance_text
    assert partial.decision == Decision.POTENTIAL_MATCH
    assert full.decision == Decision.STRONG_MATCH


def test_incomplete_nuances_are_capped_and_deduplicated() -> None:
    class NuanceClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority="Lead",
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=["Philippines"],
                uncertainties=["не указаны обязательные и дополнительные навыки"],
                role_type="Lead Java Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=NuanceClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )

    result = analyzer.analyze("Lead Java Engineer", content_completeness="PARTIAL")
    assert len(result.nuances) <= 3
    assert len(result.nuances) == len(set(result.nuances))
    assert "не указаны обязательные и дополнительные навыки" not in " ".join(result.nuances)
    assert result.gaps == []


def test_repeated_evaluation_produces_identical_output() -> None:
    analyzer = VacancyAnalyzer(
        llm_client=FakeLLMClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    first = analyzer.analyze("Backend role", content_completeness="PARTIAL")
    second = analyzer.analyze("Backend role", content_completeness="PARTIAL")
    assert first == second
    assert format_evaluation_ru(first) == format_evaluation_ru(second)


def test_partial_generic_backend_developer_becomes_potential_not_ignore() -> None:
    class GenericBackendClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=[],
                optional_skills=[],
                minimum_experience_years=None,
                seniority="Senior",
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Senior Backend Developer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=GenericBackendClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=["kafka"],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Title: Senior Backend Developer", content_completeness="PARTIAL")
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage is None
    assert result.gaps == []
    assert result.recommended_resume == RecommendedResume.JAVA_BACKEND
    assert any("нет полного описания и стека" in nuance for nuance in result.nuances)


def test_partial_generic_backend_platform_becomes_potential() -> None:
    class GenericPlatformClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=[],
                optional_skills=[],
                minimum_experience_years=None,
                seniority="Senior",
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Senior Backend Engineer - Platform",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=GenericPlatformClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Title: Senior Backend Engineer - Platform", content_completeness="PARTIAL")
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage is None
    assert result.gaps == []


def test_partial_jvm_explicit_title_uses_existing_jvm_handling() -> None:
    class JavaExplicitClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["java", "spring boot", "kafka", "postgresql"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority="Senior",
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Senior Java Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=JavaExplicitClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot", "kafka", "postgresql"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Title: Senior Java Engineer", content_completeness="PARTIAL")
    assert result.decision in {Decision.STRONG_MATCH, Decision.POTENTIAL_MATCH}
    assert "нет полного описания и стека" not in " ".join(result.nuances)


def test_full_vacancy_without_java_kotlin_still_respects_core_guardrail() -> None:
    class NonJvmFullClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=["python", "django"],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Backend Engineer",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=NonJvmFullClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze("Title: Backend Engineer", content_completeness="FULL")
    assert result.decision == Decision.IGNORE


def test_partial_python_backend_and_frontend_remain_ignore() -> None:
    class PythonBackendClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            role = "Python Backend Developer" if "Python" in vacancy else "Frontend Engineer"
            return VacancyExtraction(
                mandatory_skills=[],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type=role,
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=PythonBackendClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    python_result = analyzer.analyze("Title: Python Backend Developer", content_completeness="PARTIAL")
    frontend_result = analyzer.analyze("Title: Frontend Engineer", content_completeness="PARTIAL")
    assert python_result.decision == Decision.IGNORE
    assert frontend_result.decision == Decision.IGNORE


def test_alert_context_is_weak_evidence_and_not_strong() -> None:
    class WeakContextClient(FakeLLMClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            return VacancyExtraction(
                mandatory_skills=[],
                optional_skills=[],
                minimum_experience_years=None,
                seniority=None,
                responsibilities=[],
                employment_conditions=[],
                location_restrictions=[],
                uncertainties=[],
                role_type="Software Engineer - Platform",
                short_summary="Кратко",
            )

    analyzer = VacancyAnalyzer(
        llm_client=WeakContextClient(),
        skills_loader=lambda: CandidateSkillsProfile(
            strong_skills=["java", "spring boot"],
            practical_skills=[],
            absent_skills=[],
            aliases={},
            experience_years=6,
            core_skills=["java", "spring boot"],
            skill_weights={"java": 10, "spring boot": 9},
        ),
        prompt_loader=lambda: "PROMPT_CONTENT",
    )
    result = analyzer.analyze(
        "Title: Software Engineer - Platform\nAlert context: Kotlin Backend",
        content_completeness="PARTIAL",
    )
    assert result.decision == Decision.POTENTIAL_MATCH
    assert result.match_percentage is None
