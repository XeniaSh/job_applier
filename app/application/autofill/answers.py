from __future__ import annotations

from typing import Protocol

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import match_option, select_listed_options
from app.application.autofill.questions import (
    QuestionKind,
    is_llm_eligible_question,
    map_question,
)
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.prompt_loader import PromptLoadError, load_application_answer_prompt

_MAX_SENTENCES = 3


class ApplicationAnswerLLM(Protocol):
    def create_short_application_answer(
        self,
        *,
        prompt: str,
        vacancy_text: str,
        candidate_profile: str,
        question: str,
        options: list[str],
    ) -> tuple[str, bool]: ...


def professional_context_from_profile(profile: CandidateProfile) -> str:
    """Compact professional facts without email/phone."""
    employment = profile.employment
    lines = [
        f"Name: {profile.identity.first_name} {profile.identity.last_name}",
        f"Current title: {employment.current_title or ''}".rstrip(),
        _years_line(profile),
        f"Location: {profile.identity.current_location or ''}".rstrip(),
        f"Highest awarded academic level: {profile.awarded_academic_level() or ''}".rstrip(),
        f"Tech stack: {', '.join(employment.professional_tech_stack)}".rstrip(),
        f"Preferred fields of interest: {', '.join(employment.preferred_fields_of_interest)}".rstrip(),
    ]
    return "\n".join(line for line in lines if line and not line.endswith(":"))


def _years_line(profile: CandidateProfile) -> str:
    years = profile.relevant_experience_years()
    if years is None:
        return ""
    if abs(years - 7) < 0.51:
        return "Years of relevant experience: around seven years (7 years)"
    rendered = str(int(years)) if years == int(years) else str(years)
    return f"Years of relevant experience: {rendered} years"


def vacancy_context(vacancy: ResolvedVacancy | None) -> str:
    if vacancy is None:
        return ""
    if vacancy.vacancy is not None:
        return vacancy.vacancy.to_analysis_text()
    lines = [f"Title: {vacancy.title}"]
    if vacancy.company:
        lines.append(f"Company: {vacancy.company}")
    if vacancy.url:
        lines.append(f"Source URL: {vacancy.url}")
    return "\n".join(lines)


class ApplicationAnswerGenerator:
    def __init__(self, llm_client: ApplicationAnswerLLM) -> None:
        self._llm_client = llm_client

    def generate(
        self,
        field: DiscoveredField,
        profile: CandidateProfile,
        vacancy: ResolvedVacancy | None = None,
        *,
        markdown_profile: str | None = None,
    ) -> str | None:
        mapped = map_question(field, profile)
        if not is_llm_eligible_question(field, mapped):
            return None
        try:
            prompt = load_application_answer_prompt()
        except PromptLoadError:
            return None
        candidate = professional_context_from_profile(profile)
        if markdown_profile and markdown_profile.strip():
            candidate = f"{candidate}\n\n{markdown_profile.strip()}"
        try:
            raw_answer, confident = self._llm_client.create_short_application_answer(
                prompt=prompt,
                vacancy_text=vacancy_context(vacancy),
                candidate_profile=candidate,
                question=field.label,
                options=list(field.options),
            )
        except Exception:
            return None
        if not confident:
            return None
        answer = _limit_sentences(raw_answer)
        if not answer:
            return None
        if field.field_type in {"select", "radio", "combobox", "multiselect"} and field.options:
            if mapped.kind is QuestionKind.TECH_STACK or field.field_type == "multiselect":
                selected = select_listed_options(
                    [item.strip() for item in answer.replace(";", ",").split(",") if item.strip()],
                    field.options,
                    max_choices=mapped.max_choices,
                )
                return ", ".join(selected) if selected else None
            return match_option(answer, field.options)
        return answer


def _limit_sentences(text: str, max_sentences: int = _MAX_SENTENCES) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return ""
    sentences: list[str] = []
    current: list[str] = []
    for char in cleaned:
        current.append(char)
        if char in ".!?" and (not current or len(current) > 1):
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
            if len(sentences) >= max_sentences:
                break
    if current and len(sentences) < max_sentences:
        trailing = "".join(current).strip()
        if trailing:
            sentences.append(trailing)
    return " ".join(sentences)
