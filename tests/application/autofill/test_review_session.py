from __future__ import annotations

import threading
import time

from app.application.autofill.review_session import (
    ReviewSessionCloseOutcome,
    ReviewSessionRegistry,
)


def test_register_returns_unique_ids() -> None:
    registry = ReviewSessionRegistry()
    first = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    second = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    assert first != second
    assert registry.active_count() == 2


def test_wait_for_done_blocks_until_mark_done() -> None:
    registry = ReviewSessionRegistry()
    session_id = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    unblocked = threading.Event()

    def waiter() -> None:
        registry.wait_for_done(session_id)
        unblocked.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        assert not unblocked.is_set()
        outcome = registry.mark_done(session_id)
        assert outcome is ReviewSessionCloseOutcome.CLOSED
        thread.join(timeout=2.0)
        assert unblocked.is_set()
    finally:
        thread.join(timeout=1.0)


def test_mark_done_is_idempotent() -> None:
    registry = ReviewSessionRegistry()
    session_id = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    assert registry.mark_done(session_id) is ReviewSessionCloseOutcome.CLOSED
    assert registry.mark_done(session_id) is ReviewSessionCloseOutcome.ALREADY_CLOSED
    assert registry.mark_done(session_id) is ReviewSessionCloseOutcome.ALREADY_CLOSED


def test_mark_done_unknown_session_is_not_found() -> None:
    registry = ReviewSessionRegistry()
    assert registry.mark_done("does-not-exist") is ReviewSessionCloseOutcome.NOT_FOUND


def test_discard_then_mark_done_is_not_found() -> None:
    registry = ReviewSessionRegistry()
    session_id = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    registry.discard(session_id)
    assert registry.mark_done(session_id) is ReviewSessionCloseOutcome.NOT_FOUND


def test_wait_for_done_on_unknown_session_returns_immediately() -> None:
    registry = ReviewSessionRegistry()
    start = time.monotonic()
    registry.wait_for_done("never-registered")
    assert time.monotonic() - start < 1.0


def test_two_sessions_are_independent() -> None:
    registry = ReviewSessionRegistry()
    session_a = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    session_b = registry.register(source="target_company:greenhouse:adyen", external_id="2")
    a_unblocked = threading.Event()
    b_unblocked = threading.Event()

    threading.Thread(target=lambda: (registry.wait_for_done(session_a), a_unblocked.set()), daemon=True).start()
    threading.Thread(target=lambda: (registry.wait_for_done(session_b), b_unblocked.set()), daemon=True).start()
    time.sleep(0.05)

    assert registry.mark_done(session_a) is ReviewSessionCloseOutcome.CLOSED
    a_unblocked.wait(timeout=2.0)
    assert a_unblocked.is_set()
    time.sleep(0.05)
    assert not b_unblocked.is_set()

    assert registry.mark_done(session_b) is ReviewSessionCloseOutcome.CLOSED
    b_unblocked.wait(timeout=2.0)
    assert b_unblocked.is_set()


def test_close_all_signals_every_pending_session() -> None:
    registry = ReviewSessionRegistry()
    session_a = registry.register(source="s", external_id="1")
    session_b = registry.register(source="s", external_id="2")
    signaled = registry.close_all()
    assert signaled == 2
    # Already-signaled sessions return ALREADY_CLOSED, not a fresh CLOSED.
    assert registry.mark_done(session_a) is ReviewSessionCloseOutcome.ALREADY_CLOSED
    assert registry.mark_done(session_b) is ReviewSessionCloseOutcome.ALREADY_CLOSED


def test_wait_all_discarded_returns_true_once_empty() -> None:
    registry = ReviewSessionRegistry()
    session_id = registry.register(source="s", external_id="1")

    def discard_soon() -> None:
        time.sleep(0.05)
        registry.discard(session_id)

    threading.Thread(target=discard_soon, daemon=True).start()
    assert registry.wait_all_discarded(timeout=2.0) is True


def test_wait_all_discarded_times_out_when_sessions_remain() -> None:
    registry = ReviewSessionRegistry()
    registry.register(source="s", external_id="1")
    assert registry.wait_all_discarded(timeout=0.05) is False
