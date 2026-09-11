from __future__ import annotations

from app.application.autofill.answers import professional_context_from_profile
from app.application.autofill.cover_letter import AutofillCoverLetterProvider
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.llm_client import CoverLetterValidationError
from app.models import CoverLetterResult


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "employment": {
                "years_of_experience": 7,
                "professional_tech_stack": ["Java", "Kotlin", "Spring Boot"],
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def _vacancy() -> ResolvedVacancy:
    return ResolvedVacancy(
        source="target_company:greenhouse:agoda",
        external_id="7044713",
        title="Senior Backend Engineer",
        company="Agoda",
        url="https://example.test/jobs/1",
        application_url="https://example.test/jobs/1",
    )


class _FakeLLM:
    def create_cover_letter(self, **kwargs: object) -> CoverLetterResult:
        raise AssertionError("LLM should be invoked through generate_cover_letter_text")


def test_professional_context_includes_seven_years_wording() -> None:
    text = professional_context_from_profile(_profile()).lower()
    assert "7 years" in text
    assert "around seven years" in text


def test_cover_letter_provider_returns_generated_text(monkeypatch) -> None:
    def fake_generate(**kwargs: object) -> CoverLetterResult:
        _ = kwargs
        return CoverLetterResult(
            language="en",
            cover_letter="I am a Java Backend Engineer with around seven years of experience.",
            used_resume="java",
        )

    monkeypatch.setattr(
        "app.application.autofill.cover_letter.generate_cover_letter_text",
        fake_generate,
    )
    monkeypatch.setattr(
        "app.application.autofill.cover_letter.load_candidate_profile_context",
        lambda: type("Ctx", (), {"text": ""})(),
    )
    provider = AutofillCoverLetterProvider(_FakeLLM())  # type: ignore[arg-type]
    text = provider.generate(_vacancy(), _profile())
    assert text is not None
    assert "around seven years" in text
    assert provider.last_error is None


def test_cover_letter_provider_reports_validation_failure(monkeypatch) -> None:
    def fake_generate(**kwargs: object) -> CoverLetterResult:
        _ = kwargs
        raise CoverLetterValidationError("Cover letter mentions too many technologies.")

    monkeypatch.setattr(
        "app.application.autofill.cover_letter.generate_cover_letter_text",
        fake_generate,
    )
    monkeypatch.setattr(
        "app.application.autofill.cover_letter.load_candidate_profile_context",
        lambda: type("Ctx", (), {"text": ""})(),
    )
    provider = AutofillCoverLetterProvider(_FakeLLM())  # type: ignore[arg-type]
    assert provider.generate(_vacancy(), _profile()) is None
    assert provider.last_error is not None
    assert "too many technologies" in provider.last_error
    assert "validation rejected both attempts" in provider.last_error
