"""Synthetic (no-Chromium) tests for the Greenhouse/Ashby final resume
attachment re-check (`verify_resume_attached` -> `_wait_for_resume_attached`
/ `_wait_for_file_attached`).

These use a fake Page double instead of a real browser so they run even
where Playwright's Chromium binary is not installed -- the existing
Greenhouse/Ashby adapter test modules are gated on
`chromium_executable_available()` and cannot exercise this at all there.
Covers: the file disappearing between upload and this final re-check, the
wrong filename being reported, and the poll itself raising a
`PlaywrightError` (e.g. the page/context closed concurrently) -- which must
fail this one readback closed, never bubble out as an unrelated
`UNEXPECTED_ERROR` for the whole Prepare run.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.ashby import _file_is_attached, _wait_for_file_attached
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import (
    GreenhouseAdapter,
    _resume_file_locator,
    _resume_is_attached,
    _resume_widget_status,
    _set_resume_files,
    _wait_for_resume_attached,
)


class FakeFilesPage:
    """Minimal double for the resume-verification readback: `evaluate`
    returns the configured file list (or raises to simulate a page-level
    error), `wait_for_timeout` just counts calls (or raises, to simulate
    the page/context closing mid-poll).
    """

    def __init__(
        self,
        *,
        files_by_call: list[list[str]] | None = None,
        evaluate_error: Exception | None = None,
        wait_for_timeout_error: Exception | None = None,
        raise_after_waits: int = 0,
    ) -> None:
        self._files_by_call = files_by_call or []
        self._evaluate_error = evaluate_error
        self._wait_for_timeout_error = wait_for_timeout_error
        self._raise_after_waits = raise_after_waits
        self.evaluate_calls = 0
        self.wait_calls = 0

    def evaluate(self, script: str, arg: object = None) -> list[str]:
        _ = script, arg
        if self._evaluate_error is not None:
            raise self._evaluate_error
        index = min(self.evaluate_calls, len(self._files_by_call) - 1)
        self.evaluate_calls += 1
        return list(self._files_by_call[index]) if self._files_by_call else []

    def wait_for_timeout(self, ms: int) -> None:
        _ = ms
        self.wait_calls += 1
        if self._wait_for_timeout_error is not None and self.wait_calls > self._raise_after_waits:
            raise self._wait_for_timeout_error


def _gh_field() -> DiscoveredField:
    return DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume")


def _ashby_field() -> DiscoveredField:
    return DiscoveredField(
        label="Resume/CV", field_type="file", required=True, element_id="resume-upload-input"
    )


# --- Greenhouse ----------------------------------------------------------


def test_greenhouse_resume_attached_true_when_filename_matches() -> None:
    page = FakeFilesPage(files_by_call=[["resume.pdf"]])
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is True


def test_greenhouse_wait_for_resume_attached_detects_disappearance() -> None:
    """The file was attached earlier in the run but has since disappeared
    (e.g. a later page-side script cleared the control) -- the final
    re-check must report False, not a stale True.
    """
    page = FakeFilesPage(files_by_call=[[]])
    assert _wait_for_resume_attached(page, _gh_field(), "resume.pdf", timeout_ms=10) is False


def test_greenhouse_wait_for_resume_attached_detects_wrong_filename() -> None:
    page = FakeFilesPage(files_by_call=[["unexpected-other-file.pdf"]])
    assert _wait_for_resume_attached(page, _gh_field(), "resume.pdf", timeout_ms=10) is False


def test_greenhouse_wait_for_resume_attached_fails_closed_on_evaluate_error() -> None:
    page = FakeFilesPage(evaluate_error=PlaywrightError("Target page, context or browser has been closed"))
    assert _wait_for_resume_attached(page, _gh_field(), "resume.pdf", timeout_ms=10) is False


def test_greenhouse_wait_for_resume_attached_fails_closed_on_wait_for_timeout_error() -> None:
    """`page.wait_for_timeout` itself raising mid-poll (not just `evaluate`)
    must still fail this readback closed instead of propagating out of
    `verify_resume_attached` as an unexpected whole-Prepare error.
    """
    page = FakeFilesPage(
        files_by_call=[[]],
        wait_for_timeout_error=PlaywrightError("Target page, context or browser has been closed"),
        raise_after_waits=0,
    )
    assert _wait_for_resume_attached(page, _gh_field(), "resume.pdf", timeout_ms=10_000) is False


# --- Modern widget status (script-aware fake, no Chromium) -----------------


class FakeWidgetStatusPage:
    """Double that distinguishes `_RESUME_WIDGET_STATUS_JS` calls from
    `_file_names`'s own legacy `.files` evaluate by the one substring unique
    to each (`"completedFilename"` only appears in the widget-status
    script) -- so `_resume_is_attached`'s "modern group found -> trust it;
    otherwise fall back to legacy `.files`" branching can be exercised
    without a real browser.
    """

    def __init__(
        self,
        *,
        widget_status: dict[str, object] | None = None,
        legacy_files: list[str] | None = None,
        widget_status_error: Exception | None = None,
    ) -> None:
        self._widget_status = widget_status
        self._legacy_files = legacy_files or []
        self._widget_status_error = widget_status_error

    def evaluate(self, script: str, arg: object = None) -> object:
        _ = arg
        if "completedFilename" in script:
            if self._widget_status_error is not None:
                raise self._widget_status_error
            return self._widget_status
        return list(self._legacy_files)


def test_widget_status_none_when_no_modern_group_present() -> None:
    page = FakeWidgetStatusPage(widget_status=None, legacy_files=["resume.pdf"])
    status, observation_failed = _resume_widget_status(page, _gh_field())
    assert status is None
    assert observation_failed is False


def test_widget_status_observation_failed_when_script_raises() -> None:
    """The widget-status script itself raising (e.g. the page/context
    closed concurrently) must be reported distinctly from a genuine "no
    modern group here" -- callers must never treat the two as the same
    thing.
    """
    page = FakeWidgetStatusPage(
        widget_status=None,
        widget_status_error=PlaywrightError("Target page, context or browser has been closed"),
    )
    status, observation_failed = _resume_widget_status(page, _gh_field())
    assert status is None
    assert observation_failed is True


def test_resume_is_attached_fails_closed_on_widget_observation_error_even_with_native_files() -> None:
    """Regression: a fake page that raises on the widget-status script but
    still answers the legacy `_file_names` script with a real-looking
    `["resume.pdf"]` used to make `_resume_is_attached` report True -- the
    failed widget read was silently treated as "no modern group", falling
    through to the legacy native-input check. A failed observation must
    fail this check closed instead, leaving the bounded poll in
    `_wait_for_resume_attached` to retry.
    """
    page = FakeWidgetStatusPage(
        widget_status=None,
        legacy_files=["resume.pdf"],
        widget_status_error=PlaywrightError("Target page, context or browser has been closed"),
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


def test_read_back_returns_none_on_widget_observation_error_even_with_native_files() -> None:
    """Mirrors the `_resume_is_attached` regression above: `read_back` must
    also fail closed (None) on a failed widget-status observation, never
    report the legacy native input's filename as if the modern group had
    genuinely been proven absent.
    """
    page = FakeWidgetStatusPage(
        widget_status=None,
        legacy_files=["resume.pdf"],
        widget_status_error=PlaywrightError("Target page, context or browser has been closed"),
    )
    assert GreenhouseAdapter().read_back(page, _gh_field()) is None


def test_resume_is_attached_falls_back_to_legacy_files_when_no_modern_group() -> None:
    page = FakeWidgetStatusPage(widget_status=None, legacy_files=["resume.pdf"])
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is True


def test_resume_is_attached_true_on_completed_modern_widget() -> None:
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": False,
            "completedFilename": "resume.pdf",
            "pending": False,
            "error": False,
        }
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is True


def test_resume_is_attached_false_while_modern_widget_pending() -> None:
    """A stale legacy `.files` value must never be accepted once a modern
    group exists and reports `pending` -- the modern group's own state is
    authoritative, so this must fail closed even if `legacy_files` still
    shows the file (e.g. the native input briefly coexists mid-transition).
    """
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": False,
            "completedFilename": None,
            "pending": True,
            "error": False,
        },
        legacy_files=["resume.pdf"],
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


def test_resume_is_attached_false_on_modern_widget_error_even_with_stale_legacy_files() -> None:
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": True,
            "completedFilename": None,
            "pending": False,
            "error": True,
        },
        legacy_files=["resume.pdf"],
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


def test_resume_is_attached_false_when_completed_filename_coexists_with_pending() -> None:
    """Regression: a stale `completedFilename` must never be trusted once the
    same group also reports `pending` -- unlike the existing
    `completedFilename=None` pending test above, this is the case where the
    readout text is genuinely present but a fresh upload is simultaneously
    in flight for the same group.
    """
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": False,
            "completedFilename": "resume.pdf",
            "pending": True,
            "error": False,
        }
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


def test_resume_is_attached_false_when_completed_filename_coexists_with_error() -> None:
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": False,
            "completedFilename": "resume.pdf",
            "pending": False,
            "error": True,
        }
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


def test_resume_is_attached_exact_basename_not_substring_on_modern_widget() -> None:
    """`completedFilename` containing the expected basename as a substring
    (e.g. a longer, unrelated filename) must never match -- only an exact
    basename counts.
    """
    page = FakeWidgetStatusPage(
        widget_status={
            "nativeInputPresent": False,
            "completedFilename": "not_resume.pdf",
            "pending": False,
            "error": False,
        }
    )
    assert _resume_is_attached(page, _gh_field(), "resume.pdf") is False


# --- Upload diagnostics log bounding (script-aware fake, no Chromium) ------


class _FakeKeyboard:
    def press(self, _key: str) -> None:
        pass


class _FakeRejectingFileLocator:
    """Minimal `Locator` double whose `set_input_files` raises a configured
    error -- drives `GreenhouseAdapter.upload_resume`'s own
    `except PlaywrightError as exc: set_files_error = type(exc).__name__`
    branch without a real browser.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    @property
    def first(self) -> "_FakeRejectingFileLocator":
        return self

    def count(self) -> int:
        return 1

    def set_input_files(self, *args: object, **kwargs: object) -> None:
        raise self._error


class FakeUploadDiagnosticsPage:
    """Full double for the two DOM calls `GreenhouseAdapter.upload_resume`
    itself makes (`_set_resume_files`'s locator write, the widget-status
    poll inside `_wait_for_resume_attached`) -- no real browser -- so the
    production `greenhouse_resume stage=upload_attempt` log line can be
    inspected end-to-end for leakage.
    """

    keyboard = _FakeKeyboard()

    def __init__(self, *, set_files_error: Exception, completed_filename: str) -> None:
        self._set_files_error = set_files_error
        self._completed_filename = completed_filename

    def locator(self, _selector: str) -> _FakeRejectingFileLocator:
        return _FakeRejectingFileLocator(self._set_files_error)

    def evaluate(self, script: str, _arg: object = None) -> dict[str, object]:
        _ = script
        return {
            "nativeInputPresent": False,
            "completedFilename": self._completed_filename,
            "pending": False,
            "error": False,
        }

    def wait_for_timeout(self, _ms: int) -> None:
        pass


def test_upload_resume_diagnostic_log_is_bounded_and_never_leaks_raw_error_or_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression: a rejected `set_input_files` write (a real Playwright
    error whose own message can carry a full filesystem path or other
    sensitive detail) together with a completed-filename readout that
    itself contains a newline and is far longer than any real basename --
    the `upload_resume` diagnostic log line must stay one bounded line that
    names only the error's *class*, never its message, and never any
    filesystem path (not even this call's own resume path, only its
    basename). The expected and observed (widget-reported) basenames are
    the same here only so `_wait_for_resume_attached` confirms on its very
    first check -- this test is about the log line's shape, not the
    boolean outcome, and a real per-call timeout override (as other tests
    in this module use) is not available through this public method.
    """
    sensitive_message = (
        "Timeout 5000ms exceeded.\n=== logs ===\nwaiting for locator "
        '"input"\n/Users/ada/Secret Employer Names/resume_final.pdf'
    )
    basename = "expected\nand-observed-basename-" + "x" * 200 + ".pdf"
    page = FakeUploadDiagnosticsPage(
        set_files_error=PlaywrightError(sensitive_message),
        completed_filename=basename,
    )
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume")

    with caplog.at_level(logging.WARNING, logger="app.application.autofill.greenhouse"):
        result = GreenhouseAdapter().upload_resume(
            page, Path("/private/tmp/some-run") / basename, field
        )

    assert result is True
    traces = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("greenhouse_resume stage=upload_attempt")
    ]
    assert len(traces) == 1
    message = traces[0]

    assert "\n" not in message
    # `type(exc).__name__` on `playwright.sync_api.Error` (imported here,
    # like in `greenhouse.py`/`ashby.py`, as `PlaywrightError`) is the real
    # class name "Error" -- never the local import alias -- matching the
    # existing `click_raised=Error` precedent elsewhere in this test suite
    # (e.g. test_greenhouse_prepare_diagnostics.py).
    assert "set_files_error_class=Error" in message
    assert sensitive_message not in message
    assert "Timeout 5000ms exceeded" not in message
    assert "/Users/ada/Secret Employer Names" not in message
    assert "/private/tmp/some-run" not in message
    assert message.count("...(truncated)") == 2


# --- Ashby -----------------------------------------------------------------


def test_ashby_file_attached_true_when_filename_matches() -> None:
    page = FakeFilesPage(files_by_call=[["resume.pdf"]])
    assert _file_is_attached(page, _ashby_field(), "resume.pdf") is True


def test_ashby_wait_for_file_attached_detects_disappearance() -> None:
    page = FakeFilesPage(files_by_call=[[]])
    assert _wait_for_file_attached(page, _ashby_field(), "resume.pdf", timeout_ms=10) is False


def test_ashby_wait_for_file_attached_detects_wrong_filename() -> None:
    page = FakeFilesPage(files_by_call=[["unexpected-other-file.pdf"]])
    assert _wait_for_file_attached(page, _ashby_field(), "resume.pdf", timeout_ms=10) is False


def test_ashby_wait_for_file_attached_fails_closed_on_evaluate_error() -> None:
    page = FakeFilesPage(evaluate_error=PlaywrightError("Target page, context or browser has been closed"))
    assert _wait_for_file_attached(page, _ashby_field(), "resume.pdf", timeout_ms=10) is False


def test_ashby_wait_for_file_attached_fails_closed_on_wait_for_timeout_error() -> None:
    page = FakeFilesPage(
        files_by_call=[[]],
        wait_for_timeout_error=PlaywrightError("Target page, context or browser has been closed"),
        raise_after_waits=0,
    )
    assert _wait_for_file_attached(page, _ashby_field(), "resume.pdf", timeout_ms=10_000) is False


# --- Upload-target scoping (no cross-input "#resume" fallback) -------------


class _FakeLocator:
    """Minimal double for a Playwright `Locator`: `count()`/`.first` reflect
    whether `selector` was pre-registered as "present" on the fake page, and
    `set_input_files` records `(selector, path)` so a test can assert exactly
    which element an upload actually targeted -- never a different,
    unregistered selector such as a decoy generic "#resume" input.
    """

    def __init__(self, selector: str, *, present: set[str], calls: list[tuple[str, str]]) -> None:
        self.selector = selector
        self._present = present
        self._calls = calls

    def count(self) -> int:
        return 1 if self.selector in self._present else 0

    @property
    def first(self) -> "_FakeLocator":
        return self

    def locator(self, selector: str) -> "_FakeLocator":
        return _FakeLocator(f"{self.selector} >> {selector}", present=self._present, calls=self._calls)

    def set_input_files(self, path: str, *, timeout: int | None = None, no_wait_after: bool | None = None) -> None:
        _ = timeout, no_wait_after
        self._calls.append((self.selector, path))

    def click(self, *, timeout: int | None = None) -> None:
        _ = timeout
        self._calls.append((self.selector, "__click__"))


class _FakeFileChooser:
    """Records any `set_files` call made on a chooser opened via
    `Page.expect_file_chooser` -- a test can then assert that a decoy
    chooser was never actually used to attach the resume.
    """

    def __init__(self) -> None:
        self.set_files_calls: list[str] = []

    def set_files(self, path: str) -> None:
        self.set_files_calls.append(path)


class _FakeFileChooserContext:
    def __init__(self, chooser: _FakeFileChooser) -> None:
        self.value = chooser

    def __enter__(self) -> "_FakeFileChooserContext":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeLocatorPage:
    """Minimal double for a Playwright `Page`, only `locator()`/`.url`/
    `expect_file_chooser()` -- enough to exercise `_resume_file_locator`/
    `_set_resume_files` without a real browser (Playwright's Chromium binary
    is not available here).
    """

    def __init__(self, *, present: set[str], url: str = "https://example.test/apply") -> None:
        self._present = present
        self.url = url
        self.calls: list[tuple[str, str]] = []
        self.file_chooser = _FakeFileChooser()

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector, present=self._present, calls=self.calls)

    def expect_file_chooser(self, *, timeout: int | None = None) -> _FakeFileChooserContext:
        _ = timeout
        return _FakeFileChooserContext(self.file_chooser)


def _generic_resume_selector() -> str:
    return 'input[type="file"][id="resume"], input[type="file"][name*="resume" i]'


def test_resume_file_locator_prefers_explicit_field_identity_over_generic_resume() -> None:
    """Both the field's own typed id and a generic decoy "#resume" input are
    present -- the field's own identity must win, never the decoy.
    """
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume_other")
    page = _FakeLocatorPage(
        present={'input[type="file"][id="resume_other"]', _generic_resume_selector()}
    )
    target = _resume_file_locator(page, field)
    assert target.selector == 'input[type="file"][id="resume_other"]'


def test_resume_file_locator_never_falls_back_to_generic_resume_when_field_identity_missing() -> None:
    """The field explicitly identifies `resume_other`, which is missing from
    the DOM; a generic "#resume" input exists elsewhere on the page (e.g. a
    decoy, or some unrelated upload control) -- it must never be used as a
    substitute for the explicitly identified, missing control.
    """
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume_other")
    page = _FakeLocatorPage(present={_generic_resume_selector()})
    target = _resume_file_locator(page, field)
    assert target.count() == 0
    assert target.selector != _generic_resume_selector()


def test_set_resume_files_never_uploads_into_decoy_resume_input() -> None:
    """End-to-end through `_set_resume_files`: with the field's own control
    missing and only a decoy generic "#resume" input present (and no
    adjacent chooser button), no upload call must ever target that decoy.
    """
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume_other")
    page = _FakeLocatorPage(present={_generic_resume_selector()})
    _set_resume_files(page, field, "/tmp/resume.pdf")
    assert page.calls == []


def test_set_resume_files_uploads_into_explicitly_identified_input() -> None:
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume_other")
    page = _FakeLocatorPage(present={'input[type="file"][id="resume_other"]'})
    _set_resume_files(page, field, "/tmp/resume.pdf")
    assert page.calls == [('input[type="file"][id="resume_other"]', "/tmp/resume.pdf")]


def _decoy_chooser_button_selector() -> str:
    return '#resume >> xpath=.. >> button:not([type="submit"])'


def test_set_resume_files_never_opens_decoy_resume_chooser_for_different_missing_field() -> None:
    """The field explicitly identifies `resume_other`, which is missing from
    the DOM. An unrelated "#resume" widget elsewhere on the page *does* have
    an adjacent non-submit chooser button -- that chooser belongs to the
    other, generic "#resume" control, not to this field, and must never be
    opened or used as a substitute upload target for it.
    """
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume_other")
    page = _FakeLocatorPage(present={_decoy_chooser_button_selector()})
    _set_resume_files(page, field, "/tmp/resume.pdf")
    assert page.calls == []
    assert page.file_chooser.set_files_calls == []


def test_set_resume_files_still_uses_default_resume_chooser_when_identity_matches() -> None:
    """Legitimate case: the field's own identity *is* the default "#resume"
    widget (no distinguishing id, or `element_id == "resume"`), its input is
    missing (e.g. a dropzone that renders one only after a chooser
    interaction), and an adjacent non-submit chooser button exists -- this
    default chooser fallback must keep working.
    """
    field = DiscoveredField(label="Resume/CV", field_type="file", required=True, element_id="resume")
    page = _FakeLocatorPage(present={_decoy_chooser_button_selector()})
    _set_resume_files(page, field, "/tmp/resume.pdf")
    assert page.calls == [(_decoy_chooser_button_selector(), "__click__")]
    assert page.file_chooser.set_files_calls == ["/tmp/resume.pdf"]
