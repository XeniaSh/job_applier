from __future__ import annotations

import logging

from app.cover_letter_profiles import apply_cover_letter_profile, resolve_cover_letter_profile
from app.llm_client import LLMClient
from app.models import CoverLetterResult, VacancyEvaluation
from app.prompt_loader import load_cover_letter_prompt

logger = logging.getLogger(__name__)


def generate_cover_letter_text(
    *,
    llm_client: LLMClient,
    candidate_profile: str,
    vacancy_text: str,
    analysis: VacancyEvaluation,
    recommended_resume: str,
    preferred_language: str = "en",
    grammatical_gender: str = "neutral",
    location: str | None = None,
) -> CoverLetterResult:
    """Generate cover-letter text using the shared prompt and LLM client.

    LinkedIn preparation and Greenhouse autofill both use this helper so the
    prompt, validation, and relocation profile stay in one place.
    """
    letter_profile = resolve_cover_letter_profile(
        location=location,
        vacancy_text=vacancy_text,
    )
    logger.info("Cover letter profile: %s", letter_profile.label)
    prompt = load_cover_letter_prompt()
    result = llm_client.create_cover_letter(
        prompt=prompt,
        candidate_profile=candidate_profile,
        vacancy_text=vacancy_text,
        analysis=analysis,
        recommended_resume=recommended_resume,
        preferred_language=preferred_language,
        grammatical_gender=grammatical_gender,
        operation="cover_letter",
    )
    result.cover_letter = apply_cover_letter_profile(result.cover_letter, letter_profile)
    return result
