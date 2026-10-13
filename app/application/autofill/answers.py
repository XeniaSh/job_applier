from __future__ import annotations

from typing import Protocol
import re

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import match_option, select_listed_options
from app.application.autofill.questions import (
    QuestionKind,
    is_llm_eligible_question,
    map_question,
)
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.cover_letter_profiles import resolve_cover_letter_profile
from app.llm_client import contains_unsupported_education_claim
from app.prompt_loader import PromptLoadError, load_application_answer_prompt

_MAX_SENTENCES = 3

# Bounded, question-scoped facts for a New Zealand vacancy's own free-text
# questions (e.g. "What draws you to working in New Zealand?"). Only used
# when the question itself names New Zealand -- never appended to unrelated
# questions just because the vacancy happens to be in New Zealand -- and
# never claims current New Zealand residence, work authorization, or a
# specific arrival date, none of which are supplied facts.
_NEW_ZEALAND_RELOCATION_FACTS = (
    "Does not currently live in New Zealand and has no current New Zealand work visa; "
    "would need an employer-supported work visa to take up a role there.",
    "Is planning a visit to New Zealand in 2026 (no confirmed arrival date yet) and is "
    "willing to meet in person during that visit.",
    "Is open to relocating to New Zealand for the right opportunity.",
)

# A true catch-all prompt ("Anything else you'd like to share/add/know/
# mention?"), not every `professional_free_text` textarea -- a question
# like "Briefly describe your technical background" must never match.
_CATCH_ALL_LABEL_RE = re.compile(
    r"anything else.{0,40}(share|add|know|mention|tell us)",
    re.IGNORECASE,
)

_VISA_SENTENCE = (
    "I don't currently live in New Zealand and would need an employer-supported work "
    "visa to take up a role there."
)
_RELOCATION_AND_VISIT_SENTENCE = (
    "I'm open to relocating to New Zealand for the right opportunity and am planning a "
    "visit in 2026 (no confirmed arrival date yet), when I'd be happy to meet in person."
)
_VISIT_ONLY_SENTENCE = (
    "I'm planning a visit to New Zealand in 2026 (no confirmed arrival date yet), when "
    "I'd be happy to meet in person."
)


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
    """Compact professional facts without email/phone.

    Deliberately excludes academic level: it is only used for the
    deterministic ATS dropdown mapping (see questions.py) and must never be
    volunteered into LLM prose, which fails closed on any education mention
    via `app.llm_client.contains_unsupported_education_claim`.
    """
    employment = profile.employment
    lines = [
        f"Name: {profile.identity.first_name} {profile.identity.last_name}",
        f"Current title: {employment.current_title or ''}".rstrip(),
        _years_line(profile),
        f"Location: {profile.identity.current_location or ''}".rstrip(),
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
        sibling_kinds: frozenset[QuestionKind] = frozenset(),
        sibling_has_dedicated_new_zealand_question: bool = False,
    ) -> str | None:
        mapped = map_question(field, profile)
        if not is_llm_eligible_question(field, mapped):
            return None
        catchall_answer = _new_zealand_catchall_answer(
            field,
            profile,
            vacancy,
            kind=mapped.kind,
            sibling_kinds=sibling_kinds,
            sibling_has_dedicated_new_zealand_question=sibling_has_dedicated_new_zealand_question,
        )
        if catchall_answer is not None:
            return catchall_answer
        try:
            prompt = load_application_answer_prompt()
        except PromptLoadError:
            return None
        candidate = professional_context_from_profile(profile)
        if markdown_profile and markdown_profile.strip():
            candidate = f"{candidate}\n\n{markdown_profile.strip()}"
        nz_facts = _new_zealand_facts_for_question(field, vacancy)
        if nz_facts:
            candidate = f"{candidate}\n\n{nz_facts}"
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
        if contains_unsupported_education_claim(answer):
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


def _new_zealand_facts_for_question(field: DiscoveredField, vacancy: ResolvedVacancy | None) -> str | None:
    """Bounded New Zealand relocation facts, only for a question that itself
    names New Zealand (e.g. "What draws you to working in New Zealand?").

    Scoped to the field's own label so these facts are never repeated into
    unrelated free-text answers just because a neighboring question in the
    same group (`field.context`) or the vacancy happens to mention New
    Zealand.
    """
    if vacancy is None:
        return None
    if "new zealand" not in field.label.lower():
        return None
    location = vacancy.vacancy.location if vacancy.vacancy is not None else None
    letter_profile = resolve_cover_letter_profile(location=location, vacancy_text=vacancy_context(vacancy))
    if letter_profile.name != "relocation" or "New Zealand" not in letter_profile.label:
        return None
    return "\n".join(_NEW_ZEALAND_RELOCATION_FACTS)


_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_ONSITE_OR_HYBRID_RE = re.compile(r"\b(hybrid|on-?site|in[- ]office)\b", re.IGNORECASE)


def _is_remote_only_vacancy(vacancy: ResolvedVacancy) -> bool:
    """True when the vacancy's own location/employment fields say "remote"
    with no hybrid/on-site marker -- e.g. "Remote - New Zealand" -- so a
    user whose relocation scope is on-site/hybrid NZ is not assumed to be
    relocating or visiting in person for a role that never requires it.
    """
    normalized = vacancy.vacancy
    if normalized is None:
        return False
    haystack = " ".join(filter(None, [normalized.location, normalized.employment]))
    return bool(_REMOTE_RE.search(haystack)) and not _ONSITE_OR_HYBRID_RE.search(haystack)


def _is_nz_relocation_vacancy(vacancy: ResolvedVacancy | None) -> bool:
    if vacancy is None:
        return False
    if _is_remote_only_vacancy(vacancy):
        return False
    location = vacancy.vacancy.location if vacancy.vacancy is not None else None
    letter_profile = resolve_cover_letter_profile(location=location, vacancy_text=vacancy_context(vacancy))
    return letter_profile.name == "relocation" and "New Zealand" in letter_profile.label


def _experience_sentence(profile: CandidateProfile) -> str | None:
    """A first-person sentence stating explicit profile facts only -- never
    an LLM guess -- so it is safe to use verbatim as applicant form prose.
    """
    years = profile.relevant_experience_years()
    stack = list(profile.employment.professional_tech_stack)
    if years is None or not stack:
        return None
    if abs(years - 7) < 0.51:
        years_text = "roughly seven years"
    else:
        rendered = str(int(years)) if years == int(years) else str(years)
        years_text = f"{rendered} years"
    tech_text = stack[0] if len(stack) == 1 else ", ".join(stack[:-1]) + f" and {stack[-1]}"
    return f"I bring {years_text} of backend engineering experience; my professional stack includes {tech_text}."


def _new_zealand_catchall_answer(
    field: DiscoveredField,
    profile: CandidateProfile,
    vacancy: ResolvedVacancy | None,
    *,
    kind: QuestionKind,
    sibling_kinds: frozenset[QuestionKind],
    sibling_has_dedicated_new_zealand_question: bool,
) -> str | None:
    """A deterministic, first-person answer for a true catch-all free-text
    question (e.g. "Anything else you'd like to share?") on a New Zealand
    vacancy, used only when no other question on the form already covers
    the same ground.

    Built entirely from explicit profile facts and the fixed sentence
    constants below -- never from LLM output -- so it can never echo an
    unsafe claim (e.g. an LLM inventing "I already have a New Zealand work
    visa") about residence, visa, or relocation status.
    """
    if kind is not QuestionKind.PROFESSIONAL_FREE_TEXT or vacancy is None:
        return None
    if "new zealand" in field.label.lower() or not _CATCH_ALL_LABEL_RE.search(field.label):
        return None
    if sibling_has_dedicated_new_zealand_question:
        return None
    if not _is_nz_relocation_vacancy(vacancy):
        return None
    sentences: list[str] = []
    experience = _experience_sentence(profile)
    if experience:
        sentences.append(experience)
    if QuestionKind.VISA_SPONSORSHIP not in sibling_kinds:
        sentences.append(_VISA_SENTENCE)
    if QuestionKind.RELOCATION in sibling_kinds:
        sentences.append(_VISIT_ONLY_SENTENCE)
    else:
        sentences.append(_RELOCATION_AND_VISIT_SENTENCE)
    return " ".join(sentences[:_MAX_SENTENCES])


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
