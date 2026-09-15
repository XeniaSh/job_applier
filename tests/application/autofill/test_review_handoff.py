"""AutofillService + ReviewSessionRegistry wired together.

This is the piece that used to call builtin `input()` for every browser
handoff, including a Telegram-triggered background thread with no terminal
to type into. These tests exercise the two together the way
`app.cli._dispatch_target_company_application_prepare` wires them in
production, without any real Telegram or Playwright dependency.
"""

from __future__ import annotations

import builtins
import threading
import time
from dataclasses import dataclass

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.review_session import ReviewSessionRegistry
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


@dataclass
class _FakeSession:
    keep_open: bool = False
    closed: bool = False
    page: object = object()

    def open(self, url: str) -> object:
        _ = url
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeResolver:
    def __init__(self, application_url: str) -> None:
        self._application_url = application_url

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Example",
            url=self._application_url,
            application_url=self._application_url,
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


def _make_service(session: _FakeSession, registry: ReviewSessionRegistry, session_id: str) -> AutofillService:
    return AutofillService(
        resolver=_FakeResolver("https://job-boards.greenhouse.io/agoda/jobs/1"),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: registry.wait_for_done(session_id),
    )


def test_telegram_triggered_run_never_calls_builtin_input(monkeypatch) -> None:
    def _forbidden_input(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("Telegram-triggered autofill must not block on builtin input()")

    monkeypatch.setattr(builtins, "input", _forbidden_input)

    registry = ReviewSessionRegistry()
    session = _FakeSession()
    session_id = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    service = _make_service(session, registry, session_id)

    result_box: dict[str, object] = {}

    def run_it() -> None:
        result_box["result"] = service.run("target_company:greenhouse:agoda", "1", keep_open=True)

    thread = threading.Thread(target=run_it, daemon=True)
    thread.start()
    try:
        # Browser stays open while review is pending: run() has not returned.
        time.sleep(0.1)
        assert thread.is_alive()
        assert session.closed is False

        outcome = registry.mark_done(session_id)
        assert outcome.value == "closed"
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        thread.join(timeout=1.0)

    assert session.closed is True
    result = result_box["result"]
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False


def test_two_concurrent_review_sessions_are_independent() -> None:
    registry = ReviewSessionRegistry()
    session_a = _FakeSession()
    session_b = _FakeSession()
    session_id_a = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    session_id_b = registry.register(source="target_company:greenhouse:adyen", external_id="2")
    service_a = _make_service(session_a, registry, session_id_a)
    service_b = _make_service(session_b, registry, session_id_b)

    threads = [
        threading.Thread(
            target=lambda: service_a.run("target_company:greenhouse:agoda", "1", keep_open=True),
            daemon=True,
        ),
        threading.Thread(
            target=lambda: service_b.run("target_company:greenhouse:adyen", "2", keep_open=True),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    assert session_a.closed is False
    assert session_b.closed is False

    registry.mark_done(session_id_a)
    threads[0].join(timeout=2.0)
    assert session_a.closed is True
    # Closing session A must never touch session B's still-open browser.
    assert session_b.closed is False

    registry.mark_done(session_id_b)
    threads[1].join(timeout=2.0)
    assert session_b.closed is True


def test_duplicate_done_reviewing_is_harmless_once_browser_already_closed() -> None:
    registry = ReviewSessionRegistry()
    session = _FakeSession()
    session_id = registry.register(source="target_company:greenhouse:agoda", external_id="1")
    service = _make_service(session, registry, session_id)

    thread = threading.Thread(
        target=lambda: service.run("target_company:greenhouse:agoda", "1", keep_open=True),
        daemon=True,
    )
    thread.start()
    time.sleep(0.05)
    registry.mark_done(session_id)
    thread.join(timeout=2.0)
    assert session.closed is True

    # A duplicate tap after the worker already discarded the session (as the
    # production dispatch code does in its `finally`) is reported gracefully,
    # not as an error.
    registry.discard(session_id)
    from app.application.autofill.review_session import ReviewSessionCloseOutcome

    assert registry.mark_done(session_id) is ReviewSessionCloseOutcome.NOT_FOUND
