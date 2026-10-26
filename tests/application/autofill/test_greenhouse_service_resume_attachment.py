"""Service-level, real-browser acceptance tests for Greenhouse resume
attachment handling: a real `BrowserSession` + real `GreenhouseAdapter`
driven by `AutofillService` against the modern-widget fixture
(`greenhouse_resume_modern_widget.html`), never a fake DOM double.

Adapters here are always real `GreenhouseAdapter` subclasses -- never a
faked boolean outcome -- with overrides used only to count calls or to
inject a DOM state change (e.g. "the file was removed after upload") via
the fixture's own in-page script, so every assertion still reflects the
real, unmodified production DOM-reading code (`upload_resume`,
`verify_resume_attached`, `read_back`).

Each test is skipped individually (not module-wide) when Chromium is not
installed, so a module that later grows a non-browser test is never
accidentally skipped along with it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.greenhouse import (
    GreenhouseAdapter,
    _set_resume_files,
    _wait_for_resume_attached,
)
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile
from tests.application.autofill.conftest import _VARIANT_IDS

MODERN_WIDGET_FIXTURE = Path("tests/fixtures/autofill/greenhouse_resume_modern_widget.html")
_SKIP_NO_CHROMIUM = pytest.mark.skipif(
    not chromium_executable_available(), reason="Playwright Chromium is not installed"
)


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            # Never a real/private resume path: `resolve_autofill_resume_selection`
            # always resolves to one of the four synthetic PDFs the autouse
            # `default_resume_profiles` fixture (tests/application/autofill/conftest.py)
            # writes under `tmp_path`, so this placeholder basename is never
            # actually opened.
            "application_files": {"default_resume": "placeholder_resume.pdf"},
        }
    )


class _FakeResolver:
    """Resolves straight to the local modern-widget fixture over `file://`
    -- never a live URL -- so `AutofillService.run` drives a real page
    without any network access.
    """

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = MODERN_WIDGET_FIXTURE.resolve().as_uri()
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Modern Widget Co",
            url=url,
            application_url=url,
        )


class _SpyGreenhouseAdapter(GreenhouseAdapter):
    """Real `GreenhouseAdapter` with call counting and an optional
    DOM-injection hook that runs once, immediately after a successful
    initial `upload_resume` call and before the end-of-Prepare
    `verify_resume_attached` re-check -- e.g. to simulate the widget's
    attachment being removed or replaced independently of this adapter.
    Every return value below still comes from the real, unmodified
    production DOM-reading code; the hook only changes what the page
    itself reports.
    """

    def __init__(
        self,
        *,
        after_upload: Callable[[object], None] | None = None,
        use_production_verify_timeout: bool = False,
    ) -> None:
        self.upload_resume_calls = 0
        self.verify_resume_attached_calls = 0
        self.verified_filename: str | None = None
        self.observed_completed_filename: str | None = None
        self._after_upload = after_upload
        self._use_production_verify_timeout = use_production_verify_timeout

    def upload_resume(self, page: object, resume_path: Path, field: object) -> bool:
        self.upload_resume_calls += 1
        result = super().upload_resume(page, resume_path, field)
        if result and self._after_upload is not None:
            self._after_upload(page)
        return result

    def verify_resume_attached(self, page: object, field: object, filename: str) -> bool:
        self.verify_resume_attached_calls += 1
        self.verified_filename = filename
        self.observed_completed_filename = self.read_back(page, field)
        if self._use_production_verify_timeout:
            # Delegates to the real, unmodified `GreenhouseAdapter.verify_resume_attached`
            # -- including its own `greenhouse_resume stage=final_verify` log
            # line -- only used by a caller that already knows the widget's
            # state resolves on the very first check (the success path
            # below). Never used by the never-resolves-on-its-own failure
            # scenarios below, where the production 3s default would only
            # make those tests slower without exercising anything new.
            return super().verify_resume_attached(page, field, filename)
        # A short, per-call bound (never the module-level default, and
        # never changed globally) -- the injected failure states above
        # never resolve on their own, so the production 3s default would
        # just make these tests slower without exercising anything new.
        return _wait_for_resume_attached(page, field, filename, timeout_ms=300)


class _RejectingGreenhouseAdapter(GreenhouseAdapter):
    """Real `GreenhouseAdapter` whose `upload_resume` forces the widget's
    own genuine error-rendering state (via the fixture's `__ghSetError`)
    immediately after selecting the file -- models Greenhouse itself
    rejecting the upload post-selection (e.g. a size/type check), so both
    retry attempts in `AutofillService`'s upload loop observe a real
    `error=True` widget state rather than a hardcoded False. Uses a short,
    per-call `timeout_ms` (never the module-level default) so the failure
    path stays fast without touching any production constant globally.
    """

    upload_resume_calls = 0

    def upload_resume(self, page: object, resume_path: Path, field: object) -> bool:
        self.upload_resume_calls += 1
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        _set_resume_files(page, field, str(resume_path))
        page.evaluate(
            "(args) => window.__ghSetError(args.id, args.message)",
            {"id": field.element_id or "resume", "message": "File rejected: unsupported type"},
        )
        return _wait_for_resume_attached(page, field, resume_path.name, timeout_ms=300)


class _SubmitFlagSpyBrowserSession(BrowserSession):
    """Real `BrowserSession` that captures the fixture's own
    `window.__submitClicked` flag at `close()`, while the page is still
    alive. `AutofillService.run(..., keep_open=False)` always closes the
    session before returning, so this is the only point in this real-browser
    flow where a test can still read anything off the live page after the
    whole run (including the final resume re-check) has completed. Never
    flips or reads the flag anywhere else, and never on behalf of production
    code -- the fixture's own script traps the Submit click; this only
    observes it.
    """

    def __init__(self, *, headed: bool, keep_open: bool) -> None:
        super().__init__(headed=headed, keep_open=keep_open)
        self.submit_performed: bool | None = None

    def close(self) -> None:
        if self._page is not None:
            try:
                self.submit_performed = bool(self.page.evaluate("window.__submitClicked"))
            except PlaywrightError:
                self.submit_performed = None
        super().close()


def _run(
    adapter: GreenhouseAdapter, *, resume_variant_override: str | None = None
) -> tuple[object, _SubmitFlagSpyBrowserSession]:
    sessions: list[_SubmitFlagSpyBrowserSession] = []

    def factory() -> _SubmitFlagSpyBrowserSession:
        session = _SubmitFlagSpyBrowserSession(headed=False, keep_open=False)
        sessions.append(session)
        return session

    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=adapter,
        browser_factory=factory,
        wait_for_review=lambda: None,
        resume_variant_override=resume_variant_override,
    )
    result = service.run("target_company:greenhouse:modern-widget-co", "1", keep_open=False)
    return result, sessions[0]


@_SKIP_NO_CHROMIUM
@pytest.mark.parametrize("variant_id", _VARIANT_IDS)
def test_forced_variant_uploads_and_confirms_matching_basename(
    variant_id: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Each of the four forced resume variants must upload its own
    configured PDF, have the widget's own completed-filename readout match
    that exact basename, get verified exactly once at the end of Prepare
    with no retry, never trip a false "Resume" warning, and never submit.

    Also covers the two diagnostic log lines this flow emits
    (`greenhouse_resume_selection stage=pre_handoff` in `AutofillService.run`,
    `greenhouse_resume stage=upload_attempt`/`stage=final_verify` in
    `GreenhouseAdapter`): each must name this run's own variant/basename and
    never the synthetic PDF's temp directory (`tmp_path`, shared with the
    `default_resume_profiles` fixture that wrote it) or any other full path.
    """
    adapter = _SpyGreenhouseAdapter(use_production_verify_timeout=True)
    with caplog.at_level(logging.WARNING):
        result, session = _run(adapter, resume_variant_override=variant_id)

    expected_basename = f"{variant_id}.pdf"
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.resume_uploaded is True
    assert result.resume_attachment_incomplete is False
    assert result.submit_performed is False
    assert result.resume_variant == variant_id
    assert result.resume_file == expected_basename
    assert session.submit_performed is False
    assert adapter.upload_resume_calls == 1
    assert adapter.verify_resume_attached_calls == 1
    assert adapter.verified_filename == expected_basename
    assert adapter.observed_completed_filename == expected_basename
    assert not any("resume" in warning.lower() for warning in result.warnings)

    messages = [record.getMessage() for record in caplog.records]
    selection_traces = [msg for msg in messages if "greenhouse_resume_selection stage=pre_handoff" in msg]
    assert len(selection_traces) == 1
    assert f"variant={variant_id}" in selection_traces[0]
    assert f"configured_basename={expected_basename}" in selection_traces[0]

    upload_traces = [msg for msg in messages if "greenhouse_resume stage=upload_attempt" in msg]
    assert len(upload_traces) == 1
    assert f"expected_basename={expected_basename}" in upload_traces[0]
    assert f"completed_filename={expected_basename}" in upload_traces[0]
    assert "outcome=attached" in upload_traces[0]

    final_verify_traces = [msg for msg in messages if "greenhouse_resume stage=final_verify" in msg]
    assert len(final_verify_traces) == 1
    assert f"expected_basename={expected_basename}" in final_verify_traces[0]
    assert f"completed_filename={expected_basename}" in final_verify_traces[0]
    assert "outcome=attached" in final_verify_traces[0]

    for message in (*selection_traces, *upload_traces, *final_verify_traces):
        assert str(tmp_path) not in message
        assert "\n" not in message


@_SKIP_NO_CHROMIUM
def test_upload_rejected_both_attempts_reports_needs_manual_intervention_with_uncertain_wording() -> None:
    """Both upload attempts hitting the widget's genuine error state must
    downgrade the result and describe the failure as unconfirmed (never a
    flat "was not attached"), since the control was never proven to hold
    nothing -- only that this adapter's own attempts did not confirm it.
    """
    adapter = _RejectingGreenhouseAdapter()
    result, session = _run(adapter)

    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert result.submit_performed is False
    assert session.submit_performed is False
    assert adapter.upload_resume_calls == 2
    assert any(
        "could not be confirmed" in warning and "check its current attachment" in warning
        for warning in result.warnings
    )


@_SKIP_NO_CHROMIUM
def test_final_removal_after_successful_upload_reports_needs_manual_intervention_without_reupload() -> None:
    """The widget confirms the upload, then the attachment is removed (e.g.
    the candidate clicks "Remove file") before the end-of-Prepare re-check
    runs -- the final verify must catch this, downgrade the result, and
    must never attempt a second upload to "fix" it.
    """
    adapter = _SpyGreenhouseAdapter(
        after_upload=lambda page: page.evaluate("window.__ghRestoreInput('resume')")
    )
    result, session = _run(adapter)

    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert result.submit_performed is False
    assert session.submit_performed is False
    assert adapter.upload_resume_calls == 1
    assert adapter.verify_resume_attached_calls == 1
    assert any(
        "could not be confirmed at the end of Prepare" in warning
        and "check its current attachment" in warning
        for warning in result.warnings
    )


@_SKIP_NO_CHROMIUM
def test_final_mismatch_after_successful_upload_reports_needs_manual_intervention_without_reupload() -> None:
    """The widget confirms the upload, then shows a *different* completed
    filename by the time the end-of-Prepare re-check runs (e.g. another
    control's state leaked in) -- the mismatch must be caught the same way
    a disappearance is, and must never be treated as this upload's own
    success.
    """
    adapter = _SpyGreenhouseAdapter(
        after_upload=lambda page: page.evaluate(
            "window.__ghSetCompleted('someone-elses-resume.pdf')"
        )
    )
    result, session = _run(adapter)

    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert result.submit_performed is False
    assert session.submit_performed is False
    assert adapter.upload_resume_calls == 1
    assert adapter.verify_resume_attached_calls == 1
    assert adapter.observed_completed_filename == "someone-elses-resume.pdf"
