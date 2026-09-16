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
    def __init__(self, *, fail_first_sends: int = 0) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.texts: list[dict[str, object]] = []
        self._remaining_failures = fail_first_sends

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.answers.append((callback_query_id, text))

    def send_text_message(self, text: str, *, chat_id=None, reply_to_message_id=None, buttons=None):
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise TelegramRequestError("simulated transient network failure")
        self.texts.append({"text": text, "chat_id": chat_id, "buttons": buttons})
        return TelegramMessageRef(chat_id=str(chat_id or "222"), message_id=99)


def _run_real_prepare(
    monkeypatch, tmp_path: Path, *, client: "_FakeClient | None" = None
) -> tuple[_FakeClient, threading.Thread]:
    """Dispatch a Telegram-triggered prepare through the REAL production
    wiring (`build_prepare_application_service`), threaded exactly like
    production (`sync=False`), with only browser/LLM/network leaves faked.
    """
    monkeypatch.setattr("app.application.autofill.resolver.DefaultVacancyResolver", _FakeResolver)
    monkeypatch.setattr("app.application.autofill.service.GreenhouseAdapter", _FakeAdapter)
    monkeypatch.setattr("app.application.autofill.service.BrowserSession", _FakeBrowserSession)
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

    # 1. Completion message + Done reviewing button arrive.
    _wait_until(lambda: len(client.texts) > 0)
    message = client.texts[0]
    assert message["text"].startswith("Preparation completed.")
    buttons = message["buttons"]
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
    _wait_until(lambda: len(client_a.texts) > 0)
    session_a = parse_review_done_session_id(client_a.texts[0]["buttons"][0][0].callback_data)

    client_b, thread_b = _run_real_prepare(monkeypatch, tmp_path)
    _wait_until(lambda: len(client_b.texts) > 0)
    session_b = parse_review_done_session_id(client_b.texts[0]["buttons"][0][0].callback_data)

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


def test_flaky_completion_notification_does_not_orphan_the_session(monkeypatch, tmp_path: Path) -> None:
    """Root-cause regression for the live Adyen bug: the completion message
    (which carries the "Done reviewing" button) is sent over the network from
    inside `on_ready`. A transient failure on that specific send must not
    leave a browser open with no way to close it -- the worker must still be
    blocked in the real wait_for_review handoff, so the registry entry stays
    valid for as long as the browser genuinely stays open.
    """
    flaky_client = _FakeClient(fail_first_sends=1)
    registry = default_review_registry()
    _client, worker_thread = _run_real_prepare(monkeypatch, tmp_path, client=flaky_client)

    # Wait for the session to be registered (this happens before the flaky
    # on_ready send), then give the worker a moment to actually run through
    # the failing on_ready call and reach the browser handoff.
    _wait_until(lambda: registry.active_count() >= 1)
    time.sleep(0.05)

    # The first send (the completion message) failed, so nothing was
    # recorded -- but the worker must still be alive, parked in the real
    # browser handoff, not dead from the on_ready exception.
    assert worker_thread.is_alive()
    assert flaky_client.texts == []

    active_sessions = _session_ids(registry)
    assert len(active_sessions) == 1
    session_id = next(iter(active_sessions))

    outcome = registry.mark_done(session_id)
    assert outcome is ReviewSessionCloseOutcome.CLOSED
    worker_thread.join(timeout=2.0)
    assert not worker_thread.is_alive()
    assert session_id not in _session_ids(registry)
