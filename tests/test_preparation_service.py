from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.preparation_service import (
    ApplicationPreparationError,
    PreparationService,
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
from app.resume_profiles import ResumeProfile, ResumeProfiles
from tests.pdf_fixtures import write_minimal_valid_pdf

_VARIANT_IDS = ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")


@pytest.fixture(autouse=True)
def _default_resume_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ResumeProfiles:
    """`PreparationService.prepare`, when constructed without an explicit
    `resume_profiles=` (most tests in this module), now loads the normal
    configured profiles via `app.resume_profiles.load_resume_profiles` at
    that same seam -- never the legacy `resumes_dir`/`{name}.pdf` lookup.
    Most tests here have no real `resume_profiles.yaml`/PDF set up, so this
    autouse fixture stands in at that seam with four synthetic, structurally
    valid PDFs. A test that wants a missing/invalid selected PDF on the
    no-injection path overrides this with its own
    `monkeypatch.setattr(module, "load_resume_profiles", ...)` afterward.
    """
    profiles = ResumeProfiles(
        tuple(
            ResumeProfile(
                variant_id,
                f"{variant_id} description.",
                write_minimal_valid_pdf(tmp_path / f"{variant_id}.pdf"),
            )
            for variant_id in _VARIANT_IDS
        )
    )
    monkeypatch.setattr("app.application.preparation_service.load_resume_profiles", lambda: profiles)
    return profiles


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
        recommended_resume=RecommendedResume.JAVA,
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
            used_resume=kwargs["recommended_resume"],
        )

    def select_resume_profile(self, *, prompt: str) -> str:
        _ = prompt
        self.last_timing_events.append(
            {
                "operation": "resume_selection",
                "model": self.model,
                "elapsed_ms": 4,
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
                "error": None,
            }
        )
        return '{"selected_profile":"java_ai","reason":"AI workflow automation is central to the role."}'


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


def test_missing_configured_resume_fails_closed_before_package(tmp_path: Path, monkeypatch) -> None:
    """The no-injection path must load the normal configured profiles and
    fail closed -- never report a PREPARED package with no resume attached,
    and never silently fall back to the legacy `resumes_dir`/`{name}.pdf`
    lookup. `resumes_dir` deliberately still has a valid, readable
    `java-backend.pdf` here so a generic setup that quietly kept using that
    legacy lookup could not make this test pass for the wrong reason.
    """
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
    resumes_dir = tmp_path / "resumes"
    write_minimal_valid_pdf(resumes_dir / "java-backend.pdf")
    broken_profiles = ResumeProfiles((ResumeProfile("java_backend", "desc", tmp_path / "missing.pdf"),))
    monkeypatch.setattr(module, "load_resume_profiles", lambda: broken_profiles)
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=_FakeLLM(language="ru", text="Короткий текст без Redis и Senior."),
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        resumes_dir=resumes_dir,
    )
    with pytest.raises(ApplicationPreparationError) as exc_info:
        service.prepare("linkedin-email", "123")
    assert "java_backend" in str(exc_info.value)
    assert exc_info.value.resume_variant == "java_backend"


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
    assert "I would be happy to meet in person during that visit if helpful" in prepared.cover_letter
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


def test_prepare_uses_configured_variant_pdf_for_package(tmp_path: Path, monkeypatch) -> None:
    """Resume selection now reads the canonical variant already decided by
    `VacancyAnalyzer` (shared `app.resume_policy` logic) off the cached
    evaluation, resolves it through `resume_profiles.yaml`, and makes no
    second LLM call -- there is no production `ResumeSelector` call left.
    """
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
    ai_resume = write_minimal_valid_pdf(tmp_path / "resumes" / "ai-backend.pdf")
    java_resume = write_minimal_valid_pdf(tmp_path / "resumes" / "java-backend.pdf")
    profiles = ResumeProfiles(
        (
            ResumeProfile("java_backend", "General Java backend.", java_resume),
            ResumeProfile("ai_backend", "Java backend plus AI automation.", ai_resume),
        )
    )
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    evaluation = VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="AI automation platform",
        matched_points=["java"],
        recommended_resume=RecommendedResume.JAVA_AI,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        resume_variant="ai_backend",
        resume_variant_reason="Hands-on AI/LLM evidence in role or responsibilities ('llm').",
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(
            {
                "evaluation_json": evaluation.model_dump_json(),
                "analysis_text": "AI workflow automation platform seeking a Java engineer",
                "title": "Java Engineer",
                "company": "ACME AI",
                "location": "Remote",
                "url": "https://example.com/job",
                "content_completeness": "FULL",
            }
        ),
        resume_profiles=profiles,
    )

    prepared = service.prepare("linkedin-email", "ai-1")

    assert prepared.recommended_resume == "ai_backend"
    assert prepared.resume_path == str(ai_resume.resolve())
    assert prepared.resume_variant_reason == evaluation.resume_variant_reason
    assert prepared.timing_breakdown["llm_calls"] == 1


def test_prepare_rejects_unrecognized_cached_resume_variant(tmp_path: Path, monkeypatch) -> None:
    """An existing cached `resume_variant` is validated against the four
    canonical ids (honoring legacy aliases) -- never trusted as an arbitrary
    string and never silently substituted with a default variant.
    """
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
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    legacy_evaluation_json = json.dumps(
        {
            "decision": "POTENTIAL_MATCH",
            "summary": "role summary",
            "matched_points": ["java"],
            "recommended_resume": "java",
            "recommended_cover_template": "generic",
            "resume_variant": "not_a_real_variant",
        }
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(
            {
                "evaluation_json": legacy_evaluation_json,
                "analysis_text": "Java backend role",
                "title": "Java Engineer",
                "company": "ACME",
                "location": "Remote",
                "url": "https://example.com/job",
                "content_completeness": "FULL",
            }
        ),
    )

    with pytest.raises(ApplicationPreparationError, match="not_a_real_variant"):
        service.prepare("linkedin-email", "invalid-variant-1")


def _legacy_profiles(tmp_path: Path) -> ResumeProfiles:
    return ResumeProfiles(
        tuple(
            ResumeProfile(
                variant_id,
                f"{variant_id} description.",
                write_minimal_valid_pdf(tmp_path / "resumes" / f"{variant_id}.pdf"),
            )
            for variant_id in _VARIANT_IDS
        )
    )


def _legacy_cached_evaluation(
    *, recommended_resume: str, analysis_text: str, title: str, location: str
) -> tuple[dict, str]:
    legacy_evaluation_json = json.dumps(
        {
            "decision": "POTENTIAL_MATCH",
            "summary": "role summary",
            "matched_points": ["java"],
            "recommended_resume": recommended_resume,
            "recommended_cover_template": "generic",
            # No `resume_variant` key at all -- the pre-migration shape.
        }
    )
    cache_payload = {
        "evaluation_json": legacy_evaluation_json,
        "analysis_text": analysis_text,
        "title": title,
        "company": "ACME",
        "location": location,
        "url": "https://example.com/job",
        "content_completeness": "FULL",
    }
    return cache_payload, legacy_evaluation_json


def test_prepare_derives_legacy_variant_from_full_decision_not_recommended_resume(
    tmp_path: Path, monkeypatch
) -> None:
    """A pre-migration cached evaluation has no `resume_variant` at all --
    it must never silently default to the Java variant, and it must never
    trust the legacy `recommended_resume` enum (which only matched a bare
    "ai" substring and never reliably analyzed responsibilities). It must
    instead recompute the full AI x New Zealand decision from the cached
    title/description/location. Here `recommended_resume` is explicitly the
    non-AI legacy value, yet genuine hands-on AI evidence in the cached
    description must still select the AI variant.
    """
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
    profiles = _legacy_profiles(tmp_path)
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    cache_payload, _ = _legacy_cached_evaluation(
        recommended_resume="java",
        analysis_text="Build LLM integrations and automation workflows for our internal tooling platform.",
        title="Java Engineer",
        location="Remote",
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(cache_payload),
        resume_profiles=profiles,
    )

    prepared = service.prepare("linkedin-email", "ai-legacy-1")

    assert prepared.recommended_resume == "ai_backend"


def test_prepare_ignores_incidental_legacy_ai_recommendation_without_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """The old `recommended_resume` enum matched a bare "ai" substring in
    `role_type` (e.g. "Retail" contains "ai"), so a persisted `java_ai` value
    is not reliable evidence on its own. With no genuine hands-on AI/LLM
    evidence in the cached title/description, the recomputed decision must
    select the plain Java variant regardless of that legacy value.
    """
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
    profiles = _legacy_profiles(tmp_path)
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    cache_payload, _ = _legacy_cached_evaluation(
        recommended_resume="java_ai",
        analysis_text="Retail Java backend engineer maintaining checkout services.",
        title="Retail Backend Engineer",
        location="Remote",
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(cache_payload),
        resume_profiles=profiles,
    )

    prepared = service.prepare("linkedin-email", "retail-legacy-1")

    assert prepared.recommended_resume == "java_backend"


def test_prepare_derives_legacy_variant_ai_in_new_zealand(tmp_path: Path, monkeypatch) -> None:
    """Both dimensions are recomputed together: genuine hands-on AI evidence
    plus a New Zealand location must select the combined `ai_backend_nz`
    variant from a legacy cached evaluation with no `resume_variant`.
    """
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
    profiles = _legacy_profiles(tmp_path)
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    cache_payload, _ = _legacy_cached_evaluation(
        recommended_resume="java",
        analysis_text="Build LLM integrations and automation workflows for our internal tooling platform.",
        title="Java Engineer",
        location="Auckland, New Zealand",
    )
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(cache_payload),
        resume_profiles=profiles,
    )

    prepared = service.prepare("linkedin-email", "ai-nz-legacy-1")

    assert prepared.recommended_resume == "ai_backend_nz"


def test_prepare_recomputes_and_persists_legacy_nz_variant(tmp_path: Path, monkeypatch) -> None:
    """A legacy cached evaluation with no canonical `resume_variant` at all
    must have both its AI and New Zealand dimensions recomputed through the
    shared, deterministic `app.resume_policy` (never the LLM, and never the
    legacy `recommended_resume` enum) from the cached title/description and
    STRUCTURED location -- and that recomputed canonical choice must be
    persisted back onto the existing prepare-cache JSON, even though a cache
    hit with an already-parsed evaluation would otherwise never be
    re-persisted.
    """
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
    evaluation = _evaluation()  # recommended_resume=JAVA, no resume_variant at all
    cache = _FakePrepareCache(
        {
            "evaluation_json": evaluation.model_dump_json(),
            "analysis_text": "Title: Java Backend Engineer\nLocation: Auckland, New Zealand",
            "title": "Java Backend Engineer",
            "company": "ACME",
            "location": "Auckland, New Zealand",
            "url": "https://www.linkedin.com/jobs/view/nz-legacy/",
            "content_completeness": "PARTIAL",
            "snippet": "Snippet",
            "vacancy_json": '{"title":"Java Backend Engineer"}',
        }
    )
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=cache,
    )

    prepared = service.prepare("linkedin-email", "nz-legacy")

    assert prepared.recommended_resume == "java_backend_nz"
    assert cache.saved is not None
    persisted = json.loads(cache.saved["evaluation_json"])
    assert persisted["resume_variant"] == "java_backend_nz"
    assert persisted["resume_variant_reason"]


def test_prepare_fails_visible_warning_when_configured_variant_pdf_is_invalid(
    tmp_path: Path, monkeypatch
) -> None:
    """An invalid configured PDF for the selected variant must stop this
    Prepare outright with a clear, variant-naming error -- never report a
    PREPARED package with no resume attached, and never a silent
    substitution to a different profile's file.
    """
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
    truncated_resume = tmp_path / "resumes" / "java-backend.pdf"
    truncated_resume.parent.mkdir(parents=True)
    truncated_resume.write_bytes(b"%PDF-1.4\nnot actually a pdf body")
    profiles = ResumeProfiles((ResumeProfile("java_backend", "General Java backend.", truncated_resume),))
    llm = _FakeLLM(language="en", text="I have around seven years of Java backend experience.")
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        prepare_cache=_FakePrepareCache(
            {
                "evaluation_json": _evaluation().model_dump_json(),
                "analysis_text": "Java backend role",
                "title": "Java Engineer",
                "company": "ACME",
                "location": "Remote",
                "url": "https://example.com/job",
                "content_completeness": "FULL",
            }
        ),
        resume_profiles=profiles,
    )

    with pytest.raises(ApplicationPreparationError, match="java_backend"):
        service.prepare("linkedin-email", "invalid-pdf-1")


def test_prepare_writes_cover_letter_artifacts_once(tmp_path: Path, monkeypatch) -> None:
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
    llm = _FakeLLM(language="en", text="I can help with Java backend delivery.")
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        artifacts_dir=tmp_path / "prepared",
        candidate_name="Jane Doe",
        prepare_cache=_FakePrepareCache(
            {
                "evaluation_json": _evaluation().model_dump_json(),
                "analysis_text": "Java backend role",
                "title": "Java Engineer",
                "company": "ACME",
                "location": "Remote",
                "url": "https://example.com/job",
                "content_completeness": "FULL",
            }
        ),
    )

    prepared = service.prepare("linkedin-email", "artifact-1")

    assert llm.cover_calls == 1
    assert prepared.cover_letter_txt_path is not None
    assert prepared.cover_letter_pdf_path is not None
    assert Path(prepared.cover_letter_txt_path).is_file()
    assert Path(prepared.cover_letter_pdf_path).is_file()


def test_prepare_pdf_failure_is_non_fatal(tmp_path: Path, monkeypatch) -> None:
    from app.application import preparation_service as module
    from app.cover_letter_documents import CoverLetterArtifacts

    monkeypatch.setattr(
        module,
        "load_candidate_profile_context",
        lambda preferred_language="en", grammatical_gender="neutral": SimpleNamespace(
            text="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        ),
    )

    txt_file = tmp_path / "prepared" / "linkedin-email" / "x" / "cover_letter.txt"
    txt_file.parent.mkdir(parents=True, exist_ok=True)
    txt_file.write_text("Letter", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "generate_cover_letter_artifacts",
        lambda **kwargs: CoverLetterArtifacts(
            txt_path=txt_file,
            pdf_path=None,
            pdf_error="PDF generation failed: boom",
        ),
    )

    llm = _FakeLLM(language="en", text="I can help with Java backend delivery.")
    service = PreparationService(
        analyzer=_FakeAnalyzer(),
        llm_client=llm,
        email_client=_FailingEmailClient(),
        resumes_dir=tmp_path / "resumes",
        artifacts_dir=tmp_path / "prepared",
        candidate_name="Jane Doe",
        prepare_cache=_FakePrepareCache(
            {
                "evaluation_json": _evaluation().model_dump_json(),
                "analysis_text": "Java backend role",
                "title": "Java Engineer",
                "company": "ACME",
                "location": "Remote",
                "url": "https://example.com/job",
                "content_completeness": "FULL",
            }
        ),
    )

    prepared = service.prepare("linkedin-email", "artifact-2")

    assert llm.cover_calls == 1
    assert prepared.cover_letter_txt_path == str(txt_file)
    assert prepared.cover_letter_pdf_path is None
