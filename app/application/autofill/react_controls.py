from __future__ import annotations

import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.options import (
    PRIVACY_DATA_CUES,
    label_matches,
    match_affirmative_option,
    match_option,
)

_CHIP_SELECTOR = (
    "[class*='multi-value__label'], [class*='multiValue'] [class*='label'], "
    "[class*='select__multi-value__label'], [class*='multi-value']"
)
_SINGLE_VALUE_SELECTOR = (
    "[class*='single-value'], [class*='singleValue'], [class*='select__single-value']"
)
_CONTROL_XPATH = (
    "xpath=ancestor::*[contains(@class,'select__control') or contains(@class,'iti')][1]"
)
# A selected-display fragment that is *only* a dialing code (e.g. "+998"),
# with no other visible text alongside it -- the shape a phone-styled
# selected-value node renders when its semantic label (e.g. a country name)
# is present in the DOM but not part of the currently visible text.
_DIAL_CODE_ONLY_RE = re.compile(r"^\+\s*\d[\d\s()-]*$")
_DIAL_CODE_TOKEN_RE = re.compile(r"\+\s*\d[\d\s()-]*\d|\+\s*\d+")
_LISTBOX_ID_SUFFIX = "-listbox"


def open_menu(page: Page, locator: Locator) -> list[str]:
    control = _control_root(locator)
    try:
        control.click(timeout=3_000)
    except PlaywrightError:
        control.click(force=True, timeout=3_000)
    return wait_visible_options(page, control=locator)


def wait_visible_options(page: Page, timeout: int = 2_500, control: Locator | None = None) -> list[str]:
    if control is not None:
        # Scope the wait itself to this control's own associated listbox
        # ids -- never the page-wide "[role='option']" wait below, which
        # would resolve as soon as *any* unrelated widget's options are
        # visible (e.g. a phone country-code picker already showing 200+
        # options while the actual target combobox's own menu is still
        # opening).
        ids = _combobox_listbox_ids(control)
        if ids:
            selector = ", ".join(f'[id="{listbox_id}"] [role="option"]' for listbox_id in ids)
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=timeout)
            except PlaywrightTimeoutError:
                pass
        return visible_option_texts(page, control=control)
    try:
        page.locator("[role='option']").first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        pass
    return visible_option_texts(page, control=control)


def visible_option_texts(page: Page, control: Locator | None = None) -> list[str]:
    options = _scoped_options(page, control)
    if options is None:
        return []
    texts: list[str] = []
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if not option.is_visible():
                continue
        except PlaywrightError:
            continue
        text = " ".join((option.inner_text() or "").split())
        if text:
            texts.append(text)
    return texts


def matching_option(page: Page, wanted: str, control: Locator | None = None) -> Locator | None:
    needle = wanted.strip()
    if not needle:
        return None
    options = _scoped_options(page, control)
    if options is None:
        return None
    lowered = needle.lower()
    prefix: Locator | None = None
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if not option.is_visible():
                continue
        except PlaywrightError:
            continue
        text = " ".join((option.inner_text() or "").split())
        if not text:
            continue
        current = text.lower()
        if current == lowered:
            return option
        if label_matches(needle, text):
            prefix = prefix or option
    return prefix


def click_option(page: Page, wanted: str, control: Locator | None = None) -> bool:
    option = matching_option(page, wanted, control=control)
    if option is None:
        return False
    try:
        option.click(timeout=3_000)
    except PlaywrightError:
        try:
            option.click(force=True, timeout=3_000)
        except PlaywrightError:
            return False
    return True


def dismiss_menu(page: Page) -> None:
    try:
        page.keyboard.press("Escape")
    except PlaywrightError:
        pass


def read_selected_chips(page: Page, locator: Locator) -> list[str]:
    try:
        native = locator.evaluate(
            """el => {
                if (el.tagName && el.tagName.toLowerCase() === 'select') {
                    return Array.from(el.selectedOptions || [])
                        .map(opt => (opt.textContent || '').trim())
                        .filter(Boolean);
                }
                return [];
            }"""
        )
    except PlaywrightError:
        native = []
    if isinstance(native, list) and native:
        return [str(item).strip() for item in native if str(item).strip()]
    root = _value_root(locator)
    chips = root.locator(_CHIP_SELECTOR)
    texts: list[str] = []
    seen: set[str] = set()
    for index in range(chips.count()):
        text = " ".join((chips.nth(index).inner_text() or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        texts.append(text)
    return texts


def read_selected_label(page: Page, locator: Locator) -> str | None:
    try:
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            label = locator.evaluate(
                """el => {
                    const opt = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    return opt ? (opt.textContent || '').trim() : (el.value || '');
                }"""
            )
            if isinstance(label, str) and label.strip():
                return " ".join(label.split())
    except PlaywrightError:
        pass
    node = _single_value_node(locator)
    if node is not None:
        # A dedicated selected-value node exists in this control's markup, so
        # its content is authoritative: an empty node means nothing is
        # selected yet, even if the input still holds text the user typed
        # (e.g. a searchable select that filters options without committing
        # one). Falling through to that leftover typed text would report a
        # selection that was never actually made.
        text = " ".join((node.inner_text() or "").split())
        if text and _DIAL_CODE_ONLY_RE.match(text):
            # The only currently-visible text on the selected-value node is a
            # bare dialing code (e.g. a phone-styled country selector that
            # collapses its label at this width) -- that alone is never a
            # committed semantic selection. Recover the real label from the
            # node's full content (which, unlike visible text, also includes
            # any collapsed/hidden label fragment) with the dial-code token
            # stripped back out; fail closed if nothing semantic remains.
            return _semantic_label_ignoring_dial_code(node, text)
        return text or None
    chips = read_selected_chips(page, locator)
    if chips:
        return ", ".join(chips)
    try:
        typed = locator.input_value().strip()
    except PlaywrightError:
        typed = ""
    return typed or None


def wait_for_selected_label(page: Page, locator: Locator, wanted: str, timeout: int = 2_500) -> bool:
    try:
        page.wait_for_function(
            """(payload) => {
                const wanted = String(payload.wanted || '').trim().toLowerCase();
                const matches = (text) => {
                    const haystack = String(text || '').trim().toLowerCase();
                    if (!wanted || !haystack) return false;
                    if (haystack === wanted) return true;
                    const idx = haystack.indexOf(wanted);
                    if (idx < 0) return false;
                    const before = idx === 0 ? ' ' : haystack[idx - 1];
                    const afterIdx = idx + wanted.length;
                    const after = afterIdx >= haystack.length ? ' ' : haystack[afterIdx];
                    return !/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after);
                };
                const singles = Array.from(document.querySelectorAll(payload.singleSel))
                    .map(el => (el.textContent || '').trim());
                const chips = Array.from(document.querySelectorAll(payload.chipSel))
                    .map(el => (el.textContent || '').trim());
                return singles.some(matches) || chips.some(matches);
            }""",
            arg={"wanted": wanted, "chipSel": _CHIP_SELECTOR, "singleSel": _SINGLE_VALUE_SELECTOR},
            timeout=timeout,
        )
        return True
    except PlaywrightError:
        visible = read_selected_label(page, locator) or ""
        return _label_matches(wanted, visible)


def confirm_option_selected(
    page: Page,
    locator: Locator,
    option: Locator,
    matched_value: str,
    timeout: int = 2_500,
) -> bool:
    """Confirm a just-clicked exact option is genuinely this control's
    committed selection.

    The control's own visible display text is the primary signal
    (`wait_for_selected_label`, including its hidden-descendant recovery
    for a display collapsed to a dialing-code fragment). When that display
    is inconclusive -- a bare dialing code (e.g. "+998") with no
    resolvable semantic label anywhere in that node -- text can never
    prove which option actually committed, so fall back to the control's
    own committed React-select state instead of accepting the fragment:
    the clicked option's id retained as this control's active/selected id,
    that option still marked aria-selected="true" wherever it lives in the
    DOM (a real react-select menu can stay mounted, just hidden, after
    close), or an explicit selected semantic attribute on the control's
    own root elements. No bare dialing code, and no other non-empty text,
    is ever accepted on its own.
    """
    if wait_for_selected_label(page, locator, matched_value, timeout=timeout):
        return True
    if not selection_display_is_dial_code_fragment(locator):
        return False
    return _committed_option_label(page, locator, option) is not None


def selection_display_is_dial_code_fragment(locator: Locator) -> bool:
    node = _single_value_node(locator)
    if node is None:
        return False
    text = " ".join((node.inner_text() or "").split())
    return bool(text and _DIAL_CODE_ONLY_RE.match(text))


def select_single_option(page: Page, locator: Locator, wanted: str) -> bool:
    if _is_native_select(locator):
        try:
            locator.select_option(label=wanted)
            return _label_matches(wanted, read_selected_label(page, locator) or "")
        except PlaywrightError:
            try:
                locator.select_option(value=wanted)
                return _label_matches(wanted, read_selected_label(page, locator) or "")
            except PlaywrightError:
                return False
    open_menu(page, locator)
    live = visible_option_texts(page, control=locator)
    match = match_option(wanted, live) or wanted
    if not click_option(page, match, control=locator):
        dismiss_menu(page)
        return False
    persisted = wait_for_selected_label(page, locator, match)
    dismiss_menu(page)
    visible = read_selected_label(page, locator) or ""
    return persisted and _label_matches(match, visible)


def select_yes_no(page: Page, locator: Locator, value: bool, options: list[str] | None = None) -> bool:
    if _is_native_select(locator):
        live = list(options or [])
        if not live:
            live = locator.evaluate(
                """el => Array.from(el.options || []).map(opt => (opt.textContent || '').trim()).filter(Boolean)"""
            ) or []
        matched = match_affirmative_option(value, live)
        if matched is None:
            return False
        return select_single_option(page, locator, matched)
    open_menu(page, locator)
    live = visible_option_texts(page, control=locator) or list(options or [])
    matched = match_affirmative_option(value, live)
    if matched is None:
        dismiss_menu(page)
        return False
    if not click_option(page, matched, control=locator):
        dismiss_menu(page)
        return False
    persisted = wait_for_selected_label(page, locator, matched)
    dismiss_menu(page)
    visible = read_selected_label(page, locator) or ""
    return persisted and (
        semantic_choice_matches(value, visible) or _affirmative_ack_matches(value, visible)
    )


def select_multi_options(
    page: Page,
    locator: Locator,
    values: list[str],
    *,
    max_choices: int | None,
) -> bool:
    wanted = [item.strip() for item in values if item and str(item).strip()]
    if max_choices is not None and max_choices > 0:
        wanted = wanted[:max_choices]
    if not wanted:
        return False
    if _is_native_select(locator):
        try:
            locator.select_option(label=wanted)
        except PlaywrightError:
            try:
                locator.select_option(value=wanted)
            except PlaywrightError:
                return False
        chips = read_selected_chips(page, locator)
        return _chips_contain(chips, wanted)
    confirmed: list[str] = []
    for value in wanted:
        match = _select_one_multi_value(page, locator, value, confirmed)
        if match is None:
            continue
        confirmed.append(match)
        chips = read_selected_chips(page, locator)
        if not _chips_contain(chips, confirmed):
            return False
    if not confirmed:
        return False
    final = read_selected_chips(page, locator)
    return _chips_contain(final, confirmed)


def semantic_choice_matches(expected: bool, actual: str) -> bool:
    cleaned = actual.strip().lower()
    if not cleaned:
        return False
    if expected:
        return bool(re.match(r"^(yes)\b", cleaned))
    return bool(re.match(r"^(no)\b", cleaned))


def _affirmative_ack_matches(expected: bool, actual: str) -> bool:
    if not expected:
        return False
    cleaned = actual.strip().lower()
    if "acknowledge" in cleaned or cleaned in {
        "confirm",
        "i agree",
        "i accept",
        "agree",
        "accept",
    }:
        return True
    return "i understand" in cleaned and any(cue in cleaned for cue in PRIVACY_DATA_CUES)


def is_checked(locator: Locator) -> bool:
    try:
        aria = (locator.get_attribute("aria-checked") or "").lower()
        if aria == "true":
            return True
        if aria == "false":
            try:
                if locator.evaluate("el => el.checked === true"):
                    return True
            except PlaywrightError:
                return False
            return False
    except PlaywrightError:
        pass
    try:
        return bool(locator.is_checked())
    except PlaywrightError:
        try:
            return bool(locator.evaluate("el => el.checked === true"))
        except PlaywrightError:
            return False


def describe_checkbox_control(locator: Locator) -> str:
    try:
        return str(
            locator.evaluate(
                """el => {
                    const tag = (el.tagName || '').toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    const role = (el.getAttribute('role') || '').toLowerCase();
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const className = String(el.className || '');
                    const hiddenStyle = style.opacity === '0' || style.display === 'none'
                        || style.visibility === 'hidden' || parseFloat(style.width) <= 1
                        || parseFloat(style.height) <= 1 || rect.width <= 1 || rect.height <= 1;
                    const visuallyHidden = /sr-only|visually-hidden|visuallyhidden/.test(className);
                    const wrapped = el.closest('label') !== null;
                    const id = el.getAttribute('id');
                    const labelled = id && document.querySelector(`label[for="${id}"]`);
                    if (role === 'checkbox' && type !== 'checkbox') return 'role=checkbox';
                    if (type === 'checkbox' && (hiddenStyle || visuallyHidden)) {
                        if (wrapped || labelled) return 'hidden input + styled/label-backed checkbox';
                        return 'hidden input[type=checkbox]';
                    }
                    if (type === 'checkbox') return 'native input[type=checkbox]';
                    if (tag === 'input') return 'input';
                    return tag || 'unknown';
                }"""
            )
        )
    except PlaywrightError:
        return "unknown"


def set_checkbox(page: Page, locator: Locator, checked: bool) -> bool:
    """Check a native or styled React checkbox; success only if state persists."""
    result = set_checkbox_with_trace(page, locator, checked)
    return result["success"]


def set_checkbox_with_trace(page: Page, locator: Locator, checked: bool) -> dict[str, object]:
    control_type = describe_checkbox_control(locator)
    if is_checked(locator) is checked:
        return {
            "success": True,
            "control_type": control_type,
            "interaction_attempted": "already_in_desired_state",
            "readback_checked": checked,
            "failure_reason": None,
        }
    strategies: list[tuple[str, Locator]] = []
    text_target = _visible_label_text_target(locator)
    if text_target is not None:
        strategies.append(("click_visible_option_text", text_target))
    label_target = _checkbox_click_target(page, locator)
    if label_target is not locator:
        strategies.append(("click_associated_label", label_target))
    role_target = _role_checkbox_in_group(locator)
    if role_target is not None:
        strategies.append(("click_role_checkbox", role_target))
    if _checkbox_is_visible_enabled(locator):
        strategies.append(("locator.check", locator))
    strategies.append(("click_control", locator))

    attempted: list[str] = []
    for name, target in strategies:
        attempted.append(name)
        if name == "locator.check":
            if not _try_check(locator):
                continue
        elif not _click_user(target):
            continue
        if _wait_checked_stable(page, locator, checked):
            return {
                "success": True,
                "control_type": control_type,
                "interaction_attempted": ",".join(attempted),
                "readback_checked": checked,
                "failure_reason": None,
            }
    final = is_checked(locator)
    return {
        "success": final is checked,
        "control_type": control_type,
        "interaction_attempted": ",".join(attempted) or "none",
        "readback_checked": final,
        "failure_reason": None if final is checked else "checked_state_did_not_persist",
    }


def _select_one_multi_value(
    page: Page,
    locator: Locator,
    value: str,
    already: list[str],
) -> str | None:
    for _attempt in range(3):
        live = open_menu(page, locator)
        match = match_option(value, live) or (
            value if any(label_matches(value, item) for item in live) else None
        )
        if match is None:
            dismiss_menu(page)
            return None
        if not click_option(page, match, control=locator):
            dismiss_menu(page)
            continue
        _wait_for_chip(page, match)
        chips = read_selected_chips(page, locator)
        needed = already + [match]
        if _chips_contain(chips, needed):
            if page.locator("[role='option']").count() > 0:
                dismiss_menu(page)
            return match
        missing = [item for item in needed if not _chips_contain(chips, [item])]
        dismiss_menu(page)
        for item in missing:
            if item.lower() == match.lower():
                continue
            open_menu(page, locator)
            if click_option(page, item, control=locator):
                _wait_for_chip(page, item)
        chips = read_selected_chips(page, locator)
        if _chips_contain(chips, needed):
            dismiss_menu(page)
            return match
    return None


def _wait_for_chip(page: Page, wanted: str) -> None:
    pattern = re.compile(re.escape(wanted), re.I)
    chip = page.locator(_CHIP_SELECTOR).filter(has_text=pattern)
    try:
        chip.first.wait_for(state="visible", timeout=2_500)
    except PlaywrightTimeoutError:
        pass


def _chips_contain(chips: list[str], needed: list[str]) -> bool:
    for item in needed:
        if not any(label_matches(item, chip) for chip in chips):
            return False
    return True


def _label_matches(wanted: str, actual: str) -> bool:
    return label_matches(wanted, actual)


def _click_user(locator: Locator) -> bool:
    try:
        locator.click(timeout=3_000)
        return True
    except PlaywrightError:
        try:
            locator.click(force=True, timeout=3_000)
            return True
        except PlaywrightError:
            return False


def _try_check(locator: Locator) -> bool:
    try:
        locator.check(timeout=3_000)
        return True
    except PlaywrightError:
        try:
            locator.check(force=True, timeout=3_000)
            return True
        except PlaywrightError:
            return False


def _checkbox_is_visible_enabled(locator: Locator) -> bool:
    try:
        return bool(locator.is_visible() and locator.is_enabled())
    except PlaywrightError:
        return False


def _visible_label_text_target(locator: Locator) -> Locator | None:
    label = locator.locator("xpath=ancestor::label[1]")
    if label.count() == 0:
        element_id = locator.get_attribute("id") or ""
        if not element_id:
            return None
        labeled = locator.page.locator(f'label[for="{element_id}"]')
        if labeled.count() == 0:
            return None
        label = labeled.first
    else:
        label = label.first
    candidates = label.locator("xpath=.//*[self::span or self::div or self::p][normalize-space()]")
    for index in range(candidates.count() - 1, -1, -1):
        node = candidates.nth(index)
        try:
            if node.is_visible() and (node.inner_text() or "").strip():
                return node
        except PlaywrightError:
            continue
    try:
        if label.is_visible() and (label.inner_text() or "").strip():
            return label
    except PlaywrightError:
        return None
    return None


def _role_checkbox_in_group(locator: Locator) -> Locator | None:
    group = _question_group(locator)
    role = group.locator("[role='checkbox']")
    if role.count() == 0:
        return None
    try:
        if role.first.is_visible():
            return role.first
    except PlaywrightError:
        return None
    return role.first


def _question_group(locator: Locator) -> Locator:
    parent = locator.locator(
        "xpath=ancestor::*[self::fieldset or self::section or contains(@class,'question') "
        "or contains(@class,'field') or contains(@id,'question')][1]"
    )
    if parent.count() > 0:
        return parent.first
    parent = locator.locator("xpath=ancestor::div[1]")
    if parent.count() > 0:
        return parent.first
    return locator


def _checkbox_click_target(page: Page, locator: Locator) -> Locator:
    element_id = locator.get_attribute("id") or ""
    if element_id:
        labeled = page.locator(f'label[for="{element_id}"]')
        if labeled.count() > 0:
            try:
                if labeled.first.is_visible():
                    return labeled.first
            except PlaywrightError:
                pass
    wrapped = locator.locator("xpath=ancestor::label[1]")
    if wrapped.count() > 0:
        try:
            if wrapped.first.is_visible():
                return wrapped.first
        except PlaywrightError:
            pass
    return locator


def _wait_checked_stable(page: Page, locator: Locator, checked: bool, timeout: int = 700) -> bool:
    deadline = time.monotonic() + timeout / 1000
    while True:
        _settle_react(locator)
        if is_checked(locator) is checked:
            _settle_react(locator)
            if is_checked(locator) is checked:
                return True
        if time.monotonic() >= deadline:
            return is_checked(locator) is checked
        try:
            page.wait_for_timeout(50)
        except PlaywrightError:
            return is_checked(locator) is checked


def _settle_react(locator: Locator) -> None:
    try:
        locator.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
    except PlaywrightError:
        pass


def _is_native_select(locator: Locator) -> bool:
    try:
        return locator.evaluate("el => el.tagName.toLowerCase() === 'select'")
    except PlaywrightError:
        return False


def _control_root(locator: Locator) -> Locator:
    parent = locator.locator(_CONTROL_XPATH)
    if parent.count() > 0:
        return parent.first
    return locator


def _value_root(locator: Locator) -> Locator:
    parent = locator.locator(
        "xpath=ancestor::*[contains(@class,'field') or contains(@class,'select__control') "
        "or contains(@class,'iti')][1]"
    )
    if parent.count() > 0:
        return parent.first
    return locator


def _semantic_label_ignoring_dial_code(node: Locator, visible_text: str) -> str | None:
    try:
        full = " ".join((node.text_content() or "").split())
    except PlaywrightError:
        return None
    stripped = " ".join(_DIAL_CODE_TOKEN_RE.sub(" ", full).split())
    if stripped and stripped.lower() != visible_text.strip().lower():
        return stripped
    return None


def _single_value_node(locator: Locator) -> Locator | None:
    root = _value_root(locator)
    selected = root.locator(_SINGLE_VALUE_SELECTOR)
    if selected.count() == 0:
        return None
    return selected.first


def _committed_option_label(page: Page, locator: Locator, option: Locator) -> str | None:
    """Read a semantic selection label from the control's own committed
    React-select state -- never from display text. Used only once display
    text has already proven inconclusive (a bare dialing-code fragment);
    requires the exact clicked option's own id to show up in one of the
    control's own persisted selection signals, never a bare non-empty
    attribute value.
    """
    try:
        option_id = (option.get_attribute("id") or "").strip()
    except PlaywrightError:
        option_id = ""
    try:
        option_label = " ".join((option.inner_text() or "").split())
    except PlaywrightError:
        option_label = ""
    if not option_id or not option_label:
        return None
    control = _control_root(locator)
    for attr_owner in (locator, control):
        for attr in ("aria-activedescendant", "aria-selected-id", "data-selected-id"):
            try:
                active = (attr_owner.get_attribute(attr) or "").strip()
            except PlaywrightError:
                active = ""
            if active and active == option_id:
                return option_label
    try:
        retained = page.locator(f'[id="{option_id}"]')
        if retained.count() > 0:
            aria_selected = (retained.first.get_attribute("aria-selected") or "").strip().lower()
            if aria_selected == "true":
                return option_label
    except PlaywrightError:
        pass
    for root in (control, _value_root(locator)):
        for attr in ("aria-label", "data-value", "data-selected-value", "title"):
            try:
                raw_attr = (root.get_attribute(attr) or "").strip()
            except PlaywrightError:
                raw_attr = ""
            if raw_attr and label_matches(option_label, raw_attr):
                return option_label
    return None


def _combobox_listbox_ids(control: Locator) -> list[str]:
    """Candidate listbox element ids this control's live menu could be
    rendered under -- via the standard ARIA association first, falling back
    to react-select's own id convention (never a hardcoded field id)."""
    ids: list[str] = []
    for attr in ("aria-controls", "aria-owns"):
        try:
            raw = (control.get_attribute(attr) or "").strip()
        except PlaywrightError:
            raw = ""
        if raw:
            ids.extend(raw.split())
    try:
        control_id = (control.get_attribute("id") or "").strip()
    except PlaywrightError:
        control_id = ""
    if control_id:
        if control_id.endswith("-input"):
            ids.append(f"{control_id[: -len('-input')]}{_LISTBOX_ID_SUFFIX}")
        ids.append(f"{control_id}{_LISTBOX_ID_SUFFIX}")
        ids.append(f"react-select-{control_id}{_LISTBOX_ID_SUFFIX}")
    seen: set[str] = set()
    ordered: list[str] = []
    for item in ids:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _visible_listboxes(page: Page) -> list[Locator]:
    boxes = page.locator("[role='listbox']")
    try:
        count = boxes.count()
    except PlaywrightError:
        return []
    result: list[Locator] = []
    for index in range(count):
        node = boxes.nth(index)
        try:
            if node.is_visible():
                result.append(node)
        except PlaywrightError:
            continue
    return result


def _resolve_active_listbox(page: Page, control: Locator | None) -> Locator | None:
    """Resolve the live options menu actually associated with `control`.

    Prefers an explicit id association (ARIA or the react-select id
    convention). Only falls back to "the sole visible listbox on the page"
    when no association can be resolved -- a safe inference exactly because
    it is unambiguous; with more than one visible listbox and no resolvable
    association, callers must fail closed instead of guessing which one is
    active (e.g. never cross-match an unrelated phone-widget listbox).
    """
    if control is not None:
        for listbox_id in _combobox_listbox_ids(control):
            found = page.locator(f'[id="{listbox_id}"]')
            try:
                if found.count() > 0 and found.first.is_visible():
                    return found.first
            except PlaywrightError:
                continue
    visible = _visible_listboxes(page)
    if len(visible) == 1:
        return visible[0]
    return None


def _scoped_options(page: Page, control: Locator | None) -> Locator | None:
    scope = _resolve_active_listbox(page, control)
    if scope is None:
        return None
    return scope.locator("[role='option']")
