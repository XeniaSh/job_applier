"""Reproduces the real Telegram Prepare -> Done reviewing lifecycle.

Earlier review-session tests (`tests/test_telegram_target_company_prepare.py`
`_HandoffAutofill`, `tests/application/autofill/test_review_handoff.py`) either
hand-rolled a fake that re-implements the on_ready/wait_for_review sequence
itself, or drove the real `AutofillService` directly while bypassing
`app.application.explicit_prepare_runtime.build_prepare_application_service` --
the actual production wiring function `app.cli` calls. This module drives the
real chain instead:

    _dispatch_target_company_application_prepare (real)
      -> build_prepare_application_service (real)
      -> PrepareApplicationService.prepare (real)
      -> prepare_application (real)
      -> AutofillService.run (real)
      -> on_ready / complete_browser_handoff / wait_for_review (real)

Only the leaves that would otherwise need a live browser, network, or LLM
call are replaced: `DefaultVacancyResolver`, `GreenhouseAdapter`,
`BrowserSession`, `resolve_default_resume_path`, and the LLM client.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

import app.cli as cli_module
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.review_session import (
    ReviewSessionCloseOutcome,
    default_review_registry,
)
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.application_recommendation import (
    RECOMMENDATION_APPLY_NOW,
    ApplicationRecommendation,
)
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import TelegramRequestError, parse_review_done_session_id
from app.telegram.models import TelegramMessageRef

SOURCE = "target_company:greenhouse:adyen"
EXTERNAL_ID = "7938074"
URL = "https://job-boards.greenhouse.io/adyen/jobs/7938074"


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """`default_review_registry()` is a process-wide singleton (by design --
    it must be the one thing both the prepare worker and the Telegram
    callback handler see). Make sure no session leaks from one test into
    the next: release anything still pending before and after each test.
    """
    registry = default_review_registry()
    registry.close_all()
    registry.wait_all_discarded(timeout=2.0)
    yield
    registry.close_all()
    registry.wait_all_discarded(timeout=2.0)


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


@dataclass
class _FakeBrowserSession:
    headed: bool = True
    keep_open: bool = False
    closed: bool = False
    opened_url: str | None = None
    page: object = object()

    def open(self, url: str) -> object:
        self.opened_url = url
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Java Engineer",
            company="Adyen",
            url=URL,
            application_url=URL,
        )


class _FakeAdapter:
    def detect_challenge(self, page: object) -> str | None:
        _ = page
        return None

    def recognize(self, page: object) -> bool:
        _ = page
        return True

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [DiscoveredField(label="First Name", name="first_name", required=True)]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        return bool(getattr(classified, "fill", False))

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        _ = page, resume_path, field
        return False

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        return "Ada" if field.name == "first_name" else None


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


def _seed_cache(path: Path) -> TargetCompanyAnalysisCache:
    from app.collectors.vacancy_collector import NormalizedVacancy

    cache = TargetCompanyAnalysisCache(path)
    cache.put(
        NormalizedVacancy(
            source=SOURCE,
            external_id=EXTERNAL_ID,
            title="Senior Java Engineer",
            company="Adyen",
            location="Amsterdam",
            employment="Full-time",
            description="Java backend services",
            url=URL,
            published_at="2026-09-05T10:00:00Z",
        ),
        evaluation=_evaluation(),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label=RECOMMENDATION_APPLY_NOW, reasons=["test"]),
        seniority=SeniorityClassification(label="SENIOR", reasons=["title has senior"]),
    )
    cache.save()
    loaded = TargetCompanyAnalysisCache(path)
    loaded.load()
    return loaded


class _FakeClient:
    def __init__(
        self,
        *,
        fail_first_sends: int = 0,
        always_fail_sends: bool = False,
        raise_unexpected_error: bool = False,
    ) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.texts: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []
        self._remaining_failures = fail_first_sends
        self._always_fail_sends = always_fail_sends
        self._raise_unexpected_error = raise_unexpected_error

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.answers.append((callback_query_id, text))

    def send_text_message(self, text: str, *, chat_id=None, reply_to_message_id=None, buttons=None):
        if self._raise_unexpected_error:
            # A bug or an unwrapped client-library failure (e.g. a decode
            # error), as opposed to the TelegramRequestError the retry
            # helper knows how to classify and retry.
            raise RuntimeError("unexpected non-Telegram failure")
        if self._always_fail_sends or self._remaining_failures > 0:
            self._remaining_failures = max(0, self._remaining_failures - 1)
            # A 429 is a transient, definitely-undelivered failure: Telegram
            # rejected the call outright, so it is safe for production code
            # to retry it.
            raise TelegramRequestError(
                "simulated rate limit",
                http_status=429,
                error_code=429,
                description="Too Many Requests: retry after 1",
            )
        self.texts.append({"text": text, "chat_id": chat_id, "buttons": buttons})
        return TelegramMessageRef(chat_id=str(chat_id or "222"), message_id=99)

    def edit_message_text(self, **kwargs) -> None:
        self.edits.append(kwargs)


def _run_real_prepare(
    monkeypatch,
    tmp_path: Path,
    *,
    client: "_FakeClient | None" = None,
    browser_session_cls: type = _FakeBrowserSession,
) -> tuple[_FakeClient, threading.Thread]:
    """Dispatch a Telegram-triggered prepare through the REAL production
    wiring (`build_prepare_application_service`), threaded exactly like
    production (`sync=False`), with only browser/LLM/network leaves faked.
    """
    monkeypatch.setattr("app.application.autofill.resolver.DefaultVacancyResolver", _FakeResolver)
    monkeypatch.setattr("app.application.autofill.service.GreenhouseAdapter", _FakeAdapter)
    monkeypatch.setattr("app.application.autofill.service.BrowserSession", browser_session_cls)
    monkeypatch.setattr(
        "app.application.autofill.service.resolve_default_resume_path",
        lambda profile: Path(__file__),  # any file that exists
    )
    monkeypatch.setattr("app.application.explicit_prepare_runtime._optional_llm_client", lambda: None)

    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    cache = _seed_cache(tmp_path / "cache.json")
    client = client if client is not None else _FakeClient()

    thread_holder: dict[str, threading.Thread] = {}
    original_thread_init = threading.Thread.__init__

    def _capturing_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        original_thread_init(self, *args, **kwargs)
        if kwargs.get("name", "").startswith("tc-prepare:"):
            thread_holder["thread"] = self

    monkeypatch.setattr(threading.Thread, "__init__", _capturing_init)

    cli_module._dispatch_target_company_application_prepare(
        source=SOURCE,
        external_id=EXTERNAL_ID,
        client=client,
        storage=storage,
        chat_id="222",
        message_id=50,
        answer_once=lambda text: client.answers.append(("cb-prep", text)),
        prepare_service=None,  # force the real build_prepare_application_service path
        analysis_cache=cache,
        sync=False,
    )
    _wait_until(lambda: "thread" in thread_holder)
    return client, thread_holder["thread"]


def test_session_stays_active_after_completion_message_while_worker_waits(
    monkeypatch, tmp_path: Path
) -> None:
    client, worker_thread = _run_real_prepare(monkeypatch, tmp_path)

    # 1. Completion message + Done reviewing button arrive (the plain text
    # is sent first, then the button is attached by a separate edit).
    _wait_until(lambda: len(client.texts) > 0 and len(client.edits) > 0)
    message = client.texts[0]
    assert message["text"].startswith("Preparation completed.")
    assert message["buttons"] is None
    edit = client.edits[0]
    buttons = edit["buttons"]
    assert buttons is not None
    session_id = parse_review_done_session_id(buttons[0][0].callback_data)

    # 2. The registry entry is still ACTIVE (this is the exact bug report:
    # it must NOT already be gone at this point).
    registry = default_review_registry()
    assert registry.active_count() >= 1
    with registry._lock:  # noqa: SLF001 - assert internal state directly for this regression test
        assert session_id in registry._sessions

    # 3. The prepare worker is still alive/blocked (browser "still open").
    assert worker_thread.is_alive()

    try:
        # 4. First "Done reviewing" finds that exact session and releases it.
        outcome = registry.mark_done(session_id)
        assert outcome is ReviewSessionCloseOutcome.CLOSED

        # 5. Worker finishes only after the release (browser cleanup happens
        # inside the worker, not before).
        worker_thread.join(timeout=2.0)
        assert not worker_thread.is_alive()

        # 6. Registry cleanup happens only afterward.
        _wait_until(lambda: registry.active_count() == 0 or session_id not in _session_ids(registry))
        assert session_id not in _session_ids(registry)

        # 7. Duplicate "Done reviewing" afterward is graceful, not a crash.
        duplicate_outcome = registry.mark_done(session_id)
        assert duplicate_outcome is ReviewSessionCloseOutcome.NOT_FOUND
    finally:
        worker_thread.join(timeout=2.0)


def _session_ids(registry) -> set[str]:
    with registry._lock:  # noqa: SLF001
        return set(registry._sessions.keys())


def test_two_concurrent_real_prepares_have_independent_sessions(monkeypatch, tmp_path: Path) -> None:
    client_a, thread_a = _run_real_prepare(monkeypatch, tmp_path)
    _wait_until(lambda: len(client_a.edits) > 0)
    session_a = parse_review_done_session_id(client_a.edits[0]["buttons"][0][0].callback_data)

    client_b, thread_b = _run_real_prepare(monkeypatch, tmp_path)
    _wait_until(lambda: len(client_b.edits) > 0)
    session_b = parse_review_done_session_id(client_b.edits[0]["buttons"][0][0].callback_data)

    assert session_a != session_b
    registry = default_review_registry()

    assert registry.mark_done(session_a) is ReviewSessionCloseOutcome.CLOSED
    thread_a.join(timeout=2.0)
    assert not thread_a.is_alive()

    # Closing session A must never affect session B.
    assert thread_b.is_alive()
    assert session_b in _session_ids(registry)

    registry.mark_done(session_b)
    thread_b.join(timeout=2.0)
    assert not thread_b.is_alive()


def test_flaky_completion_notification_recovers_via_retry(monkeypatch, tmp_path: Path) -> None:
    """Root-cause regression for the live Adyen bug, updated for the bounded
    retry/recovery fix: the completion message (which carries the "Done
    reviewing" button) is sent over the network from inside `on_ready`. A
    transient, definitely-undelivered Telegram error (a 429) on that send
    must be retried -- within a small bounded budget, without re-running
    autofill or registering a second session -- so the browser handoff
    still ends up delivered instead of leaving a browser open with no
    working way to close it.
    """
    flaky_client = _FakeClient(fail_first_sends=1)
    registry = default_review_registry()
    _client, worker_thread = _run_real_prepare(monkeypatch, tmp_path, client=flaky_client)

    # The one retryable failure recovers on retry, ending up with exactly
    # one plain send (the retry) and one button-attach edit -- no duplicate
    # active completion messages.
    _wait_until(lambda: len(flaky_client.edits) > 0)
    assert len(flaky_client.texts) == 1
    assert flaky_client.texts[0]["buttons"] is None
    assert len(flaky_client.edits) == 1
    session_id = parse_review_done_session_id(flaky_client.edits[0]["buttons"][0][0].callback_data)

    active_sessions = _session_ids(registry)
    assert active_sessions == {session_id}
    assert worker_thread.is_alive()

    outcome = registry.mark_done(session_id)
    assert outcome is ReviewSessionCloseOutcome.CLOSED
    worker_thread.join(timeout=2.0)
    assert not worker_thread.is_alive()
    assert session_id not in _session_ids(registry)


def test_permanent_send_failure_closes_browser_without_hanging(monkeypatch, tmp_path: Path) -> None:
    """If every completion-notice send exhausts the retry budget, `on_ready`
    must call `mark_done` itself so `AutofillService`'s wait_for_review
    never blocks -- the browser still closes deterministically even though
    the user was never notified with a working "Done reviewing" button, and
    no ordinary-completion fallback message is left dangling either.
    """
    failing_client = _FakeClient(always_fail_sends=True)
    registry = default_review_registry()
    _client, worker_thread = _run_real_prepare(monkeypatch, tmp_path, client=failing_client)

    worker_thread.join(timeout=2.0)
    assert not worker_thread.is_alive()
    assert failing_client.texts == []
    assert failing_client.edits == []
    _wait_until(lambda: registry.active_count() == 0)


def test_non_telegram_send_failure_closes_browser_without_hanging(monkeypatch, tmp_path: Path) -> None:
    """`AutofillService.run` only logs-and-continues into the browser
    handoff when `on_ready` raises; it never calls `mark_done` itself. If
    the completion send raises something other than the Telegram-specific
    errors `_send_with_bounded_retry` knows how to classify (e.g. a bug in
    button building, or an unwrapped client error), that exception must
    still be caught inside the review-handoff notice and must not skip
    `mark_done` -- otherwise `wait_for_review()` blocks forever with no one
    left to signal it, and the real browser session is never closed.
    """

    class _CapturingBrowserSession(_FakeBrowserSession):
        instances: list["_CapturingBrowserSession"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            type(self).instances.append(self)

    failing_client = _FakeClient(raise_unexpected_error=True)
    registry = default_review_registry()
    _client, worker_thread = _run_real_prepare(
        monkeypatch, tmp_path, client=failing_client, browser_session_cls=_CapturingBrowserSession
    )

    worker_thread.join(timeout=2.0)
    assert not worker_thread.is_alive()

    # The browser was released deterministically even though delivery
    # blew up with an exception type the retry helper cannot classify.
    assert len(_CapturingBrowserSession.instances) == 1
    assert _CapturingBrowserSession.instances[0].closed is True

    # No Done-reviewing button and no misleading ordinary-completion
    # fallback were left dangling alongside the failed handoff.
    assert failing_client.edits == []
    assert failing_client.texts == []

    _wait_until(lambda: registry.active_count() == 0)

    # A failed handoff must never be mistaken for a completed application.
    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    assert storage.get_history_status(SOURCE, EXTERNAL_ID) is None


def test_completion_text_formatting_failure_closes_browser_without_hanging(
    monkeypatch, tmp_path: Path
) -> None:
    """`format_application_prepare_completed_text(result)` runs inside
    `on_ready` before `_notify_review_ready`'s own try/except can guard
    anything -- it builds the very `text` argument that call needs. If
    formatting itself raises, that exception must still be caught, and
    `mark_done` still called, before `on_ready` returns: `AutofillService.run`
    only logs-and-continues past a failed `on_ready`, so skipping `mark_done`
    here would leave the real `wait_for_review()` handoff blocked forever
    with the browser never closed.
    """

    class _CapturingBrowserSession(_FakeBrowserSession):
        instances: list["_CapturingBrowserSession"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            type(self).instances.append(self)

    def _boom(result):
        raise RuntimeError("unexpected formatting failure")

    monkeypatch.setattr(cli_module, "format_application_prepare_completed_text", _boom)

    client = _FakeClient()
    registry = default_review_registry()
    _client, worker_thread = _run_real_prepare(
        monkeypatch, tmp_path, client=client, browser_session_cls=_CapturingBrowserSession
    )

    worker_thread.join(timeout=2.0)
    assert not worker_thread.is_alive()

    # The browser was released deterministically even though formatting the
    # completion text blew up before the review-ready notice could even be
    # attempted.
    assert len(_CapturingBrowserSession.instances) == 1
    assert _CapturingBrowserSession.instances[0].closed is True

    # No Done-reviewing button was ever attached; only the best-effort
    # review-unavailable notice went out, never a misleading ordinary
    # completion.
    assert client.edits == []
    assert len(client.texts) == 1
    assert client.texts[0]["text"] == cli_module._REVIEW_UNAVAILABLE_TEXT

    _wait_until(lambda: registry.active_count() == 0)

    storage = TelegramDeliveryStorage(tmp_path / "jobs.db")
    assert storage.get_history_status(SOURCE, EXTERNAL_ID) is None
