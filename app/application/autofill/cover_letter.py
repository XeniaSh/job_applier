from __future__ import annotations

import logging

from app.application.autofill.answers import professional_context_from_profile, vacancy_context
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.application.cover_letter_generation import generate_cover_letter_text
from app.llm_client import CoverLetterValidationError, LLMClient
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation
from app.profile_loader import ProfileLoadError, load_candidate_profile_context

logger = logging.getLogger(__name__)

_GENERATION_CONSTRAINT = (
    "Cover letter constraint: write a concise letter that mentions at most two "
    "highly relevant core technologies from the tech stack. Do not dump a list "
    "of languages, frameworks, or infrastructure tools. Every claim must be truthful."
)


class AutofillCoverLetterProvider:
    """Reuse the shared cover-letter generator for Greenhouse autofill."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client
        self.last_error: str | None = None

    def generate(self, vacancy: ResolvedVacancy, profile: CandidateProfile) -> str | None:
        self.last_error = None
        candidate = professional_context_from_profile(profile)
        candidate = f"{candidate}\n\n{_GENERATION_CONSTRAINT}"
        try:
            markdown = load_candidate_profile_context().text
        except ProfileLoadError:
            markdown = ""
        except OSError:
            markdown = ""
        if markdown.strip():
            candidate = f"{candidate}\n\n{markdown.strip()}"
        vacancy_text = vacancy_context(vacancy)
        analysis = VacancyEvaluation(
            decision=Decision.POTENTIAL_MATCH,
            summary=vacancy.title or "Application",
            recommended_resume=RecommendedResume.JAVA,
            recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        )
        location = vacancy.vacancy.location if vacancy.vacancy is not None else None
        try:
            result = generate_cover_letter_text(
                llm_client=self._llm_client,
                candidate_profile=candidate,
                vacancy_text=vacancy_text,
                analysis=analysis,
                recommended_resume=RecommendedResume.JAVA.value,
                location=location,
            )
        except CoverLetterValidationError as exc:
            self.last_error = f"validation rejected both attempts: {exc}"
            logger.warning("Cover letter generation failed validation: %s", exc)
            return None
        except Exception as exc:
            self.last_error = f"LLM generation failed: {type(exc).__name__}"
            logger.warning("Autofill cover letter generation failed", exc_info=True)
            return None
        text = (result.cover_letter or "").strip()
        if not text:
            self.last_error = "generation returned empty cover-letter text"
            logger.warning("Cover letter generation returned empty text")
            return None
        logger.info("Cover letter generation succeeded (%s characters)", len(text))
        return text
