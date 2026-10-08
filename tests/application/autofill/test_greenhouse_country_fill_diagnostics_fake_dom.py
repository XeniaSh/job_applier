"""Deterministic (non-browser) coverage for two things this codebase cannot
otherwise prove without a live run:

1. That a required "current/previous job title" question -- discovered as
   an ordinary text input, mapped to `profile.employment.current_title`,
   and filled through the plain `_fill_text` path (never a combobox) --
   actually fills and confirms end to end through the real
   `GreenhouseAdapter`/`service._fill_and_confirm` code.

2. That `greenhouse._fill_combobox` (the country field's fill path) scopes
   option discovery/matching to the control's own live listbox even when
   an unrelated widget (a phone country-code picker, "iti") already has
   its own visible `[role="option"]` menu with an identically-worded
   option -- and that its dial-code-fragment confirmation fallback
   (`react_controls.confirm_option_selected`, via
   `read_committed_selected_value`) reads the clicked option's label from
   *before* the click, never by re-querying the option locator afterwards
   -- which a committed React-select can detach/unmount as part of the
   very same click.
"""

from __future__ import annotations

import logging
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill import react_controls, service
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import (
    GreenhouseAdapter,
    _combobox_field_help,
    _country_other_instructed,
    _fill_combobox,
)
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind

_SERVICE_LOGGER = "app.application.autofill.service"
_GREENHOUSE_LOGGER = "app.application.autofill.greenhouse"


class _FakeElement:
    def __init__(
        self,
        tag: str,
        *,
        classes: str = "",
        role: str | None = None,
        text: str = "",
        elem_id: str | None = None,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self.tag = tag
        self.classes = classes
        self.role = role
        self.text = text
        self.elem_id = elem_id
        self.attrs = dict(attrs or {})
        self.parent: _FakeElement | None = None
        self.children: list[_FakeElement] = []
        self.visible = True
        self.value = ""
        # Set True the instant a committed React-select would unmount this
        # node (e.g. inside its own on-click handler) -- any locator method
        # called on it afterwards raises, so a regression that re-reads the
        # clicked option post-click fails loudly instead of silently
        # "working" against a fake that never actually detaches anything.
        self.detached = False

    def add_child(self, child: "_FakeElement") -> "_FakeElement":
        child.parent = self
        self.children.append(child)
        return child

    def detach(self) -> None:
        self.detached = True
        if self.parent is not None and self in self.parent.children:
            self.parent.children.remove(self)

    def subtree(self):
        yield self
        for child in self.children:
            yield from child.subtree()


def _root_of(el: _FakeElement) -> _FakeElement:
    node = el
    while node.parent is not None:
        node = node.parent
    return node


def _aggregate_text(node: _FakeElement) -> str:
    return " ".join(part for part in (descendant.text for descendant in node.subtree()) if part)


def _simulate_combobox_field_help(el: _FakeElement) -> str:
    """Fake-DOM equivalent of `greenhouse._COMBOBOX_FIELD_HELP_JS`, so this
    suite can prove the extractor's own scoping logic (aria-describedby,
    else a bounded named+own-label ancestor climb, else empty) rather than
    just asserting on whatever `context=` a test passes in directly.
    """
    described_by = (el.attrs.get("aria-describedby") or "").strip()
    if described_by:
        root = _root_of(el)
        parts = [
            match.text
            for token in described_by.split()
            for match in root.subtree()
            if match.elem_id == token and match.text
        ]
        joined = " ".join(" ".join(parts).split())
        if joined:
            return joined
    node = el.parent
    for _ in range(4):
        if node is None or node.tag.lower() in {"form", "body", "html"}:
            break
        marker = f"{node.classes or ''} {node.elem_id or ''}"
        named = (
            re.search(r"question", marker, re.I) is not None
            or re.search(r"(?:^|\s)field\b", marker, re.I) is not None
            or node.tag.lower() in {"fieldset", "section"}
        )
        if named:
            own_label = next(
                (
                    child
                    for child in node.children
                    if child.tag.lower() in {"label", "legend"} or "label" in (child.classes or "")
                ),
                None,
            )
            if own_label is not None:
                return " ".join(_aggregate_text(node).split())
        node = node.parent
    return ""


class _FakeLocator:
    def __init__(self, elements: list[_FakeElement]) -> None:
        self._elements = elements

    def _live(self) -> _FakeElement:
        el = self._elements[0]
        if el.detached:
            raise AssertionError(
                f"stale read on detached option id={el.elem_id!r} -- must be captured before click"
            )
        return el

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> "_FakeLocator":
        return _FakeLocator([self._elements[index]])

    @property
    def first(self) -> "_FakeLocator":
        return self.nth(0) if self._elements else _FakeLocator([])

    def is_visible(self) -> bool:
        return bool(self._elements) and self._elements[0].visible and not self._elements[0].detached

    def get_attribute(self, name: str, timeout: int | None = None) -> str | None:
        _ = timeout
        if not self._elements:
            return None
        el = self._live()
        if name == "id":
            return el.elem_id
        return el.attrs.get(name)

    def inner_text(self, timeout: int | None = None) -> str:
        _ = timeout
        return self._live().text if self._elements else ""

    def text_content(self, timeout: int | None = None) -> str:
        _ = timeout
        if not self._elements:
            return ""
        parts: list[str] = []

        def _walk(node: _FakeElement) -> None:
            if node.text:
                parts.append(node.text)
            for child in node.children:
                _walk(child)

        _walk(self._live())
        return " ".join(parts)

    def input_value(self, timeout: int | None = None) -> str:
        _ = timeout
        return self._elements[0].value if self._elements else ""

    def evaluate(self, script: str, arg: object = None) -> object:
        _ = arg
        if not self._elements:
            return None
        el = self._elements[0]
        if "tagName.toLowerCase() === 'select'" in script:
            return el.tag == "select"
        if "el.tagName.toLowerCase()" in script:
            return el.tag
        if "aria-describedby" in script:
            return _simulate_combobox_field_help(el)
        return None

    def click(self, timeout: int | None = None, force: bool = False) -> None:
        _ = timeout, force
        for el in self._elements:
            if el.detached:
                raise AssertionError(f"click on already-detached element id={el.elem_id!r}")
            on_click = getattr(el, "_on_click", None)
            if on_click is not None:
                on_click()

    def fill(self, value: str, timeout: int | None = None) -> None:
        _ = timeout
        for el in self._elements:
            el.value = value

    def press_sequentially(self, value: str, delay: int | None = None, timeout: int | None = None) -> None:
        _ = delay, timeout
        self.fill(value)

    def wait_for(self, state: str | None = None, timeout: int | None = None) -> None:
        _ = state, timeout

    def locator(self, selector: str) -> "_FakeLocator":
        if selector.startswith("xpath=ancestor::"):
            return self._ancestor_locator(selector)
        if "role='option'" in selector or 'role="option"' in selector:
            results: list[_FakeElement] = []
            for root in self._elements:
                for el in root.subtree():
                    if el is root or el.detached:
                        continue
                    if el.role == "option":
                        results.append(el)
            return _FakeLocator(results)
        if "single-value" in selector or "singleValue" in selector:
            results = []
            for root in self._elements:
                for el in root.subtree():
                    if el is root:
                        continue
                    if "single-value" in (el.classes or "") or "singleValue" in (el.classes or ""):
                        results.append(el)
            return _FakeLocator(results)
        if "clear-indicator" in selector or "clearIndicator" in selector or "aria-label='Clear" in selector:
            results = []
            for root in self._elements:
                for el in root.subtree():
                    if el is root or el.detached:
                        continue
                    classes = el.classes or ""
                    aria_label = el.attrs.get("aria-label", "")
                    if (
                        "clear-indicator" in classes
                        or "clearIndicator" in classes
                        or aria_label in {"Clear", "Clear value"}
                    ):
                        results.append(el)
            return _FakeLocator(results)
        return _FakeLocator([])

    def _ancestor_locator(self, selector: str) -> "_FakeLocator":
        substrings = re.findall(r"contains\(@class,\s*'([^']+)'\)", selector)
        node = self._elements[0].parent if self._elements else None
        while node is not None:
            if any(needle in (node.classes or "") for needle in substrings):
                return _FakeLocator([node])
            node = node.parent
        return _FakeLocator([])


class _FakeKeyboard:
    def press(self, key: str) -> None:
        pass


class _FakePage:
    def __init__(self, root: _FakeElement) -> None:
        self.root = root
        self.keyboard = _FakeKeyboard()
        self.url = "https://example.com/jobs/1"

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if not el.detached and el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        if "role='option']" in selector or 'role="option"]' in selector:
            # Only ever consulted for a bounded `.first.wait_for(...)` in
            # `scoped_visible_option_texts`, whose fake `wait_for` is a
            # no-op -- real scoping/matching always goes through the
            # per-listbox id lookup above, never this page-wide fallback.
            return _FakeLocator([el for el in self.root.subtree() if el.role == "option" and not el.detached])
        return _FakeLocator([])

    def get_by_role(self, role: str, name: str | None = None) -> _FakeLocator:
        # Page-wide, unscoped -- exactly like the real
        # `react_controls.open_menu`/`select_single_option`/`click_option`
        # primitives the plain `_fill_choice` menu-choice path relies on
        # (never the `scoped_*` helpers `_fill_combobox` uses instead).
        _ = name
        return _FakeLocator([el for el in self.root.subtree() if el.role == role and not el.detached])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


# --- Required current/previous title text input -----------------------------


def _title_field() -> DiscoveredField:
    return DiscoveredField(
        label="Current or previous job title*",
        field_type="text",
        required=True,
        element_id="job_application_answers_attributes_0_text_value",
    )


def _title_item(value: str = "Staff Engineer") -> ClassifiedField:
    return ClassifiedField(
        field=_title_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.CURRENT_TITLE,
    )


def _text_input_page(element_id: str) -> _FakePage:
    root = _FakeElement("div")
    root.add_child(_FakeElement("input", elem_id=element_id))
    return _FakePage(root)


def test_required_current_title_text_input_fills_and_confirms() -> None:
    """The mapping (`questions._is_current_or_previous_title` ->
    `profile.employment.current_title`) and the ordinary `_fill_text`/
    `_write_input` path must actually commit and read back through the
    real `GreenhouseAdapter`/`service._fill_and_confirm` -- not just
    `_fill_text` in isolation -- for a plain, non-combobox required title
    input, the shape Greenhouse renders this question as by default.
    """
    item = _title_item("Staff Engineer")
    page = _text_input_page(item.field.element_id)

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is True
    input_el = next(el for el in page.root.subtree() if el.elem_id == item.field.element_id)
    assert input_el.value == "Staff Engineer"


def test_current_title_fill_diagnostic_logs_stages_without_candidate_value(caplog) -> None:
    """The compact per-field diagnostic this module adds for CURRENT_TITLE
    must report the fill/readback stages and timing so a live failure can
    be localized to mapping/locator/fill/readback -- but never the actual
    candidate value or read-back text."""
    item = _title_item("Staff Engineer")
    page = _text_input_page(item.field.element_id)

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is True
    diagnostics = [r.getMessage() for r in caplog.records if r.name == _SERVICE_LOGGER]
    assert any("field_fill_diagnostic" in message and "kind=current_title" in message for message in diagnostics)
    assert all("Staff Engineer" not in message for message in diagnostics)


# --- Country combobox: scoping + dial-code fallback -------------------------

_COUNTRY_INPUT_ID = "react-select-question_67916011-input"
_COUNTRY_LISTBOX_ID = "react-select-question_67916011-listbox"
_ITI_LISTBOX_ID = "iti-0__country-listbox"


def _country_page() -> tuple[_FakePage, _FakeElement, _FakeElement]:
    """Builds a control whose live listbox id follows the real react-select
    convention (input id minus "-input" plus "-listbox"), alongside an
    already-open, unrelated phone-widget listbox with an identically
    worded option -- the exact residence-field pollution observed live
    (a react-select country field sharing the page with an "iti" phone
    country-code picker).
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))

    country_listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    country_option = country_listbox.add_child(
        _FakeElement(
            "div",
            role="option",
            text="Uzbekistan +998",
            elem_id="react-select-question_67916011-option-234",
        )
    )

    iti_listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_ITI_LISTBOX_ID))
    iti_option = iti_listbox.add_child(_FakeElement("div", role="option", text="Uzbekistan +998"))
    iti_selected_display = root.add_child(_FakeElement("div", classes="iti__selected-country"))

    def _select_country() -> None:
        single_value.text = "Uzbekistan +998"

    def _select_iti() -> None:
        # A regression that matches/clicks page-wide instead of scoping to
        # the control's own listbox would fire this instead -- the real
        # combobox's own display must never change.
        iti_selected_display.text = "Uzbekistan +998"

    country_option._on_click = _select_country  # type: ignore[attr-defined]
    iti_option._on_click = _select_iti  # type: ignore[attr-defined]

    page = _FakePage(root)
    return page, control, country_option


def _country_locator(page: _FakePage) -> _FakeLocator:
    return page.locator(f'[id="{_COUNTRY_INPUT_ID}"]')


def test_country_combobox_scopes_to_own_listbox_ignoring_identical_iti_option() -> None:
    """A phone-widget listbox with its own visible, identically-worded
    option must never be matched/clicked instead of the country field's
    own live listbox."""
    page, control, _country_option = _country_page()
    locator = _country_locator(page)

    filled = _fill_combobox(page, locator, "Uzbekistan +998")

    assert filled is True
    single_value = next(
        el for el in page.root.subtree() if "single-value" in (el.classes or "")
    )
    assert single_value.text == "Uzbekistan +998"
    iti_display = next(el for el in page.root.subtree() if el.classes == "iti__selected-country")
    assert iti_display.text == ""


def test_country_dial_code_fragment_confirms_via_committed_control_metadata(caplog) -> None:
    """Models the real live failure: after the exact option is clicked, the
    display collapses to a bare dialing code ("+998") and the option node
    itself is detached (a committed React-select unmounting its menu) --
    but the control root retains a committed `data-selected-value`
    attribute. Confirmation must succeed using only the option label
    captured *before* the click; the detached-element guard in this
    fixture proves `confirm_option_selected` never re-reads the clicked
    option locator afterwards (that would either raise here, or hang for
    real Playwright default timeouts against a live site).
    """
    page, control, country_option = _country_page()
    locator = _country_locator(page)

    def _select_and_detach() -> None:
        single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
        single_value.text = "+998"
        locator._elements[0].value = ""
        control.attrs["data-selected-value"] = "Uzbekistan +998"
        country_option.detach()

    country_option._on_click = _select_and_detach  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger=_GREENHOUSE_LOGGER):
        filled = _fill_combobox(page, locator, "Uzbekistan +998")

    assert filled is True
    assert GreenhouseAdapter().read_back(
        page,
        DiscoveredField(
            label="Country*",
            field_type="combobox",
            required=True,
            element_id=_COUNTRY_INPUT_ID,
        ),
    ) == "Uzbekistan +998"


def test_country_dial_code_fragment_with_detached_option_and_no_signal_fails_closed(caplog) -> None:
    """Same detachment as above, but with no committed control-level signal
    anywhere: a bare dialing code alone must never be accepted as a
    confirmed selection, and the fail-closed path must not raise or
    re-read the detached option. `read_back` must independently fail
    closed too -- it must never surface the bare "+998" fragment on its
    own, whether or not the fill itself was confirmed."""
    page, control, country_option = _country_page()
    locator = _country_locator(page)

    def _select_and_detach() -> None:
        single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
        single_value.text = "+998"
        locator._elements[0].value = ""
        country_option.detach()

    country_option._on_click = _select_and_detach  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger=_GREENHOUSE_LOGGER):
        filled = _fill_combobox(page, locator, "Uzbekistan +998")

    assert filled is False
    assert GreenhouseAdapter().read_back(
        page,
        DiscoveredField(
            label="Country*",
            field_type="combobox",
            required=True,
            element_id=_COUNTRY_INPUT_ID,
        ),
    ) is None


# --- Country combobox: end-to-end service routing through fill_field --------
#
# The two direct `_fill_combobox` tests above prove that function's own
# scoping and dial-code-fragment confirmation. These tests prove what the
# real service path actually calls: for a custom `input[role=combobox]`
# COUNTRY field, `GreenhouseAdapter.fill_field` routes directly (and only
# once) to `_fill_combobox`'s scoped exact-option path -- it never tries the
# page-wide `_fill_choice` menu-choice path first, and it never falls back to
# any additional un-scoped recovery after a failed confirmation. That keeps
# the only source of truth for "was this option actually clicked" the
# option id/label `_fill_combobox` itself captured before the click, so a
# stale or mismatched control-level attribute (e.g. left over from a
# different, earlier country selection) can never be mistaken for
# confirmation of the country actually requested here.


def _country_solo_page() -> tuple[_FakePage, _FakeElement, _FakeElement]:
    """A single-option country combobox with no unrelated widget on the
    page -- isolates the service-level routing/confirmation fix below from
    the scoped-vs-unscoped listbox concern the tests above already cover.
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))

    country_listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    country_option = country_listbox.add_child(
        _FakeElement(
            "div",
            role="option",
            text="Uzbekistan +998",
            elem_id="react-select-question_67916011-option-234",
        )
    )

    def _select_country() -> None:
        single_value.text = "Uzbekistan +998"

    country_option._on_click = _select_country  # type: ignore[attr-defined]

    page = _FakePage(root)
    return page, control, country_option


def _country_field(context: str = "") -> DiscoveredField:
    return DiscoveredField(
        label="Country*",
        field_type="combobox",
        required=True,
        element_id=_COUNTRY_INPUT_ID,
        context=context,
    )


def _country_item(value: str = "Uzbekistan", *, context: str = "") -> ClassifiedField:
    return ClassifiedField(
        field=_country_field(context=context),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.COUNTRY,
    )


def test_service_fill_and_confirm_country_combobox_confirms_first_click_without_retype(caplog) -> None:
    """End-to-end regression through the real routing:
    `service._fill_and_confirm` -> `GreenhouseAdapter.fill_field` ->
    `_fill_combobox`, for a custom `input[role=combobox]` COUNTRY field.
    The requested value ("Uzbekistan") differs from the exact live option
    text ("Uzbekistan +998") the way a real site's dial-code-suffixed
    option does. That exact option is clicked once and immediately
    unmounts (a committed React-select routinely does this as part of the
    same click), the display collapses to a bare dialing code ("+998"),
    and the control root retains committed `data-selected-value` metadata
    naming the option that was actually clicked. `_fill_combobox`'s own
    dial-code-fragment confirmation resolves this on the first attempt --
    `fill_field` must adopt that resolved option label as the confirmed
    value (so `service._readback_matches` compares against "Uzbekistan
    +998", not the original "Uzbekistan" request) and never click the
    now-detached option again or retype into the control's search input.
    """
    page, control, country_option = _country_solo_page()
    locator = _country_locator(page)

    def _select_and_detach() -> None:
        single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
        single_value.text = "+998"
        locator._elements[0].value = ""
        control.attrs["data-selected-value"] = "Uzbekistan +998"
        country_option.detach()

    country_option._on_click = _select_and_detach  # type: ignore[attr-defined]
    item = _country_item("Uzbekistan")

    with caplog.at_level(logging.WARNING, logger=_SERVICE_LOGGER):
        confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is True
    # No retype into the control's search input by a `_fill_combobox` fallback.
    assert locator._elements[0].value == ""
    diagnostics = [r.getMessage() for r in caplog.records if r.name == _SERVICE_LOGGER]
    assert any(
        "field_fill_diagnostic" in message and "attempt=1" in message and "confirmed=True" in message
        for message in diagnostics
    )
    assert not any("attempt=2" in message for message in diagnostics)


def test_service_fill_and_confirm_country_combobox_fails_closed_without_committed_metadata() -> None:
    """Same click-then-detach dial-code-fragment collapse as above, but
    with no committed control-level signal anywhere. The routing fix must
    never manufacture a confirmation out of a bare dialing code alone --
    `service._fill_and_confirm` must report the field unconfirmed, and
    `read_back` must independently agree, end to end through the real
    service/adapter path (not just `_fill_combobox` in isolation).
    """
    page, control, country_option = _country_solo_page()
    locator = _country_locator(page)

    def _select_and_detach() -> None:
        single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
        single_value.text = "+998"
        locator._elements[0].value = ""
        country_option.detach()

    country_option._on_click = _select_and_detach  # type: ignore[attr-defined]
    item = _country_item("Uzbekistan")

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert GreenhouseAdapter().read_back(page, item.field) is None


def test_service_fill_and_confirm_country_combobox_fails_closed_on_mismatched_committed_metadata() -> None:
    """The committed control-level metadata must actually name the option
    that was clicked -- a stale or mismatched attribute (e.g. left over
    from a previously selected, different country) must never be trusted
    just because *some* non-empty attribute exists. This is the exact
    shape the removed unsafe recovery (a second, un-scoped fallback after
    `_fill_choice`) used to accept: any committed control metadata at all,
    regardless of whether it named the option actually clicked."""
    page, control, country_option = _country_solo_page()
    locator = _country_locator(page)

    def _select_and_detach() -> None:
        single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
        single_value.text = "+998"
        locator._elements[0].value = ""
        # Names a different country than the one actually clicked.
        control.attrs["data-selected-value"] = "Tajikistan +992"
        country_option.detach()

    country_option._on_click = _select_and_detach  # type: ignore[attr-defined]
    item = _country_item("Uzbekistan")

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert GreenhouseAdapter().read_back(page, item.field) is None


def test_service_fill_and_confirm_country_combobox_fails_closed_when_no_option_is_clicked() -> None:
    """When the requested country has no matching live option at all, no
    option is ever clicked -- `_fill_combobox` must fail closed on its own
    scoped-match lookup, and `fill_field` must never fall back to any other
    interaction (e.g. typing/committing a bare value) for the field."""
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    value_container.add_child(_FakeElement("div", classes="select__single-value"))
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    listbox.add_child(_FakeElement("div", role="option", text="France", elem_id="opt-france"))
    page = _FakePage(root)
    locator = _country_locator(page)
    item = _country_item("Uzbekistan")

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert locator._elements[0].value == ""
    assert GreenhouseAdapter().read_back(page, item.field) is None


# --- read_committed_selected_value: direct fail-closed edge cases -----------


def _dial_fragment_locator() -> tuple[_FakePage, _FakeElement, "_FakeLocator"]:
    """A control already showing a bare dial-code fragment ("+998"), as if
    an option had just been clicked and the display collapsed -- without
    driving a click, so each edge case below can set up exactly the one
    condition it means to test."""
    page, control, _country_option = _country_page()
    locator = _country_locator(page)
    single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
    single_value.text = "+998"
    return page, control, locator


def test_committed_selected_value_rejects_stale_or_mismatched_attribute() -> None:
    """A control-root attribute that names a different country/dial code
    entirely (e.g. left over from a prior selection, or simply wrong) must
    never be accepted just because *some* attribute is non-empty -- it has
    to actually relate to the dial-code fragment currently on display."""
    _page, control, locator = _dial_fragment_locator()
    control.attrs["data-selected-value"] = "Tajikistan +992"

    assert react_controls.read_committed_selected_value(locator) is None


def test_committed_selected_value_rejects_bare_dial_code_attribute() -> None:
    """A control-root attribute that is itself only the bare dialing code
    carries no more semantic information than the display already does,
    so it must never be accepted on its own either."""
    _page, control, locator = _dial_fragment_locator()
    control.attrs["data-selected-value"] = "+998"

    assert react_controls.read_committed_selected_value(locator) is None


def test_committed_selected_value_requires_cleared_search_input() -> None:
    """A leftover typed search string means no exact option click has
    actually committed a selection yet -- even with an otherwise-valid
    control-root attribute present, it must not be accepted while the
    control still shows unsearched typed text."""
    _page, control, locator = _dial_fragment_locator()
    control.attrs["data-selected-value"] = "Uzbekistan +998"
    locator._elements[0].value = "Uzb"

    assert react_controls.read_committed_selected_value(locator) is None


# --- clear_selected_value: direct fail-closed edge cases --------------------


def _clearable_locator_without_single_value_node() -> tuple[_FakeElement, _FakeLocator]:
    """A control whose markup has no dedicated single-value display node
    at all (unlike every other control built above), with a local
    clear-indicator that persists its click -- isolating whether
    `clear_selected_value` can be fooled into reporting a clear just
    because there is no display node left to contradict it.
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    control.add_child(_FakeElement("div", classes="select__clear-indicator"))
    page = _FakePage(root)
    locator = _country_locator(page)
    return control, locator


def test_clear_selected_value_rejects_cleared_claim_when_committed_attribute_remains() -> None:
    """No single-value display node exists to check, but the control root
    still carries a committed `data-selected-value` attribute -- the same
    durable signal the dial-code-fragment fallback relies on elsewhere --
    so this must never report the selection as cleared just because the
    display itself has nothing left to contradict it."""
    control, locator = _clearable_locator_without_single_value_node()
    control.attrs["data-selected-value"] = "France"

    assert react_controls.clear_selected_value(locator) is False


def test_clear_selected_value_confirms_cleared_when_no_local_signal_remains() -> None:
    """With no single-value node and no committed control-root attribute
    either, the clear is genuinely confirmed."""
    _control, locator = _clearable_locator_without_single_value_node()

    assert react_controls.clear_selected_value(locator) is True


# --- Country combobox: explicit "select Other" fallback ---------------------
#
# Anchored on the real Stripe job 7292520 / question_69459419 shape: the
# question's own field-local help text explicitly says "If you do not see
# your country in the list, please select Other." These tests use a
# hypothetical country ("Mongolia") confirmed absent from that real
# question's own option list (per the public Greenhouse jobs API) purely as
# an example value with no live option -- never a claim about any actual
# candidate's country, which this module never logs. They prove the
# fallback only ever fires on that explicit, field-local instruction --
# never as a blanket substitution for any unmatched country -- and that a
# stale, already-wrong committed selection (e.g. a leftover "France") is
# never mistaken for a successful fill.

_OTHER_INSTRUCTED_CONTEXT = (
    "What is your current country of residence? If you do not see your "
    "country in the list, please select Other."
)


def _country_other_fallback_page(
    *, stale_text: str = "", other_confirms: bool = True
) -> tuple[_FakePage, _FakeElement, _FakeElement]:
    """A residence-country combobox whose live listbox has no option for
    the candidate's actual country, but does have an "Other" option --
    optionally pre-loaded with a stale committed selection (as if a
    previous, unrelated fill left the wrong country selected before this
    attempt even starts).
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(
        _FakeElement("div", classes="select__single-value", text=stale_text)
    )
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))

    listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    other_option = listbox.add_child(_FakeElement("div", role="option", text="Other", elem_id="opt-other"))

    def _select_other() -> None:
        if other_confirms:
            single_value.text = "Other"

    other_option._on_click = _select_other  # type: ignore[attr-defined]

    page = _FakePage(root)
    return page, control, other_option


def test_country_combobox_no_match_with_stale_value_selects_explicit_other() -> None:
    """A requested country ("Mongolia") with no live option, the control
    already shows a stale, wrong committed selection ("France") from
    before this attempt, and the question's own help text explicitly
    instructs selecting Other -- the fallback must select the exact Other
    option and the fill must confirm, replacing the stale wrong value.
    """
    page, _control, _other_option = _country_other_fallback_page(stale_text="France")
    item = _country_item("Mongolia", context=_OTHER_INSTRUCTED_CONTEXT)

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is True
    single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
    assert single_value.text == "Other"


def test_country_combobox_no_match_without_other_instruction_fails_closed() -> None:
    """Same unmatched country and stale "France" committed selection, but
    the field's own help text carries no explicit Other instruction --
    the fallback must never fire, and the fill must never be reported as
    confirmed. (The stale display is a pre-existing condition this fill
    attempt did not create; what matters is that it is never silently
    accepted as a successful fill of the requested country.)
    """
    page, _control, other_option = _country_other_fallback_page(stale_text="France")
    item = _country_item("Mongolia", context="What is your current country of residence?")

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert other_option.detached is False
    single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
    assert single_value.text == "France"


def test_country_combobox_other_fallback_fails_closed_when_confirmation_never_persists() -> None:
    """The Other option is found and clicked, but the control's committed
    display never actually updates to reflect it (e.g. a swallowed click)
    -- the fallback must fail closed exactly like an ordinary unmatched
    country, never reporting a confirmed fill it cannot verify.
    """
    page, _control, _other_option = _country_other_fallback_page(
        stale_text="France", other_confirms=False
    )
    item = _country_item("Mongolia", context=_OTHER_INSTRUCTED_CONTEXT)

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False


class _GlobalMatchFakePage(_FakePage):
    """Simulates what a real browser's `page.wait_for_function` actually
    does for `react_controls.wait_for_selected_label`: it evaluates its
    matcher against every single-value/chip node on the *whole page*,
    not just the target control's own. The base `_FakePage` stub always
    raises a timeout instead, which happens to make every other test in
    this suite fall through to a scoped read-back regardless of this
    distinction -- this subclass is used only by the regression below,
    to prove the "Other" fallback's own confirmation never relies on
    that page-wide signal even when a real page would satisfy it.
    """

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, timeout
        wanted = str((arg or {}).get("wanted") or "").strip().lower()
        if not wanted:
            raise PlaywrightTimeoutError("no wanted value")
        for el in self.root.subtree():
            classes = el.classes or ""
            if "single-value" not in classes and "singleValue" not in classes:
                continue
            text = (el.text or "").strip().lower()
            if text and (text == wanted or wanted in text):
                return None
        raise PlaywrightTimeoutError("no page-wide match")


def test_country_combobox_other_fallback_ignores_unrelated_sibling_already_showing_other() -> None:
    """An unrelated sibling control elsewhere on the page (e.g. a
    work-authorization dropdown) already showing "Other" must never let
    this control's own swallowed click be mistaken for a confirmed
    "Other" selection -- even though a real page's
    `page.wait_for_function` would satisfy `confirm_option_selected`'s
    page-wide primary signal in exactly this shape. The target control
    must still show its stale "France" after the failed attempt.
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(
        _FakeElement("div", classes="select__single-value", text="France")
    )
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    listbox = root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    other_option = listbox.add_child(_FakeElement("div", role="option", text="Other", elem_id="opt-other"))
    # Swallowed click: clicking "Other" on this control never updates its
    # own display -- no `_on_click` handler is wired up, so it stays on
    # the stale "France".

    sibling_control = root.add_child(_FakeElement("div", classes="select__control"))
    sibling_value_container = sibling_control.add_child(
        _FakeElement("div", classes="select__value-container")
    )
    sibling_value_container.add_child(_FakeElement("div", classes="select__single-value", text="Other"))

    page = _GlobalMatchFakePage(root)
    item = _country_item("Mongolia", context=_OTHER_INSTRUCTED_CONTEXT)

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert single_value.text == "France"
    assert other_option.detached is False


def test_country_combobox_explicit_match_takes_precedence_over_other_instruction() -> None:
    """When the candidate's country does have a live matching option, the
    Other-instruction fallback must never be consulted -- the real match
    always wins over Other, regardless of what the field's help text says.
    """
    page, _control, _country_option = _country_solo_page()
    item = _country_item("Uzbekistan", context=_OTHER_INSTRUCTED_CONTEXT)

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is True
    single_value = next(el for el in page.root.subtree() if "single-value" in (el.classes or ""))
    assert single_value.text == "Uzbekistan +998"


def test_phone_country_selection_stays_independent_of_residence_other_fallback() -> None:
    """Phone dial-code country selection must never be influenced by the
    residence-country Other fallback: even if a phone field happened to
    carry field-local text matching the exact Other-instruction wording,
    `fill_field`'s PHONE branch routes to `_fill_phone` and must never
    consult that text or reach `_fill_combobox`'s Other-selection path --
    that path is only ever reachable via the dedicated COUNTRY branch.
    """
    root = _FakeElement("div")
    root.add_child(_FakeElement("input", elem_id="phone_input"))
    page = _FakePage(root)
    phone_field = DiscoveredField(
        label="Phone*",
        field_type="text",
        required=True,
        element_id="phone_input",
        context=_OTHER_INSTRUCTED_CONTEXT,
    )
    item = ClassifiedField(
        field=phone_field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="+34 600000000",
        fill=True,
        kind=QuestionKind.PHONE,
        country="Spain",
    )

    filled = GreenhouseAdapter().fill_field(page, item)

    assert filled is True
    input_el = next(el for el in page.root.subtree() if el.elem_id == "phone_input")
    assert input_el.value == "+34 600000000"


# --- Country combobox: field-local help text extraction scoping -------------
#
# `_combobox_field_help` (used by `_discover_control` to populate a combobox
# field's own `context`) must never climb to a shared ancestor broad enough
# to also aggregate a sibling question's own help text -- unlike the
# checkbox walk it replaces that path with, which accepts any sufficiently
# long ancestor text as a fallback. It only ever trusts an aria-describedby
# target (control-specific by construction) or a bounded, named ancestor
# that carries its own direct label/legend.


def test_combobox_field_help_captures_aria_describedby_scoped_instruction() -> None:
    """A control-specific aria-describedby target is always safely scoped
    to this control alone, so its text is captured as field-local help."""
    root = _FakeElement("div")
    root.add_child(
        _FakeElement(
            "div",
            elem_id="country-help",
            text="If you do not see your country in the list, please select Other.",
        )
    )
    country_input = root.add_child(
        _FakeElement(
            "input",
            elem_id=_COUNTRY_INPUT_ID,
            attrs={"aria-describedby": "country-help"},
        )
    )
    locator = _FakeLocator([country_input])

    context = _combobox_field_help(locator)

    assert "select Other" in context
    assert _country_other_instructed(context) is True


def test_combobox_field_help_never_leaks_sibling_field_instruction() -> None:
    """A sibling question's own "select Other" instruction, reachable only
    through a shared ancestor that is itself neither named nor owned by
    this control, must never be captured as this combobox's own
    field-local help text -- unlike the checkbox walk's broad "ancestor
    text is long enough" fallback, which would have accepted that shared
    ancestor (and so its aggregate text, including the sibling's
    instruction) once the climb reached it.
    """
    root = _FakeElement("div")
    broad_wrapper = root.add_child(_FakeElement("div", classes="application-section"))
    sibling_field = broad_wrapper.add_child(_FakeElement("div", classes="field-nationality"))
    sibling_field.add_child(_FakeElement("label", text="Nationality"))
    sibling_field.add_child(
        _FakeElement("span", text="If you do not see your nationality, please select Other.")
    )
    country_field = broad_wrapper.add_child(_FakeElement("div", classes="plain-wrapper"))
    value_container = country_field.add_child(_FakeElement("div"))
    country_input = value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    locator = _FakeLocator([country_input])

    context = _combobox_field_help(locator)

    assert context == ""
    assert _country_other_instructed(context) is False


# --- Country combobox: stale selected-value clearing on failed fill ---------
#
# A failed residence-country fill must never leave a visibly wrong,
# pre-existing committed selection (e.g. a leftover "France") silently in
# place: `_fill_combobox`'s shared fail-closed exit best-effort clears this
# control's own committed value via a local clear control scoped to this
# control's own wrapper, and explicitly warns (without ever logging the
# stale or requested country text) when that clear cannot be confirmed.


def _country_no_match_page_with_clear_control(
    *, stale_text: str = "France", clear_persists: bool = True
) -> tuple[_FakePage, _FakeElement]:
    """A residence-country combobox with no live option matching the
    requested country (so the fill fails outright) and a stale, wrong
    committed selection already shown -- with a local react-select
    clear-indicator scoped to this control's own wrapper.
    """
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(
        _FakeElement("div", classes="select__single-value", text=stale_text)
    )
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    clear_control = control.add_child(_FakeElement("div", classes="select__clear-indicator"))

    def _clear() -> None:
        if clear_persists:
            single_value.text = ""

    clear_control._on_click = _clear  # type: ignore[attr-defined]
    root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))

    page = _FakePage(root)
    return page, single_value


def test_country_combobox_failed_fill_clears_stale_selected_value_via_local_clear_control() -> None:
    """When the requested country has no live option at all, the fill
    fails, but a stale, wrong pre-existing selection must still be
    best-effort cleared via this control's own local clear control."""
    page, single_value = _country_no_match_page_with_clear_control(stale_text="France")
    item = _country_item("Mongolia")

    confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert single_value.text == ""


def test_country_combobox_failed_fill_without_clear_control_logs_manual_correction_warning(
    caplog,
) -> None:
    """With no safely scoped clear control present at all, the stale
    selection cannot be cleared -- this must be surfaced as an explicit,
    structural warning naming only the control (never the stale or
    requested country text), since the existing generic read-back-mismatch
    warning alone does not say a visibly wrong prior selection remains."""
    root = _FakeElement("div")
    control = root.add_child(_FakeElement("div", classes="select__control"))
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(
        _FakeElement("div", classes="select__single-value", text="France")
    )
    value_container.add_child(_FakeElement("input", elem_id=_COUNTRY_INPUT_ID))
    root.add_child(_FakeElement("div", role="listbox", elem_id=_COUNTRY_LISTBOX_ID))
    page = _FakePage(root)
    item = _country_item("Mongolia")

    with caplog.at_level(logging.WARNING, logger=_GREENHOUSE_LOGGER):
        confirmed = service._fill_and_confirm(GreenhouseAdapter(), page, item)

    assert confirmed is False
    assert single_value.text == "France"
    messages = [r.getMessage() for r in caplog.records if r.name == _GREENHOUSE_LOGGER]
    assert any(
        "combobox_country_stale_selection_unconfirmed" in message
        and "requires_manual_correction" in message
        for message in messages
    )
    assert all("France" not in message and "Mongolia" not in message for message in messages)
