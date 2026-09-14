"""Product orchestration: recommended vacancy → AutofillService.

This layer owns recommendation gating and application-lifecycle gating.
AutofillService still owns browser autofill. Greenhouse still owns form
discovery, interaction, and read-back. Telegram must call this service; it
must not reimplement the gates.

The diagnostic CLI `python -m app autofill SOURCE EXTERNAL_ID` must not use
this module; it remains an ungated smoke-test path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.application.autofill.models import AutofillResult
from app.company_watch.application_recommendation import (
    RECOMMENDATION_APPLY_NOW,
    RECOMMENDATION_CHECK_MANUALLY,
    RECOMMENDATION_SKIP,
    ApplicationRecommendation,
    recommendation_label,
)


class PrepareIntent(StrEnum):
    """Who is requesting application preparation."""

    AUTONOMOUS = "autonomous"
    EXPLICIT = "explicit"


BLOCKED_SKIP = "SKIP vacancies cannot enter prepare/autofill."
BLOCKED_CHECK_MANUALLY = (
    "CHECK_MANUALLY vacancies require an explicit user action "
    "and cannot proceed autonomously."
)
BLOCKED_UNKNOWN = "Unknown recommendation cannot enter prepare/autofill."
BLOCKED_ALREADY_APPLIED = "Application already submitted"

HISTORY_STATUS_APPLIED = "APPLIED"


class AutofillRunner(Protocol):
    """Existing AutofillService.run contract. No second autofill implementation."""

    def run(self, source: str, external_id: str, *, keep_open: bool = True) -> AutofillResult: ...


class ApplicationLifecycleLookup(Protocol):
    """Read canonical application_history status by vacancy identity."""

    def get_history_status(self, source: str, external_id: str) -> str | None:
        """Return current_status for (source, external_id), or None if unseen."""


@dataclass(frozen=True)
class RecommendedVacancy:
    source: str
    external_id: str
    recommendation: str

    @classmethod
    def create(
        cls,
        source: str,
        external_id: str,
        recommendation: ApplicationRecommendation | str,
    ) -> RecommendedVacancy:
        return cls(
            source=str(source).strip(),
            external_id=str(external_id).strip(),
            recommendation=recommendation_label(recommendation),
        )


@dataclass(frozen=True)
class PrepareApplicationResult:
    source: str
    external_id: str
    recommendation: str
    intent: PrepareIntent
    allowed: bool
    blocked_reason: str | None = None
    autofill: AutofillResult | None = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


def prepare_gate_reason(
    recommendation: ApplicationRecommendation | str,
    intent: PrepareIntent,
) -> str | None:
    """Return a blocked reason, or None when prepare/autofill may proceed."""
    label = recommendation_label(recommendation)
    if label == RECOMMENDATION_SKIP:
        return BLOCKED_SKIP
    if label == RECOMMENDATION_APPLY_NOW:
        return None
    if label == RECOMMENDATION_CHECK_MANUALLY:
        if intent is PrepareIntent.EXPLICIT:
            return None
        return BLOCKED_CHECK_MANUALLY
    return BLOCKED_UNKNOWN


def prepare_lifecycle_gate_reason(history_status: str | None) -> str | None:
    """Block only a known successful submission. Other statuses are not gated here."""
    if history_status == HISTORY_STATUS_APPLIED:
        return BLOCKED_ALREADY_APPLIED
    return None


def can_prepare_application(
    recommendation: ApplicationRecommendation | str,
    intent: PrepareIntent,
    *,
    history_status: str | None = None,
) -> bool:
    return (
        prepare_lifecycle_gate_reason(history_status) is None
        and prepare_gate_reason(recommendation, intent) is None
    )


class PrepareApplicationService:
    """Gate on lifecycle then recommendation, then delegate to AutofillService."""

    def __init__(self, autofill: AutofillRunner, lifecycle: ApplicationLifecycleLookup) -> None:
        self._autofill = autofill
        self._lifecycle = lifecycle

    def prepare(
        self,
        vacancy: RecommendedVacancy,
        *,
        intent: PrepareIntent = PrepareIntent.AUTONOMOUS,
        keep_open: bool = True,
    ) -> PrepareApplicationResult:
        return prepare_application(
            vacancy,
            intent=intent,
            autofill=self._autofill,
            lifecycle=self._lifecycle,
            keep_open=keep_open,
        )


def prepare_application(
    vacancy: RecommendedVacancy,
    *,
    intent: PrepareIntent,
    autofill: AutofillRunner,
    lifecycle: ApplicationLifecycleLookup,
    keep_open: bool = True,
) -> PrepareApplicationResult:
    """Prepare an already recommended vacancy via AutofillService.

    APPLIED vacancies are blocked for every recommendation and intent.
    SENT / FOUND / PREPARED / SKIPPED / missing history do not block here.

    Then recommendation:
    AUTONOMOUS: APPLY_NOW only.
    EXPLICIT: APPLY_NOW or CHECK_MANUALLY (user/manual action).
    SKIP is blocked for both product intents.
    """
    history_status = lifecycle.get_history_status(vacancy.source, vacancy.external_id)
    reason = prepare_lifecycle_gate_reason(history_status)
    if reason is None:
        reason = prepare_gate_reason(vacancy.recommendation, intent)
    if reason is not None:
        return PrepareApplicationResult(
            source=vacancy.source,
            external_id=vacancy.external_id,
            recommendation=vacancy.recommendation,
            intent=intent,
            allowed=False,
            blocked_reason=reason,
        )
    result = autofill.run(vacancy.source, vacancy.external_id, keep_open=keep_open)
    return PrepareApplicationResult(
        source=vacancy.source,
        external_id=vacancy.external_id,
        recommendation=vacancy.recommendation,
        intent=intent,
        allowed=True,
        autofill=result,
    )
