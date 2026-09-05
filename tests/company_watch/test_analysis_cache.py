from pathlib import Path

from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.analysis_cache import (
    TargetCompanyAnalysisCache,
    vacancy_description_hash,
)
from app.company_watch.application_recommendation import ApplicationRecommendation
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def test_analysis_cache_module_is_importable() -> None:
    import app.company_watch.analysis_cache as analysis_cache

    assert analysis_cache.DEFAULT_TARGET_COMPANY_ANALYSIS_CACHE_PATH.name == (
        "target_company_analysis_cache.json"
    )
    assert analysis_cache.TargetCompanyAnalysisCache is TargetCompanyAnalysisCache


def _vacancy(
    *,
    title: str = "Java Backend Engineer",
    description: str = "Java backend services",
) -> NormalizedVacancy:
    return NormalizedVacancy(
        source="target_company:greenhouse:adyen",
        external_id="101",
        title=title,
        company="Adyen",
        location="Amsterdam",
        employment="Full-time",
        description=description,
        url="https://job-boards.greenhouse.io/adyen/jobs/101",
        published_at="2026-09-05T10:00:00Z",
    )


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.STRONG_MATCH,
        summary="Strong Java backend role",
        decision_reason="Java backend match",
        matched_points=["java", "backend"],
        match_percentage=86.0,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _feasibility() -> ApplicationFeasibility:
    return ApplicationFeasibility(
        label="UNCLEAR",
        visa_sponsorship="unknown",
        relocation_support="unknown",
        remote_type="unknown",
        work_authorization_requirement="unknown",
        language_requirements=[],
        location_restrictions=[],
        warnings=["visa/relocation not mentioned; check manually"],
    )


def test_description_hash_uses_description_when_present() -> None:
    first = vacancy_description_hash(_vacancy(description="Java backend services"))
    second = vacancy_description_hash(_vacancy(description="Java backend services"))
    changed = vacancy_description_hash(_vacancy(description="Java backend services and Kafka"))
    assert first == second
    assert first != changed


def test_description_hash_falls_back_to_title_location_url() -> None:
    base = _vacancy(description="")
    same = _vacancy(description="   ")
    different_title = _vacancy(description="", title="Staff Java Engineer")
    assert vacancy_description_hash(base) == vacancy_description_hash(same)
    assert vacancy_description_hash(base) != vacancy_description_hash(different_title)


def test_cache_roundtrip_restores_analysis(tmp_path: Path) -> None:
    cache_file = tmp_path / "missing-dir" / "cache.json"
    cache = TargetCompanyAnalysisCache(cache_file)
    vacancy = _vacancy()
    cache.put(
        vacancy,
        evaluation=_evaluation(),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(
            label="CHECK_MANUALLY",
            reasons=["sponsorship/relocation/location is unclear"],
        ),
        seniority=SeniorityClassification(label="UNKNOWN", reasons=["title has no explicit seniority"]),
    )
    cache.save()

    loaded = TargetCompanyAnalysisCache(cache_file)
    loaded.load()
    record = loaded.get(vacancy)

    assert cache_file.is_file()
    assert record is not None
    assert record.evaluation.decision == Decision.STRONG_MATCH
    assert record.evaluation.match_percentage == 86.0
    assert record.feasibility.label == "UNCLEAR"
    assert record.recommendation.label == "CHECK_MANUALLY"
    assert record.seniority.label == "UNKNOWN"
    assert loaded.get(_vacancy(description="changed")) is None
