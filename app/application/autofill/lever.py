"""Lever-specific form discovery and filling. No submit capability.

Lever's hosted application pages (`jobs.lever.co/<company>/<posting-id>/apply`)
are plain server-rendered HTML: standard identity fields carry a
`<label for="id">` association, and per-posting custom questions are wrapped
in `.application-question` blocks with an `.application-label` heading
instead. There is no React-driven combobox/chip layer like Greenhouse's, so
this adapter is intentionally much smaller than `app.application.autofill.greenhouse`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever_url import is_canonical_lever_hosted_url
from app.application.autofill.options import match_option, match_yes_no
from app.application.autofill.questions import QuestionKind

_TEXT_TYPES = frozenset({"text", "email", "tel", "url", "search", "number"})
_FORM_READY_SELECTOR = "#application-form, form.application-form, input#name, input[name='name']"

# Lever's own hosted job-detail template renders its "Apply for this job"
# action as an <a data-qa="btn-apply-top|btn-apply-bottom"> inside the
# posting header/footer. That `data-qa` attribute is specific to Lever's
# template (unlike a generic "click the button that says Apply" rule), and
# the submit control on the application form itself never carries it, so
# this selector cannot accidentally match a submit button.
_JOB_DETAIL_APPLY_SELECTOR = "a[data-qa='btn-apply-top'], a[data-qa='btn-apply-bottom']"

logger = logging.getLogger(__name__)

_QUESTION_CONTEXT_JS = """el => {
    const container = el.closest('.application-question') || el.closest('li') || el.parentElement;
    if (!container) return {label: '', required: false};
    const heading = container.querySelector('.application-label, label, legend, [class*="label"]');
    const label = heading ? (heading.innerText || '') : '';
    const requiredMarker = container.querySelector('.required, [class*="required"]');
    const required = /\\*/.test(label) || !!requiredMarker;
    return {label: label.replace(/\\s+/g, ' ').trim(), required};
}"""


class LeverFormError(Exception):
    """Raised when a Lever application page cannot be used."""


def is_lever_application_page(page: Page) -> bool:
    if page.locator("#application-form, form.application-form").count() > 0:
        return True
    if (
        page.locator('input[name="resume"]').count() > 0
        and page.locator('input#name, input[name="name"]').count() > 0
    ):
        return True
    url = page.url.lower()
    return "lever.co" in url and page.locator("form").count() > 0


def _job_detail_apply_action(page: Page) -> Locator | None:
    locator = page.locator(_JOB_DETAIL_APPLY_SELECTOR)
    if locator.count() == 0:
        return None
    return locator.first


def is_lever_job_detail_page(page: Page) -> bool:
    """True for a Lever hosted job-detail page with an Apply action, not yet the form."""
    if is_lever_application_page(page):
        return False
    if _job_detail_apply_action(page) is None:
        return False
    url = page.url
    if url.startswith("file:"):
        return True
    return is_canonical_lever_hosted_url(url)


def detect_security_challenge(page: Page) -> str | None:
    title = page.title().lower()
    body = page.locator("body").inner_text()[:3000].lower()
    if "just a moment" in title or "checking your browser" in body:
        return "cloudflare_challenge"
    if page.locator("#cf-challenge, .cf-browser-verification").count() > 0:
        return "cloudflare_challenge"
    if is_lever_application_page(page):
        return None
    if page.locator(".g-recaptcha, iframe[src*='recaptcha'], iframe[src*='captcha']").count() > 0:
        return "captcha"
    if page.locator('input[type="password"]').count() > 0:
        return "login"
    return None


class LeverAdapter:
    """Lever-specific form discovery and filling. No submit capability."""

    last_cover_letter_error: str | None = None
    last_multiselect_selected: list[str] | None = None
    last_privacy_trace: dict[str, object] | None = None

    def recognize(self, page: Page) -> bool:
        return is_lever_application_page(page)

    def detect_challenge(self, page: Page) -> str | None:
        return detect_security_challenge(page)

    def prepare_page(self, page: Page) -> None:
        """Wait until the application form is usable.

        If the page is still Lever's job-detail page (the posting overview
        with an "Apply for this job" action, not the form), navigate there
        first. If that navigation does not land on the recognized
        application form, fail closed: recognize() will then be False and
        the caller reports UNSUPPORTED_FORM rather than guessing at an
        unfamiliar page.
        Local fixtures skip the extra settle.
        """
        if is_lever_job_detail_page(page):
            self._navigate_from_job_detail(page)
        timeout = 2_000 if page.url.startswith("file:") else 30_000
        try:
            page.wait_for_selector(_FORM_READY_SELECTOR, timeout=timeout)
        except PlaywrightTimeoutError:
            return
        if page.url.startswith("file:"):
            return
        page.wait_for_timeout(500)

    def _navigate_from_job_detail(self, page: Page) -> None:
        action = _job_detail_apply_action(page)
        if action is None:
            return
        try:
            action.click(timeout=5_000)
        except PlaywrightError:
            return
        timeout = 2_000 if page.url.startswith("file:") else 15_000
        try:
            page.wait_for_selector(_FORM_READY_SELECTOR, timeout=timeout)
        except PlaywrightTimeoutError:
            return

    def discover_fields(self, page: Page) -> list[DiscoveredField]:
        if not self.recognize(page):
            raise LeverFormError("UNSUPPORTED_FORM")
        form = _application_form(page)
        fields: list[DiscoveredField] = []
        seen_radio_names: set[str] = set()
        seen_ids: set[str] = set()
        controls = form.locator("input, textarea, select")
        for index in range(controls.count()):
            locator = controls.nth(index)
            field = _discover_control(page, locator, seen_radio_names)
            if field is None:
                continue
            if field.element_id:
                if field.element_id in seen_ids:
                    continue
                seen_ids.add(field.element_id)
            fields.append(field)
        return fields

    def fill_field(self, page: Page, classified: ClassifiedField) -> bool:
        self.last_multiselect_selected = None
        if not classified.fill or classified.value is None:
            return False
        field = classified.field
        locator = _field_locator(page, field)
        try:
            if classified.kind is QuestionKind.COVER_LETTER:
                ok = _fill_text(locator, str(classified.value))
                if not ok:
                    self.last_cover_letter_error = f"Cover letter not filled for '{field.label}'."
                return ok
            if field.field_type == "file":
                locator.set_input_files(str(classified.value))
                return True
            if field.field_type == "checkbox":
                checked = _checkbox_should_check(classified.value)
                if checked:
                    locator.check(timeout=3_000)
                else:
                    locator.uncheck(timeout=3_000)
                return locator.is_checked() == checked
            if field.field_type == "radio":
                return _fill_radio(page, field, classified.value)
            if isinstance(classified.value, list) or field.field_type == "multiselect":
                values = classified.value if isinstance(classified.value, list) else [str(classified.value)]
                selected = _fill_multiselect(locator, field, values, classified.max_choices)
                self.last_multiselect_selected = selected
                return bool(selected)
            if field.field_type == "select":
                return _fill_select(locator, classified.value, field.options)
            if field.field_type in _TEXT_TYPES or field.field_type == "textarea":
                return _fill_text(locator, str(classified.value))
        except PlaywrightError:
            return False
        return False

    def upload_resume(self, page: Page, resume_path: Path, field: DiscoveredField) -> bool:
        path = str(resume_path)
        filename = resume_path.name
        locator = _field_locator(page, field)
        try:
            if locator.count() > 0:
                locator.set_input_files(path, timeout=5_000, no_wait_after=True)
        except PlaywrightError:
            pass
        if _resume_is_attached(page, field, filename):
            return True
        if not page.url.startswith("file:"):
            button = locator.locator("xpath=..").locator('button:not([type="submit"])')
            if button.count() > 0:
                try:
                    with page.expect_file_chooser(timeout=5_000) as chooser:
                        button.first.click(timeout=3_000)
                    chooser.value.set_files(path)
                except PlaywrightError:
                    pass
            page.wait_for_timeout(500)
        return _resume_is_attached(page, field, filename)

    def read_back(self, page: Page, field: DiscoveredField) -> str | None:
        if field.field_type == "file":
            names = _file_names(page, field)
            return names[0] if names else None
        locator = _field_locator(page, field)
        if field.field_type == "checkbox":
            try:
                return "true" if locator.is_checked() else "false"
            except PlaywrightError:
                return None
        if field.field_type == "radio":
            if not field.name:
                return None
            checked = page.locator(f'input[type="radio"][name="{field.name}"]:checked')
            if checked.count() == 0:
                return None
            label_text = checked.first.evaluate(
                """el => {
                    const label = el.closest('label');
                    return label ? label.innerText : '';
                }"""
            )
            value = checked.first.get_attribute("value")
            visible = " ".join(str(label_text or "").split())
            return visible or value
        if field.field_type == "multiselect":
            try:
                selected = locator.evaluate(
                    "el => Array.from(el.selectedOptions || [])"
                    ".map(opt => (opt.textContent || '').trim()).filter(Boolean)"
                )
            except PlaywrightError:
                selected = []
            return ", ".join(selected) if isinstance(selected, list) and selected else None
        if field.field_type == "select":
            try:
                text = locator.evaluate(
                    "el => { const opt = el.options[el.selectedIndex];"
                    " return opt ? opt.textContent.trim() : ''; }"
                )
            except PlaywrightError:
                return None
            return text or None
        try:
            value = locator.input_value()
        except PlaywrightError:
            return None
        return value or None

    def invalid_required_empty(self, page: Page, field: DiscoveredField) -> bool:
        if not field.required:
            return False
        value = self.read_back(page, field)
        return value is None or value == "" or (value == "false" and field.field_type == "checkbox")


def _application_form(page: Page) -> Locator:
    for selector in ("#application-form", "form.application-form", "form"):
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    raise LeverFormError("UNSUPPORTED_FORM")


def _question_context(locator: Locator) -> tuple[str, bool]:
    try:
        raw = locator.evaluate(_QUESTION_CONTEXT_JS)
    except PlaywrightError:
        return "", False
    if not isinstance(raw, dict):
        return "", False
    return str(raw.get("label") or ""), bool(raw.get("required"))


def _control_label(page: Page, locator: Locator) -> str:
    aria = (locator.get_attribute("aria-label") or "").strip()
    if aria:
        return aria
    element_id = locator.get_attribute("id")
    if element_id:
        labeled = page.locator(f'label[for="{element_id}"]')
        if labeled.count() > 0:
            return " ".join(labeled.first.inner_text().split())
    wrapped = locator.evaluate(
        """el => {
            const label = el.closest('label');
            return label ? label.innerText : '';
        }"""
    )
    if isinstance(wrapped, str) and wrapped.strip():
        return " ".join(wrapped.split())
    return ""


def _field_type(tag: str, input_type: str) -> str:
    if tag == "textarea":
        return "textarea"
    if input_type in _TEXT_TYPES or input_type == "":
        return input_type or "text"
    return input_type


def _discover_control(
    page: Page,
    locator: Locator,
    seen_radio_names: set[str],
) -> DiscoveredField | None:
    tag = locator.evaluate("el => el.tagName.toLowerCase()")
    input_type = (locator.get_attribute("type") or "").lower()
    if tag == "input" and input_type in {"hidden", "submit", "button", "reset", "image"}:
        return None
    name = locator.get_attribute("name")
    element_id = locator.get_attribute("id")
    context_label, context_required = _question_context(locator)
    if tag == "input" and input_type == "radio":
        if not name or name in seen_radio_names:
            return None
        seen_radio_names.add(name)
        options = _radio_options(page, name)
        label = context_label or _radio_group_label(page, locator, name) or name
        return DiscoveredField(
            label=label,
            name=name,
            field_type="radio",
            required=_is_required_group(page, name) or context_required,
            options=options,
            current_value=_checked_radio_value(page, name),
            element_id=element_id,
            context=context_label,
        )
    if tag == "select":
        field_type = "multiselect" if locator.get_attribute("multiple") is not None else "select"
    else:
        field_type = _field_type(tag, input_type)
    direct_label = _control_label(page, locator)
    label = direct_label or context_label or name or ""
    required = _is_required(locator) or context_required
    options = _select_options(locator) if field_type in {"select", "multiselect"} else []
    return DiscoveredField(
        label=label,
        name=name,
        field_type=field_type,
        required=required,
        options=options,
        current_value=_current_value(locator, field_type),
        autocomplete=locator.get_attribute("autocomplete"),
        element_id=element_id,
        context=context_label if field_type == "checkbox" else "",
    )


def _is_required(locator: Locator) -> bool:
    if locator.get_attribute("required") is not None:
        return True
    aria = (locator.get_attribute("aria-required") or "").lower()
    return aria == "true"


def _is_required_group(page: Page, name: str) -> bool:
    group = page.locator(f'input[type="radio"][name="{name}"]')
    for index in range(group.count()):
        if _is_required(group.nth(index)):
            return True
    return False


def _radio_group_label(page: Page, locator: Locator, name: str) -> str:
    legend = locator.evaluate(
        """el => {
            const fieldset = el.closest('fieldset');
            const legend = fieldset ? fieldset.querySelector('legend') : null;
            return legend ? legend.innerText : '';
        }"""
    )
    if isinstance(legend, str) and legend.strip():
        return " ".join(legend.split())
    return _control_label(page, locator)


def _radio_options(page: Page, name: str) -> list[str]:
    radios = page.locator(f'input[type="radio"][name="{name}"]')
    options: list[str] = []
    for index in range(radios.count()):
        radio = radios.nth(index)
        value = radio.get_attribute("value") or radio.evaluate(
            """el => {
                const label = el.closest('label');
                return label ? label.innerText : '';
            }"""
        )
        if value:
            options.append(str(value).strip())
    return options


def _checked_radio_value(page: Page, name: str) -> str | None:
    checked = page.locator(f'input[type="radio"][name="{name}"]:checked')
    if checked.count() == 0:
        return None
    return checked.first.get_attribute("value")


def _select_options(locator: Locator) -> list[str]:
    return locator.evaluate(
        "el => Array.from(el.options || []).map(opt => (opt.textContent || '').trim()).filter(Boolean)"
    )


def _current_value(locator: Locator, field_type: str) -> str | None:
    if field_type == "file":
        return None
    if field_type == "checkbox":
        try:
            return "true" if locator.is_checked() else "false"
        except PlaywrightError:
            return None
    try:
        value = locator.input_value()
    except PlaywrightError:
        return None
    return value or None


def _field_locator(page: Page, field: DiscoveredField) -> Locator:
    if field.element_id:
        return page.locator(f'[id="{field.element_id}"]')
    if field.name:
        if field.field_type == "radio":
            return page.locator(f'input[type="radio"][name="{field.name}"]')
        return page.locator(f'[name="{field.name}"]')
    return page.get_by_label(field.label)


def _fill_text(locator: Locator, value: str) -> bool:
    try:
        locator.fill(value, timeout=5_000)
    except PlaywrightError:
        try:
            locator.click(timeout=3_000)
        except PlaywrightError:
            locator.click(force=True, timeout=3_000)
        locator.fill(value, timeout=5_000)
    try:
        locator.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
    except PlaywrightError:
        pass
    return True


def _semantic_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    cleaned = str(value).strip().lower()
    if re.match(r"^(yes)\b", cleaned) or cleaned in {"true", "y"}:
        return True
    if re.match(r"^(no)\b", cleaned) or cleaned in {"false", "n"}:
        return False
    return None


def _fill_select(locator: Locator, value: object, options: list[str]) -> bool:
    semantic = _semantic_bool(value)
    if semantic is not None:
        matched = match_yes_no(semantic, options) or ("Yes" if semantic else "No")
        try:
            locator.select_option(label=matched)
            return True
        except PlaywrightError:
            return False
    wanted = value if isinstance(value, str) else str(value)
    labels = [item.strip() for item in options]
    if wanted not in labels:
        matched = match_option(wanted, options)
        if matched:
            wanted = matched
    try:
        locator.select_option(label=wanted)
        return True
    except PlaywrightError:
        pass
    try:
        locator.select_option(value=wanted)
        return True
    except PlaywrightError:
        return False


def _fill_radio(page: Page, field: DiscoveredField, value: object) -> bool:
    if not field.name:
        return False
    wanted = value if isinstance(value, str) else ("Yes" if value else "No")
    semantic = _semantic_bool(value)
    if semantic is not None:
        matched = match_yes_no(semantic, field.options)
        if matched:
            wanted = matched
    radios = page.locator(f'input[type="radio"][name="{field.name}"]')
    for index in range(radios.count()):
        radio = radios.nth(index)
        option_value = (radio.get_attribute("value") or "").strip()
        label_text = radio.evaluate(
            """el => {
                const label = el.closest('label');
                return label ? label.innerText : '';
            }"""
        )
        haystack = f"{option_value} {label_text}".strip()
        if wanted.lower() == option_value.lower() or wanted.lower() == str(label_text).strip().lower():
            radio.check()
            return True
        if wanted.lower() in haystack.lower():
            radio.check()
            return True
    return False


def _fill_multiselect(
    locator: Locator,
    field: DiscoveredField,
    values: list[str],
    max_choices: int | None,
) -> list[str]:
    wanted = [str(item).strip() for item in values if str(item).strip()]
    if not wanted:
        return []
    matched: list[str] = []
    for value in wanted:
        option = match_option(value, field.options) or value
        if option in field.options and option not in matched:
            matched.append(option)
    if max_choices is not None and max_choices > 0:
        matched = matched[:max_choices]
    if not matched:
        return []
    try:
        locator.select_option(label=matched)
        return matched
    except PlaywrightError:
        return []


def _checkbox_should_check(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on", "acknowledge", "confirm", "acknowledge/confirm"}


def _file_names(page: Page, field: DiscoveredField) -> list[str]:
    selector = f'[id="{field.element_id}"]' if field.element_id else 'input[type="file"][name="resume"]'
    try:
        found = page.evaluate(
            """sel => {
                const el = document.querySelector(sel);
                return el ? Array.from(el.files || []).map(file => file.name) : [];
            }""",
            selector,
        )
    except PlaywrightError:
        return []
    if not isinstance(found, list):
        return []
    return [str(item) for item in found]


def _resume_is_attached(page: Page, field: DiscoveredField, filename: str | None) -> bool:
    names = _file_names(page, field)
    if filename and filename in names:
        return True
    if filename is None:
        return bool(names)
    return False
