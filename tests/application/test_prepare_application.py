from __future__ import annotations

from pathlib import Path

from app.application.autofill.models import AutofillStatus, stage1_autofill_result
from app.application.prepare_application import (
    BLOCKED_CHECK_MANUALLY,
    BLOCKED_SKIP,
    BLOCKED_UNKNOWN,
    PrepareApplicationService,
    PrepareIntent,
    RecommendedVacancy,
    can_prepare_application,
    prepare_application,
)
from app.company_watch.application_recommendation import (
    RECOMMENDATION_APPLY_NOW,
    RECOMMENDATION_CHECK_MANUALLY,
    RECOMMENDATION_SKIP,
    ApplicationRecommendation,
)


class _FakeAutofill:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def run(self, source: str, external_id: str, *, keep_open: bool = True):
        self.calls.append((source, external_id, keep_open))
        return stage1_autofill_result(
            source=source,
            external_id=external_id,
            application_url="https://job-boards.greenhouse.io/example/jobs/1",
            status=AutofillStatus.READY_FOR_REVIEW,
            resume_uploaded=True,
        )


def _vacancy(recommendation: str | ApplicationRecommendation, external_id: str = "100") -> RecommendedVacancy:
    return RecommendedVacancy.create(
        "target_company:greenhouse:example",
        external_id,
        recommendation,
    )


def test_apply_now_enters_prepare_autofill_orchestration() -> None:
    autofill = _FakeAutofill()
    result = prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        keep_open=False,
    )
    assert result.allowed is True
    assert result.blocked is False
    assert result.blocked_reason is None
    assert autofill.calls == [("target_company:greenhouse:example", "100", False)]
    assert result.autofill is not None
    assert result.autofill.status is AutofillStatus.READY_FOR_REVIEW
    assert result.autofill.submit_performed is False


def test_check_manually_does_not_auto_proceed() -> None:
    autofill = _FakeAutofill()
    result = PrepareApplicationService(autofill).prepare(
        _vacancy(RECOMMENDATION_CHECK_MANUALLY),
        intent=PrepareIntent.AUTONOMOUS,
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_CHECK_MANUALLY
    assert result.autofill is None
    assert autofill.calls == []


def test_check_manually_can_proceed_with_explicit_intent() -> None:
    autofill = _FakeAutofill()
    recommendation = ApplicationRecommendation(
        label=RECOMMENDATION_CHECK_MANUALLY,
        reasons=["seniority STAFF_PLUS is stretch level"],
    )
    result = prepare_application(
        _vacancy(recommendation),
        intent=PrepareIntent.EXPLICIT,
        autofill=autofill,
    )
    assert result.allowed is True
    assert result.recommendation == RECOMMENDATION_CHECK_MANUALLY
    assert autofill.calls == [("target_company:greenhouse:example", "100", True)]
    assert result.autofill is not None
    assert result.autofill.submit_performed is False


def test_skip_is_blocked_from_normal_and_explicit_preparation() -> None:
    autofill = _FakeAutofill()
    service = PrepareApplicationService(autofill)
    autonomous = service.prepare(_vacancy(RECOMMENDATION_SKIP), intent=PrepareIntent.AUTONOMOUS)
    explicit = service.prepare(_vacancy(RECOMMENDATION_SKIP), intent=PrepareIntent.EXPLICIT)
    assert autonomous.allowed is False
    assert explicit.allowed is False
    assert autonomous.blocked_reason == BLOCKED_SKIP
    assert explicit.blocked_reason == BLOCKED_SKIP
    assert autofill.calls == []


def test_unknown_recommendation_is_blocked() -> None:
    autofill = _FakeAutofill()
    result = prepare_application(
        _vacancy("STRONG_MATCH"),
        intent=PrepareIntent.EXPLICIT,
        autofill=autofill,
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_UNKNOWN
    assert autofill.calls == []


def test_apply_now_explicit_intent_also_delegates() -> None:
    autofill = _FakeAutofill()
    result = PrepareApplicationService(autofill).prepare(
        _vacancy(RECOMMENDATION_APPLY_NOW),
        intent=PrepareIntent.EXPLICIT,
        keep_open=False,
    )
    assert result.allowed is True
    assert len(autofill.calls) == 1


def test_gate_helpers_match_product_rules() -> None:
    assert can_prepare_application(RECOMMENDATION_APPLY_NOW, PrepareIntent.AUTONOMOUS) is True
    assert can_prepare_application(RECOMMENDATION_CHECK_MANUALLY, PrepareIntent.AUTONOMOUS) is False
    assert can_prepare_application(RECOMMENDATION_CHECK_MANUALLY, PrepareIntent.EXPLICIT) is True
    assert can_prepare_application(RECOMMENDATION_SKIP, PrepareIntent.EXPLICIT) is False


def test_orchestration_does_not_import_greenhouse_or_playwright() -> None:
    text = Path("app/application/prepare_application.py").read_text(encoding="utf-8")
    assert "from app.application.autofill.service" not in text
    assert "from app.application.autofill.greenhouse" not in text
    assert "from app.application.autofill.cli" not in text
    assert "import playwright" not in text
    assert "GreenhouseAdapter" not in text


def test_orchestration_delegates_to_injected_autofill_runner() -> None:
    """The product path must reuse AutofillService.run, not a second ATS fill."""
    autofill = _FakeAutofill()
    prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW, "1"),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        keep_open=False,
    )
    assert autofill.calls == [("target_company:greenhouse:example", "1", False)]
