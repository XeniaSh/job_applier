from __future__ import annotations

from pathlib import Path

from app.application.autofill.models import AutofillStatus, stage1_autofill_result
from app.application.prepare_application import (
    BLOCKED_ALREADY_APPLIED,
    BLOCKED_CHECK_MANUALLY,
    BLOCKED_SKIP,
    BLOCKED_UNKNOWN,
    HISTORY_STATUS_APPLIED,
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
from app.storage.telegram_delivery import STATUS_APPLIED, TelegramDeliveryStorage


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


class _FakeLifecycle:
    def __init__(
        self,
        status: str | None = None,
        statuses: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.default = status
        self.statuses = statuses or {}
        self.calls: list[tuple[str, str]] = []

    def get_history_status(self, source: str, external_id: str) -> str | None:
        self.calls.append((source, external_id))
        if (source, external_id) in self.statuses:
            return self.statuses[(source, external_id)]
        return self.default


def _vacancy(
    recommendation: str | ApplicationRecommendation,
    external_id: str = "100",
    source: str = "target_company:greenhouse:example",
) -> RecommendedVacancy:
    return RecommendedVacancy.create(source, external_id, recommendation)


def test_apply_now_not_applied_enters_prepare_autofill_orchestration() -> None:
    autofill = _FakeAutofill()
    lifecycle = _FakeLifecycle()
    result = prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        lifecycle=lifecycle,
        keep_open=False,
    )
    assert result.allowed is True
    assert result.blocked is False
    assert result.blocked_reason is None
    assert lifecycle.calls == [("target_company:greenhouse:example", "100")]
    assert autofill.calls == [("target_company:greenhouse:example", "100", False)]
    assert result.autofill is not None
    assert result.autofill.status is AutofillStatus.READY_FOR_REVIEW
    assert result.autofill.submit_performed is False


def test_check_manually_does_not_auto_proceed() -> None:
    autofill = _FakeAutofill()
    result = PrepareApplicationService(autofill, _FakeLifecycle()).prepare(
        _vacancy(RECOMMENDATION_CHECK_MANUALLY),
        intent=PrepareIntent.AUTONOMOUS,
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_CHECK_MANUALLY
    assert result.autofill is None
    assert autofill.calls == []


def test_check_manually_explicit_not_applied_can_proceed() -> None:
    autofill = _FakeAutofill()
    recommendation = ApplicationRecommendation(
        label=RECOMMENDATION_CHECK_MANUALLY,
        reasons=["seniority STAFF_PLUS is stretch level"],
    )
    result = prepare_application(
        _vacancy(recommendation),
        intent=PrepareIntent.EXPLICIT,
        autofill=autofill,
        lifecycle=_FakeLifecycle(status="SENT"),
    )
    assert result.allowed is True
    assert result.recommendation == RECOMMENDATION_CHECK_MANUALLY
    assert autofill.calls == [("target_company:greenhouse:example", "100", True)]
    assert result.autofill is not None
    assert result.autofill.submit_performed is False


def test_skip_is_blocked_from_normal_and_explicit_preparation() -> None:
    autofill = _FakeAutofill()
    service = PrepareApplicationService(autofill, _FakeLifecycle())
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
        lifecycle=_FakeLifecycle(),
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_UNKNOWN
    assert autofill.calls == []


def test_apply_now_explicit_intent_also_delegates() -> None:
    autofill = _FakeAutofill()
    result = PrepareApplicationService(autofill, _FakeLifecycle()).prepare(
        _vacancy(RECOMMENDATION_APPLY_NOW),
        intent=PrepareIntent.EXPLICIT,
        keep_open=False,
    )
    assert result.allowed is True
    assert len(autofill.calls) == 1


def test_applied_apply_now_is_blocked_and_does_not_reach_autofill() -> None:
    autofill = _FakeAutofill()
    result = PrepareApplicationService(
        autofill,
        _FakeLifecycle(status=HISTORY_STATUS_APPLIED),
    ).prepare(_vacancy(RECOMMENDATION_APPLY_NOW), intent=PrepareIntent.AUTONOMOUS)
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_ALREADY_APPLIED
    assert result.autofill is None
    assert autofill.calls == []


def test_applied_check_manually_explicit_is_blocked() -> None:
    autofill = _FakeAutofill()
    result = prepare_application(
        _vacancy(RECOMMENDATION_CHECK_MANUALLY),
        intent=PrepareIntent.EXPLICIT,
        autofill=autofill,
        lifecycle=_FakeLifecycle(status=HISTORY_STATUS_APPLIED),
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCKED_ALREADY_APPLIED
    assert autofill.calls == []


def test_lifecycle_identity_is_source_and_external_id() -> None:
    autofill = _FakeAutofill()
    lifecycle = _FakeLifecycle(
        statuses={
            ("target_company:greenhouse:adyen", "7342887"): HISTORY_STATUS_APPLIED,
            ("target_company:greenhouse:other", "7342887"): "SENT",
        }
    )
    blocked = prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW, "7342887", "target_company:greenhouse:adyen"),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        lifecycle=lifecycle,
        keep_open=False,
    )
    allowed = prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW, "7342887", "target_company:greenhouse:other"),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        lifecycle=lifecycle,
        keep_open=False,
    )
    assert blocked.allowed is False
    assert blocked.blocked_reason == BLOCKED_ALREADY_APPLIED
    assert allowed.allowed is True
    assert autofill.calls == [("target_company:greenhouse:other", "7342887", False)]


def test_non_applied_history_statuses_do_not_block(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    autofill = _FakeAutofill()
    service = PrepareApplicationService(autofill, storage)
    for status, external_id in (
        ("FOUND", "found-1"),
        ("SENT", "sent-1"),
        ("PREPARED", "prepared-1"),
        ("SKIPPED", "skipped-1"),
    ):
        storage.upsert_application_history(
            source="target_company:greenhouse:example",
            external_id=external_id,
            title="Role",
            company="Example",
            location=None,
            url=None,
            decision="STRONG_MATCH",
            decision_reason=None,
            recommended_resume=None,
        )
        timestamp = {
            "SENT": "sent_at",
            "PREPARED": "prepared_at",
            "SKIPPED": "skipped_at",
        }.get(status)
        storage.mark_history_status(
            source="target_company:greenhouse:example",
            external_id=external_id,
            status=status,
            timestamp_field=timestamp,
        )
        result = service.prepare(
            _vacancy(RECOMMENDATION_APPLY_NOW, external_id),
            intent=PrepareIntent.AUTONOMOUS,
            keep_open=False,
        )
        assert result.allowed is True, status
    assert len(autofill.calls) == 4


def test_real_history_lookup_blocks_applied_only_for_matching_source(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    storage.upsert_application_history(
        source="target_company:greenhouse:adyen",
        external_id="7342887",
        title="Software Engineer (Java) - Unified Platform",
        company="Adyen",
        location="Amsterdam",
        url="https://job-boards.greenhouse.io/adyen/jobs/7342887",
        decision="STRONG_MATCH",
        decision_reason="test fixture; not a live run",
        recommended_resume="java",
    )
    storage.mark_history_status(
        source="target_company:greenhouse:adyen",
        external_id="7342887",
        status=STATUS_APPLIED,
        timestamp_field="applied_at",
    )
    autofill = _FakeAutofill()
    service = PrepareApplicationService(autofill, storage)
    blocked = service.prepare(
        _vacancy(RECOMMENDATION_APPLY_NOW, "7342887", "target_company:greenhouse:adyen"),
        intent=PrepareIntent.EXPLICIT,
        keep_open=False,
    )
    other_source = service.prepare(
        _vacancy(RECOMMENDATION_APPLY_NOW, "7342887", "target_company:greenhouse:other"),
        intent=PrepareIntent.AUTONOMOUS,
        keep_open=False,
    )
    assert blocked.allowed is False
    assert blocked.blocked_reason == BLOCKED_ALREADY_APPLIED
    assert other_source.allowed is True
    assert autofill.calls == [("target_company:greenhouse:other", "7342887", False)]


def test_gate_helpers_match_product_rules() -> None:
    assert can_prepare_application(RECOMMENDATION_APPLY_NOW, PrepareIntent.AUTONOMOUS) is True
    assert can_prepare_application(RECOMMENDATION_CHECK_MANUALLY, PrepareIntent.AUTONOMOUS) is False
    assert can_prepare_application(RECOMMENDATION_CHECK_MANUALLY, PrepareIntent.EXPLICIT) is True
    assert can_prepare_application(RECOMMENDATION_SKIP, PrepareIntent.EXPLICIT) is False
    assert (
        can_prepare_application(
            RECOMMENDATION_APPLY_NOW,
            PrepareIntent.AUTONOMOUS,
            history_status=HISTORY_STATUS_APPLIED,
        )
        is False
    )
    assert (
        can_prepare_application(
            RECOMMENDATION_CHECK_MANUALLY,
            PrepareIntent.EXPLICIT,
            history_status=HISTORY_STATUS_APPLIED,
        )
        is False
    )


def test_orchestration_does_not_import_greenhouse_or_playwright() -> None:
    text = Path("app/application/prepare_application.py").read_text(encoding="utf-8")
    assert "from app.application.autofill.service" not in text
    assert "from app.application.autofill.greenhouse" not in text
    assert "from app.application.autofill.cli" not in text
    assert "import playwright" not in text
    assert "GreenhouseAdapter" not in text
    assert "sqlite3" not in text


def test_orchestration_delegates_to_injected_autofill_runner() -> None:
    """The product path must reuse AutofillService.run, not a second ATS fill."""
    autofill = _FakeAutofill()
    prepare_application(
        _vacancy(RECOMMENDATION_APPLY_NOW, "1"),
        intent=PrepareIntent.AUTONOMOUS,
        autofill=autofill,
        lifecycle=_FakeLifecycle(),
        keep_open=False,
    )
    assert autofill.calls == [("target_company:greenhouse:example", "1", False)]
