"""Regression tests for Skip/Applied responsiveness during vacancy delivery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.cli as cli_module
from app.collectors.vacancy_collector import NormalizedVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)
from app.storage.telegram_delivery import STATUS_APPLIED, STATUS_SENT, STATUS_SKIPPED, TelegramDeliveryStorage


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="fit",
        decision_reason="fit",
        matched_points=["java"],
        gaps=[],
        nuances=[],
        match_percentage=80.0,
        matched_score=8.0,
        total_possible_score=10.0,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _vacancy(external_id: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        source="linkedin-email",
        external_id=external_id,
        title=f"Java Backend {external_id}",
        company="ACME",
        location="Remote",
        employment=None,
        description="Java backend role",
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        published_at=None,
        content_completeness="FULL",
    )


def _callback(*, callback_id: str, data: str, message_id: int, chat_id: str = "123") -> dict:
    return {
        "callback_query": {
            "id": callback_id,
            "data": data,
            "message": {
                "chat": {"id": chat_id},
                "message_id": message_id,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "open", "url": "https://www.linkedin.com/jobs/view/1/"}]]
                },
            },
        }
    }


def _seed_sent(storage: TelegramDeliveryStorage, *, external_id: str, message_id: int) -> None:
    storage.save_sent(
        source="linkedin-email",
        external_id=external_id,
        chat_id="123",
        message_id=message_id,
    )
    storage.upsert_application_history(
        source="linkedin-email",
        external_id=external_id,
        title=f"Java Backend {external_id}",
        company="ACME",
        location="Remote",
        url=f"https://www.linkedin.com/jobs/view/{external_id}/",
        decision="POTENTIAL_MATCH",
        decision_reason="fit",
        recommended_resume="java-backend",
    )


class _OrderClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.answers: list[tuple[str, str | None]] = []

    def answer_callback_query(self, callback_query_id, text=None):
        self.events.append("answer")
        self.answers.append((callback_query_id, text))

    def edit_message_text(self, **kwargs):
        self.events.append("edit")
        _ = kwargs

    def delete_message(self, **kwargs):
        self.events.append("delete")
        _ = kwargs


def test_applied_answers_before_db_and_cleanup_enqueue(tmp_path: Path, monkeypatch) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage, external_id="100", message_id=10)
    events: list[str] = []
    client = _OrderClient(events)
    scheduler = cli_module._CleanupScheduler()

    original_apply = storage.apply_terminal_action

    def tracking_apply(**kwargs):
        events.append("apply")
        return original_apply(**kwargs)

    monkeypatch.setattr(storage, "apply_terminal_action", tracking_apply)
    monkeypatch.setattr(
        cli_module,
        "_cleanup_aux_messages",
        lambda **kwargs: events.append("cleanup"),
    )

    cli_module._process_callback_update(
        update=_callback(callback_id="cb-applied", data="applied:li:100", message_id=10),
        client=client,
        storage=storage,
        configured_chat_id="123",
        cleanup_scheduler=scheduler,
    )

    assert events.index("answer") < events.index("apply")
    assert "cleanup" not in events
    assert "edit" in events
    assert scheduler.pending_count() == 1
    assert scheduler.is_tracked("linkedin-email", "100")
    assert ("cb-applied", "Отклик отмечен как отправленный") in client.answers
    assert storage.get_delivery("linkedin-email", "100").status == STATUS_APPLIED


def test_skip_answers_before_db_write(tmp_path: Path, monkeypatch) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage, external_id="101", message_id=11)
    events: list[str] = []
    client = _OrderClient(events)
    original_apply = storage.apply_terminal_action

    def tracking_apply(**kwargs):
        events.append("apply")
        return original_apply(**kwargs)

    monkeypatch.setattr(storage, "apply_terminal_action", tracking_apply)
    monkeypatch.setattr(cli_module, "_cleanup_aux_messages", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_reconcile_vacancy_message", lambda **kwargs: None)

    cli_module._process_callback_update(
        update=_callback(callback_id="cb-skip", data="skip:li:101", message_id=11),
        client=client,
        storage=storage,
        configured_chat_id="123",
    )

    assert events.index("answer") < events.index("apply")
    assert storage.get_delivery("linkedin-email", "101").status == STATUS_SKIPPED


def test_deliver_polls_every_n_cards_not_every_card(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    evaluation = _evaluation()
    items = []
    for idx in range(1, 11):
        items.append(
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(str(idx)),
                storage_source="linkedin-email",
                storage_external_id=str(idx),
                preanalysis_outcome="new",
                analysis_result=evaluation,
                telegram_eligible=True,
            )
        )
    pipeline = cli_module.PipelineResult(items=items)

    sent_ids: list[str] = []
    poll_at_sent_counts: list[int] = []

    class FakeClient:
        def send_vacancy_card(self, card):
            sent_ids.append(card.external_id)
            return SimpleNamespace(message_id=len(sent_ids))

    def poll_callbacks() -> None:
        poll_at_sent_counts.append(len(sent_ids))

    cli_module._deliver_pipeline_items(
        pipeline=pipeline,
        deliveries=storage,
        telegram_client=FakeClient(),
        chat_id="123",
        poll_callbacks=poll_callbacks,
        poll_every_n_sent=4,
    )

    assert len(sent_ids) == 10
    assert poll_at_sent_counts == [4, 8]
    assert sum(1 for item in items if item.telegram_delivered) == 10


def test_applied_callback_processed_during_delivery_batch(tmp_path: Path) -> None:
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    _seed_sent(storage, external_id="existing", message_id=99)

    evaluation = _evaluation()
    items = []
    for idx in range(1, 9):
        items.append(
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(str(idx)),
                storage_source="linkedin-email",
                storage_external_id=str(idx),
                preanalysis_outcome="new",
                analysis_result=evaluation,
                telegram_eligible=True,
            )
        )
    pipeline = cli_module.PipelineResult(items=items)

    events: list[tuple[str, int | str]] = []
    sent_count = 0
    callback_status_before_send_5: str | None = None

    class FakeClient:
        def send_vacancy_card(self, card):
            nonlocal sent_count, callback_status_before_send_5
            sent_count += 1
            events.append(("send", sent_count))
            if sent_count == 5:
                delivery = storage.get_delivery("linkedin-email", "existing")
                callback_status_before_send_5 = delivery.status if delivery else None
            return SimpleNamespace(message_id=100 + sent_count)

        def answer_callback_query(self, callback_query_id, text=None):
            events.append(("answer", callback_query_id))
            _ = text

        def edit_message_text(self, **kwargs):
            events.append(("edit", kwargs.get("message_id", 0)))

        def delete_message(self, **kwargs):
            _ = kwargs

    client = FakeClient()

    def poll_callbacks() -> None:
        events.append(("poll", sent_count))
        if sent_count != 4:
            return
        cli_module._process_callback_update(
            update=_callback(
                callback_id="cb-during-batch",
                data="applied:li:existing",
                message_id=99,
            ),
            client=client,
            storage=storage,
            configured_chat_id="123",
        )
        events.append(("callback_done", sent_count))

    cli_module._deliver_pipeline_items(
        pipeline=pipeline,
        deliveries=storage,
        telegram_client=client,
        chat_id="123",
        poll_callbacks=poll_callbacks,
        poll_every_n_sent=4,
    )

    assert ("poll", 4) in events
    assert ("callback_done", 4) in events
    assert ("answer", "cb-during-batch") in events
    assert callback_status_before_send_5 == STATUS_APPLIED
    assert events.index(("callback_done", 4)) < events.index(("send", 5))
    assert storage.get_delivery("linkedin-email", "existing").status == STATUS_APPLIED
    assert storage.get_delivery("linkedin-email", "existing").previous_status == STATUS_SENT
    assert sent_count == 8


def test_run_pipeline_polls_callbacks_off_the_pipeline_thread() -> None:
    import inspect

    source = inspect.getsource(cli_module.run_pipeline)
    # Cycles run on their own thread, so the callback loop never waits for them.
    assert 'name="pipeline"' in source
    assert "target=_pipeline_worker_loop" in source
    assert 'name="cleanup-worker"' in source
    assert "cleanup_scheduler=cleanup_scheduler" in source
    assert "_poll_callbacks_now(timeout=poll_interval)" in source
    # Polling is no longer interleaved into the cycle; it is continuous instead.
    assert "_poll_callbacks_now(timeout=0)" not in source
    assert "poll_callbacks=" not in source
    # Delivery keeps the opt-in hook for callers that still drive polling themselves.
    assert "poll_every_n_sent" in inspect.getsource(cli_module._deliver_pipeline_items)
    assert cli_module._TELEGRAM_POLL_EVERY_N_SENT_CARDS >= 3
    assert cli_module._TELEGRAM_POLL_EVERY_N_SENT_CARDS <= 5
