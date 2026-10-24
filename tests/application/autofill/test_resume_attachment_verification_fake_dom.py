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

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.ashby import _file_is_attached, _wait_for_file_attached
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import (
    _resume_file_locator,
    _resume_is_attached,
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
