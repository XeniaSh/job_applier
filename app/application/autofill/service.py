from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
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
from app.application.autofill.lever import LeverAdapter, LeverFormError
from app.application.autofill.logging import log_autofill_result
from app.application.autofill.summary import format_privacy_acknowledgement_report
from app.application.autofill.models import (
    AutofillFailureReason,
    AutofillFieldResult,
    AutofillResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import (
    TARGET_COMPANY_LEVER_PREFIX,
    ResolvedVacancy,
    VacancyResolveError,
    VacancyResolver,
)
from app.application.autofill.resume import ResumeResolutionError, resolve_default_resume_path
from app.application.autofill.submit import (
    SubmitAdapter,
    attempt_auto_submit,
    default_submit_adapter_for_source,
)
from app.application.candidate_profile import CandidateProfile
from app.application.candidate_profile_loader import CandidateProfileLoadError, load_structured_candidate_profile

logger = logging.getLogger(__name__)

_UNSUPPORTED_FORM_ERRORS: tuple[type[Exception], ...] = (GreenhouseFormError, LeverFormError)


class AutofillAdapter(Protocol):
    """The DOM surface `AutofillService` drives. No adapter exposes a submit method."""

    def recognize(self, page: object) -> bool: ...
    def detect_challenge(self, page: object) -> str | None: ...
    def discover_fields(self, page: object) -> list[object]: ...
    def fill_field(self, page: object, classified: ClassifiedField) -> bool: ...
    def upload_resume(self, page: object, resume_path: Path, field: object) -> bool: ...
    def read_back(self, page: object, field: object) -> str | None: ...


def default_adapter_for_source(source: str) -> AutofillAdapter:
    """Source-aware ATS adapter selection. Unknown sources keep the historical Greenhouse default."""
    if source.strip().startswith(TARGET_COMPANY_LEVER_PREFIX):
        logger.warning(
            "autofill_adapter_dispatch provider=lever adapter=LeverAdapter source=%s",
            source,
        )
        return LeverAdapter()
    return GreenhouseAdapter()


class CoverLetterTextProvider(Protocol):
    def generate(self, vacancy: ResolvedVacancy, profile: CandidateProfile) -> str | None: ...


class AutofillService:
    def __init__(
        self,
        *,
        resolver: VacancyResolver,
        profile_loader: Callable[[], CandidateProfile] = load_structured_candidate_profile,
        adapter: AutofillAdapter | None = None,
        adapter_for_source: Callable[[str], AutofillAdapter] = default_adapter_for_source,
        browser_factory: Callable[[], BrowserSession] | None = None,
        wait_for_review: Callable[[], None] = wait_for_manual_review,
        on_ready: Callable[[AutofillResult], None] | None = None,
        answer_generator: ApplicationAnswerGenerator | None = None,
        cover_letter_provider: CoverLetterTextProvider | None = None,
        auto_submit_enabled: bool = False,
        submit_adapter: SubmitAdapter | None = None,
        submit_adapter_for_source: Callable[[str], SubmitAdapter] = default_submit_adapter_for_source,
    ) -> None:
        self._resolver = resolver
        self._profile_loader = profile_loader
        self._explicit_adapter = adapter
        self._adapter_for_source = adapter_for_source
        self._browser_factory = browser_factory or (lambda: BrowserSession(headed=True, keep_open=True))
        self._wait_for_review = wait_for_review
        self._on_ready = on_ready
        self._answer_generator = answer_generator
        self._cover_letter_provider = cover_letter_provider
        self._auto_submit_enabled = auto_submit_enabled
        self._explicit_submit_adapter = submit_adapter
        self._submit_adapter_for_source = submit_adapter_for_source

    def run(self, source: str, external_id: str, *, keep_open: bool = True) -> AutofillResult:
        session: BrowserSession | None = None
        vacancy: ResolvedVacancy | None = None
        result: AutofillResult
        try:
            vacancy = self._resolver.resolve(source, external_id)
            adapter = self._explicit_adapter or self._adapter_for_source(vacancy.source)
            profile = self._profile_loader()
            resume_path = resolve_default_resume_path(profile)
            session = self._browser_factory()
            session.keep_open = keep_open
            session.open(vacancy.application_url)
            result = self._fill_open_page(vacancy, profile, resume_path, session, adapter)
            if self._auto_submit_enabled and result.status is AutofillStatus.READY_FOR_REVIEW:
                submit_adapter = self._explicit_submit_adapter or self._submit_adapter_for_source(
                    vacancy.source
                )
                result = attempt_auto_submit(
                    result,
                    enabled=self._auto_submit_enabled,
                    challenge_detector=adapter,
                    submit_adapter=submit_adapter,
                    page=session.page,
                )
        except VacancyResolveError as exc:
            result = _failed(
                source, external_id, str(exc), vacancy, reason=AutofillFailureReason.VACANCY_RESOLVE_FAILED
            )
        except CandidateProfileLoadError as exc:
            result = _failed(
                source, external_id, str(exc), vacancy, reason=AutofillFailureReason.PROFILE_LOAD_FAILED
            )
        except ResumeResolutionError as exc:
            result = _failed(
                source, external_id, str(exc), vacancy, reason=AutofillFailureReason.RESUME_RESOLUTION_FAILED
            )
        except BrowserSetupError as exc:
            result = _failed(
                source, external_id, str(exc), vacancy, reason=AutofillFailureReason.BROWSER_SETUP_FAILED
            )
        except _UNSUPPORTED_FORM_ERRORS as exc:
            result = _failed(
                source, external_id, str(exc), vacancy, reason=AutofillFailureReason.UNSUPPORTED_FORM
            )
        except Exception as exc:
            logger.exception("Autofill failed for %s %s", source, external_id)
            result = _failed(
                source,
                external_id,
                f"Unexpected autofill error: {exc}",
                vacancy,
                reason=AutofillFailureReason.UNEXPECTED_ERROR,
            )

        if session is not None:
            should_handoff = keep_open and result.status in {
                AutofillStatus.READY_FOR_REVIEW,
                AutofillStatus.NEEDS_MANUAL_INTERVENTION,
            }
            if should_handoff:
                if self._on_ready is not None:
                    try:
                        self._on_ready(result)
                    except Exception:
                        # A failed "ready" notification (e.g. a transient
                        # Telegram send error) must not skip the browser
                        # handoff below: the browser is genuinely still
                        # open, and any review-session bookkeeping tied to
                        # this run (see app.application.autofill.review_session)
                        # must stay valid until the handoff actually
                        # releases it. Losing the notification is still
                        # logged; losing the handoff would silently orphan
                        # an open browser with no way to close it.
                        logger.exception(
                            "on_ready notification failed for %s %s; continuing browser handoff",
                            source,
                            external_id,
                        )
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
        adapter: AutofillAdapter,
    ) -> AutofillResult:
        page = session.page
        prepare = getattr(adapter, "prepare_page", None)
        if prepare is not None:
            prepare(page)
        challenge = adapter.detect_challenge(page)
        if challenge:
            return stage1_autofill_result(
                source=vacancy.source,
                external_id=vacancy.external_id,
                application_url=vacancy.application_url,
                status=AutofillStatus.NEEDS_MANUAL_INTERVENTION,
                warnings=[f"Security challenge detected: {challenge}"],
            )
        if not adapter.recognize(page):
            logger.warning(
                "autofill recognition failed reason=UNSUPPORTED_FORM source=%s external_id=%s "
                "application_host=%s page_host=%s",
                vacancy.source,
                vacancy.external_id,
                _safe_host(vacancy.application_url),
                _safe_host(getattr(page, "url", "")),
            )
            return stage1_autofill_result(
                source=vacancy.source,
                external_id=vacancy.external_id,
                application_url=vacancy.application_url,
                status=AutofillStatus.FAILED,
                warnings=["UNSUPPORTED_FORM"],
                failure_reason=AutofillFailureReason.UNSUPPORTED_FORM,
            )

        discovered = adapter.discover_fields(page)
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
                if _fill_and_confirm(adapter, page, item):
                    filled.append(record)
                    if item.kind is QuestionKind.COVER_LETTER:
                        cover_letter_filled = True
                    if item.generated:
                        generated.append(record)
                else:
                    if item.kind is QuestionKind.COVER_LETTER:
                        adapter_reason = getattr(
                            adapter, "last_cover_letter_error", None
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
                if item.unresolved_reason:
                    warnings.append(item.unresolved_reason)
            elif item.classification is FieldClassification.UNKNOWN_OPTIONAL:
                if item.kind is QuestionKind.GITLAB_USERNAME:
                    continue
                unresolved_optional.append(record)

        for item in resume_items:
            record = _field_result(item)
            resume_uploaded = adapter.upload_resume(page, resume_path, item.field)
            if not resume_uploaded:
                resume_uploaded = adapter.upload_resume(page, resume_path, item.field)
            if resume_uploaded:
                filled.append(record)
            else:
                unresolved_required.append(record) if item.field.required else unresolved_optional.append(record)
                warnings.append(f"Resume was not attached for '{item.field.label}'.")

        privacy_trace = _build_privacy_acknowledgement_trace(classified, adapter, filled)
        if privacy_trace is not None:
            privacy_report = format_privacy_acknowledgement_report(privacy_trace)
            logger.info("%s", privacy_report)
            if _privacy_trace_should_warn(privacy_trace):
                warnings.append(privacy_report)

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
            submit_performed=False,
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


def _safe_host(url: object) -> str:
    """Netloc only -- never the path, query, or fragment, which could carry PII or raw content."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _failed(
    source: str,
    external_id: str,
    warning: str,
    vacancy: ResolvedVacancy | None,
    *,
    reason: AutofillFailureReason,
) -> AutofillResult:
    return stage1_autofill_result(
        source=source,
        external_id=external_id,
        application_url=None if vacancy is None else vacancy.application_url,
        status=AutofillStatus.FAILED,
        warnings=[warning],
        failure_reason=reason,
    )


def _field_result(item: ClassifiedField) -> AutofillFieldResult:
    return AutofillFieldResult(
        label=item.field.label,
        classification=item.classification,
        field_type=item.field.field_type,
        required=item.field.required,
        name=item.field.name,
        generated=item.generated,
        note=item.unresolved_reason,
    )


def _fill_and_confirm(adapter: AutofillAdapter, page: object, item: ClassifiedField) -> bool:
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


def _parse_plain_number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


_YEARS_NATIVE_INPUT_TYPES = frozenset({"text", "number"})


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
    if item.kind is QuestionKind.PRIVACY_CONSENT and actual.lower() in {"true", "1", "on", "yes"}:
        if expected is True:
            return True
        return str(expected).strip().lower() in {
            "true",
            "1",
            "yes",
            "on",
            "acknowledge",
            "confirm",
            "acknowledge/confirm",
        }
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
    if item.kind is QuestionKind.YEARS_EXPERIENCE:
        expected_number = _parse_plain_number(wanted)
        actual_number = _parse_plain_number(actual)
        if expected_number is not None and actual_number is not None:
            return expected_number == actual_number
        if expected_number is not None and item.field.field_type in _YEARS_NATIVE_INPUT_TYPES:
            # A native text/number years input must read back as a plain number;
            # anything else (e.g. "12 years") is a mismatch, not a substring match.
            return False
    if wanted.lower() in actual.lower() or actual.lower() in wanted.lower():
        if item.kind in {
            QuestionKind.YEARS_EXPERIENCE,
            QuestionKind.FIELD_OF_INTEREST,
            QuestionKind.PRIMARY_LANGUAGE,
            QuestionKind.OPEN_SOURCE_LINKS,
            QuestionKind.GITLAB_USERNAME,
            QuestionKind.RELOCATION,
            QuestionKind.OFFICE_WORK,
            QuestionKind.EMPLOYEE_RELATIONSHIP,
            QuestionKind.PRIOR_AFFILIATION,
            QuestionKind.EMPLOYMENT_RESTRICTIONS,
            QuestionKind.LOCATED_IN,
            QuestionKind.VISA_SPONSORSHIP,
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


def _build_privacy_acknowledgement_trace(
    classified: list[ClassifiedField],
    adapter: object,
    filled: list[AutofillFieldResult],
) -> dict[str, object] | None:
    from app.application.autofill.greenhouse import _looks_like_privacy_field

    privacy_items = [item for item in classified if item.kind is QuestionKind.PRIVACY_CONSENT]
    looking = [item for item in classified if _looks_like_privacy_field(item.field)]
    adapter_trace = getattr(adapter, "last_privacy_trace", None)
    if not privacy_items and not looking and not adapter_trace:
        return None
    filled_labels = {item.label for item in filled}
    if isinstance(adapter_trace, dict) and adapter_trace:
        trace = dict(adapter_trace)
        if privacy_items and privacy_items[0].field.label in filled_labels:
            trace["readback_checked"] = True
            trace["failure_reason"] = None
        return trace
    item = privacy_items[0] if privacy_items else looking[0]
    filled_ok = item.field.label in filled_labels
    if item.kind is QuestionKind.PRIVACY_CONSENT:
        classified_as = "required_privacy" if item.field.required else "privacy_optional"
        failure = None if filled_ok else ("not_fillable" if not item.fill else "fill_or_readback_failed")
    else:
        classified_as = item.kind.value
        failure = "not_classified_as_required_privacy"
    return {
        "discovered": True,
        "classified": classified_as,
        "control_type": item.field.field_type,
        "interaction_attempted": "none" if not item.fill else "unknown",
        "readback_checked": filled_ok,
        "failure_reason": failure,
        "locator": item.field.name or item.field.element_id or item.field.label,
    }


def _privacy_trace_should_warn(trace: dict[str, object]) -> bool:
    if not trace.get("discovered"):
        return False
    reason = trace.get("failure_reason")
    if reason:
        return True
    classified = str(trace.get("classified") or "")
    if classified in {"required_privacy", "privacy_optional"}:
        return trace.get("readback_checked") is False
    return False

