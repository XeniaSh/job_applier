"""In-process coordination for handing a filled, still-open browser to a human.

AutofillService leaves a filled application form open in a headed browser for
manual review (see `browser.complete_browser_handoff`). The default handoff
blocks on builtin `input()` in the invoking terminal, which is correct for the
foreground diagnostic CLI (`autofill SOURCE EXTERNAL_ID`) but wrong for a
background thread inside the long-running `run` service: there is no
terminal to type into for that thread, and multiple concurrent preparations
would otherwise race on the process's single shared stdin.

This module is Telegram-agnostic and ATS-agnostic. It only tracks "wait until
someone reports this session as done" by a stable, opaque session id, so a UI
layer (Telegram callback handling today; anything else later) can signal
completion for one in-flight review without touching any other.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class ReviewSessionCloseOutcome(StrEnum):
    CLOSED = "closed"
    ALREADY_CLOSED = "already_closed"
    NOT_FOUND = "not_found"


@dataclass
class _ReviewSessionEntry:
    source: str
    external_id: str
    event: threading.Event = field(default_factory=threading.Event)


class ReviewSessionRegistry:
    """Tracks pending browser review handoffs by an opaque session id.

    One registry instance is safe to share across threads: every method
    takes its own lock internally.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._sessions: dict[str, _ReviewSessionEntry] = {}

    def register(self, *, source: str, external_id: str) -> str:
        """Create a new session id and mark it pending. Never blocks."""
        session_id = uuid.uuid4().hex
        with self._lock:
            self._sessions[session_id] = _ReviewSessionEntry(source=source, external_id=external_id)
        return session_id

    def wait_for_done(self, session_id: str) -> None:
        """Block the calling (autofill) thread until `mark_done` is called.

        An unknown session id (never registered, or already discarded)
        returns immediately instead of blocking forever, since there is no
        one left who could ever call `mark_done` for it.
        """
        with self._lock:
            entry = self._sessions.get(session_id)
        if entry is None:
            return
        entry.event.wait()

    def mark_done(self, session_id: str) -> ReviewSessionCloseOutcome:
        """Signal that review is finished. Safe to call more than once."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return ReviewSessionCloseOutcome.NOT_FOUND
            if entry.event.is_set():
                return ReviewSessionCloseOutcome.ALREADY_CLOSED
            entry.event.set()
            return ReviewSessionCloseOutcome.CLOSED

    def discard(self, session_id: str) -> None:
        """Drop bookkeeping once the owning thread has actually closed the browser.

        Safe to call for an id that is already gone.
        """
        with self._condition:
            self._sessions.pop(session_id, None)
            self._condition.notify_all()

    def close_all(self) -> int:
        """Signal every still-pending session to finish review.

        Best-effort process-shutdown hook: it does not itself close browsers
        or wait for them, it only unblocks threads parked in
        `wait_for_done` so they can proceed to close their own session and
        call `discard`. Returns the number of sessions signaled.
        """
        with self._lock:
            pending = [entry for entry in self._sessions.values() if not entry.event.is_set()]
            for entry in pending:
                entry.event.set()
            return len(pending)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def wait_all_discarded(self, timeout: float | None = None) -> bool:
        """Block until every session has been discarded, or timeout elapses.

        Returns True if the registry became empty, False on timeout.
        """
        with self._condition:
            return self._condition.wait_for(lambda: not self._sessions, timeout=timeout)


_default_registry = ReviewSessionRegistry()


def default_review_registry() -> ReviewSessionRegistry:
    """Process-wide registry used by the Telegram-triggered prepare path."""
    return _default_registry
