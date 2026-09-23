"""Deterministic (non-browser) coverage for the shared custom React-select
interaction/read-back mechanism (`react_controls.open_menu` /
`click_option` / `wait_for_selected_label` / `read_selected_label`) as used
through `GreenhouseAdapter._fill_choice` and `service._fill_and_confirm`.

`test_greenhouse_native_select_fake_dom.py` fakes a native `<select>`, whose
`el.options` is always readable at discovery time -- that path never hits
the bug covered here. This file fakes a *custom* React-select instead: the
underlying control is a plain `<input>` wrapped in `.select__control`
markup, live options only exist as `[role="option"]` menu items rendered
after the control is clicked, and there is no `el.options` at all. That is
the real shape a Greenhouse "Gender" question renders as when it isn't a
native select, and discovery cannot read its live options up front (see
`_gender_value`'s "prefer not to disclose" fallback literal).

Two things are exercised end to end, through the real
`react_controls`/`greenhouse`/`service` code, never mocked:

1. `_with_confirmed_choice` fix: a discovery-time placeholder value that
   textually differs from the option actually clicked (a real, observed
   failure mode -- Greenhouse's GENDER default is "prefer not to disclose",
   while the live page might label that option "I don't wish to answer")
   must still confirm successfully, because confirmation now compares
   against what was truly selected.
2. The `select_choice_unconfirmed` diagnostic: when the control's real
   ancestor class doesn't match any substring `react_controls._value_root`
   / `_control_root` recognize (a "stale/wrong value-root selector" DOM
   shape this module cannot prove without live access), the read-back
   mechanism legitimately fails, and the bounded diagnostic must surface it
   with only sanitized structural facts -- never option text or the
   resolved value.
"""

from __future__ import annotations

import logging
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill import react_controls, service
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter, _fill_combobox
from app.application.autofill.models import FieldClassification
from app.application.autofill.options import ALREADY_LOCATED_CHOICE, WOULD_RELOCATE_CHOICE
from app.application.autofill.questions import AGE_DECLINE_INTENT, QuestionKind

_LOGGER_NAME = "app.application.autofill.service"


class _FakeElement:
    """A minimal DOM node: enough structure (tag/classes/role/text/parent)
    for the ancestor-walk and descendant-search selectors `react_controls`
    actually uses, and nothing else.
    """

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

    def add_child(self, child: "_FakeElement") -> "_FakeElement":
        child.parent = self
        self.children.append(child)
        return child

    def subtree(self):
        yield self
        for child in self.children:
            yield from child.subtree()


def _compile_simple_selector(token: str):
    match = re.fullmatch(r"\[class\*='([^']+)'\]", token)
    if match:
        needle = match.group(1)
        return lambda el: needle in (el.classes or "")
    match = re.fullmatch(r"\[role='([^']+)'\]", token)
    if match:
        needle = match.group(1)
        return lambda el: (el.role or "") == needle
    return lambda el: False


def _select_descendants(roots: list[_FakeElement], selector: str) -> list[_FakeElement]:
    results: list[_FakeElement] = []
    seen: set[int] = set()
    for raw_part in selector.split(","):
        # Simplified engine: only the final compound token is matched, which
        # is sufficient for every selector `react_controls` uses against
        # this fixture (single-value / option lookups, never a descendant
        # chain whose ancestor segment matters for these tests).
        token = raw_part.strip().split()[-1]
        matcher = _compile_simple_selector(token)
        for root in roots:
            for el in root.subtree():
                if el is root:
                    continue
                if matcher(el) and id(el) not in seen:
                    seen.add(id(el))
                    results.append(el)
    return results


class _FakeKeyboard:
    def __init__(self) -> None:
        self.presses: list[str] = []

    def press(self, key: str) -> None:
        self.presses.append(key)


class _FakeLocator:
    def __init__(self, elements: list[_FakeElement]) -> None:
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> "_FakeLocator":
        return _FakeLocator([self._elements[index]])

    @property
    def first(self) -> "_FakeLocator":
        return self.nth(0) if self._elements else _FakeLocator([])

    def is_visible(self) -> bool:
        return bool(self._elements) and self._elements[0].visible

    def inner_text(self, timeout: int | None = None) -> str:
        _ = timeout
        return self._elements[0].text if self._elements else ""

    def text_content(self, timeout: int | None = None) -> str:
        # Unlike `inner_text()` above (which this fixture deliberately keeps
        # naive -- just the node's own directly-assigned text, mirroring a
        # visible-text read), this aggregates the node's own text plus every
        # descendant's text regardless of visibility, exactly like the real
        # DOM's `textContent` includes CSS-hidden/collapsed fragments that
        # `innerText`/`inner_text()` would omit.
        _ = timeout
        if not self._elements:
            return ""
        parts: list[str] = []

        def _walk(node: _FakeElement) -> None:
            if node.text:
                parts.append(node.text)
            for child in node.children:
                _walk(child)

        _walk(self._elements[0])
        return " ".join(parts)

    def get_attribute(self, name: str, timeout: int | None = None) -> str | None:
        _ = timeout
        if not self._elements:
            return None
        el = self._elements[0]
        if name == "id":
            return el.elem_id
        if name == "role":
            return el.role
        return el.attrs.get(name)

    def input_value(self, timeout: int | None = None) -> str:
        _ = timeout
        return self._elements[0].value if self._elements else ""

    def evaluate(self, script: str, arg: object = None) -> object:
        _ = arg
        el = self._elements[0]
        if "tagName.toLowerCase() === 'select'" in script:
            return el.tag == "select"
        if "el.tagName.toLowerCase()" in script:
            return el.tag
        if "selectedOptions" in script:
            return []
        if "el.closest" in script:
            return False
        return None

    def click(self, timeout: int | None = None, force: bool = False) -> None:
        _ = timeout, force
        for el in self._elements:
            on_click = getattr(el, "_on_click", None)
            if on_click is not None:
                on_click()

    def fill(self, value: str, timeout: int | None = None) -> None:
        _ = timeout
        for el in self._elements:
            el.value = value
            on_fill = getattr(el, "_on_fill", None)
            if on_fill is not None:
                on_fill(value)

    def press_sequentially(self, value: str, delay: int | None = None, timeout: int | None = None) -> None:
        _ = delay, timeout
        self.fill(value)

    def wait_for(self, state: str | None = None, timeout: int | None = None) -> None:
        _ = state, timeout

    def locator(self, selector: str) -> "_FakeLocator":
        if selector.startswith("xpath=ancestor::"):
            return self._ancestor_locator(selector)
        return _FakeLocator(_select_descendants(self._elements, selector))

    def _ancestor_locator(self, selector: str) -> "_FakeLocator":
        substrings = re.findall(r"contains\(@class,\s*'([^']+)'\)", selector)
        node = self._elements[0].parent if self._elements else None
        while node is not None:
            if any(needle in (node.classes or "") for needle in substrings):
                return _FakeLocator([node])
            node = node.parent
        return _FakeLocator([])

    def filter(self, has_text: "re.Pattern[str] | None" = None) -> "_FakeLocator":
        if has_text is None:
            return self
        return _FakeLocator([el for el in self._elements if has_text.search(el.text)])


class _CustomReactSelectPage:
    """Fakes only the Playwright `Page` surface `react_controls` calls
    against a single custom React-select field: `.locator`, `.get_by_role`,
    `.keyboard`, `.wait_for_function`, `.wait_for_timeout`.
    """

    def __init__(
        self,
        live_options: list[str],
        *,
        control_class: str = "select__control",
        element_id: str = "gender",
    ) -> None:
        self.control = _FakeElement("div", classes=control_class)
        value_container = self.control.add_child(_FakeElement("div", classes="select__value-container"))
        self.single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
        self.hidden_input = value_container.add_child(_FakeElement("input", elem_id=element_id))
        self.menu = _FakeElement("div", role="listbox")
        self.menu.visible = False
        self.options: list[_FakeElement] = []
        for text in live_options:
            option = self.menu.add_child(_FakeElement("div", role="option", text=text))
            option.visible = False
            self.options.append(option)
        self.root = _FakeElement("div")
        self.root.add_child(self.control)
        self.root.add_child(self.menu)
        self.keyboard = _FakeKeyboard()

        def _open(_el: _FakeElement = self.control) -> None:
            self.menu.visible = True
            for opt in self.options:
                opt.visible = True

        def _make_select(option: _FakeElement):
            def _select() -> None:
                self.single_value.text = option.text
                self.menu.visible = False
                for opt in self.options:
                    opt.visible = False

            return _select

        self.control._on_click = _open  # type: ignore[attr-defined]
        for option in self.options:
            option._on_click = _make_select(option)  # type: ignore[attr-defined]

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        return _FakeLocator(_select_descendants([self.root], selector))

    def get_by_role(self, role: str) -> _FakeLocator:
        return _FakeLocator([el for el in self.root.subtree() if el.role == role])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        # The fake DOM cannot execute real JS; forcing the timeout exercises
        # `wait_for_selected_label`'s fallback path (re-reading the label
        # through `read_selected_label`), which is the code this module
        # actually needs to prove.
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def _gender_field(element_id: str = "gender") -> DiscoveredField:
    # No `options`: this is exactly what real discovery sees for a custom
    # React-select, since `_select_options` reads `el.options`, which only
    # exists on a native `<select>`.
    return DiscoveredField(
        label="Gender*",
        field_type="combobox",
        required=True,
        options=[],
        element_id=element_id,
    )


def _gender_item(value: str = "prefer not to disclose") -> ClassifiedField:
    return ClassifiedField(
        field=_gender_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.GENDER,
    )


def test_gender_custom_react_select_confirms_despite_placeholder_value_mismatch() -> None:
    """Regression for the reported "Read-back failed for 'Which gender do
    you most closely identify with?*'" warning: the live page's real
    decline option is worded "I don't wish to answer", not the classifier's
    discovery-time placeholder "prefer not to disclose". The fill itself
    (`react_controls.open_menu` -> `click_option` ->
    `wait_for_selected_label` -> `read_selected_label`) succeeds and
    correctly selects the decline option on the very first attempt; the bug
    was purely in what `_fill_and_confirm` compared the read-back against.
    """
    page = _CustomReactSelectPage(["Female", "Male", "I don't wish to answer"])
    adapter = GreenhouseAdapter()
    item = _gender_item("prefer not to disclose")

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert page.single_value.text == "I don't wish to answer"
    assert adapter.read_back(page, item.field) == "I don't wish to answer"
    # The mismatch is real: without `_with_confirmed_choice`, comparing
    # read-back against the original placeholder value would fail.
    assert service._readback_matches(item, "I don't wish to answer") is False


def test_gender_custom_react_select_reports_correct_first_attempt() -> None:
    """A correctly-implemented custom select should not need the retry --
    the same DOM interaction that reads back correctly on the first attempt
    must not be masked by the mismatch bug forcing a second, equally futile
    attempt."""
    page = _CustomReactSelectPage(["Female", "Male", "Decline to self-identify"])
    adapter = GreenhouseAdapter()
    item = _gender_item("prefer not to disclose")

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert page.single_value.text == "Decline to self-identify"


def test_gender_custom_react_select_stale_value_root_class_logs_bounded_diagnostic(caplog) -> None:
    """Models a real-DOM-shape possibility this module cannot rule out
    without live access: the control's ancestor uses a class convention
    `react_controls._control_root` / `_value_root` do not recognize (here,
    a hyphenated "gh-select-control" instead of the expected
    "select__control" substring). Both the control-open click and the
    later single-value read-back then miss their target, so the option
    never actually gets selected -- a genuine, not spurious, unconfirmed
    fill. The diagnostic must still fire (now that GENDER is in
    `_SELECT_CHOICE_DIAGNOSTIC_KINDS`) with only sanitized structural
    facts.
    """
    page = _CustomReactSelectPage(
        ["Female", "Male", "I don't wish to answer"], control_class="gh-select-control"
    )
    adapter = GreenhouseAdapter()
    item = _gender_item("prefer not to disclose")

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is False
    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("select_choice_unconfirmed")
    ]
    assert len(diagnostics) == 1
    message = diagnostics[0]
    assert "kind=gender" in message
    assert "control_tag=input" in message
    assert "branch=custom_react_select" in message
    assert "option_count=0" in message
    # Never option text, the candidate's resolved value, or any DOM text.
    assert "wish to answer" not in message
    assert "prefer not to disclose" not in message.lower()
    assert "Female" not in message
    assert "Male" not in message


# --- The same custom React-select shape (no `field.options` at discovery
# time) for AGE, PRIVACY_CONSENT, and RELOCATION -- the three Wolt-observed
# kinds whose live-verified fill was blank because `map_question` requires
# `field.options` to resolve a semantic intent. These prove the intent
# carried through by `map_question` reaches `GreenhouseAdapter.fill_field`
# and confirms via `_live_choice_match` / `select_yes_no` against the
# opened menu's live options -- never a discovery-time guess. ---


def _age_field(element_id: str = "age") -> DiscoveredField:
    return DiscoveredField(
        label="What's your age?",
        field_type="combobox",
        required=True,
        options=[],
        element_id=element_id,
    )


def _age_item(value: str = AGE_DECLINE_INTENT) -> ClassifiedField:
    return ClassifiedField(
        field=_age_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.AGE,
    )


def test_age_custom_react_select_resolves_live_decline_option_without_discovery_options() -> None:
    page = _CustomReactSelectPage(["I don't wish to answer", "18-24", "25-34"], element_id="age")
    adapter = GreenhouseAdapter()
    item = _age_item()

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert page.single_value.text == "I don't wish to answer"
    assert adapter.read_back(page, item.field) == "I don't wish to answer"


def test_age_custom_react_select_stays_unfilled_without_live_decline_option() -> None:
    """The decline intent must never fall back to clicking its own sentinel
    text (or any other live option) when no live option is an explicit
    decline -- age is never inferred."""
    page = _CustomReactSelectPage(["18-24", "25-34"], element_id="age")
    adapter = GreenhouseAdapter()
    item = _age_item()

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is False
    assert page.single_value.text == ""


def _privacy_field(element_id: str = "wolt_privacy") -> DiscoveredField:
    return DiscoveredField(
        label="Wolt Recruitment Privacy Statement",
        field_type="combobox",
        required=True,
        options=[],
        element_id=element_id,
    )


def _privacy_item() -> ClassifiedField:
    return ClassifiedField(
        field=_privacy_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.PRIVACY_CONSENT,
    )


def test_privacy_custom_react_select_selects_live_understand_option_without_discovery_options() -> None:
    page = _CustomReactSelectPage(
        [
            "I understand that my personal data will be processed in accordance "
            "with Wolt’s recruitment privacy statement."
        ],
        element_id="wolt_privacy",
    )
    adapter = GreenhouseAdapter()
    item = _privacy_item()

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert "personal data" in page.single_value.text.lower()


def _relocation_field(element_id: str = "wolt_relocate") -> DiscoveredField:
    return DiscoveredField(
        label="Are you currently located in Helsinki, or would you need to relocate?",
        field_type="combobox",
        required=True,
        options=[],
        element_id=element_id,
    )


def _relocation_item(value: str) -> ClassifiedField:
    return ClassifiedField(
        field=_relocation_field(),
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.RELOCATION,
    )


def test_relocation_custom_react_select_resolves_already_located_without_discovery_options() -> None:
    page = _CustomReactSelectPage(
        [
            "I'm already located in a hiring region",
            "I would need to relocate",
            "I'm looking for a remote job",
        ],
        element_id="wolt_relocate",
    )
    adapter = GreenhouseAdapter()
    item = _relocation_item(ALREADY_LOCATED_CHOICE)

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert page.single_value.text == "I'm already located in a hiring region"


def test_relocation_custom_react_select_resolves_would_relocate_without_discovery_options() -> None:
    page = _CustomReactSelectPage(
        [
            "I'm already located in a hiring region",
            "I would need to relocate",
            "I'm looking for a remote job",
        ],
        element_id="wolt_relocate",
    )
    adapter = GreenhouseAdapter()
    item = _relocation_item(WOULD_RELOCATE_CHOICE)

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is True
    assert page.single_value.text == "I would need to relocate"


def test_relocation_custom_react_select_never_selects_remote_only_option() -> None:
    """Even when the only live option is remote-flavored, the semantic
    intent must never resolve to it -- remote availability answers a
    different question than where the candidate lives or would relocate."""
    page = _CustomReactSelectPage(["I'm looking for a remote job"], element_id="wolt_relocate")
    adapter = GreenhouseAdapter()
    item = _relocation_item(ALREADY_LOCATED_CHOICE)

    confirmed = service._fill_and_confirm(adapter, page, item)

    assert confirmed is False
    assert page.single_value.text == ""


# --- `greenhouse._fill_combobox` against a portal-rendered option menu --
# the reported live-retest failure shape for the required "Country*"
# control and the current-residence country combobox: typing into the
# search input filters a live `[role="option"]` menu that is NOT a
# descendant of the combobox's own `.select__control` markup (mirrors a
# react-select `menuPortalTarget` rendering into `document.body`).
# `_fill_combobox` must discover/match/click that menu at page scope
# (never rooted under the input), never commit merely by typing, and
# fail closed rather than press Enter when no exact live option exists.


class _PortalComboboxPage:
    def __init__(self, live_options: list[str], *, include_decoy: bool = False) -> None:
        self.keyboard = _FakeKeyboard()
        self.decoy_clicked = False

        # The combobox's own wrapper: input + single-value read-back node.
        # No option elements live under here at all.
        self.control_wrapper = _FakeElement("div", classes="select__control")
        value_container = self.control_wrapper.add_child(
            _FakeElement("div", classes="select__value-container")
        )
        self.single_value = value_container.add_child(
            _FakeElement("div", classes="select__single-value")
        )
        self.input = value_container.add_child(_FakeElement("input", elem_id="candidate-location"))

        # The portal: a sibling subtree of the control, never its
        # descendant -- this is the shape option discovery must not
        # assume away by rooting its lookup under the input/control.
        self.portal_root = _FakeElement("div", classes="select__menu-portal")
        self.menu = self.portal_root.add_child(_FakeElement("div", role="listbox"))
        self.options: list[_FakeElement] = []
        for text in live_options:
            option = self.menu.add_child(_FakeElement("div", role="option", text=text))
            option.visible = False
            self.options.append(option)

        self.root = _FakeElement("div")
        self.root.add_child(self.control_wrapper)
        self.root.add_child(self.portal_root)

        if include_decoy:
            # A stale/off-screen option node with matching text, parked
            # entirely outside the live menu and permanently invisible --
            # e.g. a leftover portal instance from a previous field.
            # Matching must never click this even though its text is
            # identical to the real, currently visible option.
            self.decoy = self.root.add_child(_FakeElement("div", role="option", text=live_options[0]))
            self.decoy.visible = False

            def _decoy_click() -> None:
                self.decoy_clicked = True

            self.decoy._on_click = _decoy_click  # type: ignore[attr-defined]

        def _open(_el: _FakeElement = self.input) -> None:
            for opt in self.options:
                opt.visible = True

        def _filter(value: str) -> None:
            needle = value.strip().lower()
            for opt in self.options:
                opt.visible = not needle or needle in opt.text.lower()

        def _make_select(option: _FakeElement):
            def _select() -> None:
                self.single_value.text = option.text
                for opt in self.options:
                    opt.visible = False

            return _select

        self.input._on_click = _open  # type: ignore[attr-defined]
        self.input._on_fill = _filter  # type: ignore[attr-defined]
        for option in self.options:
            option._on_click = _make_select(option)  # type: ignore[attr-defined]

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        return _FakeLocator(_select_descendants([self.root], selector))

    def get_by_role(self, role: str) -> _FakeLocator:
        return _FakeLocator([el for el in self.root.subtree() if el.role == role])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        # Same rationale as `_CustomReactSelectPage`: force the fallback
        # path through `read_selected_label` instead of faking real JS
        # evaluation.
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def test_typing_in_portal_combobox_filters_options_without_committing_selection() -> None:
    page = _PortalComboboxPage(["Ukraine", "United Kingdom", "Uzbekistan"])
    control = _FakeLocator([page.input])

    control.click()
    control.press_sequentially("Uzbekistan")

    assert page.single_value.text == ""
    assert [opt.text for opt in page.options if opt.visible] == ["Uzbekistan"]


def test_fill_combobox_commits_only_via_exact_portal_option_click_and_persists() -> None:
    page = _PortalComboboxPage(["Ukraine", "United Kingdom", "Uzbekistan"], include_decoy=True)
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is True
    assert page.single_value.text == "Uzbekistan"
    # The stale/invisible decoy option (identical text, outside the live
    # menu) must never be the one clicked.
    assert page.decoy_clicked is False


def test_fill_combobox_fails_closed_without_pressing_enter_when_no_exact_option() -> None:
    page = _PortalComboboxPage(["Ukraine", "United Kingdom"])
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.single_value.text == ""
    assert "Enter" not in page.keyboard.presses


def test_fill_combobox_school_never_selects_prefix_or_partial_live_option() -> None:
    """SCHOOL must never use the shared fuzzy/prefix scoped matcher (the one
    every other combobox kind above relies on): a live option that merely
    starts with the typed school name (e.g. "Aalto University of Applied
    Sciences" for a typed "Aalto University") is a different real-world
    institution and must never be clicked.
    """
    page = _PortalComboboxPage(["Aalto University of Applied Sciences"])
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Aalto University", kind=QuestionKind.SCHOOL)

    assert ok is False
    assert page.single_value.text == ""


def test_fill_combobox_school_matches_live_option_despite_case_difference() -> None:
    page = _PortalComboboxPage(["Aalto University", "University of Helsinki"])
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "AALTO UNIVERSITY", kind=QuestionKind.SCHOOL)

    assert ok is True
    assert page.single_value.text == "Aalto University"


# --- Read-back correctness fix: a failed `_fill_combobox` attempt must
# never leave typed search text behind for `GreenhouseAdapter.read_back`
# to report as a committed selection. This only reproduces against a DOM
# shape where `react_controls._value_root` cannot resolve any ancestor
# carrying `.select__control`/`.field`/`.iti` (so there is no dedicated
# single-value node to fall back on) -- `_PortalComboboxPage` above has
# such an ancestor and therefore always resolves a (possibly empty)
# single-value node, which already reads back as `None` on its own and
# never exercises the buggy raw-`input_value()` fallback this fix targets.


class _BareInputComboboxPage:
    """A country-shaped custom combobox whose markup the shared
    `_value_root`/`_control_root` ancestor-class selectors cannot resolve
    at all -- no `.select__control` (or `.field`/`.iti`) ancestor exists.
    This is the shape that forces `react_controls.read_selected_label`
    into its last-resort `locator.input_value()` fallback, which is
    exactly the leftover-typed-text-as-selection bug being fixed.
    """

    def __init__(self, live_options: list[str]) -> None:
        self.keyboard = _FakeKeyboard()
        self.input = _FakeElement("input", elem_id="country")
        self.menu = _FakeElement("div", role="listbox")
        self.options: list[_FakeElement] = []
        for text in live_options:
            option = self.menu.add_child(_FakeElement("div", role="option", text=text))
            option.visible = False
            self.options.append(option)
        self.root = _FakeElement("div")
        self.root.add_child(self.input)
        self.root.add_child(self.menu)

        def _open(_el: _FakeElement = self.input) -> None:
            for opt in self.options:
                opt.visible = True

        def _filter(value: str) -> None:
            needle = value.strip().lower()
            for opt in self.options:
                opt.visible = not needle or needle in opt.text.lower()

        self.input._on_click = _open  # type: ignore[attr-defined]
        self.input._on_fill = _filter  # type: ignore[attr-defined]

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        return _FakeLocator(_select_descendants([self.root], selector))

    def get_by_role(self, role: str) -> _FakeLocator:
        return _FakeLocator([el for el in self.root.subtree() if el.role == role])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def _country_field(element_id: str = "country") -> DiscoveredField:
    return DiscoveredField(label="Country*", field_type="combobox", required=True, element_id=element_id)


# --- Selected-value readback must resolve the semantic label, not a child
# dialing-code fragment. Regression for the observed live bug: a country
# combobox's selected-value node visibly reads only "+998" after a
# successful "Uzbekistan" click (its full label collapses to just the
# dialing code in the control's own rendered text), while the DOM still
# carries the country name elsewhere within that same node. Confirmation
# must recover "Uzbekistan" from that node's full content and must never
# accept the bare dial code as a committed selection.


def _country_dial_code_single_value() -> tuple[_FakeElement, _FakeLocator]:
    control = _FakeElement("div", classes="select__control")
    value_container = control.add_child(_FakeElement("div", classes="select__value-container"))
    single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
    input_el = value_container.add_child(_FakeElement("input", elem_id="country"))
    return single_value, _FakeLocator([input_el])


def test_read_selected_label_ignores_dial_code_child_and_resolves_country_label() -> None:
    single_value, control_locator = _country_dial_code_single_value()
    # The visible text of the node is only the dial code (mirrors a real
    # DOM where the country name fragment is present but not part of the
    # currently-rendered visible text); a sibling fragment still carries the
    # semantic country name.
    single_value.text = "+998"
    name_fragment = single_value.add_child(_FakeElement("span", classes="country-name"))
    name_fragment.text = "Uzbekistan"

    label = react_controls.read_selected_label(None, control_locator)

    assert label == "Uzbekistan"


def test_read_selected_label_fails_closed_when_only_dial_code_exists_anywhere() -> None:
    single_value, control_locator = _country_dial_code_single_value()
    single_value.text = "+998"

    assert react_controls.read_selected_label(None, control_locator) is None


# --- `_fill_combobox` post-click confirmation must fall back to the same
# durable, control-committed signal `read_committed_selected_value` itself
# uses -- never accept a bare dialing-code fragment (or any other
# non-empty text) as proof of a semantic selection, and never accept a
# signal a later, independent `read_back` pass could not itself re-derive.
# Regression for the observed live trace: a country combobox click on the
# exact option "Uzbekistan +998" (option id "react-select-country-option-234")
# committed, but the selected-value node's own text (unlike the fixture
# above) carries *no* hidden country-name fragment at all -- only "+998" --
# so `read_selected_label`'s hidden-descendant recovery cannot prove
# anything either way. A retained `aria-selected` option or
# `aria-activedescendant` on the control must never confirm the fill on
# their own: neither proves commitment (vs. mere highlight/focus), and a
# later read-back pass has no way to re-derive either once the option node
# is hidden/detached -- only a control-root attribute (e.g.
# `data-selected-value`) that ties the dial-code fragment to the fuller
# committed value, exactly like `read_committed_selected_value` itself
# requires, can confirm the fill.


class _DialCodeCollapsedCountryPage:
    def __init__(self, *, control_id: str = "country", commit_via: str | None = "control_metadata") -> None:
        self.keyboard = _FakeKeyboard()
        self.option_click_count = 0

        self.control_wrapper = _FakeElement("div", classes="select__control")
        value_container = self.control_wrapper.add_child(_FakeElement("div", classes="select__value-container"))
        self.single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
        self.input = value_container.add_child(_FakeElement("input", elem_id=control_id))

        self.menu = _FakeElement("div", role="listbox", elem_id=f"react-select-{control_id}-listbox")
        self.menu.visible = False
        self.option = self.menu.add_child(
            _FakeElement(
                "div",
                role="option",
                text="Uzbekistan +998",
                elem_id="react-select-country-option-234",
            )
        )
        self.option.visible = False

        self.root = _FakeElement("div")
        self.root.add_child(self.control_wrapper)
        self.root.add_child(self.menu)

        def _open() -> None:
            self.menu.visible = True
            self.option.visible = True

        def _select() -> None:
            self.option_click_count += 1
            # Collapse the display to *only* the dialing code -- unlike
            # the hidden-fragment fixture above, no descendant anywhere in
            # this node carries the country name, so text-based recovery
            # can never resolve it.
            self.single_value.text = "+998"
            self.menu.visible = False
            self.option.visible = False
            # A real committed React-select clears its own search input as
            # part of the same commit; `read_committed_selected_value`
            # requires this before trusting any control-root attribute.
            self.input.value = ""
            if commit_via == "control_metadata":
                # The only signal `read_committed_selected_value` itself
                # trusts: a control-root attribute tying the fragment to
                # the fuller committed value.
                self.control_wrapper.attrs["data-selected-value"] = "Uzbekistan +998"
            elif commit_via == "aria_selected":
                self.option.attrs["aria-selected"] = "true"
            elif commit_via == "activedescendant":
                self.input.attrs["aria-activedescendant"] = self.option.elem_id

        self.input._on_click = _open  # type: ignore[attr-defined]
        self.option._on_click = _select  # type: ignore[attr-defined]

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        return _FakeLocator(_select_descendants([self.root], selector))

    def get_by_role(self, role: str) -> _FakeLocator:
        return _FakeLocator([el for el in self.root.subtree() if el.role == role])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def test_fill_combobox_confirms_via_committed_control_metadata_when_display_is_bare_dial_code() -> None:
    page = _DialCodeCollapsedCountryPage(commit_via="control_metadata")
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is True
    assert page.single_value.text == "+998"
    # Confirmed on the very first click -- a dialing-code-fragment display
    # must never trigger a same-option re-click.
    assert page.option_click_count == 1


def test_fill_combobox_fails_closed_on_retained_aria_selected_option_alone() -> None:
    """A retained `aria-selected="true"` option is only ever a highlight/
    focus signal, not proof of commitment -- and a later, independent
    `read_back` pass has no way to re-derive it once the option node is
    hidden/detached, so it must never confirm the fill on its own."""
    page = _DialCodeCollapsedCountryPage(commit_via="aria_selected")
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.option_click_count == 1


def test_fill_combobox_fails_closed_on_aria_activedescendant_alone() -> None:
    """`aria-activedescendant` alone means highlighted/focused, not
    committed -- and like the retained `aria-selected` case, a later
    `read_back` pass has no way to re-derive it, so it must never confirm
    the fill on its own."""
    page = _DialCodeCollapsedCountryPage(commit_via="activedescendant")
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.option_click_count == 1


def test_fill_combobox_fails_closed_on_bare_dial_code_with_no_committed_semantic_state() -> None:
    """No hidden country-name fragment and no committed control-root
    metadata -- a bare "+998" must never be accepted as a committed
    selection, and the same option must not be re-clicked."""
    page = _DialCodeCollapsedCountryPage(commit_via=None)
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.option_click_count == 1


# --- Generic active-combobox/listbox scoping: option discovery/matching
# must resolve the live menu genuinely associated with the control being
# filled (via id association, or the react-select id convention), never a
# page-wide `[role='option']` scan -- otherwise an unrelated, simultaneously
# visible widget's options (e.g. a phone country-code picker that itself
# lists "Uzbekistan +998" among its 200+ entries) can be cross-matched.


class _MultiListboxCountryPage:
    def __init__(self, *, control_id: str = "question_67916011", set_listbox_id: bool = True) -> None:
        self.keyboard = _FakeKeyboard()

        self.control_wrapper = _FakeElement("div", classes="select__control")
        value_container = self.control_wrapper.add_child(_FakeElement("div", classes="select__value-container"))
        self.single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
        self.input = value_container.add_child(_FakeElement("input", elem_id=control_id))

        listbox_id = f"react-select-{control_id}-listbox" if set_listbox_id else None
        self.menu = _FakeElement("div", role="listbox", elem_id=listbox_id)
        self.menu.visible = False
        self.options: list[_FakeElement] = []
        for text in ["Ukraine", "United Kingdom", "Uzbekistan"]:
            option = self.menu.add_child(_FakeElement("div", role="option", text=text))
            option.visible = False
            self.options.append(option)

        # An unrelated, permanently-visible phone-widget listbox -- present
        # on the page the whole time, not just while the residence combobox
        # menu is open -- that even lists "Uzbekistan +998" among its own
        # options, exactly the cross-match hazard a page-wide option scan
        # would fall into.
        self.phone_widget = _FakeElement("div", role="listbox", elem_id="iti-0__country-listbox")
        self.phone_widget.visible = True
        self.phone_options: list[_FakeElement] = []
        self.phone_widget_clicked = False
        for text in ["Ukraine +380", "United Kingdom +44", "Uzbekistan +998"]:
            option = self.phone_widget.add_child(_FakeElement("div", role="option", text=text))
            option.visible = True
            self.phone_options.append(option)

        def _decoy_click() -> None:
            self.phone_widget_clicked = True

        for option in self.phone_options:
            option._on_click = _decoy_click  # type: ignore[attr-defined]

        self.root = _FakeElement("div")
        self.root.add_child(self.phone_widget)
        self.root.add_child(self.control_wrapper)
        self.root.add_child(self.menu)

        def _open(_el: _FakeElement = self.input) -> None:
            self.menu.visible = True
            for opt in self.options:
                opt.visible = True

        def _filter(value: str) -> None:
            needle = value.strip().lower()
            for opt in self.options:
                opt.visible = not needle or needle in opt.text.lower()

        def _make_select(option: _FakeElement):
            def _select() -> None:
                self.single_value.text = option.text
                self.menu.visible = False
                for opt in self.options:
                    opt.visible = False

            return _select

        self.input._on_click = _open  # type: ignore[attr-defined]
        self.input._on_fill = _filter  # type: ignore[attr-defined]
        for option in self.options:
            option._on_click = _make_select(option)  # type: ignore[attr-defined]

    def locator(self, selector: str) -> _FakeLocator:
        match = re.fullmatch(r'\[id="([^"]+)"\]', selector)
        if match:
            target_id = match.group(1)
            for el in self.root.subtree():
                if el.elem_id == target_id:
                    return _FakeLocator([el])
            return _FakeLocator([])
        return _FakeLocator(_select_descendants([self.root], selector))

    def get_by_role(self, role: str) -> _FakeLocator:
        return _FakeLocator([el for el in self.root.subtree() if el.role == role])

    def wait_for_function(self, script: object, arg: object = None, timeout: int | None = None) -> None:
        _ = script, arg, timeout
        raise PlaywrightTimeoutError("fake page cannot evaluate page.wait_for_function")

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def test_fill_combobox_scopes_to_associated_listbox_ignoring_unrelated_phone_widget() -> None:
    page = _MultiListboxCountryPage()
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is True
    assert page.single_value.text == "Uzbekistan"
    assert page.phone_widget_clicked is False


def test_fill_combobox_fails_closed_when_no_listbox_association_resolves_among_several() -> None:
    """With two simultaneously visible listboxes and no id/ARIA association
    to the control being filled, the fill must fail closed instead of
    guessing (page-wide search) which listbox is actually the active one."""
    page = _MultiListboxCountryPage(set_listbox_id=False)
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.single_value.text == ""
    assert page.phone_widget_clicked is False


def test_fill_combobox_failure_clears_typed_text_so_read_back_never_returns_it() -> None:
    """Regression for the read-back masquerade bug: when no live option
    matches, `_fill_combobox` must fail closed *and* leave no typed search
    text behind. Without the fix, `page.input.value` (and therefore
    `GreenhouseAdapter.read_back`) would still hold "Uzbekistan" even
    though nothing was ever selected.
    """
    page = _BareInputComboboxPage(["Ukraine", "United Kingdom"])
    control = _FakeLocator([page.input])

    ok = _fill_combobox(page, control, "Uzbekistan")

    assert ok is False
    assert page.input.value == ""

    adapter = GreenhouseAdapter()
    assert adapter.read_back(page, _country_field()) is None
