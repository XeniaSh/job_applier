import json
import logging
import re
from json import JSONDecodeError
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from app.models import CoverLetterResult, VacancyEvaluation, VacancyExtraction


class LLMRequestError(Exception):
    """Raised when an LLM API request fails."""


class LLMResponseError(Exception):
    """Raised when an LLM response is malformed or cannot be validated."""


class CoverLetterValidationError(Exception):
    """Raised when cover letter text violates deterministic rules."""


logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout or httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=15.0,
            pool=10.0,
        )
        self._provided_client = http_client

    def extract_vacancy(
        self,
        prompt: str,
        vacancy: str,
    ) -> VacancyExtraction:
        last_error: Exception | None = None

        for attempt in range(1, 3):
            try:
                raw_content = self._request_content(
                    prompt=prompt,
                    vacancy=vacancy,
                )
                payload = json.loads(raw_content)
                return VacancyExtraction.model_validate(payload)

            except (
                JSONDecodeError,
                ValidationError,
                LLMResponseError,
            ) as exc:
                last_error = exc
                logger.warning(
                    "Invalid LLM response on attempt %d of 2: %s",
                    attempt,
                    type(exc).__name__,
                )

        raise LLMResponseError(
            "LLM returned an invalid response twice."
        ) from last_error

    def create_cover_letter(
        self,
        *,
        prompt: str,
        candidate_profile: str,
        vacancy_text: str,
        analysis: VacancyEvaluation,
        recommended_resume: str,
        preferred_language: str = "en",
        grammatical_gender: str = "neutral",
    ) -> CoverLetterResult:
        last_error: Exception | None = None
        user_payload = json.dumps(
            {
                "candidate_profile": candidate_profile,
                "vacancy_text": vacancy_text,
                "analysis": analysis.model_dump(),
                "recommended_resume": recommended_resume,
            },
            ensure_ascii=False,
        )

        for attempt in range(1, 3):
            try:
                raw_content = self._request_content(
                    prompt=prompt,
                    vacancy=user_payload,
                    temperature=0.2,
                    max_tokens=500,
                )
                payload = json.loads(raw_content)
                result = CoverLetterResult.model_validate(payload)
                result.cover_letter = " ".join(result.cover_letter.strip().split())
                result.cover_letter, cleaned_count = _apply_soft_cover_letter_cleanup(result.cover_letter)
                if cleaned_count > 0:
                    logger.info("Cleaned %d soft cover-letter phrases", cleaned_count)
                _validate_cover_letter(
                    result=result,
                    vacancy_text=vacancy_text,
                    candidate_profile=candidate_profile,
                    preferred_language=preferred_language,
                    grammatical_gender=grammatical_gender,
                )
                return result
            except (
                JSONDecodeError,
                ValidationError,
                LLMResponseError,
                CoverLetterValidationError,
            ) as exc:
                last_error = exc
                logger.warning(
                    "Invalid cover letter response on attempt %d of 2: %s",
                    attempt,
                    type(exc).__name__,
                )

        if isinstance(last_error, CoverLetterValidationError):
            raise CoverLetterValidationError(str(last_error)) from last_error
        raise LLMResponseError("LLM returned an invalid cover-letter response twice.") from last_error

    def _request_content(
        self,
        prompt: str,
        vacancy: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model,
            "response_format": {
                "type": "json_object",
            },
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": f"Vacancy text:\n{vacancy}\n",
                },
            ],
        }

        endpoint = f"{self._api_url}/chat/completions"
        request_started_at = perf_counter()

        try:
            response = self._send_request(
                endpoint=endpoint,
                headers=headers,
                payload=payload,
            )
            response.raise_for_status()

        except httpx.TimeoutException as exc:
            raise LLMRequestError(
                "LLM API request timed out."
            ) from exc

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise LLMRequestError(
                f"LLM API returned HTTP {status_code}."
            ) from exc

        except httpx.HTTPError as exc:
            raise LLMRequestError(
                "LLM API request failed."
            ) from exc

        finally:
            duration_seconds = perf_counter() - request_started_at
            logger.info(
                "LLM request took %.1fs",
                duration_seconds,
            )

        return self._extract_text_content(response)

    def _send_request(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        if self._provided_client is not None:
            return self._provided_client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )

        with httpx.Client() as client:
            return client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )

    @staticmethod
    def _extract_text_content(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMResponseError(
                "LLM API returned a non-JSON response."
            ) from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                "Unexpected LLM API response structure."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                "LLM response contained no textual content. "
                f"finish_reason={finish_reason!r}"
            )

        logger.info(
            "LLM response format: chat_completions; length=%d",
            len(content),
        )

        return content.strip()


_SOFT_STYLE_PHRASES = (
    "aligns with my skills",
    "fits your needs",
    "i am open to discussing",
    "i would like to contribute",
    "i believe",
    "i am excited",
    "my passion",
    "perfect fit",
    "dynamic team",
    "cutting-edge",
    "cutting edge",
    "leverage my skills",
    "utilize my experience",
)
_FORBIDDEN_OVERSTATEMENT = (
    "align well with the role",
    "strong alignment with your requirements",
    "excellent match",
)
_FORBIDDEN_PROMPT_LEAKS = (
    "how my experience is relevant to this role",
    "how my experience",
    "concise relevance statement",
    "final self-check",
    "return json only",
)
_FORBIDDEN_TEMPLATE_STARTERS = (
    "this opportunity aligns",
    "this opportunity",
    "this backend engineering opportunity",
)
_CANDIDATE_SENIORITY_CLAIM_RE = re.compile(
    r"\b(?:i am|i'm|as)\s+(?:a\s+)?(?:senior|lead|principal|staff|architect)\b",
    flags=re.IGNORECASE,
)
_FORBIDDEN_LEVEL_TERMS = (
    "lead developer",
    "backend lead",
    "tech lead",
    "principal",
    "staff engineer",
)
_FORBIDDEN_OWNERSHIP_TERMS = (
    "management experience",
    "team leadership",
    "led teams",
    "managed teams",
    "architecture ownership",
    "owned architecture",
)
_ALLOWED_RESUME_IDS = {
    "java-backend",
    "kotlin-backend",
    "fintech-backend",
    "ai-adjacent-backend",
}
_TECH_AREAS: dict[str, tuple[str, ...]] = {
    "jvm_language": ("java", "kotlin"),
    "spring_boot": ("spring boot",),
    "microservices": ("microservices",),
    "distributed_systems": ("distributed systems",),
    "event_driven": ("event-driven", "event driven"),
    "kafka": ("kafka",),
    "postgresql": ("postgresql",),
    "docker": ("docker",),
    "kubernetes": ("kubernetes",),
    "oracle": ("oracle",),
    "concurrency": ("concurrency", "multithreading", "concurrent programming"),
    "grpc": ("grpc",),
    "redis": ("redis",),
}


def _validate_cover_letter(
    *,
    result: CoverLetterResult,
    vacancy_text: str,
    candidate_profile: str,
    preferred_language: str,
    grammatical_gender: str,
) -> None:
    letter = " ".join(result.cover_letter.strip().split())
    lower = letter.lower()

    if "comfortable with concurrency" in lower and "comfortable with concurrency" not in candidate_profile.lower():
        raise CoverLetterValidationError("Phrase 'comfortable with concurrency' is not allowed by profile.")
    for phrase in _FORBIDDEN_OVERSTATEMENT:
        if phrase in lower:
            raise CoverLetterValidationError(f"Overstated fit phrase detected: {phrase}")
    for phrase in _FORBIDDEN_OWNERSHIP_TERMS:
        if phrase in lower:
            raise CoverLetterValidationError(f"Forbidden ownership phrase detected: {phrase}")
    for phrase in _FORBIDDEN_PROMPT_LEAKS:
        if phrase in lower:
            raise CoverLetterValidationError(f"Prompt/instruction fragment detected: {phrase}")
    for starter in _FORBIDDEN_TEMPLATE_STARTERS:
        if lower.startswith(starter) or f". {starter}" in lower:
            raise CoverLetterValidationError(f"Template starter detected: {starter}")
    if "backend backend" in lower:
        raise CoverLetterValidationError("Duplicated 'backend backend' wording is not allowed.")
    if "6 years" in lower or "six years" in lower:
        raise CoverLetterValidationError("Experience must be around seven years, not six.")
    if "redis" in lower:
        raise CoverLetterValidationError("Redis mention is not allowed without explicit confirmation.")
    if _CANDIDATE_SENIORITY_CLAIM_RE.search(letter):
        raise CoverLetterValidationError("Candidate must not claim Senior/Lead/Principal/Staff/Architect title.")
    for term in _FORBIDDEN_LEVEL_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lower):
            raise CoverLetterValidationError(f"Forbidden seniority/title term detected: {term}")

    # Around-seven-years wording must exist explicitly.
    if (
        "around seven years" not in lower
        and "approximately seven years" not in lower
        and "около семи лет" not in lower
    ):
        raise CoverLetterValidationError("Letter must use approximate seven-years wording.")

    language = result.language.lower().strip()
    expected_language = _select_cover_letter_language(
        vacancy_text=vacancy_text,
        preferred_language=preferred_language,
    )
    if language != expected_language:
        raise CoverLetterValidationError(
            f"Cover-letter language must be {expected_language}, got {language}."
        )
    word_count = len(re.findall(r"\b[\w'-]+\b", letter))
    if language == "en" and word_count > 80:
        raise CoverLetterValidationError("English cover letter exceeds 80 words.")
    if language == "ru" and word_count > 90:
        raise CoverLetterValidationError("Russian cover letter exceeds 90 words.")

    sentence_count = _count_complete_sentences(letter)
    if language == "en" and not (3 <= sentence_count <= 5):
        raise CoverLetterValidationError(
            f"English summary must contain 3-5 complete sentences, got {sentence_count}."
        )

    matched_areas = 0
    for patterns in _TECH_AREAS.values():
        if any(re.search(rf"\b{re.escape(term)}\b", lower) for term in patterns):
            matched_areas += 1
    if matched_areas > 4:
        raise CoverLetterValidationError("Cover letter mentions too many technologies.")

    # For incomplete LinkedIn cards enforce neutral language.
    if "content completeness: partial" in vacancy_text.lower() or "content completeness: minimal" in vacancy_text.lower():
        if any(
            marker in lower
            for marker in (
                "strong alignment",
                "align well",
                "excellent match",
                "perfect fit",
            )
        ):
            raise CoverLetterValidationError("Incomplete vacancy requires neutral wording.")
    _validate_lead_title_handling(vacancy_text=vacancy_text, letter=lower)

    # Ensure source of truth includes seven years in candidate profile.
    profile_lower = candidate_profile.lower()
    if (
        "7 years" not in profile_lower
        and "seven years" not in profile_lower
        and "семи лет" not in profile_lower
        and "семь лет" not in profile_lower
    ):
        raise CoverLetterValidationError("Candidate profile does not confirm around seven years.")

    _validate_ru_gender_forms(
        language=language,
        text=letter,
        grammatical_gender=grammatical_gender,
    )
    _validate_technology_origin(letter=lower, candidate_profile=candidate_profile.lower(), vacancy_text=vacancy_text.lower())
    if result.used_resume not in _ALLOWED_RESUME_IDS:
        raise CoverLetterValidationError("Unsupported resume identifier in cover letter.")


def _count_complete_sentences(text: str) -> int:
    parts = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    return len(parts)


def _select_cover_letter_language(*, vacancy_text: str, preferred_language: str) -> str:
    text = vacancy_text.strip()
    if _looks_like_russian(text):
        return "ru"
    if _looks_like_english(text):
        return "en"
    preferred = preferred_language.strip().lower()
    if preferred in {"ru", "en"}:
        return preferred
    return "en"


def _looks_like_russian(text: str) -> bool:
    return re.search(r"[а-яё]", text.lower()) is not None


def _looks_like_english(text: str) -> bool:
    if _looks_like_russian(text):
        return False
    letters = re.findall(r"[a-zA-Z]", text)
    return len(letters) >= 20


def _validate_ru_gender_forms(*, language: str, text: str, grammatical_gender: str) -> None:
    if language != "ru":
        return
    lower = text.lower()
    female_markers = (r"\bработала\b", r"\bзанималась\b", r"\bучаствовала\b", r"\bразрабатывала\b")
    male_markers = (r"\bработал\b", r"\bзанимался\b", r"\bучаствовал\b", r"\bразрабатывал\b")
    if grammatical_gender == "female":
        if any(re.search(pattern, lower) for pattern in male_markers):
            raise CoverLetterValidationError("Female profile must not use masculine verb forms.")
    elif grammatical_gender == "male":
        if any(re.search(pattern, lower) for pattern in female_markers):
            raise CoverLetterValidationError("Male profile must not use feminine verb forms.")
    else:  # neutral
        if any(re.search(pattern, lower) for pattern in (*female_markers, *male_markers)):
            raise CoverLetterValidationError("Neutral profile must avoid gendered past-tense verbs.")


def _validate_technology_origin(*, letter: str, candidate_profile: str, vacancy_text: str) -> None:
    # Enforce this check when we have a substantial profile payload.
    if len(candidate_profile.strip()) < 120:
        return
    for patterns in _TECH_AREAS.values():
        for term in patterns:
            if re.search(rf"\b{re.escape(term)}\b", letter):
                if term not in candidate_profile and term not in vacancy_text:
                    raise CoverLetterValidationError(
                        f"Technology '{term}' is not present in candidate profile or vacancy."
                    )


def _validate_lead_title_handling(*, vacancy_text: str, letter: str) -> None:
    vacancy_lower = vacancy_text.lower()
    lead_like = any(token in vacancy_lower for token in ("lead", "principal", "staff", "architect", "manager"))
    if not lead_like:
        return
    bad_phrases = (
        "lead position",
        "principal position",
        "staff position",
        "architect position",
        "manager position",
        "backend lead",
        "tech lead",
    )
    if any(phrase in letter for phrase in bad_phrases):
        raise CoverLetterValidationError("Lead-level vacancy title must not be used as candidate positioning.")


def _apply_soft_cover_letter_cleanup(text: str) -> tuple[str, int]:
    cleaned = text
    applied = 0

    # Drop whole awkward/template sentences first to avoid leaving fragments.
    sentence_drop_patterns: tuple[str, ...] = (
        r"[^.?!]*\bhow my experience is relevant to this role\b[^.?!]*[.?!]?",
        r"[^.?!]*\bhow my experience\b[^.?!]*[.?!]?",
        r"[^.?!]*\bthis opportunity aligns\b[^.?!]*[.?!]?",
        r"[^.?!]*\bthis opportunity\b[^.?!]*[.?!]?",
        r"[^.?!]*\bthis backend engineering opportunity\b[^.?!]*[.?!]?",
        r"[^.?!]*\baligns with my skills\b[^.?!]*[.?!]?",
        r"[^.?!]*\bfits your needs\b[^.?!]*[.?!]?",
        r"[^.?!]*\bi am open to discussing\b[^.?!]*[.?!]?",
        r"[^.?!]*\bi would like to contribute\b[^.?!]*[.?!]?",
        r"[^.?!]*\bi am excited to apply\b[^.?!]*[.?!]?",
        r"[^.?!]*\bi am excited\b[^.?!]*[.?!]?",
        r"[^.?!]*\bi believe\b[^.?!]*[.?!]?",
        r"[^.?!]*\bmy passion\b[^.?!]*[.?!]?",
    )
    for pattern in sentence_drop_patterns:
        updated, count = re.subn(pattern, " ", cleaned, flags=re.IGNORECASE)
        if count > 0:
            applied += count
            cleaned = updated

    replacements: tuple[tuple[str, str], ...] = (
        (r"\bperfect fit\b", "relevant fit"),
        (r"\bdynamic team\b", "engineering team"),
        (r"\bcutting-?edge\b", "production"),
        (r"\bleverage my skills\b", "apply my experience"),
        (r"\butilize my experience\b", "apply my experience"),
        (r"\bbackend\s+backend\b", "backend"),
    )
    for pattern, replacement in replacements:
        updated, count = re.subn(pattern, replacement, cleaned, flags=re.IGNORECASE)
        if count > 0:
            applied += count
            cleaned = updated
    for phrase in _SOFT_STYLE_PHRASES:
        updated, count = re.subn(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)
        if count > 0:
            applied += count
            cleaned = updated
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")
    return cleaned, applied