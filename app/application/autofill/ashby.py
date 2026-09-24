"""Ashby-specific form discovery and filling. No submit capability.

Bounded to the control shapes evidenced from a real Ashby `/application`
page: plain `[data-field-path]`-wrapped text/email/url/number/tel inputs
(Name, Email, Phone, LinkedIn Profile, ...), the main resume file input,
native radio groups, and Ashby's custom Yes/No button control
(`.ashby-application-form-input-yesno-option[data-option]`). A handful of
narrowly-scoped extensions are layered on top, each bounded to an exact
observed question shape and never guessing beyond it:

- A "Current Location"-worded autocomplete combobox
  (`input[role="combobox"]`) is filled only via a safe generic ARIA
  associated-listbox exact-option path (`_fill_combobox_location`): type the
  explicit `identity.current_location` value, resolve the live listbox via
  `aria-controls`/`aria-owns` (or the sole unambiguous visible listbox, via
  the shared `react_controls` helpers Greenhouse's own combobox path already
  relies on), click the one exact-normalized matching option, and require the
  input to persist that exact text afterward. Any other autocomplete
  (e.g. "Where did you hear...") stays an unsupported control (never a plain
  text field, never typed into blindly, never a bare Enter) -- a snapshot
  only shows the closed combo, never a resolvable listbox association, so
  there is nothing safe to match a typed value against.
- A "...hear about..."-worded multi-option checkbox group is discovered as a
  single `checkbox_group` field (never as individual per-option checkboxes)
  and may only ever check an exact "Company Website"/"Careers Website" option
  (see `_adjust_application_source_checkbox_group`) -- the only claims a
  verified direct public Ashby company board discovery
  (`_is_verified_ashby_direct_source`: both a `target_company:ashby:` vacancy
  source and the canonical Ashby board URL shape, never the URL shape alone)
  can actually prove truthful -- gated on that verified discovery and never a
  hardcoded company name; the shared classifier's own generic preference
  matching resolving some *other* present option (e.g. "LinkedIn") is
  reverted the same way an unverified discovery is.
  Every other multi-option checkbox group (e.g. the optional
  employee-relationship group) is never discovered at all: per-option
  semantics cannot be resolved generically, and guessing which box to check
  would risk checking the wrong one.
- The recruiting-contact consent checkbox (`_systemfield_data_consent_ack`)
  is discovered (so it still surfaces for manual review) but `fill_field`
  refuses to write to it under any circumstances -- opting a candidate into
  marketing contact must never be automatic. A distinct, exact-worded
  "WhatsApp"-style optional communications-consent Yes/No control is instead
  always answered explicit No (never Yes/opt-in) via `adjust_classified_field`.
  That control is also recognized when Ashby renders it as a nested
  `.ashby-application-form-texting-consent-description` component sharing the
  Phone Number field's own `[data-field-path]` wrapper (see
  `_discover_nested_whatsapp_consent`): discovery always yields the phone
  `tel` input as its own text field regardless, and additionally yields the
  consent question as a separate field only when the nested component's
  radio group name is the platform's own exact `whatsAppConsent` and the
  live options include the platform's exact safe negative wording -- never
  guessing at an unrelated same-block SMS-consent radio group under a
  different name.

`AshbyAdapter.adjust_classified_field` is an optional, Ashby-only hook
`AutofillService` calls (when present) right after the shared
`classifier.classify_field` pass, using the explicit profile and resolved
vacancy to narrowly adjust a handful of Ashby-specific question shapes
(named fully-on-site office feasibility, salary currency/period
applicability, the conjunctive skill-experience Yes/No, notice period, the
discovered "Current Location"-worded combobox above whenever its exact live
wording (e.g. "Where are you currently based?") falls outside the shared
classifier's own `_is_identity_location` coverage, the WhatsApp consent
decline, the application-source checkbox group above, and a
country-relative "legal authorisation to work" Yes/No resolved only from the
single vacancy work country plus an explicit
`work_eligibility.work_authorizations` fact -- never from citizenship,
location, or relocation willingness) without touching the shared classifier
or any other provider.

`.ashby-application-form-submit-button` is recognition evidence only and is
never queried for interaction anywhere in this module -- there is no submit
capability here, matching `GreenhouseAdapter`/`LeverAdapter`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill import react_controls
from app.application.autofill.ashby_url import is_canonical_ashby_hosted_url, is_same_job_apply_url
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.options import (
    match_named_skill_set,
    match_option_exact_normalized,
    match_yes_no,
    split_technology_scope_terms,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import TARGET_COMPANY_ASHBY_PREFIX, ResolvedVacancy
from app.application.candidate_profile import CandidateProfile, countries_mentioned

logger = logging.getLogger(__name__)

_TEXT_TYPES = frozenset({"text", "email", "url", "number", "tel", "search"})

_APPLICATION_PANEL_SELECTOR = (
    '[role="tabpanel"]:has(button.ashby-application-form-submit-button)'
    ':has([data-field-path="_systemfield_name"], [data-field-path="_systemfield_email"])'
)
_FIELD_WRAPPER_SELECTOR = "[data-field-path]"
_YESNO_OPTION_CLASS = "ashby-application-form-input-yesno-option"
_TEXTING_CONSENT_DESCRIPTION_CLASS = "ashby-application-form-texting-consent-description"
_WHATSAPP_CONSENT_RADIO_NAME = "whatsAppConsent"
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

_LOCATION_AUTOCOMPLETE_LABEL_CUES = (
    "current location",
    "currently based",
    "where are you based",
    "where are you currently based",
)
_APPLICATION_SOURCE_GROUP_LABEL_CUES = (
    "hear about",
    "how did you find",
    "how did you learn about",
)
_WHATSAPP_CONSENT_CUES = (
    "contact you",
    "message you",
    "text you",
    "reach you",
    "reach out to you",
    "may we",
    "can we",
)
_TECH_EXPERIENCE_QUESTION_RE = re.compile(
    r"experience\s+(?:with|in|using)\s+(.+?)\s*\??\s*$",
    re.IGNORECASE,
)
_WHATSAPP_CONSENT_SAFE_NEGATIVE_RE = re.compile(r"^no\b.*\bdo not consent\b", re.IGNORECASE)
_SALARY_LOCAL_CURRENCY_CUE = "local currency"
_SALARY_ANNUAL_LABEL_TERMS = ("annual", "per year", "yearly", "/year")
_SALARY_ANNUAL_PERIOD_VALUES = frozenset(
    {"annual", "annually", "year", "yearly", "per year", "per annum"}
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
    const comboInputId = comboInput ? (comboInput.id || '') : '';
    const yesnoEl = q('.ashby-application-form-input-yesno');
    const radios = Array.from(el.querySelectorAll('input[type="radio"]'));
    const checkboxes = Array.from(el.querySelectorAll('input[type="checkbox"]'));
    const textInput = q(
        'input[type="text"], input[type="email"], input[type="url"], '
        + 'input[type="number"], input[type="tel"], textarea'
    );
    const consentEl = q('.ashby-application-form-texting-consent-description');
    const consentRadioNames = consentEl
        ? Array.from(new Set(
            Array.from(consentEl.querySelectorAll('input[type="radio"]'))
                .map((radio) => radio.name || '')
                .filter(Boolean)
        ))
        : [];
    const collectNonInputText = (node) => {
        let text = '';
        node.childNodes.forEach((child) => {
            if (child.nodeType === 3) {
                text += child.textContent;
            } else if (child.nodeType === 1 && child.tagName !== 'INPUT' && child.tagName !== 'LABEL') {
                text += ' ' + collectNonInputText(child);
            }
        });
        return text;
    };
    const consentText = consentEl ? collectNonInputText(consentEl).replace(/\\s+/g, ' ').trim() : '';
    const consentRequired = consentEl
        ? (/\\*/.test(consentEl.innerText || '') || !!consentEl.querySelector('[aria-required="true"], [required]'))
        : false;
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
        comboInputId: comboInputId,
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
        hasConsentBlock: !!consentEl,
        consentRadioNames: consentRadioNames,
        consentText: consentText,
        consentRequired: consentRequired,
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
            # A wrapper can yield more than one field -- e.g. the Phone
            # Number wrapper's own nested WhatsApp-consent component (see
            # `_discover_nested_whatsapp_consent`) -- so every field this
            # wrapper produced is deduped and logged the same way.
            for field in _discover_wrapper(wrappers.nth(index), seen_radio_names):
                if field.element_id:
                    if field.element_id in seen_element_ids:
                        continue
                    seen_element_ids.add(field.element_id)
                fields.append(field)
                # Compact, privacy-safe pipeline diagnostic -- `context` is
                # Ashby's own generated `data-field-path` identifier (never a
                # candidate value or the question label), so this can freely
                # log at this volume. Paired with the "resolved"/"interaction"/
                # "readback" stage logs below, this lets a live run's logs alone
                # localize which stage a given field (e.g. the phone field)
                # stalled at, without ever exposing what was typed.
                logger.warning(
                    "ashby_field stage=discovered context=%s field_type=%s required=%s",
                    field.context,
                    field.field_type,
                    field.required,
                )
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
        result = self._attempt_fill(page, classified)
        logger.warning(
            "ashby_field stage=interaction context=%s field_type=%s result=%s",
            field.context,
            field.field_type,
            result,
        )
        return result

    def _attempt_fill(self, page: Page, classified: ClassifiedField) -> bool:
        field = classified.field
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
            if field.field_type == "checkbox_group":
                return _fill_checkbox_group(page, field, classified.value)
            if field.field_type == "combobox_location":
                return _fill_combobox_location(page, field, classified.value)
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
        value = self._read_back_value(page, field)
        logger.warning(
            "ashby_field stage=readback context=%s field_type=%s empty=%s",
            field.context,
            field.field_type,
            not value,
        )
        return value

    def _read_back_value(self, page: Page, field: DiscoveredField) -> str | None:
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
        if field.field_type == "checkbox_group":
            checkboxes = _checkbox_group_locator(page, field)
            labels = _checkbox_group_option_labels(checkboxes)
            checked_labels = [
                labels[index]
                for index in range(checkboxes.count())
                if _safe_is_checked(checkboxes.nth(index))
            ]
            if len(checked_labels) != 1:
                # Never guess between an unchecked group and an ambiguous
                # multi-checked state -- see `_fill_checkbox_group`.
                return None
            return checked_labels[0] or None
        try:
            value = _field_locator(page, field).input_value()
        except PlaywrightError:
            return None
        return value or None

    def adjust_classified_field(
        self,
        item: ClassifiedField,
        *,
        profile: CandidateProfile,
        vacancy: ResolvedVacancy,
    ) -> ClassifiedField:
        """Ashby-only narrow post-classification adjustments.

        `AutofillService` calls this (when present -- other adapters do not
        define it) right after the shared `classifier.classify_field` pass,
        before `_enrich_unresolved`. Every adjustment here is a no-op unless
        its own narrow, bounded condition is met; each targets a distinct
        question kind/shape so the order between them does not matter. None
        of this changes `classify_field`, `map_question`, or any other
        provider -- see the module docstring.
        """
        item = _adjust_office_work_feasibility(item, profile)
        item = _adjust_salary_applicability(item, profile)
        item = _adjust_application_source_checkbox_group(item, vacancy)
        item = _adjust_optional_messaging_consent(item)
        item = _adjust_conjunctive_skill_experience(item, profile)
        item = _adjust_notice_period(item, profile)
        item = _adjust_current_location_combobox(item, profile)
        item = _adjust_country_relative_work_authorization(item, profile, vacancy)
        logger.warning(
            "ashby_field stage=resolved context=%s field_type=%s kind=%s fill=%s",
            item.field.context,
            item.field.field_type,
            item.kind,
            item.fill,
        )
        return item


def _revert_to_manual(item: ClassifiedField, *, reason: str) -> ClassifiedField:
    """Force `item` back to unresolved/manual, preserving the required-vs-
    optional split `classify_field` itself uses. Never used to invent a
    negative ("No") answer -- only to withdraw a fill that this module has
    determined it cannot truthfully stand behind.
    """
    unresolved = (
        FieldClassification.UNKNOWN_REQUIRED if item.field.required else FieldClassification.UNKNOWN_OPTIONAL
    )
    return replace(
        item,
        classification=unresolved,
        value=None,
        fill=False,
        generated=False,
        unresolved_reason=reason,
    )


_OFFICE_ATTENDANCE_FEASIBILITY_LABEL_CUES = (
    "days per week in the office",
    "days a week in the office",
    "days per week at the office",
    "days a week at the office",
    "days per week from the office",
    "full time in the office",
    "full-time in the office",
    "fully on-site",
    "fully onsite",
    "100% on-site",
    "100% onsite",
)


def _looks_like_named_office_feasibility_question(label: str) -> bool:
    """The one narrow, exact-worded "requires working <N> days ... in the
    office"/"fully on-site" attendance-feasibility shape this adjuster is
    scoped to (the shape evidenced on a real Ashby form) -- never every
    question the shared classifier's much broader `QuestionKind.OFFICE_WORK`
    bucket can match (e.g. a bare "are you willing to work onsite?" or a
    generic hybrid-schedule question), which this module has never verified
    the shape of and so leaves untouched.
    """
    lowered = " ".join(label.lower().split())
    return any(cue in lowered for cue in _OFFICE_ATTENDANCE_FEASIBILITY_LABEL_CUES)


def _adjust_office_work_feasibility(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """Never auto-fills a named, site-specific fully-onsite/office-attendance
    feasibility question (see `_looks_like_named_office_feasibility_question`)
    from the shared `office_work` policy's generic default (its class default
    is `willing=True`, indistinguishable here from a candidate who genuinely
    configured it) -- only an explicit, unambiguous decline
    (`office_work_answer() is False`, which can never be the class default)
    or an explicit `application_policy.question_overrides` answer for this
    exact question is trusted. Never derives this from relocation
    willingness or any other generic policy. Every other `OFFICE_WORK`-kind
    question (a shape this module has never verified) is left exactly as the
    shared classifier resolved it.
    """
    if item.kind is not QuestionKind.OFFICE_WORK:
        return item
    if not _looks_like_named_office_feasibility_question(item.field.label):
        return item
    if profile.office_work_answer() is False:
        return item
    override = profile.question_override_answer(item.field.label)
    if isinstance(override, bool):
        return replace(
            item,
            classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            value=override,
            fill=True,
            unresolved_reason=None,
        )
    if isinstance(override, str):
        matched = (
            match_option_exact_normalized(override, item.field.options) if item.field.options else override
        )
        if matched:
            return replace(
                item,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                value=matched,
                fill=True,
                unresolved_reason=None,
            )
    return _revert_to_manual(
        item,
        reason=(
            "named office/on-site attendance feasibility requires an explicit "
            "application_policy.question_overrides answer for this exact question; "
            "the generic office_work willingness default is never used"
        ),
    )


def _adjust_salary_applicability(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """Fails closed on a salary question the shared classifier resolved
    without a provable currency/period match to this exact question's own
    wording. A "local currency" question can never be verified without
    inferring which currency counts as local for the role, so it always
    stays manual. Any other salary question requires an explicit configured
    currency, and -- when the question itself says "annual" -- an explicit
    configured annual period too. Never derives a value from years of
    experience, current salary, or any other unrelated fact.
    """
    if item.kind is not QuestionKind.SALARY:
        return item
    label = " ".join(item.field.label.lower().split())
    if _SALARY_LOCAL_CURRENCY_CUE in label:
        return _revert_to_manual(
            item,
            reason=(
                "salary question requires the applicant's local currency, which cannot be "
                "verified from an explicit profile fact without inferring which currency "
                "counts as local for this role"
            ),
        )
    if not item.fill:
        return item
    expectations = profile.employment.salary_expectations
    currency = (expectations.currency or "").strip() if expectations else ""
    if not currency:
        return _revert_to_manual(
            item, reason="salary question requires an explicit configured currency"
        )
    if any(term in label for term in _SALARY_ANNUAL_LABEL_TERMS):
        period = (expectations.period or "").strip().lower() if expectations else ""
        if period not in _SALARY_ANNUAL_PERIOD_VALUES:
            return _revert_to_manual(
                item,
                reason="annual salary question requires an explicit configured annual period",
            )
    return item


_SOURCE_CHECKBOX_TRUTHFUL_LABELS = ("Company Website", "Careers Website")


def _is_verified_ashby_direct_source(vacancy: ResolvedVacancy) -> bool:
    """True only when the vacancy was both discovered directly through this
    module's own Target Company Ashby pipeline (`vacancy.source` starts with
    `target_company:ashby:`) and carries the canonical public Ashby board URL
    shape. Neither signal alone proves the candidate could truthfully check
    "Company Website"/"Careers Website": a canonical-shaped URL can still
    reach here through an unrelated, non-target-company source (e.g. a
    generic job aggregator that also links to Ashby-hosted boards), and the
    URL shape itself is never inferred as sufficient on its own.
    """
    if not vacancy.source.startswith(TARGET_COMPANY_ASHBY_PREFIX):
        return False
    return is_canonical_ashby_hosted_url(vacancy.application_url)


def _adjust_application_source_checkbox_group(
    item: ClassifiedField, vacancy: ResolvedVacancy
) -> ClassifiedField:
    """The application-source checkbox group (see module docstring) may only
    check an exact "Company Website" or "Careers Website" option -- the only
    two claims a verified direct public Ashby company board discovery can
    actually prove truthful (see `_is_verified_ashby_direct_source`). The
    shared classifier's generic source-preference matching
    (`options.match_application_source`) can otherwise resolve a *different*
    option present in the group (e.g. "LinkedIn"), which a verified board
    discovery does not make any more truthful than a guess; that resolution
    is reverted here just like an unverified discovery is. Never trusted
    merely because the field was discovered, and never a hardcoded company
    name.
    """
    if item.kind is not QuestionKind.APPLICATION_SOURCE or item.field.field_type != "checkbox_group":
        return item
    if not item.fill:
        return item
    if not _is_verified_ashby_direct_source(vacancy):
        return _revert_to_manual(
            item,
            reason=(
                "application-source checkbox group requires a verified direct public Ashby "
                "company board discovery -- both a target_company:ashby source and the "
                "canonical Ashby board application URL -- never the URL shape alone"
            ),
        )
    truthful = (
        isinstance(item.value, str)
        and match_option_exact_normalized(item.value, list(_SOURCE_CHECKBOX_TRUTHFUL_LABELS)) is not None
    )
    if not truthful:
        return _revert_to_manual(
            item,
            reason=(
                "application-source checkbox group only ever truthfully checks an exact "
                "'Company Website' or 'Careers Website' option; a verified direct board URL "
                "does not make any other resolved option truthful"
            ),
        )
    return item


_YES_PREFIX_RE = re.compile(r"^yes\b", re.IGNORECASE)
_NO_PREFIX_RE = re.compile(r"^no\b", re.IGNORECASE)


def _is_yes_no_control(field: DiscoveredField) -> bool:
    """True for Ashby's custom Yes/No button widget (`field_type ==
    "yesno"`) and for a plain native radio group Ashby renders for the exact
    same semantic Yes/No question shape instead (`field_type == "radio"`
    with exactly two live options, one Yes-prefixed and one No-prefixed,
    e.g. "Yes - I consent to receiving WhatsApp messages" /
    "No - I do not consent..."). A live posting was observed rendering both
    the WhatsApp-consent and Java/Spring-Boot-experience questions this way
    -- via `.ashby-application-form-input-radio-group` -- rather than the
    yesno widget, so `_looks_like_optional_whatsapp_consent` and
    `_adjust_conjunctive_skill_experience` must recognize either shape.
    Never any other radio group (three-plus options, or two options that are
    not a Yes/No pair) -- this never broadens radio handling beyond the
    exact semantic shape both callers already require.
    """
    if field.field_type == "yesno":
        return True
    if field.field_type != "radio":
        return False
    options = [option.strip() for option in field.options if option and option.strip()]
    if len(options) != 2:
        return False
    return any(_YES_PREFIX_RE.match(option) for option in options) and any(
        _NO_PREFIX_RE.match(option) for option in options
    )


def _looks_like_optional_whatsapp_consent(field: DiscoveredField) -> bool:
    if not _is_yes_no_control(field) or field.required:
        # A required control is never eligible: declining a mandatory
        # question is not the same safe no-op as declining a clearly
        # optional one, so this stays manual instead.
        return False
    if field.name == _WHATSAPP_CONSENT_RADIO_NAME:
        # The exact `whatsAppConsent` radio name alone is not proof this
        # field went through `_discover_nested_whatsapp_consent`'s safe
        # negative-option check -- a same-named group could in principle
        # reach here via the generic radio-group discovery branch instead,
        # which never checks live option wording. So this re-verifies the
        # platform's own exact "No - I do not consent..." wording is present
        # among this field's *own* discovered options before trusting the
        # name alone; only then can `label` skip the generic cue-phrase
        # check below (the nested component's free-form description text --
        # or a fallback placeholder -- need not itself mention WhatsApp).
        return any(_WHATSAPP_CONSENT_SAFE_NEGATIVE_RE.match(option.strip()) for option in field.options)
    label = " ".join(field.label.lower().split())
    if "whatsapp" not in label:
        return False
    return any(cue in label for cue in _WHATSAPP_CONSENT_CUES)


def _adjust_optional_messaging_consent(item: ClassifiedField) -> ClassifiedField:
    """A distinct, exact-worded "can we contact/message/text you on
    WhatsApp..." optional consent Yes/No control is always answered explicit
    No -- opting a candidate into optional messaging contact must never be
    automatic, regardless of any profile consent fact. This overrides the
    shared classifier's own resolution whenever it already ran (e.g. a
    WhatsApp-worded question that also happens to match the shared
    `SMS_UPDATES` cues and so gets resolved to Yes from an explicit
    `sms_interview_updates` opt-in fact): that fact answers a different,
    generic SMS/interview-updates question, never this exact optional
    WhatsApp control, so a prior Yes must still be forced back to No, never
    left standing. Never touches the required application-privacy
    acknowledgement or the separate recruiting-contact checkbox this module
    already refuses to write to, and never touches a required or otherwise
    ambiguous question -- see `_looks_like_optional_whatsapp_consent`.
    """
    if not _looks_like_optional_whatsapp_consent(item.field):
        return item
    return replace(
        item,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=False,
        fill=True,
        kind=QuestionKind.SMS_UPDATES,
        unresolved_reason=None,
    )


def _parse_conjunctive_tech_question(label: str) -> list[str] | None:
    """Named technologies from a conjunctive "...experience with X and Y..."
    question, or None when the question does not name at least two terms.
    Never invents a technology name; only reads what the question itself
    names, the same way `options.parse_years_experience_technology` does for
    years-of-experience questions.
    """
    match = _TECH_EXPERIENCE_QUESTION_RE.search(label)
    if not match:
        return None
    terms = split_technology_scope_terms(match.group(1))
    return terms if len(terms) >= 2 else None


def _adjust_conjunctive_skill_experience(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """A Yes/No "...hands-on experience with X and Y..." question is answered
    Yes only when every named technology exactly matches one of the
    candidate's own explicitly configured technology names. When even one
    named technology has no configured match, the question stays manual --
    never answered No, since the absence of a configured entry is not
    truthful evidence the candidate lacks that experience.
    """
    if item.fill or item.kind is not QuestionKind.UNKNOWN or not _is_yes_no_control(item.field):
        return item
    named = _parse_conjunctive_tech_question(item.field.label)
    if not named:
        return item
    matches = match_named_skill_set(named, profile.known_technology_names())
    if len(matches) != len(named):
        return item
    return replace(
        item,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.SKILL_SET_CHOICE,
        unresolved_reason=None,
    )


def _adjust_notice_period(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """A plain text/textarea "...notice period..." question is filled only
    from the explicit `employment.notice_period` fact. There is no generic
    shared mapping for this key (see module docstring); this is Ashby-only.
    """
    if item.fill or item.kind is not QuestionKind.UNKNOWN:
        return item
    if item.field.field_type not in _TEXT_TYPES and item.field.field_type != "textarea":
        return item
    label = " ".join(item.field.label.lower().split())
    if "notice period" not in label:
        return item
    value = profile.employment.notice_period
    if not value:
        return item
    return replace(
        item,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        unresolved_reason=None,
    )


def _adjust_current_location_combobox(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """The Ashby-discovered `combobox_location` field type already recognizes
    live wordings the shared classifier's own `_is_identity_location` was
    never taught -- e.g. the observed "Where are you currently based?",
    which contains neither "current location" nor a bare "location"/"city"
    substring, so `classify_field` leaves it `UNKNOWN` even though discovery
    itself already resolved the control shape (see
    `_looks_like_current_location_label`). This is narrower Ashby-only
    coverage for the one control shape this adapter itself discovers -- it
    never broadens the shared `_is_identity_location` semantics Greenhouse
    and Lever still rely on. Fills only the explicit
    `profile.identity.current_location` fact, and only when it is
    non-empty; an absent fact stays manual, same as the shared classifier's
    own LOCATION path would leave it.
    """
    if item.fill or item.kind is not QuestionKind.UNKNOWN or item.field.field_type != "combobox_location":
        return item
    value = profile.identity.current_location
    if not value:
        return item
    return replace(
        item,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.LOCATION,
        unresolved_reason=None,
    )


def _looks_like_country_relative_work_auth_yesno(field: DiscoveredField) -> bool:
    """The observed Ashby wording "Do you have legal authorisation to work in
    the country where this role is based?" -- distinct from (and narrower
    than) the shared classifier's own work-authorization patterns, which this
    exact phrasing does not match (see
    `test_legal_authorization_yesno_stays_unresolved_by_default`). Never a
    sponsorship question, which asks something else entirely.
    """
    if field.field_type != "yesno":
        return False
    label = " ".join(field.label.lower().split())
    if "sponsor" in label:
        return False
    if not ("authoris" in label or "authoriz" in label):
        return False
    return "work in" in label and "country" in label


def _single_vacancy_work_country(vacancy: ResolvedVacancy) -> str | None:
    """The vacancy's work country, only when exactly one is deterministically
    named in its structured location. Ambiguous or missing locations fail
    closed (None), never guessing a country. Duplicates
    `service._single_vacancy_work_country`'s exact rule rather than importing
    it -- `service` itself imports this module, so importing back would be
    circular.
    """
    normalized = vacancy.vacancy
    location = normalized.location if normalized else None
    if not location:
        return None
    countries = countries_mentioned(location)
    if len(countries) != 1:
        return None
    return countries[0]


def _adjust_country_relative_work_authorization(
    item: ClassifiedField, profile: CandidateProfile, vacancy: ResolvedVacancy
) -> ClassifiedField:
    """Resolves the observed country-relative "legal authorisation to work"
    Yes/No only when the vacancy names exactly one deterministic work country
    and the candidate has an explicit `work_eligibility.work_authorizations`
    fact for that exact country. Never infers authorization from citizenship,
    current location, or relocation willingness.

    Always recomputes from scratch for this exact recognized wording,
    regardless of whatever `item.fill`/`item.kind` the shared
    `questions.map_question` already produced -- that shared classifier has
    its own sole-explicit-fact fallback (see
    `_is_generic_country_relative_work_auth_phrase`) that can answer a
    generic country-relative control straight from the candidate's only
    `work_authorizations` fact even when the vacancy's own work country is
    absent, ambiguous, or a different country than that fact names. That
    fallback exists for ATSes that never surface a resolvable vacancy
    country to the classifier at all, but Ashby's discovered field always
    carries a `ResolvedVacancy`, so this exact control is instead always
    re-derived here from the vacancy country and a matching explicit fact,
    never trusting a prior resolution keyed on an unrelated country. When
    the vacancy country or a matching explicit fact is unavailable, the
    field is forced back to manual -- `_enrich_work_authorization` in
    `service` applies this identical vacancy-country-plus-matching-fact rule
    on top of a manual item, so it can never resurrect the sole-unrelated-
    country answer this function just withdrew.
    """
    if not _looks_like_country_relative_work_auth_yesno(item.field):
        return item
    country = _single_vacancy_work_country(vacancy)
    if country is not None:
        answer = profile.work_authorization_for(country)
        if answer is not None:
            return replace(
                item,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                value=answer,
                fill=True,
                kind=QuestionKind.WORK_AUTHORIZATION,
                country=country,
                unresolved_reason=None,
            )
    return _revert_to_manual(
        item,
        reason=(
            "country-relative work authorization requires exactly one deterministic "
            "vacancy work country and an explicit work_eligibility.work_authorizations "
            "fact for that exact country -- never a fact for an unrelated country"
        ),
    )


def _text_field_from_info(info: dict, label: str, required: bool, data_field_path: str) -> DiscoveredField:
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


def _discover_nested_whatsapp_consent(
    wrapper: Locator,
    data_field_path: str,
    consent_radio_names: list[str],
    consent_text: str,
    consent_required: bool,
) -> DiscoveredField | None:
    """A live posting was observed rendering the Phone Number field's own
    `[data-field-path]` wrapper with a second, nested
    `.ashby-application-form-texting-consent-description` component holding
    a distinct optional WhatsApp-consent radio group -- real evidence from
    Ashby's own public frontend bundle names that radio group's `name`
    attribute exactly `whatsAppConsent`, alongside (on some postings) an
    unrelated same-block SMS-consent radio group under a different name.
    Scoped strictly to `name="whatsAppConsent"` within the consent
    component -- never the SMS group, and never the phone `tel` input in
    the same wrapper -- so neither is ever read, selected, or exposed as if
    it were this control. Also requires the platform's own exact,
    safe "No - I do not consent..." negative wording to actually be present
    among the live options: component identity (the class plus the exact
    radio name) alone is not enough to trust an unverified live options
    list, so an unrecognized options list fails closed (returns None) here,
    leaving the wrapper's phone text field as the only field discovered
    from it.
    """
    if _WHATSAPP_CONSENT_RADIO_NAME not in consent_radio_names:
        return None
    scoped_radios = wrapper.locator(
        f'.{_TEXTING_CONSENT_DESCRIPTION_CLASS} input[type="radio"][name="{_WHATSAPP_CONSENT_RADIO_NAME}"]'
    )
    options: list[str] = []
    for index in range(scoped_radios.count()):
        radio = scoped_radios.nth(index)
        value = _radio_option_label(radio) or radio.get_attribute("value")
        if value:
            options.append(str(value).strip())
    if not any(_WHATSAPP_CONSENT_SAFE_NEGATIVE_RE.match(option) for option in options):
        return None
    return DiscoveredField(
        label=consent_text or "WhatsApp messaging consent",
        name=_WHATSAPP_CONSENT_RADIO_NAME,
        field_type="radio",
        required=consent_required,
        options=options,
        context=data_field_path,
    )


def _discover_wrapper(wrapper: Locator, seen_radio_names: set[str]) -> list[DiscoveredField]:
    try:
        info = wrapper.evaluate(_WRAPPER_INFO_JS)
    except PlaywrightError:
        return []
    if not isinstance(info, dict):
        return []

    data_field_path = str(info.get("dataFieldPath") or "")
    label = str(info.get("label") or "")
    required = bool(info.get("required"))

    if info.get("isAutocomplete"):
        if _looks_like_current_location_label(label) and data_field_path:
            # The live combobox input has no stable id on every observed
            # Ashby form -- its wrapper's `data-field-path` is the only
            # reliable handle, so discovery (and `_field_locator` below)
            # never depend on an id being present.
            combo_input_id = str(info.get("comboInputId") or "")
            return [
                DiscoveredField(
                    label=label,
                    field_type="combobox_location",
                    required=required,
                    element_id=combo_input_id or None,
                    context=data_field_path,
                )
            ]
        # Fail-closed regardless of what the shared classifier would
        # otherwise resolve -- see module docstring.
        return [
            DiscoveredField(
                label=label or "Unknown", field_type="unknown", required=required, context=data_field_path
            )
        ]

    if info.get("hasFileInput"):
        file_id = str(info.get("fileInputId") or "")
        if not file_id:
            return []
        return [
            DiscoveredField(
                label=label,
                name=file_id,
                field_type="file",
                required=required,
                element_id=file_id,
                context=data_field_path,
            )
        ]

    if info.get("hasYesno"):
        options = _yesno_options(wrapper)
        return [
            DiscoveredField(
                label=label, field_type="yesno", required=required, options=options, context=data_field_path
            )
        ]

    if info.get("hasTextInput") and info.get("hasConsentBlock"):
        # The phone `tel` input and a nested WhatsApp/SMS-consent radio
        # component can share this exact wrapper (see
        # `_discover_nested_whatsapp_consent`) -- the text input is always
        # this wrapper's primary field regardless of whether the nested
        # consent component's own identity resolves cleanly below, so this
        # never falls through to the generic `radioCount > 0` branch and
        # gets misdiscovered as a single radio field labeled "Phone Number".
        fields = [_text_field_from_info(info, label, required, data_field_path)]
        consent_radio_names = [str(name) for name in (info.get("consentRadioNames") or []) if name]
        consent_field = _discover_nested_whatsapp_consent(
            wrapper,
            data_field_path,
            consent_radio_names,
            str(info.get("consentText") or ""),
            bool(info.get("consentRequired")),
        )
        if consent_field is not None:
            fields.append(consent_field)
        return fields

    radio_count = int(info.get("radioCount") or 0)
    if radio_count > 0:
        radio_name = str(info.get("radioName") or "")
        if radio_name:
            if radio_name in seen_radio_names:
                return []
            seen_radio_names.add(radio_name)
        options = _radio_options_in_wrapper(wrapper)
        return [
            DiscoveredField(
                label=label,
                name=radio_name or None,
                field_type="radio",
                required=required,
                options=options,
                context=data_field_path,
            )
        ]

    checkbox_count = int(info.get("checkboxCount") or 0)
    if checkbox_count > 1:
        if _looks_like_application_source_group_label(label):
            source_options = _checkbox_options_in_wrapper(wrapper)
            if len(source_options) >= 2:
                return [
                    DiscoveredField(
                        label=label,
                        field_type="checkbox_group",
                        required=required,
                        options=source_options,
                        context=data_field_path,
                    )
                ]
        # An optional multi-option checkbox group (e.g. employee
        # relationship) has no safe, generic per-option semantic this
        # adapter can resolve -- left undiscovered so it is never guessed
        # at or auto-checked. See module docstring.
        return []
    if checkbox_count == 1:
        checkbox_id = str(info.get("checkboxId") or "")
        checkbox_name = str(info.get("checkboxName") or "")
        return [
            DiscoveredField(
                label=label,
                name=checkbox_name or None,
                field_type="checkbox",
                required=required,
                element_id=checkbox_id or None,
                context=data_field_path,
            )
        ]

    if info.get("hasTextInput"):
        return [_text_field_from_info(info, label, required, data_field_path)]

    return []


def _looks_like_current_location_label(label: str) -> bool:
    lowered = " ".join(label.lower().split())
    return any(cue in lowered for cue in _LOCATION_AUTOCOMPLETE_LABEL_CUES)


def _looks_like_application_source_group_label(label: str) -> bool:
    lowered = " ".join(label.lower().split())
    return any(cue in lowered for cue in _APPLICATION_SOURCE_GROUP_LABEL_CUES)


def _checkbox_options_in_wrapper(wrapper: Locator) -> list[str]:
    checkboxes = wrapper.locator('input[type="checkbox"]')
    options: list[str] = []
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        value = _radio_option_label(checkbox) or checkbox.get_attribute("value")
        if value:
            options.append(str(value).strip())
    return options


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
    if field.field_type == "combobox_location":
        # Scoped to the unique `data-field-path` wrapper rather than an id --
        # the live input has no stable id on every observed Ashby form (see
        # `_discover_wrapper`).
        return page.locator(f'[data-field-path="{field.context}"] input[role="combobox"]')
    if field.field_type in _TEXT_TYPES or field.field_type == "textarea":
        return _text_field_locator(page, field)
    if field.element_id:
        return page.locator(f'[id="{field.element_id}"]')
    if field.name:
        return page.locator(f'[name="{field.name}"]')
    return page.get_by_label(field.label)


def _text_field_locator(page: Page, field: DiscoveredField) -> Locator:
    """Scoped to the field's own `data-field-path` wrapper -- a bare,
    page-wide `[id="..."]`/`[name="..."]` lookup (the prior behavior) can
    also match an unrelated node elsewhere on the page that happens to reuse
    the same id/name (e.g. a masked/formatted phone-input widget's own
    mirrored or hidden value input), which would either silently target the
    wrong element or make Playwright's strict-mode matching raise on more
    than one match -- either way, a fill/readback that looks like it simply
    did nothing. Narrowing to this field's own wrapper can only ever reduce
    the match set, never change which element a working field (one with no
    such id/name collision) already resolves to. Falls back to the unscoped
    id/name lookup, and finally to label association, only when there is no
    `data-field-path` context to scope to.
    """
    tag = "textarea" if field.field_type == "textarea" else "input"
    scope = f'[data-field-path="{field.context}"] ' if field.context else ""
    if field.element_id:
        return page.locator(f'{scope}{tag}[id="{field.element_id}"]')
    if field.name:
        return page.locator(f'{scope}{tag}[name="{field.name}"]')
    if scope:
        return page.locator(f"{scope}{tag}")
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


def _checkbox_group_locator(page: Page, field: DiscoveredField) -> Locator:
    return page.locator(f'[data-field-path="{field.context}"] input[type="checkbox"]')


def _checkbox_group_option_labels(checkboxes: Locator) -> list[str]:
    labels: list[str] = []
    for index in range(checkboxes.count()):
        box = checkboxes.nth(index)
        labels.append(_radio_option_label(box) or (box.get_attribute("value") or "").strip())
    return labels


def _fill_checkbox_group(page: Page, field: DiscoveredField, value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    checkboxes = _checkbox_group_locator(page, field)
    labels = _checkbox_group_option_labels(checkboxes)
    matches = [
        index
        for index, label in enumerate(labels)
        if label and match_option_exact_normalized(value, [label]) is not None
    ]
    if len(matches) != 1:
        # No match, or an ambiguous match against more than one live option --
        # never guess which box the candidate meant, and never check any
        # option this module was not explicitly told to check.
        return False
    target = checkboxes.nth(matches[0])
    try:
        target.check(timeout=3_000)
    except PlaywrightError:
        return False
    return _safe_is_checked(target) is True


def _fill_combobox_location(page: Page, field: DiscoveredField, value: object) -> bool:
    """Type `value` into the autocomplete input, resolve its live listbox via
    the shared `react_controls` ARIA-association helpers (the same generic
    path Greenhouse's own combobox fill already relies on), and click the one
    exact-normalized matching option. Fails closed -- returns False without
    clicking anything -- when the listbox cannot be safely associated, or has
    no exact match; never presses Enter or accepts a bare typed value.
    """
    wanted = str(value or "").strip()
    if not wanted:
        return False
    locator = _field_locator(page, field)
    try:
        locator.click(timeout=3_000)
    except PlaywrightError:
        try:
            locator.click(force=True, timeout=3_000)
        except PlaywrightError:
            return False
    try:
        locator.fill(wanted, timeout=5_000)
        locator.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
    except PlaywrightError:
        return False
    try:
        page.locator("[role='option']").first.wait_for(state="visible", timeout=2_500)
    except PlaywrightTimeoutError:
        pass
    option = react_controls.scoped_matching_option_exact_normalized(page, locator, wanted)
    if option is None:
        return False
    try:
        option.click(timeout=3_000)
    except PlaywrightError:
        return False
    try:
        actual = locator.input_value()
    except PlaywrightError:
        return False
    return match_option_exact_normalized(wanted, [actual]) is not None


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
