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

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill import react_controls, service
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter, _fill_combobox
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


def _country_field() -> DiscoveredField:
    return DiscoveredField(
        label="Country*",
        field_type="combobox",
        required=True,
        element_id=_COUNTRY_INPUT_ID,
    )


def _country_item(value: str = "Uzbekistan") -> ClassifiedField:
    return ClassifiedField(
        field=_country_field(),
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
