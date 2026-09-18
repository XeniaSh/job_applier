from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.cli as cli_module
from app.application.autofill.models import (
    AutofillFailureReason,
    AutofillFieldResult,
    AutofillStatus,
    FieldClassification,
    stage1_autofill_result,
)
from app.application.autofill.review_session import default_review_registry
from app.application.prepare_application import (
    BLOCKED_ALREADY_APPLIED,
    BLOCKED_SKIP,
    BLOCKED_UNKNOWN,
    PrepareApplicationService,
    PrepareIntent,
    RecommendedVacancy,
)
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.application_recommendation import (
    RECOMMENDATION_APPLY_NOW,
    RECOMMENDATION_CHECK_MANUALLY,
    RECOMMENDATION_SKIP,
    ApplicationRecommendation,
)
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)
from app.storage.telegram_delivery import STATUS_APPLIED, TelegramDeliveryStorage
from app.telegram.application_prepare import (
    FAILED_TEXT,
    STARTING_TEXT,
    TelegramPrepareState,
    format_application_prepare_failed_text,
    run_explicit_application_prepare,
)
from app.telegram.client import (
    APPLICATION_PREPARE_ACTION,
    APPLICATION_PREPARE_BUTTON_TEXT,
    TelegramRequestError,
    build_action_buttons,
    parse_callback_data,
    parse_review_done_session_id,
)
from app.telegram.models import TelegramMessageRef


SOURCE = "target_company:greenhouse:agoda"
EXTERNAL_ID = "6886113"
URL = "https://job-boards.greenhouse.io/agoda/jobs/6886113"


class _FakeAutofill:
    def __init__(
        self,
        *,
        status: AutofillStatus = AutofillStatus.READY_FOR_REVIEW,
        failure_reason: AutofillFailureReason | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.status = status
        self.failure_reason = failure_reason

    def run(self, source: str, external_id: str, *, keep_open: bool = True):
        self.calls.append((source, external_id, keep_open))
        return stage1_autofill_result(
            source=source,
            external_id=external_id,
            application_url=URL,
            status=self.status,
            failure_reason=self.failure_reason,
            filled_fields=[
                AutofillFieldResult(
                    label="First name",
                    classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                ),
                AutofillFieldResult(
                    label="Email",
                    classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                ),
            ],
            unresolved_required_fields=[
                AutofillFieldResult(
                    label="Why this role?",
                    classification=FieldClassification.UNKNOWN_REQUIRED,
                    required=True,
                )
            ],
            resume_uploaded=True,
        )


class _RecordingPrepareService:
    def __init__(self, inner: PrepareApplicationService) -> None:
        self.inner = inner
        self.calls: list[tuple[RecommendedVacancy, PrepareIntent, bool]] = []

    def prepare(self, vacancy: RecommendedVacancy, *, intent: PrepareIntent, keep_open: bool = True):
        self.calls.append((vacancy, intent, keep_open))
        return self.inner.prepare(vacancy, intent=intent, keep_open=keep_open)


class _ExplodingPrepareService:
    def prepare(self, vacancy: RecommendedVacancy, *, intent: PrepareIntent, keep_open: bool = True):
        _ = vacancy, intent, keep_open
        raise RuntimeError("prepare exploded")


class _FakeClient:
    def __init__(self) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.texts: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.answers.append((callback_query_id, text))

    def send_text_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        buttons: list | None = None,
    ):
        self.texts.append(
            {
                "text": text,
                "chat_id": chat_id,
                "message_id": 99,
                "reply_to": reply_to_message_id,
                "buttons": buttons,
            }
        )
        return TelegramMessageRef(chat_id=str(chat_id or "222"), message_id=99)

    def edit_message_text(self, **kwargs) -> None:
        self.edits.append(kwargs)

    def edit_message_reply_markup(self, **kwargs) -> None:
        _ = kwargs


class _EditFailingClient(_FakeClient):
    """Client whose edit_message_text always fails, for edit-failure tests."""

    def edit_message_text(self, **kwargs) -> None:
        self.edits.append(kwargs)
        raise TelegramRequestError("boom", method="editMessageText")


class _TerminalEditFailingClient(_FakeClient):
    """Client whose edit_message_text fails only for button-clearing edits.

    The review-ready button-attach edit (which always carries a non-empty
    `buttons` list) still succeeds, so the handoff itself completes
    normally; only the later "Done reviewing" terminal edit (which always
    clears the button, i.e. `buttons == []`) fails.
    """

    def edit_message_text(self, **kwargs) -> None:
        self.edits.append(kwargs)
        if not kwargs.get("buttons"):
            raise TelegramRequestError("boom", method="editMessageText")


class _UnexpectedAttachEditFailingClient(_FakeClient):
    """Client whose edit_message_text raises a non-Telegram exception, but
    only for the button-attach edit (buttons non-empty). The later
    best-effort terminal edit (buttons == []) still succeeds, so the
    recovery path (edit the already-sent message instead of sending a
    second one) can be observed.
    """

    def edit_message_text(self, **kwargs) -> None:
        self.edits.append(kwargs)
        if kwargs.get("buttons"):
            raise RuntimeError("unexpected non-Telegram failure while attaching button")


class _FlakySendOnceClient(_FakeClient):
    """Client whose first `send_text_message` call fails with a transient,
    definitely-undelivered error before succeeding, for retry-recovery
    tests. Defaults to a 429; pass `error=` for a different definitely-
    undelivered failure (e.g. a connection failure)."""

    def __init__(self, *, error: TelegramRequestError | None = None) -> None:
        super().__init__()
        self._send_failures_remaining = 1
        self._error = error or TelegramRequestError(
            "Too Many Requests",
            http_status=429,
            error_code=429,
            description="Too Many Requests: retry after 1",
        )

    def send_text_message(self, text, *, chat_id=None, reply_to_message_id=None, buttons=None):
        if self._send_failures_remaining > 0:
            self._send_failures_remaining -= 1
            raise self._error
        return super().send_text_message(
            text, chat_id=chat_id, reply_to_message_id=reply_to_message_id, buttons=buttons
        )


class _AlwaysFailSendClient(_FakeClient):
    """Client whose `send_text_message` always fails with a definitely-
    undelivered error, for permanent-failure tests. Tracks the number of
    attempts so a test can confirm the bounded retry budget was exhausted
    and nothing tried again afterwards."""

    def __init__(self, *, error: TelegramRequestError | None = None) -> None:
        super().__init__()
        self.send_attempts = 0
        self._error = error or TelegramRequestError(
            "Too Many Requests",
            http_status=429,
            error_code=429,
            description="Too Many Requests: retry after 1",
        )

    def send_text_message(self, text, *, chat_id=None, reply_to_message_id=None, buttons=None):
        self.send_attempts += 1
        raise self._error


def _vacancy(*, description: str = "Java backend services") -> NormalizedVacancy:
    return NormalizedVacancy(
        source=SOURCE,
        external_id=EXTERNAL_ID,
        title="Java Backend Engineer",
        company="Agoda",
        location="Bangkok",
        employment="Full-time",
        description=description,
        url=URL,
        published_at="2026-09-05T10:00:00Z",
    )


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.STRONG_MATCH,
        summary="Strong Java backend role",
        decision_reason="Java backend match",
        matched_points=["java"],
        match_percentage=86.0,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _feasibility() -> ApplicationFeasibility:
    return ApplicationFeasibility(
        label="UNCLEAR",
        visa_sponsorship="unknown",
        relocation_support="unknown",
        remote_type="unknown",
        work_authorization_requirement="unknown",
        language_requirements=[],
        location_restrictions=[],
        warnings=[],
    )


def _seed_cache(
    path: Path,
    *,
    recommendation: str,
    vacancy: NormalizedVacancy | None = None,
) -> TargetCompanyAnalysisCache:
    cache = TargetCompanyAnalysisCache(path)
    cache.put(
        vacancy or _vacancy(),
        evaluation=_evaluation(),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label=recommendation, reasons=["test"]),
        seniority=SeniorityClassification(label="SENIOR", reasons=["title has senior"]),
    )
    cache.save()
    loaded = TargetCompanyAnalysisCache(path)
    loaded.load()
    return loaded


def _callback_update(*, chat_id: str = "222", data: str | None = None) -> dict:
    return {
        "callback_query": {
            "id": "cb-prep",
            "data": data or f"{APPLICATION_PREPARE_ACTION}:tcg.agoda:{EXTERNAL_ID}",
            "message": {
                "chat": {"id": chat_id},
                "message_id": 50,
                "text": "Java Backend Engineer\nAgoda",
                "reply_markup": {"inline_keyboard": [[{"text": "Open vacancy", "url": URL}]]},
            },
        }
    }


def _process(
    *,
    tmp_path: Path,
    recommendation: str = RECOMMENDATION_APPLY_NOW,
    autofill: _FakeAutofill | None = None,
    history_status: str | None = None,
    prepare_service=None,
    data: str | None = None,
    chat_id: str = "222",
    allowed_chat_ids: frozenset[str] | None = None,
    cache: TargetCompanyAnalysisCache | None = None,
    client: _FakeClient | None = None,
) -> tuple[_FakeClient, _FakeAutofill, object, TelegramDeliveryStorage]:
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    if history_status is not None:
        storage.upsert_application_history(
            source=SOURCE,
            external_id=EXTERNAL_ID,
            title="Java Backend Engineer",
            company="Agoda",
            location="Bangkok",
            url=URL,
            decision="STRONG_MATCH",
            decision_reason="Java backend match",
            recommended_resume="java",
        )
        storage.mark_history_status(
            source=SOURCE,
            external_id=EXTERNAL_ID,
            status=history_status,
            timestamp_field="applied_at" if history_status == STATUS_APPLIED else None,
        )
    runner = autofill if autofill is not None else _FakeAutofill()
    inner = PrepareApplicationService(runner, storage)
    service = prepare_service if prepare_service is not None else _RecordingPrepareService(inner)
    lookup = cache if cache is not None else _seed_cache(tmp_path / "cache.json", recommendation=recommendation)
    client = client if client is not None else _FakeClient()
    cli_module._process_callback_update(
        update=_callback_update(chat_id=chat_id, data=data),
        client=client,
        storage=storage,
        configured_chat_id=chat_id,
        allowed_chat_ids=allowed_chat_ids or frozenset({chat_id}),
        application_prepare_service=service,
        application_prepare_cache=lookup,
        application_prepare_sync=True,
    )
    return client, runner, service, storage


def test_eligible_target_company_message_exposes_prepare_application() -> None:
    buttons = build_action_buttons(SOURCE, EXTERNAL_ID, URL)
    assert buttons[0][0].text == APPLICATION_PREPARE_BUTTON_TEXT
    assert buttons[0][0].callback_data == f"{APPLICATION_PREPARE_ACTION}:tcg.agoda:{EXTERNAL_ID}"
    parsed = parse_callback_data(buttons[0][0].callback_data)
    assert parsed == (APPLICATION_PREPARE_ACTION, SOURCE, EXTERNAL_ID, None)
    assert len(buttons[0][0].callback_data.encode("utf-8")) <= 64


def test_callback_resolves_vacancy_and_recommendation_without_in_memory_object(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    _seed_cache(cache_path, recommendation=RECOMMENDATION_APPLY_NOW)
    restarted = TargetCompanyAnalysisCache(cache_path)
    restarted.load()
    autofill = _FakeAutofill()
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    service = _RecordingPrepareService(PrepareApplicationService(autofill, storage))
    outcome = run_explicit_application_prepare(
        source=SOURCE,
        external_id=EXTERNAL_ID,
        prepare_service=service,
        recommendation_lookup=restarted,
        keep_open=True,
    )
    vacancy, intent, keep_open = service.calls[0]
    assert vacancy.source == SOURCE
    assert vacancy.external_id == EXTERNAL_ID
    assert vacancy.recommendation == RECOMMENDATION_APPLY_NOW
    assert intent is PrepareIntent.EXPLICIT
    assert keep_open is True
    assert outcome.state is TelegramPrepareState.COMPLETED
    assert autofill.calls == [(SOURCE, EXTERNAL_ID, True)]
    assert "Filled fields: 2" in outcome.text
    assert "Unresolved required fields: 1" in outcome.text
    assert "Manual review needed." in outcome.text


def test_apply_now_explicit_prepare_succeeds_via_callback(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(tmp_path=tmp_path, recommendation=RECOMMENDATION_APPLY_NOW)
    assert client.answers == [("cb-prep", STARTING_TEXT)]
    assert service.calls[0][1] is PrepareIntent.EXPLICIT
    assert autofill.calls == [(SOURCE, EXTERNAL_ID, True)]
    assert client.texts[0]["text"].startswith("Preparation completed.")
    assert client.texts[0]["chat_id"] == "222"


def test_check_manually_explicit_prepare_succeeds_via_callback(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(
        tmp_path=tmp_path,
        recommendation=RECOMMENDATION_CHECK_MANUALLY,
    )
    assert service.calls[0][0].recommendation == RECOMMENDATION_CHECK_MANUALLY
    assert service.calls[0][1] is PrepareIntent.EXPLICIT
    assert autofill.calls == [(SOURCE, EXTERNAL_ID, True)]
    assert "Preparation completed." in client.texts[0]["text"]


def test_applied_stale_callback_is_blocked_and_autofill_not_reached(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(
        tmp_path=tmp_path,
        recommendation=RECOMMENDATION_APPLY_NOW,
        history_status=STATUS_APPLIED,
    )
    assert service.calls[0][1] is PrepareIntent.EXPLICIT
    assert autofill.calls == []
    assert client.texts[0]["text"] == BLOCKED_ALREADY_APPLIED


def test_skip_callback_is_blocked(tmp_path: Path) -> None:
    client, autofill, _service, _storage = _process(
        tmp_path=tmp_path,
        recommendation=RECOMMENDATION_SKIP,
    )
    assert autofill.calls == []
    assert client.texts[0]["text"] == BLOCKED_SKIP


def test_unknown_recommendation_fails_closed(tmp_path: Path) -> None:
    empty = TargetCompanyAnalysisCache(tmp_path / "empty.json")
    empty.load()
    autofill = _FakeAutofill()
    client, _runner, service, _storage = _process(
        tmp_path=tmp_path,
        cache=empty,
        autofill=autofill,
    )
    assert service.calls[0][0].recommendation == "UNKNOWN"
    assert service.calls[0][1] is PrepareIntent.EXPLICIT
    assert autofill.calls == []
    assert client.texts[0]["text"] == BLOCKED_UNKNOWN


def test_malformed_identity_fails_safely(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(
        tmp_path=tmp_path,
        data="prepapp:not-a-source",
    )
    assert service.calls == []
    assert autofill.calls == []
    assert client.answers == [("cb-prep", "Некорректное действие")]
    assert client.texts == []


def test_linkedin_or_generic_greenhouse_prepapp_fails_safely(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(
        tmp_path=tmp_path,
        data="prepapp:li:4439013108",
    )
    assert service.calls == []
    assert autofill.calls == []
    assert client.answers == [("cb-prep", "Preparation is not available for this vacancy.")]
    assert client.texts == []


def test_preparation_failure_is_reported_safely(tmp_path: Path) -> None:
    client, _autofill, _service, _storage = _process(
        tmp_path=tmp_path,
        prepare_service=_ExplodingPrepareService(),
    )
    assert client.answers == [("cb-prep", STARTING_TEXT)]
    assert client.texts[0]["text"] == FAILED_TEXT


def test_autofill_failed_status_is_reported_safely(tmp_path: Path) -> None:
    autofill = _FakeAutofill(status=AutofillStatus.FAILED)
    client, _runner, _service, _storage = _process(tmp_path=tmp_path, autofill=autofill)
    assert autofill.calls == [(SOURCE, EXTERNAL_ID, True)]
    assert client.texts[0]["text"] == FAILED_TEXT


def test_unsupported_form_outcome_send_recovers_after_transient_connection_failure(
    tmp_path: Path,
) -> None:
    """A definitely-undelivered failure (connection reset before the
    request was sent) on the ordinary prepare-outcome send -- not just the
    review-ready handoff -- must get the same bounded retry, so a transient
    Telegram/network blip right after an UNSUPPORTED_FORM result does not
    silently drop the outcome.
    """
    autofill = _FakeAutofill(
        status=AutofillStatus.FAILED,
        failure_reason=AutofillFailureReason.UNSUPPORTED_FORM,
    )
    flaky_client = _FlakySendOnceClient(
        error=TelegramRequestError(
            "connection reset",
            description="connection failed before request was sent",
        )
    )
    client, runner, _service, _storage = _process(
        tmp_path=tmp_path,
        autofill=autofill,
        client=flaky_client,
    )
    assert runner.calls == [(SOURCE, EXTERNAL_ID, True)]
    expected_text = format_application_prepare_failed_text(
        SimpleNamespace(failure_reason=AutofillFailureReason.UNSUPPORTED_FORM)
    )
    # Exactly one delivered message -- the retry recovered the same outcome,
    # it never produced a second, duplicate delivery.
    assert len(client.texts) == 1
    assert client.texts[0]["text"] == expected_text


def test_unsupported_form_outcome_send_fails_closed_without_duplicate_attempt(
    tmp_path: Path,
) -> None:
    """A definitely-undelivered failure that persists past the bounded
    retry budget must fail closed -- exhausting exactly that budget -- and
    must not fall through to a second, unbounded send from the generic
    exception handler.
    """
    autofill = _FakeAutofill(
        status=AutofillStatus.FAILED,
        failure_reason=AutofillFailureReason.UNSUPPORTED_FORM,
    )
    always_fail_client = _AlwaysFailSendClient()
    client, runner, _service, _storage = _process(
        tmp_path=tmp_path,
        autofill=autofill,
        client=always_fail_client,
    )
    assert runner.calls == [(SOURCE, EXTERNAL_ID, True)]
    assert client.texts == []
    assert client.send_attempts == cli_module._REVIEW_NOTICE_MAX_ATTEMPTS


def test_wrong_chat_is_rejected(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(
        tmp_path=tmp_path,
        chat_id="999",
        allowed_chat_ids=frozenset({"222"}),
    )
    assert service.calls == []
    assert autofill.calls == []
    assert client.answers == [("cb-prep", "Действие недоступно для этого чата")]
    assert client.texts == []


def test_linkedin_prepare_behavior_is_unchanged(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    storage.save_sent(
        source="linkedin-email",
        external_id="4439013109",
        chat_id="123",
        message_id=11,
    )
    autofill = _FakeAutofill()
    service = _RecordingPrepareService(PrepareApplicationService(autofill, storage))
    client = _FakeClient()
    cli_module._process_callback_update(
        update={
            "callback_query": {
                "id": "cb-li",
                "data": "prepare:li:4439013109",
                "message": {
                    "chat": {"id": "123"},
                    "message_id": 11,
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "open", "url": "https://www.linkedin.com/jobs/view/4439013109/"}]
                        ]
                    },
                },
            }
        },
        client=client,
        storage=storage,
        configured_chat_id="123",
        application_prepare_service=service,
        application_prepare_cache=_seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW),
        application_prepare_sync=True,
    )
    assert service.calls == []
    assert autofill.calls == []
    assert ("cb-li", "Добавлено в очередь на подготовку отклика") in client.answers
    delivery = storage.get_delivery("linkedin-email", "4439013109")
    assert delivery is not None
    assert delivery.status == "PREPARE_REQUESTED"


def test_handler_does_not_import_original_vacancy_object(tmp_path: Path) -> None:
    client, autofill, service, _storage = _process(tmp_path=tmp_path)
    vacancy, _intent, _keep_open = service.calls[0]
    assert vacancy.__class__ is RecommendedVacancy
    assert not hasattr(vacancy, "description")
    assert autofill.calls[0][:2] == (SOURCE, EXTERNAL_ID)
    assert "Java backend services" not in client.texts[0]["text"]


# --- "Done reviewing" browser handoff (production wiring path) ---
#
# These tests exercise the real cli.py dispatch (`service is None`, so
# `build_prepare_application_service` actually runs) with a fake autofill
# runner standing in for AutofillService/Playwright, monkeypatched at the
# same seam `tests/application/autofill/test_cli.py` uses for the diagnostic
# CLI. This is the only way to reach the review-session wiring in
# `_dispatch_target_company_application_prepare` without a real browser.


class _HandoffAutofill:
    """Stands in for AutofillService: drives on_ready/wait_for_review exactly
    like the real browser handoff, without Playwright."""

    def __init__(self) -> None:
        self.on_ready = None
        self.wait_for_review = None
        self.calls: list[tuple[str, str, bool]] = []
        self.finished = False

    def run(self, source: str, external_id: str, *, keep_open: bool = True):
        self.calls.append((source, external_id, keep_open))
        result = stage1_autofill_result(
            source=source,
            external_id=external_id,
            application_url=URL,
            status=AutofillStatus.READY_FOR_REVIEW,
            filled_fields=[
                AutofillFieldResult(
                    label="First name",
                    classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                )
            ],
            resume_uploaded=True,
        )
        if self.on_ready is not None:
            self.on_ready(result)
        if keep_open and self.wait_for_review is not None:
            self.wait_for_review()
        self.finished = True
        return result


def _fake_build_prepare_application_service(lifecycle, *, on_ready=None, wait_for_review=None):
    runner = _HandoffAutofill()
    runner.on_ready = on_ready
    runner.wait_for_review = wait_for_review
    service = PrepareApplicationService(runner, lifecycle)
    return service, runner


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_completed_message_includes_done_reviewing_button(monkeypatch, tmp_path: Path) -> None:
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FakeClient()

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )

    _wait_until(lambda: len(client.texts) > 0 and len(client.edits) > 0)
    message = client.texts[0]
    assert message["text"].startswith("Preparation completed.")
    # The completion text is sent without a button first (to learn its
    # message_id); the button is attached by a separate, retry-safe edit.
    assert message["buttons"] is None
    edit = client.edits[0]
    assert edit["message_id"] == message["message_id"]
    assert edit["text"] == message["text"]
    buttons = edit["buttons"]
    assert buttons is not None
    session_id = parse_review_done_session_id(buttons[0][0].callback_data)
    assert session_id

    runner = holder["runner"]
    # The runner is parked inside wait_for_review(); the browser is still
    # "open" (run() has not returned) until "Done reviewing" is tapped.
    assert runner.calls == [(SOURCE, EXTERNAL_ID, True)]

    cli_module._process_callback_update(
        update=_callback_update(data=f"revdone:{session_id}"),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
    )
    assert client.answers[-1] == ("cb-prep", "Closing the browser now.")

    # A duplicate tap is graceful either way (already closed, or the
    # session was already discarded once the worker thread finished).
    cli_module._process_callback_update(
        update=_callback_update(data=f"revdone:{session_id}"),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
    )
    assert client.answers[-1][1] in {"Already closed.", "This review session is no longer available."}


def test_review_ready_send_recovers_after_transient_429(monkeypatch, tmp_path: Path) -> None:
    """A transient, definitely-undelivered Telegram error (429) on the
    review-completion send must be retried -- within a small bounded
    budget, without registering a second session or duplicating the
    completion message -- rather than treated as a permanent failure.
    """
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FlakySendOnceClient()

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )

    _wait_until(lambda: len(client.edits) > 0)
    # Exactly one plain send (after the one retry) and one button-attach
    # edit -- the retry never produced a second, duplicate completion
    # message.
    assert len(client.texts) == 1
    session_id = parse_review_done_session_id(client.edits[0]["buttons"][0][0].callback_data)

    runner = holder["runner"]
    assert not runner.finished

    _tap_review_done(client, storage, session_id)
    _wait_until(lambda: runner.finished)


def test_review_ready_send_retries_after_connection_failure(monkeypatch, tmp_path: Path) -> None:
    """A connection failure that happens before any request bytes are sent
    is, like a 429, known not to have reached Telegram -- so it must also
    get a bounded retry, not just explicit rate-limit responses.
    """
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FlakySendOnceClient(
        error=TelegramRequestError(
            "connection refused",
            description="connection failed before request was sent",
        )
    )

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )

    _wait_until(lambda: len(client.edits) > 0)
    assert len(client.texts) == 1
    session_id = parse_review_done_session_id(client.edits[0]["buttons"][0][0].callback_data)

    runner = holder["runner"]
    _tap_review_done(client, storage, session_id)
    _wait_until(lambda: runner.finished)


def test_two_concurrent_prepares_have_independent_done_reviewing_sessions(monkeypatch, tmp_path: Path) -> None:
    runners: list[_HandoffAutofill] = []

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        runners.append(runner)
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FakeClient()

    for message_id in (50, 51):
        cli_module._process_callback_update(
            update={
                "callback_query": {
                    "id": f"cb-{message_id}",
                    "data": f"{APPLICATION_PREPARE_ACTION}:tcg.agoda:{EXTERNAL_ID}",
                    "message": {"chat": {"id": "222"}, "message_id": message_id, "text": "Agoda"},
                }
            },
            client=client,
            storage=storage,
            configured_chat_id="222",
            allowed_chat_ids=frozenset({"222"}),
            application_prepare_service=None,
            application_prepare_cache=cache,
            application_prepare_sync=False,
        )

    _wait_until(lambda: len(client.edits) >= 2)
    session_ids = [
        parse_review_done_session_id(edit["buttons"][0][0].callback_data) for edit in client.edits
    ]
    assert len(set(session_ids)) == 2

    cli_module._process_callback_update(
        update=_callback_update(data=f"revdone:{session_ids[0]}"),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
    )
    _wait_until(lambda: runners[0].finished)

    # Closing the first session must never touch the second's open browser.
    assert runners[1].finished is False

    cli_module._process_callback_update(
        update=_callback_update(data=f"revdone:{session_ids[1]}"),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
    )
    _wait_until(lambda: runners[1].finished)


def _start_review_session(monkeypatch, client, cache, storage) -> str:
    """Trigger an explicit prepare and return the resulting review session id."""

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, _runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )
    _wait_until(lambda: len(client.edits) > 0)
    return parse_review_done_session_id(client.edits[0]["buttons"][0][0].callback_data)


def _tap_review_done(client, storage, session_id: str, *, message_text: str | None = None) -> None:
    completion = client.texts[0]
    update = {
        "callback_query": {
            "id": "cb-prep",
            "data": f"revdone:{session_id}",
            "message": {
                "chat": {"id": completion["chat_id"]},
                "message_id": completion["message_id"],
                "text": message_text if message_text is not None else completion["text"],
            },
        }
    }
    cli_module._process_callback_update(
        update=update,
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
    )


def test_review_done_tap_edits_completion_message_and_removes_button(monkeypatch, tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FakeClient()

    session_id = _start_review_session(monkeypatch, client, cache, storage)
    # The button-attach edit from the review-ready notice itself.
    assert len(client.edits) == 1
    _tap_review_done(client, storage, session_id)

    assert len(client.edits) == 2
    edit = client.edits[1]
    assert edit["chat_id"] == "222"
    assert edit["message_id"] == 99
    assert edit["buttons"] == []
    # The original message content is preserved, not discarded.
    assert "Preparation completed." in edit["text"]
    assert "Filled fields: 1" in edit["text"]
    assert "Manual review needed." not in edit["text"]
    assert "Manual review finished." in edit["text"]
    assert "Review finished." in edit["text"]
    assert "Submission was not verified" in edit["text"]


def test_review_done_duplicate_or_stale_tap_is_handled_gracefully(monkeypatch, tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FakeClient()

    session_id = _start_review_session(monkeypatch, client, cache, storage)
    _tap_review_done(client, storage, session_id)
    assert client.answers[-1] == ("cb-prep", "Closing the browser now.")

    # A duplicate/stale tap must never raise and must still answer safely.
    terminal_text = client.edits[-1]["text"]
    _tap_review_done(client, storage, session_id, message_text=terminal_text)
    assert client.answers[-1][1] in {"Already closed.", "This review session is no longer available."}
    # edits[0] is the button-attach edit from the review-ready notice.
    assert len(client.edits) == 3
    assert client.edits[-1]["buttons"] == []
    assert client.edits[-1]["text"] == terminal_text


def test_review_done_edit_failure_does_not_block_browser_release(monkeypatch, tmp_path: Path) -> None:
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _TerminalEditFailingClient()

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )
    _wait_until(lambda: len(client.edits) > 0)
    session_id = parse_review_done_session_id(client.edits[0]["buttons"][0][0].callback_data)

    # The revdone terminal edit raises, but mark_done (browser release)
    # happens before that edit is even attempted, so the failure must not
    # block it.
    _tap_review_done(client, storage, session_id)

    runner = holder["runner"]
    _wait_until(lambda: runner.finished)
    assert runner.finished is True
    assert client.answers[-1] == ("cb-prep", "Closing the browser now.")
    # edits[0] is the (successful) button-attach edit; edits[1] is the
    # failing revdone terminal edit.
    assert len(client.edits) == 2


def test_review_ready_permanent_edit_failure_closes_browser_without_waiting(
    monkeypatch, tmp_path: Path
) -> None:
    """If the button-attach edit can never succeed, `on_ready` itself must
    mark_done the session so the browser closes deterministically -- the
    worker must not sit blocked in wait_for_review waiting for a
    "Done reviewing" tap on a button that was never delivered, and no
    misleading ordinary-completion fallback must fire alongside it.
    """
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _EditFailingClient()
    registry = default_review_registry()

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )

    runner = holder["runner"]
    _wait_until(lambda: runner.finished)
    assert runner.finished is True

    # Exactly one plain completion message was sent; no duplicate or
    # misleading ordinary-completion fallback fired alongside it.
    assert len(client.texts) == 1
    assert client.texts[0]["buttons"] is None
    _wait_until(lambda: registry.active_count() == 0)


def test_review_ready_unexpected_attach_edit_failure_edits_existing_message(
    monkeypatch, tmp_path: Path
) -> None:
    """If attaching the Done-reviewing button raises something other than a
    `TelegramRequestError` (e.g. a bug in button building, or an unwrapped
    client error), `_notify_review_ready`'s outer `except` runs after the
    plain completion message has already been sent and its id is known. It
    must best-effort terminal-edit that same message instead of sending a
    second, separate "unavailable" notice -- otherwise the original
    completion message is left looking like an ordinary, still-actionable
    completion while a confusing second message sits next to it.
    """
    holder: dict[str, object] = {}

    def fake_builder(lifecycle, *, on_ready=None, wait_for_review=None):
        service, runner = _fake_build_prepare_application_service(
            lifecycle, on_ready=on_ready, wait_for_review=wait_for_review
        )
        holder["runner"] = runner
        return service

    monkeypatch.setattr(
        "app.application.explicit_prepare_runtime.build_prepare_application_service",
        fake_builder,
    )
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _UnexpectedAttachEditFailingClient()
    registry = default_review_registry()

    cli_module._process_callback_update(
        update=_callback_update(),
        client=client,
        storage=storage,
        configured_chat_id="222",
        allowed_chat_ids=frozenset({"222"}),
        application_prepare_service=None,
        application_prepare_cache=cache,
        application_prepare_sync=False,
    )

    runner = holder["runner"]
    _wait_until(lambda: runner.finished)
    assert runner.finished is True

    # Exactly one message was ever sent -- no duplicate "unavailable" notice.
    assert len(client.texts) == 1
    sent_message_id = client.texts[0]["message_id"]

    # Two edit attempts: the failing button-attach, then the best-effort
    # terminal edit -- both targeting the one message that was actually sent.
    assert len(client.edits) == 2
    assert client.edits[0]["buttons"]
    terminal_edit = client.edits[1]
    assert terminal_edit["message_id"] == sent_message_id
    assert terminal_edit["buttons"] == []

    _wait_until(lambda: registry.active_count() == 0)


def test_review_done_never_writes_applied_status(monkeypatch, tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json", recommendation=RECOMMENDATION_APPLY_NOW)
    client = _FakeClient()

    session_id = _start_review_session(monkeypatch, client, cache, storage)
    assert storage.get_history_status(SOURCE, EXTERNAL_ID) is None

    _tap_review_done(client, storage, session_id)
    _tap_review_done(client, storage, session_id)

    assert storage.get_history_status(SOURCE, EXTERNAL_ID) is None
