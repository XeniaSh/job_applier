from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import logging
import re
from time import perf_counter
from typing import Any, Protocol

from app.collectors.email_imap_client import EmailIMAPClient
from app.collectors.linkedin_email_parser import parse_linkedin_email
from app.collectors.linkedin_models import LinkedInEmailVacancy
from app.llm_client import LLMClient
from app.models import CoverLetterResult, VacancyEvaluation
from app.profile_loader import CandidateProfileContext, load_candidate_profile_context
from app.prompt_loader import load_cover_letter_prompt
from app.vacancy_analyzer import VacancyAnalyzer

logger = logging.getLogger(__name__)

PREPARE_PHASE_ORDER = (
    "resume_generation",
    "application_answers",
    "imap_fetch",
    "analysis",
    "cover_letter",
    "validation",
    "serialization",
)


@dataclass(frozen=True)
class PreparedApplication:
    source: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    decision: str
    match_percentage: float | None
    recommended_resume: str
    resume_path: str | None
    cover_letter: str
    language: str
    warnings: list[str] = field(default_factory=list)
    timing_breakdown: dict[str, Any] = field(default_factory=dict)


class ApplicationPreparationError(Exception):
    """Raised when preparation cannot continue."""


class PrepareCacheStore(Protocol):
    def get_prepare_cache(self, source: str, external_id: str) -> dict | None: ...

    def save_prepare_cache(
        self,
        *,
        source: str,
        external_id: str,
        evaluation_json: str,
        analysis_text: str,
        title: str | None,
        company: str | None,
        location: str | None,
        url: str | None,
        content_completeness: str | None,
        snippet: str | None = None,
    ) -> None: ...


class PreparationService:
    def __init__(
        self,
        *,
        analyzer: VacancyAnalyzer,
        llm_client: LLMClient,
        email_client: EmailIMAPClient,
        resumes_dir: Path,
        preferred_language: str = "en",
        grammatical_gender: str = "neutral",
        prepare_cache: PrepareCacheStore | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._llm_client = llm_client
        self._email_client = email_client
        self._resumes_dir = resumes_dir
        self._preferred_language = preferred_language
        self._grammatical_gender = grammatical_gender
        self._prepare_cache = prepare_cache
        self._profile_context: CandidateProfileContext | None = None

    def prepare(self, source: str, external_id: str) -> PreparedApplication:
        if source != "linkedin-email":
            raise ApplicationPreparationError(f"Unsupported source for preparation: {source}")

        prepare_started = perf_counter()
        phase_ms: dict[str, int] = {name: 0 for name in PREPARE_PHASE_ORDER}
        llm_events_before = len(getattr(self._llm_client, "last_timing_events", []))

        logger.info("START resume_generation")
        profile_started = perf_counter()
        profile = self._get_profile_context()
        phase_ms["resume_generation"] = int((perf_counter() - profile_started) * 1000)
        logger.info("END resume_generation +%dms", phase_ms["resume_generation"])

        logger.info("START application_answers")
        phase_ms["application_answers"] = 0
        logger.info("END application_answers +0ms (skipped)")

        cached = self._load_prepare_cache(source, external_id)
        analysis: VacancyEvaluation
        analysis_text: str
        vacancy_meta: _VacancyMeta
        analysis_cached = False

        if cached is not None:
            logger.info("START imap_fetch")
            phase_ms["imap_fetch"] = 0
            logger.info("END imap_fetch +0ms (skipped, cache hit)")

            logger.info("START analysis")
            analysis_started = perf_counter()
            analysis = VacancyEvaluation.model_validate_json(cached["evaluation_json"])
            analysis_text = str(cached["analysis_text"])
            vacancy_meta = _VacancyMeta(
                title=str(cached.get("title") or "Untitled"),
                company=cached.get("company"),
                location=cached.get("location"),
                url=str(cached.get("url") or ""),
                content_completeness=str(cached.get("content_completeness") or "PARTIAL"),
                snippet=cached.get("snippet"),
            )
            analysis_cached = True
            phase_ms["analysis"] = int((perf_counter() - analysis_started) * 1000)
            logger.info("END analysis +%dms (cached)", phase_ms["analysis"])
        else:
            logger.info("START imap_fetch")
            imap_started = perf_counter()
            vacancy = self._find_linkedin_vacancy(external_id)
            phase_ms["imap_fetch"] = int((perf_counter() - imap_started) * 1000)
            logger.info("END imap_fetch +%dms", phase_ms["imap_fetch"])
            if vacancy is None:
                raise ApplicationPreparationError(
                    "Не удалось найти данные вакансии в последних LinkedIn-письмах."
                )

            logger.info("START analysis")
            analysis_started = perf_counter()
            analysis_text = vacancy.to_analysis_text()
            analysis = self._analyzer.analyze(
                analysis_text,
                content_completeness=vacancy.content_completeness.value,
            )
            vacancy_meta = _VacancyMeta(
                title=vacancy.title,
                company=vacancy.company,
                location=vacancy.location,
                url=vacancy.url,
                content_completeness=vacancy.content_completeness.value,
                snippet=vacancy.snippet,
            )
            phase_ms["analysis"] = int((perf_counter() - analysis_started) * 1000)
            logger.info("END analysis +%dms", phase_ms["analysis"])

        recommended_resume = analysis.recommended_resume.value
        resume_path, resume_warning = resolve_resume_path(self._resumes_dir, recommended_resume)

        logger.info("START cover_letter")
        cover_started = perf_counter()
        cover_result = self._build_cover_letter(
            analysis_text=analysis_text,
            analysis=analysis,
            recommended_resume=recommended_resume,
            profile=profile,
        )
        phase_ms["cover_letter"] = int((perf_counter() - cover_started) * 1000)
        logger.info("END cover_letter +%dms", phase_ms["cover_letter"])

        logger.info("START validation")
        validation_started = perf_counter()
        _validate_prepared_cover_letter(cover_result)
        phase_ms["validation"] = int((perf_counter() - validation_started) * 1000)
        logger.info("END validation +%dms", phase_ms["validation"])

        logger.info("START serialization")
        serialization_started = perf_counter()
        if not analysis_cached:
            self._store_prepare_cache(
                source=source,
                external_id=external_id,
                analysis=analysis,
                analysis_text=analysis_text,
                meta=vacancy_meta,
            )
        phase_ms["serialization"] = int((perf_counter() - serialization_started) * 1000)
        logger.info("END serialization +%dms", phase_ms["serialization"])

        warnings: list[str] = []
        if vacancy_meta.content_completeness in {"PARTIAL", "MINIMAL"}:
            warnings.append("Описание вакансии неполное — требуется открыть LinkedIn")
        if resume_warning:
            warnings.append(resume_warning)
        warnings.extend(_collect_warning_nuances(analysis.nuances))

        llm_events = list(getattr(self._llm_client, "last_timing_events", []))[llm_events_before:]
        total_ms = int((perf_counter() - prepare_started) * 1000)
        model = getattr(self._llm_client, "model", "unknown")
        timing_breakdown = {
            "llm_calls": len(llm_events),
            "model": model,
            "analysis_cached": analysis_cached,
            "phases_ms": phase_ms,
            "llm_events": llm_events,
            "total_ms": total_ms,
        }
        logger.info(
            "Prepare timing breakdown llm_calls=%d model=%s analysis_cached=%s "
            "resume_generation=%dms application_answers=%dms imap_fetch=%dms analysis=%dms "
            "cover_letter=%dms validation=%dms serialization=%dms total=%dms",
            timing_breakdown["llm_calls"],
            model,
            analysis_cached,
            phase_ms.get("resume_generation", 0),
            phase_ms.get("application_answers", 0),
            phase_ms.get("imap_fetch", 0),
            phase_ms.get("analysis", 0),
            phase_ms.get("cover_letter", 0),
            phase_ms.get("validation", 0),
            phase_ms.get("serialization", 0),
            total_ms,
        )
        for event in llm_events:
            logger.info(
                "Prepare LLM call operation=%s latency_ms=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                event.get("operation"),
                event.get("elapsed_ms"),
                event.get("prompt_tokens", "n/a"),
                event.get("completion_tokens", "n/a"),
                event.get("total_tokens", "n/a"),
            )

        return PreparedApplication(
            source=source,
            external_id=external_id,
            title=vacancy_meta.title,
            company=vacancy_meta.company,
            location=vacancy_meta.location,
            url=vacancy_meta.url,
            decision=analysis.decision.value,
            match_percentage=analysis.match_percentage,
            recommended_resume=recommended_resume,
            resume_path=str(resume_path) if resume_path is not None else None,
            cover_letter=cover_result.cover_letter,
            language=cover_result.language,
            warnings=_dedupe_preserve(warnings),
            timing_breakdown=timing_breakdown,
        )

    def _get_profile_context(self) -> CandidateProfileContext:
        if self._profile_context is None:
            self._profile_context = load_candidate_profile_context(
                preferred_language=self._preferred_language,
                grammatical_gender=self._grammatical_gender,
            )
        return self._profile_context

    def _load_prepare_cache(self, source: str, external_id: str) -> dict | None:
        if self._prepare_cache is None:
            return None
        try:
            return self._prepare_cache.get_prepare_cache(source, external_id)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read prepare cache for %s:%s", source, external_id)
            return None

    def _store_prepare_cache(
        self,
        *,
        source: str,
        external_id: str,
        analysis: VacancyEvaluation,
        analysis_text: str,
        meta: _VacancyMeta,
    ) -> None:
        if self._prepare_cache is None:
            return
        try:
            self._prepare_cache.save_prepare_cache(
                source=source,
                external_id=external_id,
                evaluation_json=analysis.model_dump_json(),
                analysis_text=analysis_text,
                title=meta.title,
                company=meta.company,
                location=meta.location,
                url=meta.url,
                content_completeness=meta.content_completeness,
                snippet=meta.snippet,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to write prepare cache for %s:%s", source, external_id)

    def _find_linkedin_vacancy(self, external_id: str) -> LinkedInEmailVacancy | None:
        messages = self._email_client.fetch_linkedin_messages()
        for message in messages:
            try:
                vacancies = parse_linkedin_email(message)
            except Exception:  # noqa: BLE001
                continue
            for vacancy in vacancies:
                if vacancy.external_id == external_id:
                    return vacancy
        return None

    def _build_cover_letter(
        self,
        *,
        analysis_text: str,
        analysis: VacancyEvaluation,
        recommended_resume: str,
        profile: CandidateProfileContext,
    ) -> CoverLetterResult:
        prompt = load_cover_letter_prompt()
        return self._llm_client.create_cover_letter(
            prompt=prompt,
            candidate_profile=profile.text,
            vacancy_text=analysis_text,
            analysis=analysis,
            recommended_resume=recommended_resume,
            preferred_language=profile.preferred_language,
            grammatical_gender=profile.grammatical_gender,
            operation="cover_letter",
        )


@dataclass(frozen=True)
class _VacancyMeta:
    title: str
    company: str | None
    location: str | None
    url: str
    content_completeness: str
    snippet: str | None = None


def resolve_resume_path(resumes_dir: Path, resume_name: str) -> tuple[Path | None, str | None]:
    safe_name = resume_name.strip().lower()
    if not re.fullmatch(r"[a-z0-9-]+", safe_name):
        return None, f"Resume file missing: {resumes_dir.as_posix()}/{safe_name}.pdf"

    root = resumes_dir.resolve()
    candidate = (resumes_dir / f"{safe_name}.pdf").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"Resume file missing: {resumes_dir.as_posix()}/{safe_name}.pdf"

    if candidate.exists() and candidate.is_file():
        return candidate, None
    return None, f"Resume file missing: {resumes_dir.as_posix()}/{safe_name}.pdf"


def _validate_prepared_cover_letter(cover_result: CoverLetterResult) -> None:
    text = (cover_result.cover_letter or "").strip()
    if not text:
        raise ApplicationPreparationError("Cover letter validation failed: empty text")
    if not (cover_result.language or "").strip():
        raise ApplicationPreparationError("Cover letter validation failed: missing language")


def _collect_warning_nuances(nuances: list[str]) -> list[str]:
    result: list[str] = []
    for nuance in nuances:
        text = nuance.lower()
        if "локац" in text or "country" in text or "филиппин" in text:
            result.append("Локация требует дополнительного уточнения")
        if "роль уровня lead" in text:
            result.append("Роль уровня Lead — стоит проверить ожидания по управлению и архитектурной ответственности")
    return result


def _dedupe_preserve(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(item.strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out
