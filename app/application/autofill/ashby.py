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
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.ashby_url import is_same_job_apply_url
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
_GENERIC_FORM_CONTAINER_SELECTOR = ".ashby-application-form-container"
_SUBMIT_BUTTON_SELECTOR = "button.ashby-application-form-submit-button"
_NAME_FIELD_SELECTOR = '[data-field-path="_systemfield_name"]'
_EMAIL_FIELD_SELECTOR = '[data-field-path="_systemfield_email"]'
_TAB_SELECTOR = '[role="tab"]'
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


def _safe_host(url: object) -> str:
    """Netloc only -- never the path, query, or fragment, which could carry PII or raw content."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _safe_host_path(url: object) -> str:
    """Hostname + path only -- never userinfo, query, or fragment."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.hostname or ''}{parsed.path}"
    except ValueError:
        return ""


def _final_page_state(page: Page) -> dict[str, object]:
    """Compact, bounded snapshot of page structure taken only when the
    form-readiness wait fails to produce the strict panel -- distinguishes a
    still-loading pre-hydration shell (nothing present) from a rendered page
    whose form is structurally different (some pieces present, panel still
    absent). Deliberately just booleans/counts and a sanitized host+path --
    never the full DOM, a candidate's values, or the URL's query/fragment.
    """

    def _present(selector: str) -> bool:
        try:
            return page.locator(selector).count() > 0
        except PlaywrightError:
            return False

    return {
        "strict_panel": _present(_APPLICATION_PANEL_SELECTOR),
        "generic_form_container": _present(_GENERIC_FORM_CONTAINER_SELECTOR),
        "name_field": _present(_NAME_FIELD_SELECTOR),
        "email_field": _present(_EMAIL_FIELD_SELECTOR),
        "submit_button": _present(_SUBMIT_BUTTON_SELECTOR),
        "tab_or_apply_action": _present(_TAB_SELECTOR) or _same_posting_apply_action(page) is not None,
        "host_path": _safe_host_path(page.url),
    }


def _log_final_page_state(page: Page) -> None:
    state = _final_page_state(page)
    logger.warning(
        "ashby_prepare stage=final_page_state strict_panel=%s generic_form_container=%s "
        "name_field=%s email_field=%s submit_button=%s tab_or_apply_action=%s host_path=%s",
        state["strict_panel"],
        state["generic_form_container"],
        state["name_field"],
        state["email_field"],
        state["submit_button"],
        state["tab_or_apply_action"],
        state["host_path"],
    )


def _same_posting_apply_action(page: Page, anchors: Locator | None = None) -> Locator | None:
    """An anchor whose resolved href is exactly this exact posting's own
    verified `/application` link, or None.

    The resolver (`AshbyTargetVacancyResolver`) always navigates straight to
    that link, so this only ever matters if Ashby's own routing instead
    lands on the job-detail page for this same posting (an "Apply" action,
    not yet the rendered form). Matching is done on the anchor's
    browser-resolved absolute URL (`el.href`) against `page.url` via
    `ashby_url.is_same_job_apply_url` -- never on text/class/styling -- so
    this can never click an unrelated button, a different job's apply link,
    or an external URL that merely looks like one.
    """
    if anchors is None:
        anchors = page.locator("a[href]")
    count = anchors.count()
    for index in range(count):
        anchor = anchors.nth(index)
        try:
            href = anchor.evaluate("el => el.href")
        except PlaywrightError:
            continue
        if isinstance(href, str) and is_same_job_apply_url(page.url, href):
            return anchor
    return None


def is_ashby_job_detail_page(page: Page) -> bool:
    """True for an Ashby hosted job-detail page (not yet the application
    form) that carries an Apply link to this exact posting's own
    `/application` page. False once the strict application panel is
    already recognized, or when no such same-posting apply anchor is
    structurally present -- including the brief pre-hydration loading shell,
    which has no anchors at all yet and so is never mistaken for the
    job-detail page. `_same_posting_apply_action` only ever matches when
    `page.url` is itself a canonical Ashby job-detail URL (that is what
    `ashby_url.is_same_job_apply_url` requires of it), so no separate host
    check is needed here.
    """
    if is_ashby_application_page(page):
        return False
    return _same_posting_apply_action(page) is not None


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

    def prepare_page(self, page: Page) -> None:
        """Wait until the application form is usable.

        The resolver always navigates straight to this exact posting's
        verified `/application` URL, so the ordinary case is only the brief
        pre-hydration loading shell -- a bounded wait for the strict
        application panel selector to appear once React finishes rendering
        the form. There is no bare-host/bare-field fallback: recognition
        stays exactly `_APPLICATION_PANEL_SELECTOR`.

        If Ashby instead lands on the job-detail page for this same posting
        (an Apply action but no rendered form yet), take the one click that
        is structurally guaranteed to be this exact posting's own
        `/application` link (`is_ashby_job_detail_page` /
        `_same_posting_apply_action`) -- `_navigate_from_job_detail` itself
        owns the one bounded form-readiness wait for that path, so this
        method never waits a second time on top of it (that would let a
        single `prepare_page` call block for the sum of both timeouts
        instead of just one). A page that is already the recognized form is
        never redundantly clicked. If neither path produces the strict form,
        a compact diagnostic snapshot of the resulting page is logged and
        `recognize()` will then be False, so the caller reports
        UNSUPPORTED_FORM rather than guessing at an unfamiliar page. Local
        fixtures skip the extra settle.
        """
        is_job_detail = is_ashby_job_detail_page(page)
        logger.warning(
            "ashby_prepare stage=detail_detection is_job_detail=%s page_host=%s",
            is_job_detail,
            _safe_host(page.url),
        )
        if is_job_detail:
            self._navigate_from_job_detail(page)
        else:
            timeout = 2_000 if page.url.startswith("file:") else 30_000
            try:
                page.wait_for_selector(_APPLICATION_PANEL_SELECTOR, timeout=timeout)
            except PlaywrightTimeoutError:
                _log_final_page_state(page)
                return
        if not is_ashby_application_page(page):
            _log_final_page_state(page)
            return
        if page.url.startswith("file:"):
            return
        page.wait_for_timeout(500)

    def _navigate_from_job_detail(self, page: Page) -> None:
        """Attempt the job-detail -> application-form transition and always
        log a navigation-stage record at the end, even when no same-posting
        apply action exists or the click raises, so a single run identifies
        every failure stage. Never dismisses cookie overlays or other page
        chrome -- if one blocks the click, the click raises, the stage is
        logged, and the page fails closed via the caller's normal
        `recognize()` check.
        """
        pre_url = page.url
        action = _same_posting_apply_action(page)
        apply_locator_count = 1 if action is not None else 0
        logger.warning(
            "ashby_prepare stage=apply_action apply_locator_count=%s",
            apply_locator_count,
        )
        click_attempted = False
        click_raised: str | None = None
        form_ready_selector_found = False
        if action is not None:
            click_attempted = True
            try:
                action.click(timeout=5_000)
            except PlaywrightError as exc:
                click_raised = type(exc).__name__
            logger.warning(
                "ashby_prepare stage=apply_action click_attempted=%s click_raised=%s",
                click_attempted,
                click_raised,
            )
            if click_raised is None:
                timeout = 2_000 if page.url.startswith("file:") else 15_000
                try:
                    page.wait_for_selector(_APPLICATION_PANEL_SELECTOR, timeout=timeout)
                    form_ready_selector_found = True
                except PlaywrightTimeoutError:
                    form_ready_selector_found = False
        post_url = page.url
        logger.warning(
            "ashby_prepare stage=navigation pre=%s post=%s url_changed=%s "
            "click_attempted=%s click_raised=%s form_ready_selector_found=%s",
            _safe_host_path(pre_url),
            _safe_host_path(post_url),
            pre_url != post_url,
            click_attempted,
            click_raised,
            form_ready_selector_found,
        )

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
