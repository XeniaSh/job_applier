from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.preparation_service import (
    ApplicationPreparationError,
    PreparationService,
    resolve_resume_path,
)
from app.collectors.email_imap_client import RawEmailMessage
from app.collectors.linkedin_models import ContentCompleteness, LinkedInEmailVacancy
from app.models import (
    CoverLetterResult,
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


def _raw_message() -> RawEmailMessage:
    m = EmailMessage()
    m["From"] = "jobs-noreply@linkedin.com"
    m["Subject"] = "Job alert"
    m.set_content("placeholder")
    return RawEmailMessage(
        uid="1",
        message_id="<m1>",
        from_address="jobs-noreply@linkedin.com",
        subject="Job alert",
        received_at=datetime.now(timezone.utc),
        email_message=m,
    )


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="summary",
        matched_points=["java"],
        gaps=[],
        nuances=["Локация требует уточнения", "Роль уровня Lead — стоит проверить ожидания по управлению и архитектурной ответственности"],
        match_percentage=None,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=False,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


@dataclass
class _FakeEmailClient:
    messages: list[RawEmailMessage]

    def fetch_linkedin_messages(self):
        return self.messages


class _FakeAnalyzer:
    def analyze(self, vacancy: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        _ = vacancy, content_completeness
        return _evaluation()


class _FakeLLM:
    def __init__(self, language: str, text: str) -> None:
        self.language = language
        self.text = text
        self.last_candidate_profile: str | None = None

    def create_cover_letter(self, **kwargs) -> CoverLetterResult:
        self.last_candidate_profile = kwargs["candidate_profile"]
        return CoverLetterResult(
            language=self.language,
            cover_letter=self.text,
            used_resume="java-backend",
        )


def test_resolve_resume_path_safe(tmp_path: Path) -> None:
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    (resumes_dir / "java-backend.pdf").write_bytes(b"pdf")
    path, warning = resolve_resume_path(resumes_dir, "java-backend")
    assert path is not None
    assert warning is None

    path2, warning2 = resolve_resume_path(resumes_dir, "../etc/passwd")
    assert path2 is None
    assert warning2 is not None


def test_missing_resume_warning(tmp_path: Path, monkeypatch) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda message: [
            LinkedInEmailVacancy(
                external_id="123",
                title="Java Backend Engineer",
                company="ACME",
                location="Remote",
                url="https://www.linkedin.com/jobs/view/123/",
                snippet="Snippet",
                email_message_id="m1",
                received_at=None,
                content_completeness=ContentCompleteness.PARTIAL,
            )
        ],
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=_FakeLLM(language="ru", text="Короткий текст без Redis и Senior."),
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        resumes_dir=tmp_path / "resumes",
    )
    prepared = service.prepare("linkedin-email", "123")
    assert prepared.resume_path is None
    assert any("Resume file missing:" in warning for warning in prepared.warnings)


def test_vacancy_not_found_raises(monkeypatch, tmp_path: Path) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(module, "parse_linkedin_email", lambda message: [])
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=_FakeLLM(language="en", text="Simple text."),
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        resumes_dir=tmp_path / "resumes",
    )
    with pytest.raises(ApplicationPreparationError):
        service.prepare("linkedin-email", "999")


def test_candidate_profile_md_used_as_source_of_truth(tmp_path: Path, monkeypatch) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda message: [
            LinkedInEmailVacancy(
                external_id="55",
                title="Java Backend Engineer",
                company="ACME",
                location="Remote",
                url="https://www.linkedin.com/jobs/view/55/",
                snippet="Snippet",
                email_message_id="m1",
                received_at=None,
                content_completeness=ContentCompleteness.PARTIAL,
            )
        ],
    )
    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )

    fake_llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=fake_llm,
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        resumes_dir=tmp_path / "resumes",
    )
    service.prepare("linkedin-email", "55")
    assert fake_llm.last_candidate_profile is not None
    assert "around seven years" in fake_llm.last_candidate_profile
    assert "strong_skills:" not in fake_llm.last_candidate_profile
