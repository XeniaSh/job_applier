"""Ashby-specific form discovery and filling. No submit capability.

Bounded to the control shapes evidenced from a real Ashby `/application`
page: plain `[data-field-path]`-wrapped text/email/url/number inputs (Name,
Email, LinkedIn Profile, ...), the main resume file input, native radio
groups, and Ashby's custom Yes/No button control
(`.ashby-application-form-input-yesno-option[data-option]`). Three kinds of
field are deliberately never auto-filled, even if a future change to the
shared classifier/mapping would otherwise resolve them:

- The "Where did you hear..." / "Current Location" autocomplete comboboxes
  (`.ashby-application-form-input-autocomplete`) are discovered as an
  unsupported control (never a plain text field) -- a snapshot only ever
  shows the closed combo, never its listbox/options, so there is nothing to
  safely match a typed value against.
- The optional multi-option employee-relationship checkbox group is never
  discovered at all: per-option semantics cannot be resolved generically, and
  guessing which box to check would risk checking the wrong one.
- The recruiting-contact consent checkbox (`_systemfield_data_consent_ack`)
  is discovered (so it still surfaces for manual review) but `fill_field`
  refuses to write to it under any circumstances -- opting a candidate into
  marketing contact must never be automatic.

`.ashby-application-form-submit-button` is recognition evidence only and is
never queried for interaction anywhere in this module -- there is no submit
capability here, matching `GreenhouseAdapter`/`LeverAdapter`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import match_option_exact_normalized, match_yes_no

logger = logging.getLogger(__name__)

_TEXT_TYPES = frozenset({"text", "email", "url", "number", "tel", "search"})

_APPLICATION_PANEL_SELECTOR = (
    '[role="tabpanel"]:has(button.ashby-application-form-submit-button)'
    ':has([data-field-path="_systemfield_name"], [data-field-path="_systemfield_email"])'
)
_FIELD_WRAPPER_SELECTOR = "[data-field-path]"
_YESNO_OPTION_CLASS = "ashby-application-form-input-yesno-option"
_CONSENT_FIELD_NAME = "_systemfield_data_consent_ack"
_ACTIVE_CAPTCHA_SELECTOR = (
    'iframe[src*="recaptcha" i], iframe[title*="recaptcha" i], '
    'iframe[src*="hcaptcha" i], iframe[title*="hcaptcha" i], '
    'div.g-recaptcha, div#hcaptcha, [class*="captcha-modal" i], [class*="challenge-modal" i]'
)

_BADGE_ANCESTOR_JS = "el => !!el.closest('.grecaptcha-badge')"

_RADIO_OPTION_LABEL_JS = """el => {
    const wrapped = el.closest('label');
    if (wrapped && wrapped.innerText && wrapped.innerText.trim()) return wrapped.innerText;
    const id = el.id;
    if (id) {
        const forLabel = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (forLabel && forLabel.innerText && forLabel.innerText.trim()) return forLabel.innerText;
    }
    return '';
}"""

_WRAPPER_INFO_JS = """el => {
    const q = (sel) => el.querySelector(sel);
    const titleEl = q('.ashby-application-form-question-title');
    const label = titleEl ? titleEl.innerText : '';
    const fileInput = q('input[type="file"]');
    const comboInput = q('input[role="combobox"]');
    const hasAutocompleteClass = !!q('.ashby-application-form-input-autocomplete');
    const yesnoEl = q('.ashby-application-form-input-yesno');
    const radios = Array.from(el.querySelectorAll('input[type="radio"]'));
    const checkboxes = Array.from(el.querySelectorAll('input[type="checkbox"]'));
    const textInput = q(
        'input[type="text"], input[type="email"], input[type="url"], '
        + 'input[type="number"], input[type="tel"], textarea'
    );
    const hasRequiredClassPrefix = (node) => !!node && Array.from(node.classList || [])
        .some((cls) => cls.indexOf('_required_') === 0);
    const requiredClassMarker = titleEl
        && (hasRequiredClassPrefix(titleEl) || hasRequiredClassPrefix(titleEl.parentElement));
    const requiredMarker = /\\*/.test(label)
        || !!el.querySelector('[aria-required="true"], [required]')
        || requiredClassMarker;
    return {
        dataFieldPath: el.getAttribute('data-field-path') || '',
        label: (label || '').replace(/\\s+/g, ' ').trim(),
        hasFileInput: !!fileInput,
        fileInputId: fileInput ? (fileInput.id || '') : '',
        isAutocomplete: !!comboInput || (hasAutocompleteClass && !yesnoEl),
        hasYesno: !!yesnoEl,
        radioCount: radios.length,
        radioName: radios.length ? (radios[0].name || '') : '',
        checkboxCount: checkboxes.length,
        checkboxId: checkboxes.length === 1 ? (checkboxes[0].id || '') : '',
        checkboxName: checkboxes.length === 1 ? (checkboxes[0].name || '') : '',
        hasTextInput: !!textInput,
        textInputTag: textInput ? textInput.tagName.toLowerCase() : '',
        textInputType: textInput ? (textInput.type || '') : '',
        textInputId: textInput ? (textInput.id || '') : '',
        textInputName: textInput ? (textInput.name || '') : '',
        required: requiredMarker,
    };
}"""


class AshbyFormError(Exception):
    """Raised when an Ashby application page cannot be used."""


def _classify_application_page(has_panel: bool) -> tuple[bool, str]:
    """Pure decision rule, kept separate from the DOM query in
    `_application_page_check` so it can be regression-tested without a
    browser. `has_panel` must reflect the actual application panel: the
    `[role="tabpanel"]` that owns both the submit button and the main
    name/email systemfields as descendants. On a real Ashby page the submit
    button and the name/email fields sit in separate sibling
    `.ashby-application-form-container` regions under that same tabpanel --
    the submit button is never a descendant of the container that holds the
    fields -- so both must be checked against the shared tabpanel ancestor,
    never against a single container element. A survey-only container (e.g.
    the EEOC section, which Ashby also wraps in that same container class)
    must never satisfy this on its own. There is deliberately no fallback to
    a bare name/email systemfield wrapper without that panel: a page could
    carry those wrappers outside the real, submittable application form
    (e.g. a different tab), and discovery must never be able to fall back to
    scanning the whole page for fields to drive in that case.
    """
    if has_panel:
        return True, "application_form_container"
    return False, "no_match"


def _application_page_check(page: Page) -> tuple[bool, str]:
    has_panel = page.locator(_APPLICATION_PANEL_SELECTOR).count() > 0
    return _classify_application_page(has_panel)


def is_ashby_application_page(page: Page) -> bool:
    matched, _ = _application_page_check(page)
    return matched


def _has_visible_active_captcha(page: Page) -> bool:
    """True only for a CAPTCHA that is actually presented to the user right
    now (a visible challenge iframe/modal) -- never the passive reCAPTCHA
    badge that real, usable Ashby forms load even when there is no active
    challenge. That badge is excluded by ancestry (`.grecaptcha-badge`),
    not by visibility: on a real snapshot the badge's own challenge iframe
    can report `is_visible() == True` (e.g. positioned off-screen rather
    than hidden), so visibility alone cannot distinguish it from a real
    challenge -- only every other candidate is still gated on visibility.
    """
    candidates = page.locator(_ACTIVE_CAPTCHA_SELECTOR)
    count = candidates.count()
    for index in range(count):
        candidate = candidates.nth(index)
        try:
            if candidate.evaluate(_BADGE_ANCESTOR_JS):
                continue
            if candidate.is_visible():
                return True
        except PlaywrightError:
            continue
    return False


def detect_security_challenge(page: Page) -> str | None:
    title = page.title().lower()
    body = page.locator("body").inner_text()[:3000].lower()
    if "just a moment" in title or "checking your browser" in body:
        return "cloudflare_challenge"
    if page.locator("#cf-challenge, .cf-browser-verification").count() > 0:
        return "cloudflare_challenge"
    if _has_visible_active_captcha(page):
        return "captcha"
    if is_ashby_application_page(page):
        return None
    if page.locator('input[type="password"]').count() > 0:
        return "login"
    return None


def _is_consent_field(field: DiscoveredField) -> bool:
    # The live consent checkbox's own id/name are Ashby-generated (a random
    # UUID id, name="I agree") and carry no stable semantic meaning -- only
    # its wrapper's `data-field-path`, captured in `context`, reliably
    # identifies it.
    return field.context == _CONSENT_FIELD_NAME


class AshbyAdapter:
    """Ashby-specific form discovery and filling. No submit capability."""

    last_multiselect_selected: list[str] | None = None
    last_cover_letter_error: str | None = None

    def recognize(self, page: Page) -> bool:
        result, reason = _application_page_check(page)
        logger.warning(
            "ashby_prepare stage=form_recognition result=%s reason=%s",
            result,
            reason,
        )
        return result

    def detect_challenge(self, page: Page) -> str | None:
        return detect_security_challenge(page)

    def discover_fields(self, page: Page) -> list[DiscoveredField]:
        if not self.recognize(page):
            raise AshbyFormError("UNSUPPORTED_FORM")
        fields: list[DiscoveredField] = []
        seen_radio_names: set[str] = set()
        seen_element_ids: set[str] = set()
        # Scoped to the recognized application panel -- recognition above
        # already requires that panel to exist, so a `[data-field-path]`
        # element that lives elsewhere on the page (e.g. a different tab)
        # can never be discovered or driven.
        scope = page.locator(_APPLICATION_PANEL_SELECTOR).first
        wrappers = scope.locator(_FIELD_WRAPPER_SELECTOR)
        for index in range(wrappers.count()):
            field = _discover_wrapper(wrappers.nth(index), seen_radio_names)
            if field is None:
                continue
            if field.element_id:
                if field.element_id in seen_element_ids:
                    continue
                seen_element_ids.add(field.element_id)
            fields.append(field)
        return fields

    def fill_field(self, page: Page, classified: ClassifiedField) -> bool:
        field = classified.field
        if field.field_type == "checkbox" and _is_consent_field(field):
            # Recruiting-contact consent must never be auto-checked -- see
            # module docstring. Logged even when nothing upstream tried to
            # fill it, so a run's logs always show this guard fired.
            logger.warning("ashby_final_write field=recruiting_contact_consent action=skip")
            return False
        if not classified.fill or classified.value is None:
            return False
        try:
            if field.field_type == "file":
                locator = _field_locator(page, field)
                locator.set_input_files(str(classified.value), timeout=5_000)
                return True
            if field.field_type == "checkbox":
                locator = _field_locator(page, field)
                checked = _checkbox_should_check(classified.value)
                if checked:
                    locator.check(timeout=3_000)
                else:
                    locator.uncheck(timeout=3_000)
                return _safe_is_checked(locator) == checked
            if field.field_type == "radio":
                return _fill_radio(page, field, classified.value)
            if field.field_type == "yesno":
                return _fill_yesno(_field_locator(page, field), field, classified.value)
            if field.field_type in _TEXT_TYPES or field.field_type == "textarea":
                return _fill_text(_field_locator(page, field), str(classified.value))
        except PlaywrightError:
            return False
        return False

    def upload_resume(self, page: Page, resume_path: Path, field: DiscoveredField) -> bool:
        if field.field_type != "file":
            return False
        locator = _field_locator(page, field)
        try:
            locator.set_input_files(str(resume_path), timeout=5_000)
        except PlaywrightError:
            return False
        return _resume_is_attached(page, field, resume_path.name)

    def read_back(self, page: Page, field: DiscoveredField) -> str | None:
        if field.field_type == "file":
            names = _file_names(page, field)
            return names[0] if names else None
        if field.field_type == "checkbox":
            try:
                return "true" if _field_locator(page, field).is_checked() else "false"
            except PlaywrightError:
                return None
        if field.field_type == "radio":
            checked = _checked_radio_locator(page, field)
            if checked.count() == 0:
                return None
            visible = _radio_option_label(checked.first)
            value = checked.first.get_attribute("value")
            return visible or value
        if field.field_type == "yesno":
            buttons = _field_locator(page, field)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if (button.get_attribute("aria-pressed") or "").lower() == "true":
                    return " ".join((button.inner_text() or "").split())
            return None
        try:
            value = _field_locator(page, field).input_value()
        except PlaywrightError:
            return None
        return value or None


def _discover_wrapper(wrapper: Locator, seen_radio_names: set[str]) -> DiscoveredField | None:
    try:
        info = wrapper.evaluate(_WRAPPER_INFO_JS)
    except PlaywrightError:
        return None
    if not isinstance(info, dict):
        return None

    data_field_path = str(info.get("dataFieldPath") or "")
    label = str(info.get("label") or "")
    required = bool(info.get("required"))

    if info.get("isAutocomplete"):
        # Fail-closed regardless of what the shared classifier would
        # otherwise resolve -- see module docstring.
        return DiscoveredField(
            label=label or "Unknown", field_type="unknown", required=required, context=data_field_path
        )

    if info.get("hasFileInput"):
        file_id = str(info.get("fileInputId") or "")
        if not file_id:
            return None
        return DiscoveredField(
            label=label,
            name=file_id,
            field_type="file",
            required=required,
            element_id=file_id,
            context=data_field_path,
        )

    if info.get("hasYesno"):
        options = _yesno_options(wrapper)
        return DiscoveredField(
            label=label, field_type="yesno", required=required, options=options, context=data_field_path
        )

    radio_count = int(info.get("radioCount") or 0)
    if radio_count > 0:
        radio_name = str(info.get("radioName") or "")
        if radio_name:
            if radio_name in seen_radio_names:
                return None
            seen_radio_names.add(radio_name)
        options = _radio_options_in_wrapper(wrapper)
        return DiscoveredField(
            label=label,
            name=radio_name or None,
            field_type="radio",
            required=required,
            options=options,
            context=data_field_path,
        )

    checkbox_count = int(info.get("checkboxCount") or 0)
    if checkbox_count > 1:
        # An optional multi-option checkbox group (e.g. employee
        # relationship) has no safe, generic per-option semantic this
        # adapter can resolve -- left undiscovered so it is never guessed
        # at or auto-checked. See module docstring.
        return None
    if checkbox_count == 1:
        checkbox_id = str(info.get("checkboxId") or "")
        checkbox_name = str(info.get("checkboxName") or "")
        return DiscoveredField(
            label=label,
            name=checkbox_name or None,
            field_type="checkbox",
            required=required,
            element_id=checkbox_id or None,
            context=data_field_path,
        )

    if info.get("hasTextInput"):
        tag = str(info.get("textInputTag") or "")
        input_type = str(info.get("textInputType") or "")
        element_id = str(info.get("textInputId") or "")
        name = str(info.get("textInputName") or "")
        field_type = "textarea" if tag == "textarea" else (input_type or "text")
        if field_type not in _TEXT_TYPES and field_type != "textarea":
            field_type = "text"
        return DiscoveredField(
            label=label,
            name=name or None,
            field_type=field_type,
            required=required,
            element_id=element_id or None,
            context=data_field_path,
        )

    return None


def _yesno_options(wrapper: Locator) -> list[str]:
    buttons = wrapper.locator(f".{_YESNO_OPTION_CLASS}")
    options: list[str] = []
    for index in range(buttons.count()):
        text = " ".join((buttons.nth(index).inner_text() or "").split())
        if text:
            options.append(text)
    return options


def _radio_option_label(radio: Locator) -> str:
    try:
        text = radio.evaluate(_RADIO_OPTION_LABEL_JS)
    except PlaywrightError:
        return ""
    return " ".join(str(text or "").split())


def _radio_options_in_wrapper(wrapper: Locator) -> list[str]:
    radios = wrapper.locator('input[type="radio"]')
    options: list[str] = []
    for index in range(radios.count()):
        radio = radios.nth(index)
        value = _radio_option_label(radio) or radio.get_attribute("value")
        if value:
            options.append(str(value).strip())
    return options


def _radio_group_locator(page: Page, field: DiscoveredField) -> Locator:
    if field.name:
        return page.locator(f'input[type="radio"][name="{field.name}"]')
    return page.locator(f'[data-field-path="{field.context}"] input[type="radio"]')


def _checked_radio_locator(page: Page, field: DiscoveredField) -> Locator:
    if field.name:
        return page.locator(f'input[type="radio"][name="{field.name}"]:checked')
    return page.locator(f'[data-field-path="{field.context}"] input[type="radio"]:checked')


def _field_locator(page: Page, field: DiscoveredField) -> Locator:
    """Locator for every field type except "radio" -- radio groups are always
    resolved via `_radio_group_locator`/`_checked_radio_locator` instead,
    since a group has no single element this function's id/name/label
    fallbacks could target.
    """
    if field.field_type == "yesno":
        return page.locator(f'[data-field-path="{field.context}"] .{_YESNO_OPTION_CLASS}')
    if field.element_id:
        return page.locator(f'[id="{field.element_id}"]')
    if field.name:
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


def _label_or_value_candidates(label_text: str, option_value: str) -> list[str]:
    """The candidate text(s) a live radio option's `wanted` match is checked
    against: the visible label alone when present, else the underlying input
    `value` as a fallback. Shared by pre-click matching and post-click
    readback so both apply the identical visible-label-first rule -- a
    hidden `value` that happens to match `wanted` must never be consulted,
    let alone rescue a mismatch, once a visible label exists (e.g. a "Yes, I
    have a criminal record" option whose `value="yes"` must never match a
    plain "Yes" wanted).
    """
    if label_text.strip():
        return [label_text]
    if option_value:
        return [option_value]
    return []


def _matching_radio_indices(wanted: str, live_options: list[tuple[str, str]]) -> list[int]:
    """Indices of `live_options` (label, value) pairs that are an exact
    normalized match for `wanted` -- never substring/prefix (a "Yes" wanted
    must never match a "Yes, with restrictions" option, and "Gender" wanted
    must never match an unrelated option that merely contains it). Pure and
    browser-free so the matching rule itself can be regression-tested
    directly, independent of the live DOM.
    """
    matches: list[int] = []
    for index, (label_text, option_value) in enumerate(live_options):
        candidates = _label_or_value_candidates(label_text, option_value)
        if candidates and match_option_exact_normalized(wanted, candidates) is not None:
            matches.append(index)
    return matches


def _radio_live_options(page: Page, field: DiscoveredField) -> list[tuple[str, str]]:
    radios = _radio_group_locator(page, field)
    live: list[tuple[str, str]] = []
    for index in range(radios.count()):
        radio = radios.nth(index)
        option_value = (radio.get_attribute("value") or "").strip()
        label_text = _radio_option_label(radio)
        live.append((label_text, option_value))
    return live


def _fill_radio(page: Page, field: DiscoveredField, value: object) -> bool:
    if isinstance(value, bool):
        wanted = match_yes_no(value, field.options) or ("Yes" if value else "No")
    else:
        wanted = str(value)
    live_options = _radio_live_options(page, field)
    matches = _matching_radio_indices(wanted, live_options)
    if len(matches) != 1:
        # No match, or an ambiguous match against more than one live option --
        # never guess which radio the candidate meant.
        return False
    radios = _radio_group_locator(page, field)
    radio = radios.nth(matches[0])
    try:
        radio.check(timeout=3_000)
    except PlaywrightError:
        return False
    checked = _checked_radio_locator(page, field)
    if checked.count() != 1:
        return False
    checked_label = _radio_option_label(checked.first)
    checked_value = (checked.first.get_attribute("value") or "").strip()
    checked_candidates = _label_or_value_candidates(checked_label, checked_value)
    if not checked_candidates:
        return False
    return match_option_exact_normalized(wanted, checked_candidates) is not None


def _fill_yesno(buttons: Locator, field: DiscoveredField, value: object) -> bool:
    if isinstance(value, bool):
        wanted = match_yes_no(value, field.options) or ("Yes" if value else "No")
    else:
        wanted = str(value)
    for index in range(buttons.count()):
        button = buttons.nth(index)
        text = " ".join((button.inner_text() or "").split())
        if text.strip().lower() == wanted.strip().lower():
            try:
                button.click(timeout=3_000)
            except PlaywrightError:
                return False
            return (button.get_attribute("aria-pressed") or "").lower() == "true"
    return False


def _checkbox_should_check(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on", "acknowledge", "confirm"}


def _safe_is_checked(locator: Locator) -> bool | None:
    try:
        return locator.is_checked()
    except PlaywrightError:
        return None


def _file_names(page: Page, field: DiscoveredField) -> list[str]:
    if not field.element_id:
        return []
    try:
        found = page.evaluate(
            """sel => {
                const el = document.querySelector(sel);
                return el ? Array.from(el.files || []).map(file => file.name) : [];
            }""",
            f'[id="{field.element_id}"]',
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
