from app.formatter import format_evaluation_ru
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def test_formatter_omits_empty_gaps_and_nuances_sections() -> None:
    result = VacancyEvaluation(
        decision=Decision.STRONG_MATCH,
        summary="Краткое резюме",
        matched_points=["java", "spring boot"],
        gaps=[],
        nuances=[],
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )

    output = format_evaluation_ru(result)

    assert "Совпадение по стеку: n/a" in output
    assert "Пробелы:" not in output
    assert "Нюансы:" not in output


def test_formatter_shows_no_matches_text() -> None:
    result = VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="Краткое резюме",
        matched_points=[],
        gaps=["redis"],
        nuances=[],
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )

    output = format_evaluation_ru(result)

    assert "Совпадения: нет" in output
    assert "Совпадение по стеку: n/a" in output
    assert "Need clarification:" not in output
    assert "Optional gap:" not in output


def test_formatter_applies_output_limits_and_is_stable() -> None:
    result = VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="  Java/Kotlin backend  для enterprise- и fintech-проектов.  ",
        matched_points=["java", "spring boot", "kafka", "postgresql", "microservices", "kotlin"],
        gaps=["redis", "oracle", "elasticsearch", "mongodb"],
        nuances=["проектный формат", "удаленная работа не указана", "нужен timezone overlap", "еще один нюанс"],
        match_percentage=88.9,
        recommended_resume=RecommendedResume.FINTECH_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.FINTECH,
    )

    first = format_evaluation_ru(result)
    second = format_evaluation_ru(result)

    assert first == second
    assert "Совпадение по стеку: 88.9%" in first
    assert first.count("\n- ") == 11
    assert "- kotlin" not in first
    assert "- mongodb" not in first
    assert "- еще один нюанс" not in first
