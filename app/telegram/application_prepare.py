"""Telegram explicit prepare for Target Company Greenhouse vacancies.

Telegram is the control UI. It resolves source + external_id, loads the
cached recommendation, and calls PrepareApplicationService with EXPLICIT
intent. It does not start browser autofill or talk to Greenhouse, and it
does not reimplement APPLIED / SKIP / CHECK_MANUALLY policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.application.autofill.models import AutofillFailureReason, AutofillResult, AutofillStatus
from app.application.autofill.review_session import ReviewSessionCloseOutcome
from app.application.prepare_application import (
    PrepareApplicationResult,
    PrepareIntent,
    RecommendedVacancy,
)
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.telegram.client import source_supports_application_prepare


UNKNOWN_RECOMMENDATION = "UNKNOWN"
STARTING_TEXT = "Preparation starting"
FAILED_TEXT = "Preparation failed."

_CHALLENGE_WARNING_PREFIX = "Security challenge detected: "
_CHALLENGE_LABELS: dict[str, str] = {
    "cloudflare_challenge": "a Cloudflare security check",
    "captcha": "a CAPTCHA",
    "login": "a login/authentication prompt",
}

_RESIDENCE_COUNTRY_UNCONFIRMED_PREFIX = "Residence country selection for '"
_RESIDENCE_COUNTRY_UNCONFIRMED_SUFFIX = (
    "was not confirmed: any visible selection must be checked and corrected manually."
)
RESIDENCE_COUNTRY_UNCONFIRMED_TEXT = (
    "The residence country selection could not be confirmed: check and correct it "
    "manually before using Submit."
)

_FAILURE_REASON_TEXT: dict[AutofillFailureReason, str] = {
    AutofillFailureReason.UNSUPPORTED_FORM: (
        "Preparation failed: automatic filling is not supported for this "
        "application form/ATS yet. Please apply manually."
    ),
    AutofillFailureReason.BROWSER_SETUP_FAILED: (
        "Preparation failed: the browser could not be started. Check the "
        "local Playwright/Chromium setup."
    ),
    AutofillFailureReason.VACANCY_RESOLVE_FAILED: (
        "Preparation failed: the vacancy could not be resolved (it may be "
        "closed, removed, or temporarily unavailable)."
    ),
    AutofillFailureReason.PROFILE_LOAD_FAILED: (
        "Preparation failed: the candidate profile could not be loaded."
    ),
    AutofillFailureReason.RESUME_RESOLUTION_FAILED: (
        "Preparation failed: no usable resume could be resolved."
    ),
    AutofillFailureReason.UNEXPECTED_ERROR: (
        "Preparation failed due to an unexpected error. Check the application logs for details."
    ),
}


def format_application_prepare_blocked_text(reason: str) -> str:
    cleaned = " ".join(str(reason or "").split())
    return cleaned or "Preparation blocked."


def format_application_prepare_failed_text(result: AutofillResult | None = None) -> str:
    """Map a FAILED AutofillResult to a concise, safe, actionable reason.

    Falls back to the generic text when there is no result (for example, the
    request never reached the autofill runner) or the reason is not recognized.
    Never forwards raw exception text or stack traces here; that detail stays
    in server-side logs (see `log_autofill_result` / `logger.exception`).
    """
    if result is None or result.failure_reason is None:
        return FAILED_TEXT
    text = _FAILURE_REASON_TEXT.get(result.failure_reason, FAILED_TEXT)
    if result.failure_reason is AutofillFailureReason.RESUME_RESOLUTION_FAILED:
        text = f"{text} Check the resume_profiles.yaml configuration and prepare again."
    # The resume variant/filename/location-uncertainty context is attached to
    # `result` whenever a variant was resolved before the run's eventual
    # failure (see `AutofillRunner._attach_resume_selection`'s unconditional
    # post-try/except call) -- not only on `RESUME_RESOLUTION_FAILED`. A
    # `BROWSER_SETUP_FAILED` after a successful resume selection, for
    # example, must surface the same typed uncertainty here too.
    if result.resume_variant:
        variant_line = f"Selected resume variant: {result.resume_variant}"
        if result.resume_file:
            variant_line += f" ({result.resume_file})"
        text = f"{text}\n{variant_line}"
    if result.resume_location_warning:
        text = f"{text}\n{result.resume_location_warning}"
    return text


def format_application_prepare_completed_text(result: AutofillResult) -> str:
    heading = (
        "Preparation incomplete: the required resume attachment could not be confirmed."
        if result.resume_attachment_incomplete
        else "Preparation completed."
    )
    lines = [
        heading,
        f"Filled fields: {len(result.filled_fields)}",
        f"Unresolved required fields: {len(result.unresolved_required_fields)}",
    ]
    if result.resume_variant:
        resume_line = f"Resume variant: {result.resume_variant}"
        if result.resume_file:
            resume_line += f" ({result.resume_file})"
        lines.append(resume_line)
    if result.resume_location_warning:
        lines.append(f"Location uncertainty: {result.resume_location_warning}")
    if result.status in {
        AutofillStatus.READY_FOR_REVIEW,
        AutofillStatus.NEEDS_MANUAL_INTERVENTION,
    }:
        lines.append("Manual review needed.")
    if result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION:
        lines.append(f"Reason: {_manual_intervention_reason(result)}")
    if _has_residence_country_unconfirmed_warning(result.warnings):
        lines.append(RESIDENCE_COUNTRY_UNCONFIRMED_TEXT)
    lines.append("Submit was not performed.")
    return "\n".join(lines)


def _has_residence_country_unconfirmed_warning(warnings: list[str]) -> bool:
    return any(
        warning.startswith(_RESIDENCE_COUNTRY_UNCONFIRMED_PREFIX)
        and warning.endswith(_RESIDENCE_COUNTRY_UNCONFIRMED_SUFFIX)
        for warning in warnings
    )


def _challenge_label(warnings: list[str]) -> str | None:
    """A security/login challenge label, or None when `warnings` names no
    such challenge. Must never be defaulted to a generic challenge label:
    `NEEDS_MANUAL_INTERVENTION` can also be caused by a required resume
    attachment that could not be confirmed, which is not a challenge at all.
    """
    for warning in warnings:
        if warning.startswith(_CHALLENGE_WARNING_PREFIX):
            token = warning[len(_CHALLENGE_WARNING_PREFIX) :].strip()
            return _CHALLENGE_LABELS.get(token, "a security/login challenge")
    return None


def _manual_intervention_reason(result: AutofillResult) -> str:
    """An actionable, accurate reason for a `NEEDS_MANUAL_INTERVENTION`
    result -- never claims a security challenge was detected unless
    `warnings` actually names one (see `_challenge_label`), and never
    pattern-matches `warnings` text to detect an incomplete resume
    attachment (see the typed `resume_attachment_incomplete` field instead).
    """
    challenge = _challenge_label(result.warnings)
    if challenge is not None:
        return f"{challenge} was detected in the browser."
    if result.resume_attachment_incomplete:
        reason = "the required resume attachment could not be confirmed; attach it manually before submitting."
        if result.resume_variant:
            variant_note = f"Selected resume variant: {result.resume_variant}"
            if result.resume_file:
                variant_note += f" ({result.resume_file})"
            reason = f"{reason} {variant_note}."
        return reason
    return "manual action is required before this application can be submitted."


_REVIEW_DONE_RESPONSE_TEXT: dict[ReviewSessionCloseOutcome, str] = {
    ReviewSessionCloseOutcome.CLOSED: "Closing the browser now.",
    ReviewSessionCloseOutcome.ALREADY_CLOSED: "Already closed.",
    ReviewSessionCloseOutcome.NOT_FOUND: "This review session is no longer available.",
}


def format_review_done_response_text(outcome: ReviewSessionCloseOutcome) -> str:
    """Text for the callback-query toast answering a 'Done reviewing' tap."""
    return _REVIEW_DONE_RESPONSE_TEXT.get(outcome, _REVIEW_DONE_RESPONSE_TEXT[ReviewSessionCloseOutcome.NOT_FOUND])


REVIEW_ENDED_NOTICE = (
    "Review finished. Submission was not verified by this app. "
    "Confirm it manually before marking Applied."
)


def format_review_done_terminal_text(original_text: str, outcome: ReviewSessionCloseOutcome) -> str:
    """Text for editing the completion message once 'Done reviewing' is tapped.

    Preserves the original preparation summary, states plainly that review
    ended and the application was not confirmed submitted, and never claims
    or implies the manual Submit succeeded. Used with an empty button list so
    the (now inactive) 'Done reviewing' button is removed from the message.
    """
    _ = outcome
    lines = [
        "Manual review finished." if line == "Manual review needed." else line
        for line in (original_text or "").rstrip().splitlines()
    ]
    base = "\n".join(lines).rstrip()
    if REVIEW_ENDED_NOTICE in base:
        return base
    return f"{base}\n\n{REVIEW_ENDED_NOTICE}" if base else REVIEW_ENDED_NOTICE


class TelegramPrepareState(StrEnum):
    STARTING = "starting"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class RecommendationLookup(Protocol):
    def get_by_identity(self, source: str, external_id: str) -> object | None: ...


@dataclass(frozen=True)
class TelegramPrepareOutcome:
    state: TelegramPrepareState
    text: str
    source: str
    external_id: str
    recommendation: str
    prepare_result: PrepareApplicationResult | None = None


class ExplicitPrepareRunner(Protocol):
    def prepare(
        self,
        vacancy: RecommendedVacancy,
        *,
        intent: PrepareIntent = PrepareIntent.AUTONOMOUS,
        keep_open: bool = True,
    ) -> PrepareApplicationResult: ...


def resolve_recommendation(
    lookup: RecommendationLookup | TargetCompanyAnalysisCache,
    source: str,
    external_id: str,
) -> str:
    record = lookup.get_by_identity(source, external_id)
    if record is None:
        return UNKNOWN_RECOMMENDATION
    recommendation = getattr(record, "recommendation", None)
    label = getattr(recommendation, "label", recommendation)
    cleaned = str(label or "").strip().upper()
    return cleaned or UNKNOWN_RECOMMENDATION


def run_explicit_application_prepare(
    *,
    source: str,
    external_id: str,
    prepare_service: ExplicitPrepareRunner,
    recommendation_lookup: RecommendationLookup | TargetCompanyAnalysisCache,
    keep_open: bool = True,
) -> TelegramPrepareOutcome:
    """Resolve identity + recommendation and delegate to the application layer."""
    cleaned_source = str(source or "").strip()
    cleaned_id = str(external_id or "").strip()
    if not cleaned_source or not cleaned_id or not source_supports_application_prepare(cleaned_source):
        return TelegramPrepareOutcome(
            state=TelegramPrepareState.FAILED,
            text=format_application_prepare_failed_text(),
            source=cleaned_source,
            external_id=cleaned_id,
            recommendation=UNKNOWN_RECOMMENDATION,
        )

    recommendation = resolve_recommendation(recommendation_lookup, cleaned_source, cleaned_id)
    vacancy = RecommendedVacancy.create(cleaned_source, cleaned_id, recommendation)
    try:
        result = prepare_service.prepare(
            vacancy,
            intent=PrepareIntent.EXPLICIT,
            keep_open=keep_open,
        )
    except Exception:
        return TelegramPrepareOutcome(
            state=TelegramPrepareState.FAILED,
            text=format_application_prepare_failed_text(),
            source=cleaned_source,
            external_id=cleaned_id,
            recommendation=recommendation,
        )
    return outcome_from_prepare_result(result)


def outcome_from_prepare_result(result: PrepareApplicationResult) -> TelegramPrepareOutcome:
    if result.blocked:
        return TelegramPrepareOutcome(
            state=TelegramPrepareState.BLOCKED,
            text=format_application_prepare_blocked_text(result.blocked_reason or ""),
            source=result.source,
            external_id=result.external_id,
            recommendation=result.recommendation,
            prepare_result=result,
        )
    autofill = result.autofill
    if autofill is None or autofill.status is AutofillStatus.FAILED:
        return TelegramPrepareOutcome(
            state=TelegramPrepareState.FAILED,
            text=format_application_prepare_failed_text(autofill),
            source=result.source,
            external_id=result.external_id,
            recommendation=result.recommendation,
            prepare_result=result,
        )
    return TelegramPrepareOutcome(
        state=TelegramPrepareState.COMPLETED,
        text=format_application_prepare_completed_text(autofill),
        source=result.source,
        external_id=result.external_id,
        recommendation=result.recommendation,
        prepare_result=result,
    )
