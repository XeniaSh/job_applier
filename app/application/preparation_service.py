from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from app.collectors.email_imap_client import EmailIMAPClient
from app.collectors.linkedin_email_parser import parse_linkedin_email
from app.collectors.linkedin_models import LinkedInEmailVacancy
from app.llm_client import LLMClient
from app.models import CoverLetterResult, VacancyEvaluation
from app.profile_loader import load_candidate_profile_context
from app.prompt_loader import load_cover_letter_prompt
from app.vacancy_analyzer import VacancyAnalyzer


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


class ApplicationPreparationError(Exception):
    """Raised when preparation cannot continue."""


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
    ) -> None:
        self._analyzer = analyzer
        self._llm_client = llm_client
        self._email_client = email_client
        self._resumes_dir = resumes_dir
        self._preferred_language = preferred_language
        self._grammatical_gender = grammatical_gender

    def prepare(self, source: str, external_id: str) -> PreparedApplication:
        if source != "linkedin-email":
            raise ApplicationPreparationError(f"Unsupported source for preparation: {source}")
        vacancy = self._find_linkedin_vacancy(external_id)
        if vacancy is None:
            raise ApplicationPreparationError("Не удалось найти данные вакансии в последних LinkedIn-письмах.")

        analysis = self._analyzer.analyze(
            vacancy.to_analysis_text(),
            content_completeness=vacancy.content_completeness.value,
        )
        recommended_resume = analysis.recommended_resume.value
        resume_path, resume_warning = resolve_resume_path(self._resumes_dir, recommended_resume)
        cover_result = self._build_cover_letter(
            vacancy=vacancy,
            analysis=analysis,
            recommended_resume=recommended_resume,
        )

        warnings: list[str] = []
        if vacancy.content_completeness.value in {"PARTIAL", "MINIMAL"}:
            warnings.append("Описание вакансии неполное — требуется открыть LinkedIn")
        if resume_warning:
            warnings.append(resume_warning)
        warnings.extend(_collect_warning_nuances(analysis.nuances))

        return PreparedApplication(
            source=source,
            external_id=external_id,
            title=vacancy.title,
            company=vacancy.company,
            location=vacancy.location,
            url=vacancy.url,
            decision=analysis.decision.value,
            match_percentage=analysis.match_percentage,
            recommended_resume=recommended_resume,
            resume_path=str(resume_path) if resume_path is not None else None,
            cover_letter=cover_result.cover_letter,
            language=cover_result.language,
            warnings=_dedupe_preserve(warnings),
        )

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
        vacancy: LinkedInEmailVacancy,
        analysis: VacancyEvaluation,
        recommended_resume: str,
    ) -> CoverLetterResult:
        prompt = load_cover_letter_prompt()
        profile_context = load_candidate_profile_context(
            preferred_language=self._preferred_language,
            grammatical_gender=self._grammatical_gender,
        )
        vacancy_text = vacancy.to_analysis_text()
        return self._llm_client.create_cover_letter(
            prompt=prompt,
            candidate_profile=profile_context.text,
            vacancy_text=vacancy_text,
            analysis=analysis,
            recommended_resume=recommended_resume,
            preferred_language=profile_context.preferred_language,
            grammatical_gender=profile_context.grammatical_gender,
        )


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
