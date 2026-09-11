from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol
import logging

from app.application.autofill.answers import ApplicationAnswerGenerator
from app.application.autofill.browser import (
    BrowserSession,
    BrowserSetupError,
    complete_browser_handoff,
    wait_for_manual_review,
)
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.greenhouse import GreenhouseAdapter, GreenhouseFormError
from app.application.autofill.logging import log_autofill_result
from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy, VacancyResolveError, VacancyResolver
from app.application.autofill.resume import ResumeResolutionError, resolve_default_resume_path
from app.application.candidate_profile import CandidateProfile
from app.application.candidate_profile_loader import CandidateProfileLoadError, load_structured_candidate_profile

logger = logging.getLogger(__name__)


class CoverLetterTextProvider(Protocol):
    def generate(self, vacancy: ResolvedVacancy, profile: CandidateProfile) -> str | None: ...


class AutofillService:
    def __init__(
        self,
        *,
        resolver: VacancyResolver,
        profile_loader: Callable[[], CandidateProfile] = load_structured_candidate_profile,
        adapter: GreenhouseAdapter | None = None,
        browser_factory: Callable[[], BrowserSession] | None = None,
        wait_for_review: Callable[[], None] = wait_for_manual_review,
        on_ready: Callable[[AutofillResult], None] | None = None,
        answer_generator: ApplicationAnswerGenerator | None = None,
        cover_letter_provider: CoverLetterTextProvider | None = None,
    ) -> None:
        self._resolver = resolver
        self._profile_loader = profile_loader
        self._adapter = adapter or GreenhouseAdapter()
        self._browser_factory = browser_factory or (lambda: BrowserSession(headed=True, keep_open=True))
        self._wait_for_review = wait_for_review
        self._on_ready = on_ready
        self._answer_generator = answer_generator
        self._cover_letter_provider = cover_letter_provider

    def run(self, source: str, external_id: str, *, keep_open: bool = True) -> AutofillResult:
        session: BrowserSession | None = None
        vacancy: ResolvedVacancy | None = None
        result: AutofillResult
        try:
            vacancy = self._resolver.resolve(source, external_id)
            profile = self._profile_loader()
            resume_path = resolve_default_resume_path(profile)
            session = self._browser_factory()
            session.keep_open = keep_open
            session.open(vacancy.application_url)
            result = self._fill_open_page(vacancy, profile, resume_path, session)
        except VacancyResolveError as exc:
            result = _failed(source, external_id, str(exc), vacancy)
        except CandidateProfileLoadError as exc:
            result = _failed(source, external_id, str(exc), vacancy)
        except ResumeResolutionError as exc:
            result = _failed(source, external_id, str(exc), vacancy)
        except BrowserSetupError as exc:
            result = _failed(source, external_id, str(exc), vacancy)
        except GreenhouseFormError as exc:
            result = _failed(source, external_id, str(exc), vacancy)
        except Exception as exc:
            logger.exception("Autofill failed for %s %s", source, external_id)
            result = _failed(source, external_id, f"Unexpected autofill error: {exc}", vacancy)

        if session is not None:
            should_handoff = keep_open and result.status in {
                AutofillStatus.READY_FOR_REVIEW,
                AutofillStatus.NEEDS_MANUAL_INTERVENTION,
            }
            if should_handoff:
                if self._on_ready is not None:
                    self._on_ready(result)
                complete_browser_handoff(session, wait=self._wait_for_review)
            else:
                session.close()

        log_autofill_result(result)
        return result

    def _fill_open_page(
        self,
        vacancy: ResolvedVacancy,
        profile: CandidateProfile,
        resume_path: Path,
        session: BrowserSession,
    ) -> AutofillResult:
        page = session.page
        prepare = getattr(self._adapter, "prepare_page", None)
        if prepare is not None:
            prepare(page)
        challenge = self._adapter.detect_challenge(page)
        if challenge:
            return stage1_autofill_result(
                source=vacancy.source,
                external_id=vacancy.external_id,
                application_url=vacancy.application_url,
                status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
                warnings=[f"Security challenge detected: {challenge}"],
            )
        if not self._adapter.recognize(page):
            return stage1_autofill_result(
                source=vacancy.source,
                external_id=vacancy.external_id,
                application_url=vacancy.application_url,
                status=AutofillStatus.FAILED,
                warnings=["UNSUPPORTED_FORM"],
            )

        discovered = self._adapter.discover_fields(page)
        classified = [classify_field(item, profile) for item in discovered]
        cover_letter_text = _maybe_cover_letter(
            self._cover_letter_provider, classified, vacancy, profile
        )
        if cover_letter_text:
            logger.info(
                "Cover letter text is available (%s characters); attempting Enter manually.",
                len(cover_letter_text),
            )
        elif any(item.kind is QuestionKind.COVER_LETTER for item in classified):
            generation_error = getattr(self._cover_letter_provider, "last_error", None)
            if self._cover_letter_provider is None:
                generation_error = "generation was never invoked (no LLM provider)"
            elif generation_error is None:
                generation_error = "generation returned no text"
            logger.warning("Cover letter text unavailable: %s", generation_error)
        filled: list[AutofillFieldResult] = []
        unresolved_required: list[AutofillFieldResult] = []
        unresolved_optional: list[AutofillFieldResult] = []
        sensitive: list[AutofillFieldResult] = []
        unsupported: list[AutofillFieldResult] = []
        generated: list[AutofillFieldResult] = []
        warnings: list[str] = []
        resume_uploaded = False
        cover_letter_filled = False

        resume_items: list[ClassifiedField] = []

        for item in classified:
            item = _enrich_unresolved(
                item,
                profile=profile,
                vacancy=vacancy,
                cover_letter_text=cover_letter_text,
                answer_generator=self._answer_generator,
            )
            record = _field_result(item)
            if item.inactive_conditional:
                continue
            if item.classification is FieldClassification.SENSITIVE_OPTIONAL:
                sensitive.append(record)
                continue
            if item.classification is FieldClassification.UNSUPPORTED:
                unsupported.append(record)
                continue
            if item.kind is QuestionKind.RESUME and item.fill:
                resume_items.append(item)
                continue
            if item.fill:
                if _fill_and_confirm(self._adapter, page, item):
                    filled.append(record)
                    if item.kind is QuestionKind.COVER_LETTER:
                        cover_letter_filled = True
                    if item.generated:
                        generated.append(record)
                else:
                    if item.kind is QuestionKind.COVER_LETTER:
                        adapter_reason = getattr(
                            self._adapter, "last_cover_letter_error", None
                        )
                        warnings.append(
                            adapter_reason
                            or f"Cover letter not filled: read-back failed for '{item.field.label}'."
                        )
                    else:
                        warnings.append(f"Read-back failed for '{item.field.label}'.")
                    _append_unresolved(item, record, unresolved_required, unresolved_optional)
                continue
            if item.kind is QuestionKind.COVER_LETTER:
                generation_error = getattr(self._cover_letter_provider, "last_error", None)
                if self._cover_letter_provider is None:
                    warnings.append(
                        "Cover letter was left unresolved: generation was never invoked (no LLM provider)."
                    )
                elif generation_error:
                    warnings.append(f"Cover letter was left unresolved: {generation_error}")
                else:
                    warnings.append(
                        f"Cover letter was left unresolved for '{item.field.label}'."
                    )
            if item.classification is FieldClassification.UNKNOWN_REQUIRED:
                unresolved_required.append(record)
            elif item.classification is FieldClassification.UNKNOWN_OPTIONAL:
                unresolved_optional.append(record)

        for item in resume_items:
            record = _field_result(item)
            resume_uploaded = self._adapter.upload_resume(page, resume_path, item.field)
            if not resume_uploaded:
                resume_uploaded = self._adapter.upload_resume(page, resume_path, item.field)
            if resume_uploaded:
                filled.append(record)
            else:
                unresolved_required.append(record) if item.field.required else unresolved_optional.append(record)
                warnings.append(f"Resume was not attached for '{item.field.label}'.")

        return stage1_autofill_result(
            source=vacancy.source,
            external_id=vacancy.external_id,
            application_url=vacancy.application_url,
            status=AutofillStatus.READY_FOR_REVIEW,
            filled_fields=filled,
            unresolved_required_fields=unresolved_required,
            unresolved_optional_fields=unresolved_optional,
            sensitive_fields=sensitive,
            unsupported_fields=unsupported,
            generated_fields=generated,
            warnings=warnings,
            resume_uploaded=resume_uploaded,
            cover_letter_filled=cover_letter_filled,
            submit_performed=True,
        )


def _maybe_cover_letter(
    provider: CoverLetterTextProvider | None,
    classified: list[ClassifiedField],
    vacancy: ResolvedVacancy,
    profile: CandidateProfile,
) -> str | None:
    if not any(item.kind is QuestionKind.COVER_LETTER for item in classified):
        logger.info("Cover letter generation skipped: no Cover Letter field discovered.")
        return None
    if provider is None:
        logger.warning("Cover letter generation was never invoked: no LLM provider.")
        return None
    try:
        text = provider.generate(vacancy, profile)
    except Exception:
        logger.warning("Cover letter generation failed", exc_info=True)
        return None
    if text is None:
        return None
    cleaned = text.strip()
    return cleaned or None


def _enrich_unresolved(
    item: ClassifiedField,
    *,
    profile: CandidateProfile,
    vacancy: ResolvedVacancy,
    cover_letter_text: str | None,
    answer_generator: ApplicationAnswerGenerator | None,
) -> ClassifiedField:
    if item.fill:
        return item
    if item.kind is QuestionKind.COVER_LETTER and cover_letter_text:
        return replace(
            item,
            fill=True,
            value=cover_letter_text,
            generated=True,
        )
    if answer_generator is None:
        return item
    answer = answer_generator.generate(item.field, profile, vacancy)
    if not answer:
        return item
    value: str | list[str] = answer
    if item.field.field_type == "multiselect" or item.max_choices:
        parts = [part.strip() for part in answer.replace(";", ",").split(",") if part.strip()]
        if parts:
            value = parts
    return replace(
        item,
        fill=True,
        value=value,
        generated=True,
    )


def _failed(
    source: str,
    external_id: str,
    warning: str,
    vacancy: ResolvedVacancy | None,
) -> AutofillResult:
    return stage1_autofill_result(
        source=source,
        external_id=external_id,
        application_url=None if vacancy is None else vacancy.application_url,
        status=AutofillStatus.FAILED,
        warnings=[warning],
    )


def _field_result(item: ClassifiedField) -> AutofillFieldResult:
    return AutofillFieldResult(
        label=item.field.label,
        classification=item.classification,
        field_type=item.field.field_type,
        required=item.field.required,
        name=item.field.name,
        generated=item.generated,
    )


def _fill_and_confirm(adapter: GreenhouseAdapter, page: object, item: ClassifiedField) -> bool:
    ok = adapter.fill_field(page, item)
    check_item = _with_confirmed_multiselect(adapter, item)
    read_back = adapter.read_back(page, item.field)
    if ok and _readback_matches(check_item, read_back):
        return True
    ok = adapter.fill_field(page, item)
    check_item = _with_confirmed_multiselect(adapter, item)
    read_back = adapter.read_back(page, item.field)
    return bool(ok and _readback_matches(check_item, read_back))


def _with_confirmed_multiselect(adapter: object, item: ClassifiedField) -> ClassifiedField:
    selected = getattr(adapter, "last_multiselect_selected", None)
    if isinstance(item.value, list) and selected:
        return replace(item, value=list(selected))
    return item


def _append_unresolved(
    item: ClassifiedField,
    record: AutofillFieldResult,
    required: list[AutofillFieldResult],
    optional: list[AutofillFieldResult],
) -> None:
    if item.field.required:
        required.append(record)
    else:
        optional.append(record)


def _readback_matches(item: ClassifiedField, raw: str | None) -> bool:
    if raw is None or raw == "":
        return False
    expected = item.value
    actual = raw.strip()
    if isinstance(expected, bool):
        from app.application.autofill.react_controls import semantic_choice_matches

        lowered = actual.lower()
        if lowered in {"true", "1", "on", "yes"}:
            return expected is True
        if lowered in {"false", "0", "off", "no"}:
            return expected is False
        return semantic_choice_matches(expected, actual)
    if isinstance(expected, list):
        expected_lower = [str(item_value).strip().lower() for item_value in expected if str(item_value).strip()]
        actual_parts = [part.strip().lower() for part in re_split_choices(actual)]
        if not expected_lower:
            return False
        return all(
            any(expected_value in part or part in expected_value for part in actual_parts)
            for expected_value in expected_lower
        )
    wanted = str(expected).strip()
    if item.kind is QuestionKind.ACADEMIC_LEVEL:
        from app.application.candidate_profile import canonical_academic_level

        expected_level = canonical_academic_level(wanted)
        actual_level = canonical_academic_level(actual)
        return expected_level is not None and expected_level == actual_level
    if wanted == actual:
        return True
    if item.kind is QuestionKind.PHONE:
        from app.application.autofill.phone import calling_code_occurs_once, digits_only, extract_calling_code

        code = extract_calling_code(actual)
        if code and not calling_code_occurs_once(actual, code):
            return False
        wanted_digits = digits_only(wanted)
        actual_digits = digits_only(actual)
        national = wanted_digits
        if code and wanted_digits.startswith(code) and len(wanted_digits) > len(code):
            national = wanted_digits[len(code):]
        if national and national in actual_digits:
            return True
        if item.country and item.country.lower() in actual.lower():
            return bool(national and national in actual_digits)
        return False
    if wanted.lower() in actual.lower() or actual.lower() in wanted.lower():
        if item.kind in {
            QuestionKind.YEARS_EXPERIENCE,
            QuestionKind.FIELD_OF_INTEREST,
            QuestionKind.RELOCATION,
            QuestionKind.EMPLOYEE_RELATIONSHIP,
            QuestionKind.PRIOR_AFFILIATION,
            QuestionKind.APPLICATION_SOURCE,
            QuestionKind.PRIVACY_CONSENT,
            QuestionKind.NEWSLETTER,
            QuestionKind.SMS_UPDATES,
            QuestionKind.QUESTION_OVERRIDE,
            QuestionKind.GENDER,
            QuestionKind.COVER_LETTER,
            QuestionKind.WHY_COMPANY,
            QuestionKind.PROFESSIONAL_FREE_TEXT,
        }:
            return True
    if item.kind in {QuestionKind.LOCATION, QuestionKind.COUNTRY}:
        if wanted.lower() in actual.lower() or actual.lower() in wanted.lower():
            return True
        if item.kind is QuestionKind.COUNTRY and actual.startswith("+"):
            return True
        return False
    return False


def re_split_choices(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for char in value:
        if char in {",", ";", "\n"}:
            chunk = "".join(current).strip()
            if chunk:
                parts.append(chunk)
            current = []
        else:
            current.append(char)
    trailing = "".join(current).strip()
    if trailing:
        parts.append(trailing)
    return parts or [value]
