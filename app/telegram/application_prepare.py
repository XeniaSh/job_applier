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

from app.application.autofill.models import AutofillResult, AutofillStatus
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


def format_application_prepare_blocked_text(reason: str) -> str:
    cleaned = " ".join(str(reason or "").split())
    return cleaned or "Preparation blocked."


def format_application_prepare_failed_text() -> str:
    return FAILED_TEXT


def format_application_prepare_completed_text(result: AutofillResult) -> str:
    lines = [
        "Preparation completed.",
        f"Filled fields: {len(result.filled_fields)}",
        f"Unresolved required fields: {len(result.unresolved_required_fields)}",
    ]
    if result.status in {
        AutofillStatus.READY_FOR_REVIEW,
        AutofillStatus.NEEDS_MANUAL_INTERVENTION,
    }:
        lines.append("Manual review needed.")
    lines.append("Submit was not performed.")
    return "\n".join(lines)


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
            text=format_application_prepare_failed_text(),
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
