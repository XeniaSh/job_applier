from __future__ import annotations

import logging

import pytest

from app.models import Decision
from app.skills_profile_loader import CandidateSkillsProfile
from app.title_rules import (
    RULE_EXPLICIT_JVM_BACKEND,
    RULE_INCOMPATIBLE_EDUCATION,
    RULE_INCOMPATIBLE_NON_ENGINEERING,
    RULE_JAVA_ANDROID_DOWNGRADE,
    RULE_JAVA_QA_DOWNGRADE,
    RULE_LLM_FALLBACK,
    classify_title_match,
)
from app.vacancy_analyzer import VacancyAnalyzer


class _ExplodingLLM:
    def extract_vacancy(self, prompt: str, vacancy: str):
        raise AssertionError("LLM must be skipped for deterministic title classification")


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def extract_vacancy(self, prompt: str, vacancy: str):
        self.calls += 1
        from app.models import VacancyExtraction

        title = "Role"
        for line in vacancy.splitlines():
            if line.lower().startswith("title:"):
                title = line.split(":", 1)[1].strip()
                break
        return VacancyExtraction(
            mandatory_skills=[],
            optional_skills=[],
            minimum_experience_years=None,
            seniority=None,
            responsibilities=[],
            employment_conditions=[],
            location_restrictions=[],
            uncertainties=[],
            role_type=title,
            short_summary="summary",
        )


def _profile() -> CandidateSkillsProfile:
    return CandidateSkillsProfile(
        strong_skills=["java", "spring boot"],
        practical_skills=[],
        absent_skills=[],
        aliases={},
        experience_years=7,
        core_skills=["java"],
        skill_weights={"java": 10, "spring boot": 9},
    )


@pytest.mark.parametrize(
    "title",
    [
        "Java Developer",
        "Senior Java Developer",
        "Java Backend Developer",
        "Senior Java Backend Engineer",
        "Mid/Senior Backend Engineer (Java)",
        "Senior Back-end Developer (JAVA)",
        "Software Engineer, Java",
        "Java Software Engineer",
        "Kotlin Backend Developer",
        "JVM Backend Engineer",
        "Spring Boot Developer",
    ],
)
def test_explicit_jvm_backend_titles_are_strong(title: str) -> None:
    result = classify_title_match(title)
    assert result.match_strength == Decision.STRONG_MATCH
    assert result.rule == RULE_EXPLICIT_JVM_BACKEND
    assert result.llm_skipped is True


@pytest.mark.parametrize(
    "title",
    [
        "JavaScript Developer",
        "Senior JavaScript Backend Engineer",
        "Node.js Developer",
    ],
)
def test_javascript_is_not_java_strong(title: str) -> None:
    result = classify_title_match(title)
    assert result.match_strength != Decision.STRONG_MATCH
    assert "java" not in result.jvm_hits


@pytest.mark.parametrize(
    ("title", "rule"),
    [
        ("Java Teacher", RULE_INCOMPATIBLE_EDUCATION),
        ("Java Tutor", RULE_INCOMPATIBLE_EDUCATION),
        ("Java Trainer", RULE_INCOMPATIBLE_EDUCATION),
        ("Java Instructor", RULE_INCOMPATIBLE_EDUCATION),
        ("Java Recruiter", RULE_INCOMPATIBLE_NON_ENGINEERING),
        ("Java Technical Writer", RULE_INCOMPATIBLE_NON_ENGINEERING),
    ],
)
def test_incompatible_titles_are_ignore(title: str, rule: str) -> None:
    result = classify_title_match(title)
    assert result.match_strength == Decision.IGNORE
    assert result.rule == rule
    assert result.llm_skipped is True


@pytest.mark.parametrize(
    ("title", "rule"),
    [
        ("QA Automation Engineer Java", RULE_JAVA_QA_DOWNGRADE),
        ("Test Automation Engineer (Java)", RULE_JAVA_QA_DOWNGRADE),
        ("Android Developer Java", RULE_JAVA_ANDROID_DOWNGRADE),
    ],
)
def test_java_qa_and_android_are_potential(title: str, rule: str) -> None:
    result = classify_title_match(title)
    assert result.match_strength == Decision.POTENTIAL_MATCH
    assert result.rule == rule
    assert result.llm_skipped is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Backend Engineer",
        "Software Engineer",
    ],
)
def test_generic_backend_titles_use_llm_fallback(title: str) -> None:
    result = classify_title_match(title)
    assert result.match_strength is None
    assert result.rule == RULE_LLM_FALLBACK
    assert result.llm_skipped is False


def test_partial_java_backend_is_strong_without_snippet_and_skips_llm(caplog) -> None:
    analyzer = VacancyAnalyzer(
        llm_client=_ExplodingLLM(),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    with caplog.at_level(logging.INFO):
        result = analyzer.analyze(
            "Title: Senior Java Backend Engineer\nDescription:\n<not available in LinkedIn email>",
            content_completeness="PARTIAL",
        )
    assert result.decision == Decision.STRONG_MATCH
    assert result.decision_reason == "Explicit Java + backend signals in title"
    assert any("Job description is not available in the LinkedIn email" in item for item in result.info_items)
    assert not any("неполн" in (signal.get("evidence") or "").lower() for signal in result.warning_signals)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert 'CLASSIFICATION_RULE title="Senior Java Backend Engineer"' in log_text
    assert "rule=EXPLICIT_JVM_BACKEND_TITLE" in log_text
    assert "match_strength=STRONG" in log_text
    assert "content_completeness=PARTIAL" in log_text
    assert "llm_skipped=True" in log_text


def test_qa_java_partial_is_potential_and_skips_llm(caplog) -> None:
    analyzer = VacancyAnalyzer(
        llm_client=_ExplodingLLM(),
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    with caplog.at_level(logging.INFO):
        result = analyzer.analyze(
            "Title: QA Automation Engineer Java",
            content_completeness="PARTIAL",
        )
    assert result.decision == Decision.POTENTIAL_MATCH
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "rule=JAVA_QA_DOWNGRADE" in log_text
    assert "match_strength=POTENTIAL" in log_text
    assert "llm_skipped=True" in log_text


def test_generic_backend_partial_calls_llm(caplog) -> None:
    llm = _RecordingLLM()
    analyzer = VacancyAnalyzer(
        llm_client=llm,
        skills_loader=_profile,
        prompt_loader=lambda: "PROMPT",
    )
    with caplog.at_level(logging.INFO):
        result = analyzer.analyze(
            "Title: Senior Backend Engineer\nDescription:\n<not available in LinkedIn email>",
            content_completeness="PARTIAL",
        )
    assert llm.calls == 1
    assert result.decision == Decision.POTENTIAL_MATCH
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert 'CLASSIFICATION_RULE title="Senior Backend Engineer"' in log_text
    assert "rule=LLM_FALLBACK" in log_text
    assert "llm_skipped=False" in log_text
