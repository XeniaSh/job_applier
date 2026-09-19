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

from app.application.autofill import service
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind

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
    ) -> None:
        self.tag = tag
        self.classes = classes
        self.role = role
        self.text = text
        self.elem_id = elem_id
        self.parent: _FakeElement | None = None
        self.children: list[_FakeElement] = []
        self.visible = True

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
    def press(self, key: str) -> None:
        _ = key


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

    def get_attribute(self, name: str) -> str | None:
        if not self._elements:
            return None
        el = self._elements[0]
        if name == "id":
            return el.elem_id
        if name == "role":
            return el.role
        return None

    def input_value(self, timeout: int | None = None) -> str:
        _ = timeout
        return ""

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

    def __init__(self, live_options: list[str], *, control_class: str = "select__control") -> None:
        self.control = _FakeElement("div", classes=control_class)
        value_container = self.control.add_child(_FakeElement("div", classes="select__value-container"))
        self.single_value = value_container.add_child(_FakeElement("div", classes="select__single-value"))
        self.hidden_input = value_container.add_child(_FakeElement("input", elem_id="gender"))
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
