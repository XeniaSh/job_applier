"""Unit tests for the Lever resume-attachment verification helpers.

These exercise `_resume_is_attached` / `_wait_for_resume_attached` /
`LeverAdapter.verify_resume_attached` directly against small hand-written
Playwright `Page`/`Locator` stand-ins -- never a real browser -- so they run
even where Playwright Chromium cannot launch (e.g. a sandboxed CI/agent
environment). The real public Lever `/apply` markup
(`<li class="application-question resume"><label>...<a
class="visible-resume-upload"><span class="filename"></span>...<input
class="application-file-input invisible-resume-upload" ... type="file">`)
has no `required` attribute on the input itself and exposes the actual
attachment state only via the input's own `FileList` and the sibling
`.filename` span Lever's own JS updates asynchronously -- the scenarios
below model exactly that shape, including the "file selected but UI not
yet caught up" transient state and a later "attachment quietly disappears"
state that only a final re-check (not just the upload-time check) can
catch.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import (
    LeverAdapter,
    _resume_is_attached,
    _wait_for_resume_attached,
)

_FILENAME = "resume.pdf"


class _FakeResumePage:
    """Stand-in for a Playwright `Page`, implementing only the two calls
    `_resume_is_attached` makes (`evaluate` for the raw `FileList` read and
    for the `.filename` UI readback) and the one `_wait_for_resume_attached`
    makes (`wait_for_timeout`). `states` is consumed one entry per
    `_resume_is_attached` call (clamped to the last entry once exhausted),
    modeling the page settling over successive polls; `wait_for_timeout` is
    a no-op counter, never a real sleep, so these tests run instantly
    regardless of the bounded timeout passed in. `fail_filename_readback`
    models the `.filename` `page.evaluate` call itself raising (e.g. a
    mid-navigation page), distinct from it running fine and finding no such
    element.
    """

    def __init__(
        self,
        states: list[tuple[list[str], str | None]],
        *,
        fail_filename_readback: bool = False,
    ) -> None:
        self._states = states
        self._index = 0
        self.wait_calls = 0
        self._fail_filename_readback = fail_filename_readback

    def evaluate(self, script: str, _arg: object = None) -> object:
        files, filename_text = self._states[min(self._index, len(self._states) - 1)]
        if "el.files" in script:
            return list(files)
        if self._fail_filename_readback:
            raise PlaywrightError("evaluate failed")
        return filename_text

    def wait_for_timeout(self, _timeout_ms: int) -> None:
        # Advances to the next state only on an actual poll-to-poll wait
        # (never mid-poll, between the `FileList` read and the `.filename`
        # read `_resume_is_attached` makes for the *same* poll), so a run
        # that short-circuits on the `FileList` check alone still steps the
        # state machine forward exactly once per real retry.
        self.wait_calls += 1
        self._index += 1


def _resume_field() -> DiscoveredField:
    return DiscoveredField(
        label="Resume/CV",
        name="resume",
        field_type="file",
        required=True,
        element_id="resume-upload-input",
    )


def test_attached_requires_both_filelist_and_matching_filename_ui() -> None:
    """A `FileList` hit alone is not enough once the markup exposes a
    `.filename` readback -- Lever's own async parse/working state can leave
    the raw input showing the file while the visible UI is still blank, and
    that must not read as attached.
    """
    page = _FakeResumePage(states=[([_FILENAME], "")])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is False


def test_attached_true_when_filelist_and_ui_both_confirm() -> None:
    page = _FakeResumePage(states=[([_FILENAME], _FILENAME)])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is True


def test_attached_false_for_wrong_file_in_filelist() -> None:
    """A different, stale file sitting in the control's `FileList` (e.g. a
    previous attempt's leftover) must never be read as this upload having
    attached.
    """
    page = _FakeResumePage(states=[(["old_cv.pdf"], "old_cv.pdf")])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is False


def test_attached_false_for_partial_or_wrong_ui_filename() -> None:
    """A displayed filename that is merely a substring of (or a superstring
    containing) the expected name must not pass -- e.g. a truncated UI
    readback or a different file that happens to share a common prefix/
    suffix. Only an exact (whitespace/path-prefix normalized) match may
    confirm attachment.
    """
    page = _FakeResumePage(states=[([_FILENAME], "resume")])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is False

    page = _FakeResumePage(states=[([_FILENAME], f"not_{_FILENAME}")])
    assert _resume_is_attached(page, field, _FILENAME) is False


def test_attached_true_for_ui_filename_with_harmless_whitespace_or_path_prefix() -> None:
    """Only whitespace and a browser-style path prefix are normalized
    before the exact comparison -- not the filename text itself.
    """
    page = _FakeResumePage(states=[([_FILENAME], f"  {_FILENAME}  ")])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is True

    page = _FakeResumePage(states=[([_FILENAME], f"C:\\fakepath\\{_FILENAME}")])
    assert _resume_is_attached(page, field, _FILENAME) is True


def test_attached_false_when_filename_readback_evaluation_fails() -> None:
    """A `.filename` readback that could not even be evaluated (as opposed
    to running fine and finding nothing) must fail closed -- never fall
    through to a `FileList`-only success.
    """
    page = _FakeResumePage(states=[([_FILENAME], _FILENAME)], fail_filename_readback=True)
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is False


def test_attached_true_without_ui_element_present() -> None:
    """Back-compat: a markup shape with no `.filename` span at all (the
    existing plain `<label for=...>` test fixtures) must still confirm
    attachment from the raw `FileList` alone -- `None` from the UI read is
    "no signal", not "blocked".
    """
    page = _FakeResumePage(states=[([_FILENAME], None)])
    field = _resume_field()
    assert _resume_is_attached(page, field, _FILENAME) is True


def test_wait_for_resume_attached_tolerates_transient_unsettled_state() -> None:
    """The first poll sees an empty `FileList` (upload not yet applied by
    the browser), the second sees the file listed but the `.filename` UI
    still blank (Lever's own async handler hasn't run yet), and only the
    third shows both settled -- a bounded wait must ride this out rather
    than reporting failure on the first unsettled read.
    """
    page = _FakeResumePage(
        states=[
            ([], None),
            ([_FILENAME], ""),
            ([_FILENAME], _FILENAME),
        ]
    )
    field = _resume_field()
    assert _wait_for_resume_attached(page, field, _FILENAME, timeout_ms=5_000) is True
    assert page.wait_calls == 2


def test_wait_for_resume_attached_is_bounded_not_indefinite() -> None:
    """A control that never settles must fail after the bound, not hang."""
    page = _FakeResumePage(states=[([], None)])
    field = _resume_field()
    assert _wait_for_resume_attached(page, field, _FILENAME, timeout_ms=50) is False


def test_verify_resume_attached_catches_a_later_disappearance() -> None:
    """`LeverAdapter.verify_resume_attached` is the end-of-Prepare re-check:
    it must report failure once the control no longer shows the file, even
    though an earlier, separate check (modeled here by the first state
    this same fake page would have returned) had confirmed it.
    """
    page = _FakeResumePage(states=[([], None)])
    field = _resume_field()
    adapter = LeverAdapter()
    assert adapter.verify_resume_attached(page, field, _FILENAME) is False


def test_verify_resume_attached_confirms_a_still_present_file() -> None:
    page = _FakeResumePage(states=[([_FILENAME], _FILENAME)])
    field = _resume_field()
    adapter = LeverAdapter()
    assert adapter.verify_resume_attached(page, field, _FILENAME) is True
