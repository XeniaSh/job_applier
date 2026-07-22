from app.collectors.linkedin_models import ContentCompleteness, LinkedInEmailVacancy
from app.collectors.vacancy_collector import NormalizedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
    VacancyExtraction,
)
from app.skills_profile_loader import CandidateSkillsProfile
from app.telegram.formatter import card_display_sections, format_telegram_card_html
from app.telegram.models import TelegramVacancyCard
from app.vacancy_analyzer import VacancyAnalyzer


class _TitleEchoClient:
    def __init__(self, *, mandatory: list[str] | None = None, role_type: str | None = None) -> None:
        self.mandatory = mandatory or []
        self.role_type = role_type

    def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
        title = "Role"
        for line in vacancy.splitlines():
            if line.lower().startswith("title:"):
                title = line.split(":", 1)[1].strip()
                break
        return VacancyExtraction(
            mandatory_skills=self.mandatory,
            optional_skills=[],
            minimum_experience_years=None,
            seniority=None,
            responsibilities=[],
            employment_conditions=["Hybrid work"],
            location_restrictions=[],
            uncertainties=[],
            role_type=self.role_type or title,
            short_summary="Кратко",
        )


def _profile() -> CandidateSkillsProfile:
    return CandidateSkillsProfile(
        strong_skills=["java", "spring boot", "kafka", "postgresql"],
        practical_skills=[],
        absent_skills=[],
        aliases={},
        experience_years=7,
        core_skills=["java", "spring boot"],
        skill_weights={"java": 10, "spring boot": 9, "kafka": 7, "postgresql": 6},
    )


def test_analysis_text_has_no_duplicated_title_company_location() -> None:
    linkedin = LinkedInEmailVacancy(
        external_id="1",
        title="Senior Backend Engineer (Java)",
        company="ACME",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/1/",
        snippet=None,
        email_message_id="m1",
        received_at=None,
        content_completeness=ContentCompleteness.PARTIAL,
    )
    direct = linkedin.to_analysis_text()
    assert direct.count("Title:") == 1
    assert direct.count("Company:") == 1
    assert direct.count("Location:") == 1
    assert "Description:\n<not available in LinkedIn email>" in direct

    normalized = NormalizedVacancy(
        source="linkedin-email",
        external_id="1",
        title=linkedin.title,
        company=linkedin.company,
        location=linkedin.location,
        employment=None,
        description=linkedin.description_for_normalized(),
        url=linkedin.url,
        published_at=None,
        snippet=None,
        content_completeness="PARTIAL",
    )
    wrapped = normalized.to_analysis_text()
    assert wrapped.count("Title:") == 1
    assert wrapped.count("Company:") == 1
    assert wrapped.count("Location:") == 1
    assert "Description:\n<not available in LinkedIn email>" in wrapped
    assert wrapped.count("Source URL:") == 1


def test_java_and_spring_titles_are_strong_without_description() -> None:
    analyzer = VacancyAnalyzer(
        llm_client=_TitleEchoClient(mandatory=[]),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    cases = (
        "Senior Backend Engineer (Java)",
        "Java Backend Engineer",
        "Spring Boot Developer",
        "Java API Developer",
    )
    for title in cases:
        result = analyzer.analyze(f"Title: {title}", content_completeness="PARTIAL")
        assert result.decision == Decision.STRONG_MATCH, title
        assert "Explicit Java stack is already present in the trusted title." in result.decision_reason


def test_generic_backend_title_stays_potential() -> None:
    analyzer = VacancyAnalyzer(
        llm_client=_TitleEchoClient(mandatory=[], role_type="Back-End Developer"),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    result = analyzer.analyze("Title: Back-End Developer", content_completeness="PARTIAL")
    assert result.decision == Decision.POTENTIAL_MATCH


def test_hybrid_and_onsite_are_info_not_warnings() -> None:
    analyzer = VacancyAnalyzer(
        llm_client=_TitleEchoClient(mandatory=["java", "spring boot", "kafka", "postgresql"]),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    hybrid = analyzer.analyze(
        "Title: Java Backend Engineer\nEmployment: Hybrid work arrangement",
        content_completeness="FULL",
    )
    assert any(item.startswith("Work mode: Hybrid") for item in hybrid.info_items)
    assert not any("hybrid" in (signal.get("evidence") or "").lower() for signal in hybrid.warning_signals)
    assert not any(signal.get("code") == "nuance" and "hybrid" in (signal.get("evidence") or "").lower() for signal in hybrid.warning_signals)

    class OnsiteClient(_TitleEchoClient):
        def extract_vacancy(self, prompt: str, vacancy: str) -> VacancyExtraction:
            payload = super().extract_vacancy(prompt, vacancy)
            return payload.model_copy(update={"employment_conditions": ["On-site in office"]})

    onsite = VacancyAnalyzer(
        llm_client=OnsiteClient(mandatory=["java", "spring boot", "kafka", "postgresql"]),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    ).analyze("Title: Java Backend Engineer\nLocation: On-site", content_completeness="FULL")
    assert any(item.startswith("Constraints: On-site") for item in onsite.info_items)
    assert not any("on-site" in (signal.get("evidence") or "").lower() and signal.get("code") == "nuance" for signal in onsite.warning_signals)


def test_telegram_card_separates_warnings_and_information() -> None:
    evaluation = VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="summary",
        matched_points=[],
        gaps=[],
        nuances=["Requires relocation to PH"],
        info_items=["Work mode: Hybrid", "Salary: ₱2.3M–₱3.5M"],
        match_percentage=None,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=False,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        warning_signals=[
            {"code": "location_constraint", "source": "vacancy_text", "evidence": "Requires relocation to PH"},
        ],
    )
    warnings, info_items = card_display_sections(evaluation)
    card = TelegramVacancyCard(
        source="li",
        external_id="1",
        decision="POTENTIAL_MATCH",
        title="Java Backend Engineer",
        company="ACME",
        location="Manila",
        url="https://www.linkedin.com/jobs/view/1/",
        match_percentage=None,
        gaps=[],
        nuances=evaluation.nuances,
        warnings=warnings,
        info_items=info_items,
        recommended_resume="java-backend",
        content_completeness="FULL",
    )
    rendered = format_telegram_card_html(card)
    assert "⚠️ Requires relocation to PH" in rendered
    assert "Work mode:\nHybrid" in rendered
    assert "Salary:\n₱2.3M–₱3.5M" in rendered
    assert "⚠️ Hybrid" not in rendered
    assert "⚠️ Work mode" not in rendered
