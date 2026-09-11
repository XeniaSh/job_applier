from __future__ import annotations

import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.options import (
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


def open_menu(page: Page, locator: Locator) -> list[str]:
    control = _control_root(locator)
    try:
        control.click(timeout=3_000)
    except PlaywrightError:
        control.click(force=True, timeout=3_000)
    return wait_visible_options(page)


def wait_visible_options(page: Page, timeout: int = 2_500) -> list[str]:
    try:
        page.locator("[role='option']").first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        pass
    return visible_option_texts(page)


def visible_option_texts(page: Page) -> list[str]:
    options = page.get_by_role("option")
    if options.count() == 0:
        options = page.locator("[role='option']")
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


def matching_option(page: Page, wanted: str) -> Locator | None:
    needle = wanted.strip()
    if not needle:
        return None
    options = page.get_by_role("option")
    if options.count() == 0:
        options = page.locator("[role='option']")
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


def click_option(page: Page, wanted: str) -> bool:
    option = matching_option(page, wanted)
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
    root = _value_root(locator)
    selected = root.locator(_SINGLE_VALUE_SELECTOR)
    if selected.count() > 0:
        text = " ".join((selected.first.inner_text() or "").split())
        if text:
            return text
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
    live = visible_option_texts(page)
    match = match_option(wanted, live) or wanted
    if not click_option(page, match):
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
    live = visible_option_texts(page) or list(options or [])
    matched = match_affirmative_option(value, live)
    if matched is None:
        dismiss_menu(page)
        return False
    if not click_option(page, matched):
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
    return "acknowledge" in cleaned or cleaned in {
        "confirm",
        "i agree",
        "i accept",
        "agree",
        "accept",
    }


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
        if not click_option(page, match):
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
            if click_option(page, item):
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
