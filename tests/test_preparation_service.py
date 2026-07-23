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
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, vacancy: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        _ = vacancy, content_completeness
        self.calls += 1
        return _evaluation()


class _FakeLLM:
    def __init__(self, language: str, text: str) -> None:
        self.language = language
        self.text = text
        self.last_candidate_profile: str | None = None
        self.last_timing_events: list[dict] = []
        self.model = "test-model"
        self.cover_calls = 0

    def create_cover_letter(self, **kwargs) -> CoverLetterResult:
        self.cover_calls += 1
        self.last_candidate_profile = kwargs["candidate_profile"]
        self.last_timing_events.append(
            {
                "operation": kwargs.get("operation", "cover_letter"),
                "model": self.model,
                "elapsed_ms": 12,
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
                "error": None,
            }
        )
        return CoverLetterResult(
            language=self.language,
            cover_letter=self.text,
            used_resume="java-backend",
        )


class _FakePrepareCache:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.saved: dict | None = None

    def get_prepare_cache(self, source: str, external_id: str) -> dict | None:
        _ = source, external_id
        return self.payload

    def save_prepare_cache(self, **kwargs) -> None:
        self.saved = kwargs


class _FailingEmailClient:
    def fetch_linkedin_messages(self):
        raise AssertionError("IMAP must not be called when prepare cache hits")


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


def test_prepare_uses_cached_analysis_and_skips_imap(tmp_path: Path, monkeypatch, caplog) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )
    evaluation = _evaluation()
    cache = _FakePrepareCache(
        {
            "evaluation_json": evaluation.model_dump_json(),
            "analysis_text": "Title: Java Backend Engineer\nContent completeness: PARTIAL",
            "title": "Java Backend Engineer",
            "company": "ACME",
            "location": "Remote",
            "url": "https://www.linkedin.com/jobs/view/123/",
            "content_completeness": "PARTIAL",
            "snippet": "Snippet",
            "vacancy_json": '{"title":"Java Backend Engineer","analysis_text":"Title: Java Backend Engineer"}',
        }
    )
    analyzer = _FakeAnalyzer()
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    service = PreparationService(
        analyzer=analyzer,
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=cache,
    )
    with caplog.at_level("INFO"):
        prepared = service.prepare("linkedin-email", "123")

    assert analyzer.calls == 0
    assert llm.cover_calls == 1
    assert prepared.timing_breakdown["llm_calls"] == 1
    assert prepared.timing_breakdown["analysis_cached"] is True
    assert prepared.timing_breakdown["imap_fallback"] is False
    assert prepared.timing_breakdown["phases_ms"]["imap_fetch"] == 0
    phases = prepared.timing_breakdown["phases_ms"]
    for phase_name in (
        "resume_generation",
        "application_answers",
        "imap_fetch",
        "analysis",
        "cover_letter",
        "validation",
        "serialization",
    ):
        assert phase_name in phases, phase_name
        assert isinstance(phases[phase_name], int), phase_name
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "START resume_generation" in log_text
    assert "END resume_generation +" in log_text
    assert "START analysis" in log_text
    assert "END analysis +" in log_text
    assert "(cached)" in log_text
    assert "START cover_letter" in log_text
    assert "END cover_letter +" in log_text
    assert "START application_answers" in log_text
    assert "END application_answers +0ms" in log_text
    assert "START validation" in log_text
    assert "END validation +" in log_text
    assert "START serialization" in log_text
    assert "END serialization +" in log_text
    assert "Prepare timing breakdown" in log_text
    assert "llm_calls=1" in log_text
    assert "resume_generation=" in log_text
    assert "validation=" in log_text
    assert "serialization=" in log_text


def test_prepare_uses_vacancy_cache_without_evaluation_and_skips_imap(tmp_path: Path, monkeypatch) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )
    cache = _FakePrepareCache(
        {
            "evaluation_json": None,
            "analysis_text": "Title: Java Backend Engineer\nCompany: ACME\nLocation: Remote\nContent completeness: PARTIAL",
            "title": "Java Backend Engineer",
            "company": "ACME",
            "location": "Remote",
            "url": "https://www.linkedin.com/jobs/view/321/",
            "content_completeness": "PARTIAL",
            "snippet": None,
            "vacancy_json": '{"title":"Java Backend Engineer","analysis_text":"Title: Java Backend Engineer"}',
        }
    )
    analyzer = _FakeAnalyzer()
    service = PreparationService(
        analyzer=analyzer,
        llm_client=_FakeLLM(language="en", text="I have around seven years of Java backend experience."),
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=cache,
        allow_imap_fallback=False,
    )
    prepared = service.prepare("linkedin-email", "321")
    assert analyzer.calls == 1
    assert prepared.timing_breakdown["analysis_cached"] is False
    assert prepared.timing_breakdown["imap_fallback"] is False
    assert prepared.timing_breakdown["phases_ms"]["imap_fetch"] == 0
    assert cache.saved is not None
    assert cache.saved["evaluation_json"]


def test_prepare_without_cache_raises_when_imap_fallback_disabled(tmp_path: Path, monkeypatch) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="profile",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=_FakeLLM(language="en", text="letter"),
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(None),
        allow_imap_fallback=False,
    )
    with pytest.raises(ApplicationPreparationError, match="Cached vacancy is missing"):
        service.prepare("linkedin-email", "999")


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


def test_prepare_appends_relocation_block_for_new_zealand(tmp_path: Path, monkeypatch, caplog) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )
    evaluation = _evaluation()
    base_letter = "I have around seven years of Java backend experience."
    cache = _FakePrepareCache(
        {
            "evaluation_json": evaluation.model_dump_json(),
            "analysis_text": "Title: Java Backend Engineer\nLocation: Auckland, New Zealand",
            "title": "Java Backend Engineer",
            "company": "ACME",
            "location": "Auckland, New Zealand",
            "url": "https://www.linkedin.com/jobs/view/nz1/",
            "content_completeness": "PARTIAL",
            "snippet": "Snippet",
            "vacancy_json": '{"title":"Java Backend Engineer"}',
        }
    )
    llm = _FakeLLM(language="en", text=base_letter)
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=cache,
    )
    with caplog.at_level("INFO"):
        prepared = service.prepare("linkedin-email", "nz1")

    assert prepared.cover_letter.startswith(base_letter)
    assert "Although I currently live outside New Zealand" in prepared.cover_letter
    assert "I can travel to New Zealand for in-person interviews" in prepared.cover_letter
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Cover letter profile: relocation (New Zealand)" in log_text


def test_prepare_keeps_default_cover_letter_for_australia(tmp_path: Path, monkeypatch, caplog) -> None:
    from app.application import preparation_service as module

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )
    evaluation = _evaluation()
    base_letter = "I have around seven years of Java backend experience."
    cache = _FakePrepareCache(
        {
            "evaluation_json": evaluation.model_dump_json(),
            "analysis_text": "Title: Java Backend Engineer\nLocation: Sydney, Australia",
            "title": "Java Backend Engineer",
            "company": "ACME",
            "location": "Sydney, Australia",
            "url": "https://www.linkedin.com/jobs/view/au1/",
            "content_completeness": "PARTIAL",
            "snippet": "Snippet",
            "vacancy_json": '{"title":"Java Backend Engineer"}',
        }
    )
    llm = _FakeLLM(language="en", text=base_letter)
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=cache,
    )
    with caplog.at_level("INFO"):
        prepared = service.prepare("linkedin-email", "au1")

    assert prepared.cover_letter == base_letter
    assert "New Zealand" not in prepared.cover_letter
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Cover letter profile: default" in log_text
