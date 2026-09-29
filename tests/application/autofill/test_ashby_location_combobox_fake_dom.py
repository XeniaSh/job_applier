"""Deterministic (non-browser) coverage for the Ashby "Current Location"
combobox fix in `ashby._fill_combobox_location` / `_read_back_combobox_location`
/ `_wait_for_location_listbox_options`.

Regression context: a live Moss run showed `combobox_location` resolved to
fill, but both fill attempts reported `interaction=False`, with the second
attempt's own read-back reporting a *non-empty* value despite that failure.
The prior implementation had two bugs neither of the existing (real-browser)
Ashby fixture tests exposes, because that fixture's synthetic autocomplete
reveals a fixed, already-populated listbox synchronously and commits a
selection by writing `input.value` directly on option click, with no
async delay and no blur-driven reset:

1. It waited for *any* `[role="option"]` to appear anywhere on the page
   before matching, rather than this control's own associated listbox --
   an unrelated, already-visible listbox elsewhere on the page could
   satisfy that wait before the real async suggestions for this field ever
   arrive, causing the match to be attempted (and fail) too early.
2. It read the combobox's raw `input_value()` immediately after clicking
   an option, rather than after a blur -- Ashby's own control only resets
   its visible text to the actual committed selection on blur, so an
   uncommitted typed query (or a transient blanked-out display) could be
   misread as either a false failure or a false (non-empty but wrong)
   success.

This file fakes only the narrow Playwright surface this exact control path
uses -- `page.locator`, `Locator.click/fill/evaluate/input_value/blur/
get_attribute`, and a resolved listbox's own `[role='option']` children --
modeling both bugs directly, never a real browser.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill import ashby
from app.application.autofill.fields import DiscoveredField

_CONTEXT = "loc-uuid"
_COMBOBOX_SELECTOR = f'[data-field-path="{_CONTEXT}"] input[role="combobox"]'


def _location_field() -> DiscoveredField:
    return DiscoveredField(label="Current Location", field_type="combobox_location", context=_CONTEXT)


class _FakeOption:
    def __init__(self, text: str) -> None:
        self.text = text
        self.visible = True
        self.on_click: object | None = None


class _FakeOptionLocator:
    def __init__(self, options: list[_FakeOption]) -> None:
        self._options = options

    def count(self) -> int:
        return len(self._options)

    def nth(self, index: int) -> "_FakeOptionLocator":
        return _FakeOptionLocator([self._options[index]])

    def is_visible(self) -> bool:
        return bool(self._options) and self._options[0].visible

    def inner_text(self) -> str:
        return self._options[0].text if self._options else ""

    def click(self, timeout: int | None = None) -> None:
        _ = timeout
        for option in self._options:
            if option.on_click is not None:
                option.on_click()


class _FakeListbox:
    """A `[role="listbox"]` node. `options` is queried live on every
    `.locator("[role='option']")` call, so appending to it later (mimicking
    an async suggestions fetch completing) is visible to any locator already
    holding a reference to this listbox, exactly like a live DOM mutation
    would be to a live Playwright locator.
    """

    def __init__(self, elem_id: str, *, visible: bool = True) -> None:
        self.elem_id = elem_id
        self.visible = visible
        self.options: list[_FakeOption] = []


class _FakeListboxLocator:
    def __init__(self, listboxes: list[_FakeListbox]) -> None:
        self._listboxes = listboxes

    def count(self) -> int:
        return len(self._listboxes)

    @property
    def first(self) -> "_FakeListboxLocator":
        return _FakeListboxLocator(self._listboxes[:1])

    def is_visible(self) -> bool:
        return bool(self._listboxes) and self._listboxes[0].visible

    def locator(self, selector: str) -> _FakeOptionLocator:
        assert selector == "[role='option']", selector
        if not self._listboxes:
            return _FakeOptionLocator([])
        return _FakeOptionLocator(list(self._listboxes[0].options))


class _FakeInput:
    def __init__(self, *, aria_controls: str) -> None:
        self.aria_controls = aria_controls
        self.value = ""
        self.blur_calls = 0
        # The value the control's own onBlur handler resets the visible
        # input to -- i.e. the true committed `selectedItemText`, empty
        # until a real selection commits. Never mutated by typing alone.
        self.committed_display = ""
        # When True, selecting an option blanks the visible input
        # immediately (mirrors "clears search" firing before the display
        # re-syncs to the committed value) rather than showing the
        # selected text right away -- only blur ever produces the
        # trustworthy display.
        self.clears_display_on_select = False
        # When True, `blur()` raises instead of resetting the display --
        # models a Playwright-level blur failure, where whatever the reset
        # this whole path depends on is simply not known to have happened.
        self.raise_on_blur = False


class _FakeInputLocator:
    def __init__(self, input_el: _FakeInput) -> None:
        self._input = input_el

    def click(self, timeout: int | None = None, force: bool = False) -> None:
        _ = timeout, force

    def fill(self, value: str, timeout: int | None = None) -> None:
        _ = timeout
        self._input.value = value

    def evaluate(self, script: str) -> None:
        _ = script

    def input_value(self, timeout: int | None = None) -> str:
        _ = timeout
        return self._input.value

    def get_attribute(self, name: str, timeout: int | None = None) -> str | None:
        _ = timeout
        if name == "aria-controls":
            return self._input.aria_controls
        return None

    def blur(self, timeout: int | None = None) -> None:
        _ = timeout
        self._input.blur_calls += 1
        if self._input.raise_on_blur:
            raise PlaywrightError("blur failed")
        self._input.value = self._input.committed_display


class _FakeLocationPage:
    """Models one associated (real) listbox plus one unrelated, already
    populated and visible decoy listbox elsewhere on the page -- the shape
    that exposed bug (1): an old page-wide `[role='option']` wait would be
    satisfied by the decoy the instant it is typed into, well before the
    real listbox's own async suggestions arrive.
    """

    def __init__(
        self,
        *,
        reveal_option_text: str | None,
        reveal_after_polls: int = 0,
        clears_display_on_select: bool = False,
        include_decoy: bool = True,
        commit_succeeds: bool = True,
    ) -> None:
        self.input = _FakeInput(aria_controls="loc-listbox")
        self.input.clears_display_on_select = clears_display_on_select
        # When False, a click "succeeds" (no exception) but never actually
        # persists the selection to `committed_display` -- models a stale or
        # reverted commit so a bare click-succeeded signal is never mistaken
        # for a real selection.
        self.commit_succeeds = commit_succeeds
        self.listbox = _FakeListbox("loc-listbox")
        self.decoy_listbox = _FakeListbox("decoy-listbox")
        self.decoy_clicked = False
        if include_decoy:
            decoy_option = _FakeOption(reveal_option_text or "Decoy")
            decoy_option.on_click = self._mark_decoy_clicked
            self.decoy_listbox.options.append(decoy_option)
        self._reveal_option_text = reveal_option_text
        self._reveal_after_polls = reveal_after_polls
        self._poll_count = 0

    def _mark_decoy_clicked(self) -> None:
        self.decoy_clicked = True

    def _select(self, option: _FakeOption) -> None:
        if self.commit_succeeds:
            self.input.committed_display = option.text
        if self.input.clears_display_on_select:
            self.input.value = ""
        else:
            self.input.value = option.text
        self.listbox.options = []

    def locator(self, selector: str):
        if selector == _COMBOBOX_SELECTOR:
            return _FakeInputLocator(self.input)
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target = match.group(1)
            if target == self.listbox.elem_id:
                return _FakeListboxLocator([self.listbox])
            if target == self.decoy_listbox.elem_id:
                return _FakeListboxLocator([self.decoy_listbox])
            return _FakeListboxLocator([])
        raise AssertionError(f"unexpected selector {selector!r}")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout
        self._poll_count += 1
        if (
            self._reveal_option_text is not None
            and not self.listbox.options
            and self._poll_count >= self._reveal_after_polls
        ):
            option = _FakeOption(self._reveal_option_text)
            option.on_click = lambda: self._select(option)
            self.listbox.options.append(option)


def test_fill_waits_for_own_associated_listbox_not_an_already_visible_decoy() -> None:
    # The real listbox starts empty (still-loading async suggestions) and
    # only gets its option after a couple of polls; an unrelated decoy
    # listbox is already visible with a same-text option the whole time.
    # The old page-wide "any [role=option] visible" wait would have been
    # satisfied by the decoy instantly and never waited for the real one.
    page = _FakeLocationPage(reveal_option_text="Berlin, Germany", reveal_after_polls=2)
    field = _location_field()

    assert ashby._fill_combobox_location(page, field, "Berlin, Germany") is True
    assert page.decoy_clicked is False
    assert page.input.blur_calls >= 1


def test_wait_for_location_listbox_options_fails_closed_when_never_populated() -> None:
    # Bounded wait, never a hang: the associated listbox exists (is
    # resolvable) but never receives any options within the bound.
    page = _FakeLocationPage(reveal_option_text=None, include_decoy=False)
    field = _location_field()
    locator = ashby._field_locator(page, field)

    assert ashby._wait_for_location_listbox_options(page, locator, timeout_ms=50) is False


def test_fill_confirms_only_after_blur_reset_not_raw_post_click_display() -> None:
    # Models the observed live shape: selecting an option blanks the
    # visible input immediately (a transient, uncommitted-looking display)
    # and only the post-blur reset shows the actually-committed text. A
    # raw post-click `input_value()` read (the prior behavior) would see
    # "" here and wrongly report failure.
    page = _FakeLocationPage(
        reveal_option_text="Berlin, Germany",
        reveal_after_polls=1,
        clears_display_on_select=True,
        include_decoy=False,
    )
    field = _location_field()

    assert ashby._fill_combobox_location(page, field, "Berlin, Germany") is True
    assert page.input.blur_calls >= 1
    assert page.input.value == "Berlin, Germany"


def test_read_back_never_exposes_uncommitted_typed_query_left_by_a_failed_fill() -> None:
    # No live option ever matches "Nowhere, Atlantis" -- the fill must fail
    # closed, and the leftover typed query sitting in the input must never
    # be reported back as if it were a real, committed selection once
    # `read_back` blurs the control (mirroring Ashby's own onBlur reset of
    # an uncommitted query back to the actually-committed, empty value).
    page = _FakeLocationPage(reveal_option_text="Berlin, Germany", reveal_after_polls=1, include_decoy=False)
    field = _location_field()

    assert ashby._fill_combobox_location(page, field, "Nowhere, Atlantis") is False
    assert page.input.value == "Nowhere, Atlantis"

    assert ashby._read_back_combobox_location(page, field) is None
    assert page.input.value == ""


def test_read_back_fails_closed_when_blur_itself_errors() -> None:
    # If `blur()` raises, whatever reset this whole read-back depends on is
    # not known to have happened. Falling through to a raw `input_value()`
    # read (the prior behavior) would risk handing back a leftover,
    # uncommitted typed query as if it were the real committed selection.
    page = _FakeLocationPage(reveal_option_text=None, include_decoy=False)
    field = _location_field()
    page.input.value = "Nowhere, Atlantis"
    page.input.raise_on_blur = True

    assert ashby._read_back_combobox_location(page, field) is None


def test_fill_requires_post_blur_match_not_mere_click_success() -> None:
    # The click on the exact option succeeds (no exception), but the
    # selection never actually persists once blurred (a stale/reverted
    # commit). Click succeeding alone must never be counted as commitment --
    # only a post-blur value that matches `wanted` may.
    page = _FakeLocationPage(
        reveal_option_text="Berlin, Germany",
        reveal_after_polls=1,
        include_decoy=False,
        commit_succeeds=False,
    )
    field = _location_field()

    assert ashby._fill_combobox_location(page, field, "Berlin, Germany") is False
    assert page.input.blur_calls >= 1
    assert page.input.value == ""
