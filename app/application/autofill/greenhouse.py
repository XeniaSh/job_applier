from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import (
    ALREADY_LOCATED_CHOICE,
    WOULD_RELOCATE_CHOICE,
    match_academic_option,
    match_application_source,
    match_decline_to_answer_option,
    match_gender_option,
    match_located_or_relocate_option,
    match_option,
    match_prefer_not_to_disclose_gender,
    match_sponsorship_option,
    match_work_authorization_option,
    match_years_option,
    select_listed_options,
)
from app.application.candidate_profile import canonical_academic_level
from app.application.autofill.phone import (
    calling_code_occurs_once,
    extract_calling_code,
    national_number_for_calling_code,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill import react_controls

_TEXT_TYPES = frozenset({"text", "email", "tel", "url", "search", "number", "combobox"})
_BOOLEAN_CHOICE_KINDS = frozenset(
    {
        QuestionKind.VISA_SPONSORSHIP,
        QuestionKind.WORK_AUTHORIZATION,
        QuestionKind.RELOCATION,
        QuestionKind.OFFICE_WORK,
        QuestionKind.EMPLOYEE_RELATIONSHIP,
        QuestionKind.PRIOR_AFFILIATION,
        QuestionKind.EMPLOYMENT_RESTRICTIONS,
        QuestionKind.LOCATED_IN,
        QuestionKind.APPLICATION_SOURCE,
        QuestionKind.NEWSLETTER,
        QuestionKind.SMS_UPDATES,
        QuestionKind.QUESTION_OVERRIDE,
        QuestionKind.PRIVACY_CONSENT,
    }
)
_MENU_CHOICE_KINDS = _BOOLEAN_CHOICE_KINDS | frozenset(
    {
        QuestionKind.GENDER,
        QuestionKind.FIELD_OF_INTEREST,
        QuestionKind.AGE,
        QuestionKind.NATIONALITY,
        QuestionKind.SENSITIVE,
    }
)

_TEXT_TYPES = frozenset({"text", "email", "tel", "url", "search", "number", "combobox"})
_FORM_READY_SELECTOR = (
    "#application-form, #application_form, form[data-ats='greenhouse'], "
    "#first_name, input[name='job_application[first_name]']"
)
_COMBOBOX_IDS = frozenset({"country", "candidate-location", "candidate_location"})

# Structural evidence that an anchor is Greenhouse's own apply action on a
# target company's custom careers domain (see `GreenhouseTargetVacancyResolver`
# / `original_url`: the canonical job-boards.greenhouse.io URL can redirect
# there). An `/apply` path segment is the job-boards application-form URL
# shape; `gh_jid` is the query marker Greenhouse's own embed snippet attaches
# to job links it controls. A bare `greenhouse.io` host is not enough on its
# own -- a generic job-board link without either marker could be unrelated
# navigation. Never a generic "Apply" text/class match -- an unrelated custom
# career page could reuse that wording for something else entirely.
_APPLY_HREF_PATH_MARKERS = ("/apply",)
_APPLY_HREF_QUERY_MARKER = "gh_jid="

logger = logging.getLogger(__name__)


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


def _is_greenhouse_apply_href(href: str) -> bool:
    """True when `href` carries one of the Greenhouse-specific apply markers
    above. Only inspects the resolved URL's path/query/host -- never logged
    or matched against visible text -- so it stays a structural check.
    """
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    path = parsed.path.lower()
    if any(marker in path for marker in _APPLY_HREF_PATH_MARKERS):
        return True
    query = parsed.query.lower()
    if _APPLY_HREF_QUERY_MARKER in query:
        return True
    host = (parsed.hostname or "").lower()
    return (
        host.endswith(".greenhouse.io")
        and "/embed/job_app" in path
        and "for=" in query
        and "token=" in query
    )


def _custom_domain_apply_matches(page: Page) -> list[Locator]:
    """Every visible anchor on `page` whose resolved href carries a
    Greenhouse apply marker. Matching is done on the anchor's
    browser-resolved absolute URL (`el.href`), never on text/class/styling,
    so it cannot match an unrelated button. Callers only act on this when
    exactly one match exists -- more than one means the target action cannot
    be disambiguated and must fail closed rather than guess.
    """
    anchors = page.locator("a[href]")
    matches: list[Locator] = []
    destinations: set[str] = set()
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        try:
            href = anchor.evaluate("el => el.href")
        except PlaywrightError:
            continue
        if not isinstance(href, str) or not _is_greenhouse_apply_href(href):
            continue
        try:
            parsed = urlparse(href)
            destination = parsed._replace(fragment="").geturl()
        except ValueError:
            continue
        if destination in destinations:
            continue
        try:
            visible = anchor.is_visible()
        except PlaywrightError:
            visible = False
        if visible:
            destinations.add(destination)
            matches.append(anchor)
    return matches

_CHECKBOX_QUESTION_META_JS = """el => {
    const option = ((el.closest('label') && el.closest('label').innerText)
        || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
    let node = el.parentElement;
    let root = el.parentElement;
    while (node && node.tagName && !['FORM', 'BODY', 'HTML'].includes(node.tagName)) {
        const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
        const marker = String(node.className || '') + ' ' + String(node.id || '');
        const named = /question/i.test(marker)
            || /(?:^|\\s)field\\b/i.test(marker)
            || ['FIELDSET', 'SECTION'].includes(node.tagName);
        const broader = option
            ? (text.toLowerCase().includes(option.toLowerCase()) && text.length > option.length + 6)
            : text.length > 24;
        if (named || broader) {
            root = node;
            break;
        }
        node = node.parentElement;
    }
    const text = ((root && root.innerText) || '').replace(/\\s+/g, ' ').trim();
    const firstLine = ((root && root.innerText) || '').split('\\n').map(s => s.trim()).find(Boolean) || '';
    const ariaRequired = ((root && root.getAttribute('aria-required')) || '').toLowerCase();
    const markedRequired = !!(root && root.querySelector(
        '[aria-required="true"], [class*="asterisk"], [data-required="true"]'
    ));
    const required = ariaRequired === 'true' || markedRequired || /\\*/.test(firstLine);
    return {context: text, required};
}"""

# Bounded visual/container required-marker detection for ordinary (non-checkbox)
# text/textarea/select controls. Mirrors the checkbox walk above -- same
# named/broader stop condition, same marker selectors -- but caps the climb
# to a few ancestors so it cannot wander into an unrelated sibling
# question's asterisk. `required`/`aria-required` on the control itself is
# checked separately by `_is_required`; this only covers the case where the
# requirement is communicated purely by a visible label marker.
_ORDINARY_REQUIRED_MARKER_JS = """(el, optionLabel) => {
    const option = String(optionLabel || '').replace(/\\s+/g, ' ').trim();
    let node = el.parentElement;
    let root = null;
    for (let i = 0; i < 4 && node && node.tagName && !['FORM', 'BODY', 'HTML'].includes(node.tagName); i++) {
        const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
        const marker = String(node.className || '') + ' ' + String(node.id || '');
        const named = /question/i.test(marker)
            || /(?:^|\\s)field\\b/i.test(marker)
            || ['FIELDSET', 'SECTION'].includes(node.tagName);
        const broader = option
            ? (text.toLowerCase().includes(option.toLowerCase()) && text.length > option.length + 6)
            : text.length > 24;
        if (named || broader) {
            root = node;
            break;
        }
        node = node.parentElement;
    }
    if (!root) return false;
    const ariaRequired = ((root.getAttribute('aria-required')) || '').toLowerCase();
    const markedRequired = !!root.querySelector(
        '[aria-required="true"], [class*="asterisk"], [data-required="true"]'
    );
    const firstLine = (root.innerText || '').split('\\n').map(s => s.trim()).find(Boolean) || '';
    return ariaRequired === 'true' || markedRequired || /\\*/.test(firstLine);
}"""

_ORDINARY_VISUAL_REQUIRED_TYPES = frozenset({"text", "textarea", "select", "multiselect"} | _TEXT_TYPES)


class GreenhouseFormError(Exception):
    """Raised when a Greenhouse application page cannot be used."""


def is_greenhouse_application_page(page: Page) -> bool:
    if page.locator("form[data-ats='greenhouse']").count() > 0:
        return True
    if page.locator('input[name^="job_application["]').count() > 0:
        return True
    if page.locator("#application_form, #application-form, form#new_job_application").count() > 0:
        return True
    url = page.url.lower()
    return "greenhouse.io" in url and page.locator("form").count() > 0


def detect_security_challenge(page: Page) -> str | None:
    title = page.title().lower()
    body = page.locator("body").inner_text()[:3000].lower()
    if "just a moment" in title or "checking your browser" in body:
        return "cloudflare_challenge"
    if page.locator("#cf-challenge, .cf-browser-verification").count() > 0:
        return "cloudflare_challenge"
    if is_greenhouse_application_page(page):
        # Invisible / enterprise reCAPTCHA often sits on real Greenhouse forms.
        # Do not treat that as a blocking challenge.
        return None
    if page.locator(".g-recaptcha, iframe[src*='recaptcha'], iframe[src*='captcha']").count() > 0:
        return "captcha"
    if page.locator('input[type="password"]').count() > 0:
        return "login"
    return None


class GreenhouseAdapter:
    """Greenhouse-specific form discovery and filling. No submit capability."""

    last_cover_letter_error: str | None = None
    last_multiselect_selected: list[str] | None = None
    last_privacy_trace: dict[str, object] | None = None
    last_choice_selected: str | None = None
    last_choice_trace: dict[str, object] | None = None

    def recognize(self, page: Page) -> bool:
        return is_greenhouse_application_page(page)

    def detect_challenge(self, page: Page) -> str | None:
        return detect_security_challenge(page)

    def prepare_page(self, page: Page) -> None:
        """Wait until the application form is usable.

        If the page is not already a recognized Greenhouse form -- e.g. the
        canonical job-boards.greenhouse.io URL redirected to a target
        company's own custom careers domain -- attempt one bounded,
        structurally-validated navigation via Greenhouse's own apply action
        first. If that does not land on a recognized form, fail closed:
        recognize() will then be False and the caller reports
        UNSUPPORTED_FORM rather than guessing at an unfamiliar page.
        Local fixtures skip the extra settle.
        """
        already_form = self.recognize(page)
        logger.warning(
            "greenhouse_prepare stage=detail_detection already_form=%s page_host=%s",
            already_form,
            _safe_host(page.url),
        )
        if not already_form:
            self._navigate_from_custom_domain(page)
        timeout = 2_000 if page.url.startswith("file:") else 30_000
        try:
            page.wait_for_selector(_FORM_READY_SELECTOR, timeout=timeout)
        except PlaywrightTimeoutError:
            self._log_final_recognition(page)
            return
        if page.url.startswith("file:"):
            self._log_final_recognition(page)
            return
        try:
            page.wait_for_selector("#first_name:visible, #application-form:visible", timeout=10_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(800)
        self._log_final_recognition(page)

    def _navigate_from_custom_domain(self, page: Page) -> None:
        """Attempt the custom-domain detail -> Greenhouse-form transition and
        always log a navigation-stage record at the end, even when no
        uniquely identifiable apply action exists or the click raises, so a
        single run identifies every failure stage. Never dismisses cookie
        overlays or other page chrome -- if one blocks the click, the click
        raises, the stage is logged, and the page fails closed via the
        caller's normal `recognize()` check.
        """
        pre_url = page.url
        click_attempted = False
        click_raised: str | None = None
        form_ready_selector_found = False
        for _ in range(2):
            matches = _custom_domain_apply_matches(page)
            apply_locator_count = len(matches)
            logger.warning(
                "greenhouse_prepare stage=apply_action apply_locator_count=%s",
                apply_locator_count,
            )
            action = matches[0] if apply_locator_count == 1 else None
            if action is None:
                break
            click_attempted = True
            try:
                action.click(timeout=5_000)
            except PlaywrightError as exc:
                click_raised = type(exc).__name__
            logger.warning(
                "greenhouse_prepare stage=apply_action click_attempted=%s click_raised=%s",
                click_attempted,
                click_raised,
            )
            if click_raised is None:
                timeout = 2_000 if page.url.startswith("file:") else 15_000
                try:
                    page.wait_for_selector(_FORM_READY_SELECTOR, timeout=timeout)
                    form_ready_selector_found = True
                    break
                except PlaywrightTimeoutError:
                    form_ready_selector_found = False
            if click_raised is not None:
                break
        post_url = page.url
        logger.warning(
            "greenhouse_prepare stage=navigation pre=%s post=%s url_changed=%s "
            "click_attempted=%s click_raised=%s form_ready_selector_found=%s",
            _safe_host_path(pre_url),
            _safe_host_path(post_url),
            pre_url != post_url,
            click_attempted,
            click_raised,
            form_ready_selector_found,
        )

    def _log_final_recognition(self, page: Page) -> None:
        logger.warning(
            "greenhouse_prepare stage=form_recognition result=%s page_host=%s",
            self.recognize(page),
            _safe_host(page.url),
        )

    def discover_fields(self, page: Page) -> list[DiscoveredField]:
        if not self.recognize(page):
            raise GreenhouseFormError("UNSUPPORTED_FORM")
        form = _application_form(page)
        fields: list[DiscoveredField] = []
        seen_radio_names: set[str] = set()
        seen_ids: set[str] = set()
        controls = form.locator("input, textarea, select, [role='checkbox']:not(input)")
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
        if not any(_is_cover_letter_field(item) for item in fields) and _has_cover_letter_section(page):
            fields.append(
                DiscoveredField(
                    label="Cover Letter",
                    field_type="cover_letter",
                    element_id="cover_letter",
                )
            )
        return fields

    def observe_final_fill_plan(self, item: ClassifiedField) -> None:
        """Emit bounded diagnostics for Stripe-shaped profile fields.

        This is intentionally before the DOM write and excludes all semantic
        values, keeping live runs useful when a field is discovered under an
        unexpected Greenhouse control shape or profile data is absent.
        """
        label = " ".join((item.field.label or "").lower().split())
        targeted_kinds = {
            QuestionKind.ACADEMIC_LEVEL,
            QuestionKind.SCHOOL,
            QuestionKind.COUNTRY,
            QuestionKind.ANTICIPATED_WORK_COUNTRY,
            QuestionKind.CURRENT_EMPLOYER,
            QuestionKind.CURRENT_TITLE,
        }
        if item.kind not in targeted_kinds and not any(
            term in label for term in ("school", "degree", "reside", "anticipate", "employer", "job title")
        ):
            return
        logger.warning(
            "greenhouse_final_fill label=%s required=%s kind=%s value_present=%s action=%s field_type=%s",
            label[:160],
            item.field.required,
            item.kind.value,
            item.value is not None,
            "fill" if item.fill else "skip",
            item.field.field_type,
        )

    def fill_field(self, page: Page, classified: ClassifiedField) -> bool:
        self.last_multiselect_selected = None
        self.last_choice_selected = None
        self.last_choice_trace = None
        if not classified.fill or classified.value is None:
            return False
        field = classified.field
        if classified.kind is QuestionKind.COVER_LETTER:
            return self.fill_cover_letter(page, str(classified.value))
        locator = _field_locator(page, field)
        try:
            if isinstance(classified.value, list) or field.field_type == "multiselect":
                values = classified.value if isinstance(classified.value, list) else [str(classified.value)]
                selected = _fill_multiselect(page, locator, field, values, classified.max_choices)
                self.last_multiselect_selected = selected
                return bool(selected)
            if field.field_type == "checkbox":
                trace = react_controls.set_checkbox_with_trace(
                    page,
                    locator,
                    classified.kind is QuestionKind.APPLICATION_SOURCE
                    or _checkbox_should_check(classified.value),
                )
                self._record_privacy_trace(classified, trace)
                logger.info(
                    "checkbox fill label=%r control_type=%s attempted=%s readback=%s ok=%s reason=%s",
                    field.label,
                    trace.get("control_type"),
                    trace.get("interaction_attempted"),
                    trace.get("readback_checked"),
                    trace.get("success"),
                    trace.get("failure_reason"),
                )
                return bool(trace["success"])
            if field.field_type == "radio":
                return _fill_radio(page, field, classified.value)
            if classified.kind is QuestionKind.COUNTRY:
                resolved: dict[str, str] = {}
                choice_ok = _fill_choice(page, locator, field, classified.value, kind=classified.kind, resolved=resolved)
                self.last_choice_trace = _describe_choice_interaction(
                    locator, field, classified.kind, choice_ok
                )
                if choice_ok:
                    self.last_choice_selected = resolved.get("label")
                    return True
                combo_ok = _fill_combobox(page, locator, str(classified.value), trace_field=field)
                self.last_choice_trace = _describe_choice_interaction(
                    locator, field, classified.kind, combo_ok
                )
                return combo_ok
            if classified.kind in _MENU_CHOICE_KINDS or isinstance(classified.value, bool):
                resolved = {}
                ok = _fill_choice(
                    page,
                    locator,
                    field,
                    classified.value,
                    kind=classified.kind,
                    referenced_country=classified.country,
                    resolved=resolved,
                )
                self.last_choice_selected = resolved.get("label")
                self.last_choice_trace = _describe_choice_interaction(locator, field, classified.kind, ok)
                if classified.kind is QuestionKind.PRIVACY_CONSENT:
                    self.last_privacy_trace = {
                        "discovered": True,
                        "classified": (
                            "required_privacy" if field.required else "privacy_optional"
                        ),
                        "control_type": field.field_type,
                        "interaction_attempted": "select_affirmative_option",
                        "readback_checked": ok,
                        "failure_reason": None if ok else "choice_select_did_not_persist",
                        "locator": _field_locator_debug(field),
                    }
                return ok
            if classified.kind is QuestionKind.PHONE:
                return _fill_phone(page, locator, str(classified.value), classified.country)
            if classified.kind is QuestionKind.PRIMARY_LANGUAGE:
                return _fill_primary_language(page, locator, field, str(classified.value))
            if classified.kind is QuestionKind.YEARS_EXPERIENCE:
                return _fill_years(page, locator, field, classified.value)
            if classified.kind is QuestionKind.ACADEMIC_LEVEL:
                return _fill_academic(page, locator, field, str(classified.value))
            if field.field_type == "select":
                return _fill_select(page, locator, classified.value, field.options)
            if field.field_type == "file":
                locator.set_input_files(str(classified.value))
                return True
            if classified.kind in {QuestionKind.LOCATION} or _is_combobox(locator, field):
                return _fill_combobox(page, locator, str(classified.value))
            if field.field_type in _TEXT_TYPES or field.field_type == "textarea":
                return _fill_text(page, locator, str(classified.value))
        except PlaywrightError:
            return False
        return False

    def _record_privacy_trace(self, classified: ClassifiedField, trace: dict[str, object]) -> None:
        field = classified.field
        if classified.kind is not QuestionKind.PRIVACY_CONSENT and not _looks_like_privacy_field(field):
            return
        if classified.kind is QuestionKind.PRIVACY_CONSENT:
            classified_as = "required_privacy" if field.required else "privacy_optional"
        else:
            classified_as = classified.kind.value
        self.last_privacy_trace = {
            "discovered": True,
            "classified": classified_as,
            "control_type": trace.get("control_type") or field.field_type,
            "interaction_attempted": trace.get("interaction_attempted") or "none",
            "readback_checked": trace.get("readback_checked"),
            "failure_reason": trace.get("failure_reason"),
            "locator": _field_locator_debug(field),
        }

    def fill_cover_letter(self, page: Page, text: str) -> bool:
        self.last_cover_letter_error = None
        if not text.strip():
            self.last_cover_letter_error = (
                "Cover letter not filled: generated text was empty."
            )
            logger.warning(self.last_cover_letter_error)
            return False
        section = _cover_letter_container(page)
        if section is None:
            self.last_cover_letter_error = (
                "Cover letter not filled: Cover Letter section was not found."
            )
            logger.warning(self.last_cover_letter_error)
            return False
        activated, activate_error = _activate_cover_letter_manual(page, section)
        if not activated:
            self.last_cover_letter_error = activate_error
            logger.warning(self.last_cover_letter_error)
            return False
        editor = _wait_cover_letter_editor(page, section)
        if editor is None:
            self.last_cover_letter_error = (
                "Cover letter not filled: Enter manually was clicked but the editor was not detected."
            )
            logger.warning(self.last_cover_letter_error)
            return False
        filled = _fill_text(page, editor, text)
        if not filled:
            self.last_cover_letter_error = "Cover letter not filled: editor fill failed."
            logger.warning(self.last_cover_letter_error)
            return False
        persisted = _wait_cover_letter_text(page, editor, text)
        visible = (_cover_letter_visible_text(editor) or "").strip()
        if not visible:
            self.last_cover_letter_error = (
                "Cover letter not filled: React cleared the editor after fill (read-back empty)."
            )
            logger.warning(self.last_cover_letter_error)
            return False
        if not persisted or text.strip()[:40] not in visible:
            self.last_cover_letter_error = (
                "Cover letter not filled: editor text did not persist after React settled."
            )
            logger.warning(self.last_cover_letter_error)
            return False
        logger.info("Cover letter filled via Enter manually; persisted read-back confirmed.")
        return True

    def upload_resume(self, page: Page, resume_path: Path, field: DiscoveredField) -> bool:
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        path = str(resume_path)
        filename = resume_path.name
        try:
            _set_resume_files(page, field, path)
        except PlaywrightError:
            pass
        if _resume_is_attached(page, field, filename):
            return True
        if not page.url.startswith("file:"):
            page.wait_for_timeout(1500)
        return _resume_is_attached(page, field, filename)

    def resume_file_attached(self, page: Page, field: DiscoveredField) -> bool:
        return bool(_file_names(page, field))

    def read_back(self, page: Page, field: DiscoveredField) -> str | None:
        if _is_cover_letter_field(field) or field.field_type == "cover_letter":
            editor = _cover_letter_editor(page, _cover_letter_container(page))
            if editor is not None:
                typed = _input_value(editor).strip()
                if typed:
                    return typed
                try:
                    inner = (editor.inner_text() or "").strip()
                except PlaywrightError:
                    inner = ""
                if inner:
                    return inner
        if field.field_type == "file":
            names = _file_names(page, field)
            return names[0] if names else None
        locator = _field_locator(page, field)
        if field.field_type == "checkbox":
            return "true" if react_controls.is_checked(locator) else "false"
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
            selected = react_controls.read_selected_chips(page, locator)
            return ", ".join(selected) if selected else None
        chips = react_controls.read_selected_chips(page, locator)
        if chips:
            return ", ".join(chips)
        selected_label = react_controls.read_selected_label(page, locator)
        if _looks_like_phone_widget(page, locator):
            return _composed_phone_value(page, locator)
        if selected_label:
            typed = _input_value(locator).strip()
            if typed and typed.lower() not in selected_label.lower() and field.field_type in {"select", "combobox"}:
                return selected_label
            return selected_label
        value = _visible_control_value(page, locator)
        return value if value else None

    def invalid_required_empty(self, page: Page, field: DiscoveredField) -> bool:
        if not field.required:
            return False
        value = self.read_back(page, field)
        return value is None or value == "" or value == "false" and field.field_type == "checkbox"


def _application_form(page: Page) -> Locator:
    for selector in (
        "form[data-ats='greenhouse']",
        "#application_form",
        "#application-form",
        "form#new_job_application",
        "form",
    ):
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    raise GreenhouseFormError("UNSUPPORTED_FORM")


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
    if tag == "input" and input_type == "radio":
        if not name or name in seen_radio_names:
            return None
        seen_radio_names.add(name)
        options = _radio_options(page, name)
        return DiscoveredField(
            label=_radio_group_label(page, locator, name),
            name=name,
            field_type="radio",
            required=_is_required_group(page, name) or _is_required(locator),
            options=options,
            current_value=_checked_radio_value(page, name),
            element_id=element_id,
        )
    field_type = _field_type(tag, input_type, locator)
    label = _control_label(page, locator)
    heading = _question_heading(locator)
    if heading and _looks_like_control_name(label):
        label = heading
    context = ""
    context_required = False
    if field_type == "checkbox":
        if tag != "input" and _group_has_native_checkbox(locator):
            return None
        context, context_required = _checkbox_question_meta(locator)
        label = _checkbox_display_label(label, context)
    if _is_decoy_control(locator, label, name, element_id, field_type):
        return None
    if _is_cover_letter_label(label, name, element_id):
        field_type = "cover_letter"
        if re.search(r"cover\s+letter", label, re.I) is None:
            label = "Cover Letter"
    options = []
    if field_type in {"select", "multiselect"}:
        options = _select_options(locator)
    required = _is_required(locator)
    if field_type == "checkbox" and not required:
        required = context_required
    elif not required and field_type in _ORDINARY_VISUAL_REQUIRED_TYPES:
        required = _has_ordinary_visual_required_marker(locator, label)
    return DiscoveredField(
        label=label,
        name=name,
        field_type=field_type,
        required=required,
        options=options,
        current_value=_current_value(locator, field_type),
        autocomplete=locator.get_attribute("autocomplete"),
        element_id=element_id,
        context=context if field_type == "checkbox" else "",
    )


def _field_type(tag: str, input_type: str, locator: Locator) -> str:
    role = (locator.get_attribute("role") or "").lower()
    if role == "checkbox" and input_type != "checkbox":
        return "checkbox"
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        if locator.get_attribute("multiple") is not None:
            return "multiselect"
        return "select"
    if _is_combobox(locator, None):
        return "combobox"
    if input_type in _TEXT_TYPES or input_type == "":
        return input_type or "text"
    if input_type in {"file", "checkbox", "radio"}:
        return input_type
    return input_type or "text"


def _is_combobox(locator: Locator, field: DiscoveredField | None) -> bool:
    role = (locator.get_attribute("role") or "").lower()
    autocomplete = (locator.get_attribute("aria-autocomplete") or "").lower()
    element_id = (locator.get_attribute("id") or "")
    if field is not None:
        element_id = field.element_id or element_id
        if field.field_type == "combobox":
            return True
    if role == "combobox" or autocomplete in {"list", "both"}:
        return True
    return element_id in _COMBOBOX_IDS


def _is_decoy_control(
    locator: Locator,
    label: str,
    name: str | None,
    element_id: str | None,
    field_type: str,
) -> bool:
    if field_type in {"file", "checkbox"}:
        return False
    ident = (element_id or "").lower()
    if ident.startswith("react-select") or ident.endswith("-input"):
        if not (label.strip() or name):
            return True
    aria_hidden = (locator.get_attribute("aria-hidden") or "").lower()
    if aria_hidden == "true":
        return True
    # Inner react-select search boxes often have no stable id/name.
    if not ident and not name:
        return True
    if label.strip() or name:
        return False
    return not ident


def _group_has_native_checkbox(locator: Locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                """el => {
                    const group = el.closest('label') || el.parentElement;
                    return !!(group && group.querySelector('input[type="checkbox"]'));
                }"""
            )
        )
    except PlaywrightError:
        return False


def _checkbox_question_meta(locator: Locator) -> tuple[str, bool]:
    try:
        raw = locator.evaluate(_CHECKBOX_QUESTION_META_JS)
    except PlaywrightError:
        return "", False
    if not isinstance(raw, dict):
        return "", False
    context = " ".join(str(raw.get("context") or "").split())
    return context, bool(raw.get("required"))


def _looks_like_privacy_field(field: DiscoveredField) -> bool:
    text = f"{field.label} {field.context}".lower()
    return any(
        term in text
        for term in (
            "data transfer",
            "privacy notice",
            "privacy policy",
            "acknowledge/confirm",
            "applicant privacy",
            "processing my application data",
        )
    )


def _field_locator_debug(field: DiscoveredField) -> str:
    parts = [field.field_type]
    if field.element_id:
        parts.append(f"id={field.element_id}")
    if field.name:
        parts.append(f"name={field.name}")
    return " ".join(parts)


def _is_required(locator: Locator) -> bool:
    if locator.get_attribute("required") is not None:
        return True
    aria = (locator.get_attribute("aria-required") or "").lower()
    return aria == "true"


def _has_ordinary_visual_required_marker(locator: Locator, label: str) -> bool:
    try:
        return bool(locator.evaluate(_ORDINARY_REQUIRED_MARKER_JS, label))
    except PlaywrightError:
        return False


def _checkbox_display_label(option_label: str, context: str) -> str:
    option = " ".join((option_label or "").split())
    heading = " ".join((context or "").split())
    if heading:
        heading = heading.replace("*", " ").strip()
        # Section title is the start of the context, before help copy.
        for separator in (" Information ", " I ", ". "):
            if separator.strip() in heading:
                heading = heading.split(separator)[0].strip()
                break
        if len(heading) > 80:
            heading = heading[:80].rsplit(" ", 1)[0]
    generic = option.lower().rstrip("*").strip() in {
        "acknowledge",
        "confirm",
        "acknowledge/confirm",
        "i agree",
        "i accept",
        "agree",
        "accept",
    }
    if generic and heading:
        return f"{heading} — {option}" if option else heading
    if option:
        return option
    return heading


def _checkbox_should_check(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on", "acknowledge", "confirm", "acknowledge/confirm"}


def _is_required_group(page: Page, name: str) -> bool:
    group = page.locator(f'input[type="radio"][name="{name}"]')
    for index in range(group.count()):
        if _is_required(group.nth(index)):
            return True
    return False


def _control_label(page: Page, locator: Locator) -> str:
    aria = (locator.get_attribute("aria-label") or "").strip()
    if aria:
        return aria
    labelledby = (locator.get_attribute("aria-labelledby") or "").strip()
    if labelledby:
        parts: list[str] = []
        for token in labelledby.split():
            node = page.locator(f'[id="{token}"]')
            if node.count() > 0:
                text = " ".join(node.first.inner_text().split())
                if text:
                    parts.append(text)
        if parts:
            return " ".join(parts)
    element_id = locator.get_attribute("id")
    if element_id:
        labeled = page.locator(f'label[for="{element_id}"]')
        if labeled.count() > 0:
            return " ".join(labeled.first.inner_text().split())
        by_id = page.locator(f'[id="{element_id}-label"]')
        if by_id.count() > 0:
            return " ".join(by_id.first.inner_text().split())
    wrapped = locator.evaluate(
        """el => {
            const label = el.closest('label');
            return label ? label.innerText : '';
        }"""
    )
    if isinstance(wrapped, str) and wrapped.strip():
        return " ".join(wrapped.split())
    name = locator.get_attribute("name") or ""
    return name


def _looks_like_control_name(label: str) -> bool:
    cleaned = " ".join((label or "").split())
    if not cleaned:
        return True
    if "[" in cleaned and "]" in cleaned:
        return True
    return bool(re.match(r"^(question|job_application)[_-]?\d*$", cleaned, re.I))


_QUESTION_HEADING_JS = """el => {
    const firstLine = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
    const tooBroad = (text) => text.length > 240;
    let node = el.parentElement;
    for (let i = 0; i < 6 && node; i++) {
        const direct = node.matches && node.matches('label')
            ? node
            : node.querySelector(':scope > label, :scope > legend, :scope > .input-label, :scope > [class*="field-label"]');
        if (direct && direct !== el) {
            const text = firstLine(direct.innerText || '');
            if (text && !tooBroad(text)) return text;
        }
        const prev = node.previousElementSibling;
        if (prev && prev !== el) {
            const text = firstLine(prev.innerText || '');
            if (text && text.length > 12 && !tooBroad(text)) return text;
        }
        const marker = String(node.className || '') + ' ' + String(node.tagName || '');
        if (/FIELDSET|SECTION|question|field/i.test(marker)) {
            const heading = node.querySelector('label, legend, p, h2, h3, h4, [class*="label"]');
            if (heading && heading !== el && !heading.contains(el)) {
                const text = firstLine(heading.innerText || '');
                if (text && !tooBroad(text)) return text;
            }
        }
        node = node.parentElement;
    }
    return '';
}"""


def _question_heading(locator: Locator) -> str:
    try:
        raw = locator.evaluate(_QUESTION_HEADING_JS)
    except PlaywrightError:
        return ""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())


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
    return _control_label(page, locator) or name


def _radio_options(page: Page, name: str) -> list[str]:
    radios = page.locator(f'input[type="radio"][name="{name}"]')
    options: list[str] = []
    for index in range(radios.count()):
        value = radios.nth(index).get_attribute("value") or radios.nth(index).evaluate(
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
        """el => Array.from(el.options || []).map(opt => (opt.textContent || '').trim()).filter(Boolean)"""
    )


def _current_value(locator: Locator, field_type: str) -> str | None:
    if field_type == "file":
        return None
    if field_type == "checkbox":
        return "true" if react_controls.is_checked(locator) else "false"
    try:
        value = locator.input_value()
    except PlaywrightError:
        return None
    return value or None


def _id_locator(page: Page, element_id: str) -> Locator:
    return page.locator(f'[id="{element_id}"]')


def _field_locator(page: Page, field: DiscoveredField) -> Locator:
    if field.element_id:
        return _id_locator(page, field.element_id)
    if field.name:
        if field.field_type == "radio":
            return page.locator(f'input[type="radio"][name="{field.name}"]')
        return page.locator(f'[name="{field.name}"]')
    locator = page.get_by_label(field.label)
    if locator.count() > 0:
        return locator
    if field.field_type == "combobox":
        accessible_label = field.label.rstrip("*").strip()
        locator = page.get_by_role("combobox", name=accessible_label)
        if locator.count() > 0:
            return locator.first
    return locator


def _set_resume_files(page: Page, field: DiscoveredField, path: str) -> None:
    # The discovered resume file input is always preferred, over file:// and
    # HTTP(S) alike: a nearby button is never assumed to be the attach
    # trigger just because the transport is HTTP(S). Only when no resume
    # file input exists in the DOM (a dropzone widget that renders one only
    # after a chooser interaction) do we fall back to clicking an adjacent
    # non-submit control to open the native file chooser.
    resume_input = page.locator('input[type="file"]#resume')
    target = resume_input if resume_input.count() > 0 else _resume_file_locator(page, field)
    if target.count() > 0:
        target.set_input_files(path, timeout=5_000, no_wait_after=True)
        return
    if page.url.startswith("file:"):
        return
    # A submit control is never the resume-attach trigger; excluding it keeps
    # this path from ever mistaking a nearby Submit button for one (no submit
    # capability exists anywhere in this adapter).
    button = page.locator("#resume").locator("xpath=..").locator('button:not([type="submit"])')
    if button.count() == 0:
        return
    with page.expect_file_chooser(timeout=5_000) as chooser:
        button.first.click(timeout=3_000)
    chooser.value.set_files(path)


def _resume_file_locator(page: Page, field: DiscoveredField) -> Locator:
    if field.element_id:
        typed = page.locator(f'input[type="file"][id="{field.element_id}"]')
        if typed.count() > 0:
            return typed.first
    locator = page.locator('input[type="file"][id="resume"], input[type="file"][name*="resume" i]')
    if locator.count() > 0:
        return locator.first
    return _field_locator(page, field).first


def _fill_primary_language(page: Page, locator: Locator, field: DiscoveredField, value: str) -> bool:
    """Fill a primary language/framework answer as text unless the control is a menu."""
    wanted = value.strip()
    if not wanted:
        return False
    if field.field_type in {"select", "radio"}:
        if _fill_choice(page, locator, field, wanted, kind=QuestionKind.PRIMARY_LANGUAGE):
            return True
        return _fill_text(page, locator, wanted)
    if field.field_type == "combobox" or _is_combobox(locator, field):
        if field.options and match_option(wanted, field.options):
            if _fill_choice(page, locator, field, wanted, kind=QuestionKind.PRIMARY_LANGUAGE):
                return True
        typed = _fill_text(page, locator, wanted)
        current = _input_value(locator)
        if typed and wanted.lower() in current.lower():
            return True
        visible = react_controls.read_selected_label(page, locator) or ""
        if wanted.lower() in visible.lower():
            return True
        return _fill_choice(page, locator, field, wanted, kind=QuestionKind.PRIMARY_LANGUAGE)
    return _fill_text(page, locator, wanted)


def _fill_text(page: Page, locator: Locator, value: str) -> bool:
    _write_input(locator, value)
    if page.url.startswith("file:"):
        return True
    page.wait_for_timeout(350)
    current = _input_value(locator)
    if current.strip() != value.strip():
        _write_input(locator, value, sequential=True)
    return True


def _write_input(locator: Locator, value: str, *, sequential: bool = False) -> None:
    try:
        locator.click(timeout=3_000)
    except PlaywrightError:
        locator.click(force=True, timeout=3_000)
    locator.fill("", timeout=5_000)
    if sequential:
        locator.press_sequentially(value, delay=15, timeout=10_000)
        _dispatch_value_events(locator, value)
        if _input_value(locator).strip() != value.strip():
            _set_native_value(locator, value)
        return
    locator.fill(value, timeout=5_000)
    _dispatch_value_events(locator, value)
    if _input_value(locator).strip() != value.strip():
        _set_native_value(locator, value)


def _dispatch_value_events(locator: Locator, value: str) -> None:
    try:
        locator.evaluate(
            """(el, next) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                try {
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: next, inputType: 'insertText' }));
                } catch (err) {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )
    except PlaywrightError:
        pass


def _set_native_value(locator: Locator, value: str) -> None:
    try:
        locator.evaluate(
            """(el, next) => {
                const tag = (el.tagName || '').toLowerCase();
                const proto = tag === 'textarea'
                    ? Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
                    : Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                        || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (proto && proto.set) {
                    proto.set.call(el, next);
                } else if (el.isContentEditable) {
                    el.textContent = next;
                } else {
                    el.value = next;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                try {
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: next, inputType: 'insertText' }));
                } catch (err) {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )
    except PlaywrightError:
        pass


def _fill_phone(page: Page, locator: Locator, value: str, country: str | None) -> bool:
    if not _looks_like_phone_widget(page, locator):
        return _fill_text(page, locator, value)
    if country:
        _select_phone_country(page, locator, country)
    try:
        page.keyboard.press("Escape")
    except PlaywrightError:
        pass
    calling_code = _widget_calling_code(page, locator)
    national = national_number_for_calling_code(value, calling_code)
    filled = _fill_text(page, locator, national)
    composed = _composed_phone_value(page, locator) or ""
    if calling_code and not calling_code_occurs_once(composed, calling_code):
        _write_input(locator, national)
        composed = _composed_phone_value(page, locator) or ""
        if not calling_code_occurs_once(composed, calling_code):
            return False
    actual_digits = "".join(ch for ch in composed if ch.isdigit())
    return filled and bool(national and (national in composed or "".join(ch for ch in national if ch.isdigit()) in actual_digits))


def _looks_like_phone_widget(page: Page, locator: Locator) -> bool:
    _ = page
    try:
        return bool(
            locator.evaluate(
                """el => el.closest('.iti, .intl-tel-input, [class*="iti"]') !== null"""
            )
        )
    except PlaywrightError:
        return False


def _widget_calling_code(page: Page, locator: Locator) -> str | None:
    parts: list[str] = []
    try:
        from_widget = locator.evaluate(
            """el => {
                const root = el.closest('.iti, .intl-tel-input, [class*="iti"]') || el.parentElement;
                if (!root) return '';
                const selected = root.querySelector('[data-dial-code]');
                const flag = root.querySelector(
                    '.iti__selected-country, .iti__selected-flag, .iti__selected-dial-code'
                );
                const active = document.querySelector('.iti__country.iti__active, .iti__country[aria-selected="true"]');
                return [
                    selected && selected.getAttribute('data-dial-code'),
                    flag && flag.getAttribute('data-dial-code'),
                    flag && flag.getAttribute('aria-label'),
                    flag && flag.textContent,
                    active && active.getAttribute('data-dial-code'),
                ].filter(Boolean).join(' ');
            }"""
        )
        if from_widget:
            parts.append(str(from_widget))
    except PlaywrightError:
        pass
    for selector in (
        ".iti__selected-dial-code",
        ".iti__selected-country",
        ".iti__selected-flag",
        "[aria-label*='Change country']",
        ".iti__country.iti__active",
    ):
        node = page.locator(selector)
        if node.count() == 0:
            continue
        parts.append(node.first.get_attribute("aria-label") or "")
        parts.append(node.first.get_attribute("data-dial-code") or "")
        try:
            parts.append(node.first.inner_text())
        except PlaywrightError:
            pass
    return extract_calling_code(" ".join(part for part in parts if part))


def _composed_phone_value(page: Page, locator: Locator) -> str | None:
    code = _widget_calling_code(page, locator)
    national = _input_value(locator).strip()
    aria = ""
    toggle = page.locator(".iti__selected-country, [aria-label*='Change country']")
    if toggle.count() > 0:
        aria = toggle.first.get_attribute("aria-label") or ""
        if not aria:
            try:
                aria = toggle.first.inner_text()
            except PlaywrightError:
                aria = ""
    chunks: list[str] = []
    if aria:
        chunks.append(" ".join(aria.split()))
    elif code:
        chunks.append(f"+{code}")
    if national:
        chunks.append(national)
    if not chunks:
        return None
    return " ".join(dict.fromkeys(chunks))


def _select_phone_country(page: Page, phone_locator: Locator, country: str) -> bool:
    widget = phone_locator.evaluate(
        """el => el.closest('.iti, .intl-tel-input, [class*="iti"]') !== null"""
    )
    toggle = page.locator(
        ".iti__selected-country, .iti__selected-country-primary, "
        "button.iti__selected-country, [aria-label*='Change country']"
    )
    if not widget and toggle.count() == 0:
        return False
    try:
        if toggle.count() > 0:
            toggle.first.click(timeout=2_000)
        search = page.locator(
            ".iti__search-input, input.iti__search-input, .iti__country-list input"
        )
        if search.count() > 0:
            search.first.fill(country)
        option = page.locator(".iti__country, .iti__country-name, [data-country-code]").filter(
            has_text=re.compile(re.escape(country), re.I)
        )
        if option.count() == 0:
            page.keyboard.press("Escape")
            return False
        option.first.click()
        dial = option.first.get_attribute("data-dial-code")
        if dial and toggle.count() > 0:
            toggle.first.evaluate(
                """(el, code) => {
                    el.setAttribute('data-dial-code', code);
                    const label = el.getAttribute('aria-label') || el.textContent || '';
                    if (!label.includes('+' + code)) {
                        el.setAttribute('aria-label', (label.trim() + ' (+' + code + ')').trim());
                    }
                }""",
                dial,
            )
        react_controls.dismiss_menu(page)
        return True
    except PlaywrightError:
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        return False


def _dismiss_overlays(page: Page) -> None:
    try:
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
    except PlaywrightError:
        pass


def _clear_combobox_input(locator: Locator) -> None:
    """Best-effort wipe of a searchable combobox's typed text on a failed
    fill attempt, so leftover search text can never be mistaken later for a
    committed selection by `read_back` (which otherwise falls back to the
    raw input value when no dedicated selected-value node or chip exists).
    """
    try:
        locator.fill("", timeout=1_000)
    except PlaywrightError:
        pass


# Stable, greppable prefix for temporary bounded diagnostics on the country
# custom-combobox interaction only -- never emitted for other combobox
# fills (years/location/etc.), and never containing full DOM/HTML, only
# sanitized structural facts plus the field's own requested/typed value
# (already known to whoever configured the profile, not sensitive DOM
# content).
_COUNTRY_TRACE_PREFIX = "greenhouse_country_combo_trace"
_COUNTRY_TRACE_OPTION_LIMIT = 8
_COUNTRY_TRACE_LISTBOX_LIMIT = 4
_COUNTRY_TRACE_TEXT_LIMIT = 60

_LISTBOX_TRACE_SUMMARY_JS = """el => {
    const attrs = [];
    for (const name of el.getAttributeNames()) {
        if (name === 'id' || name === 'class' || name === 'role' || name.startsWith('aria-')) {
            attrs.push(`${name}=${(el.getAttribute(name) || '').slice(0, 40)}`);
        }
    }
    return `${el.tagName.toLowerCase()} ${attrs.join(' ')}`.trim().slice(0, 160);
}"""


def _trace_field_meta(field: DiscoveredField | None) -> str:
    if field is None:
        return "label=? id=? name=?"
    label = " ".join((field.label or "").split())[:80]
    return f"label={label!r} id={field.element_id} name={field.name}"


def _trace_normalize(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _trace_option_count(page: Page) -> int:
    try:
        options = page.get_by_role("option")
        count = options.count()
        if count == 0:
            count = page.locator("[role='option']").count()
        return count
    except PlaywrightError:
        return -1


def _trace_live_options(page: Page, limit: int = _COUNTRY_TRACE_OPTION_LIMIT) -> list[dict[str, object]]:
    try:
        options = page.get_by_role("option")
        if options.count() == 0:
            options = page.locator("[role='option']")
        count = options.count()
    except PlaywrightError:
        return []
    records: list[dict[str, object]] = []
    for index in range(min(count, limit)):
        option = options.nth(index)
        try:
            visible = option.is_visible()
        except PlaywrightError:
            visible = False
        try:
            text = " ".join((option.inner_text() or "").split())[:_COUNTRY_TRACE_TEXT_LIMIT]
        except PlaywrightError:
            text = ""
        try:
            elem_id = option.get_attribute("id") or ""
        except PlaywrightError:
            elem_id = ""
        try:
            aria_selected = option.get_attribute("aria-selected")
        except PlaywrightError:
            aria_selected = None
        records.append(
            {
                "text": text,
                "role": "option",
                "visible": visible,
                "id": elem_id,
                "aria_selected": aria_selected,
            }
        )
    return records


def _trace_listbox_summary(page: Page) -> tuple[int, list[str]]:
    try:
        listboxes = page.locator("[role='listbox']")
        total = listboxes.count()
    except PlaywrightError:
        return 0, []
    visible_count = 0
    summaries: list[str] = []
    for index in range(total):
        node = listboxes.nth(index)
        try:
            if not node.is_visible():
                continue
        except PlaywrightError:
            continue
        visible_count += 1
        if len(summaries) >= _COUNTRY_TRACE_LISTBOX_LIMIT:
            continue
        try:
            summary = node.evaluate(_LISTBOX_TRACE_SUMMARY_JS)
        except PlaywrightError:
            summary = "unknown"
        summaries.append(str(summary)[:160])
    return visible_count, summaries


def _trace_control_state(page: Page, locator: Locator) -> dict[str, object]:
    try:
        value = _input_value(locator)
    except PlaywrightError:
        value = ""
    try:
        expanded = locator.get_attribute("aria-expanded")
    except PlaywrightError:
        expanded = None
    try:
        activedescendant = locator.get_attribute("aria-activedescendant")
    except PlaywrightError:
        activedescendant = None
    try:
        selected_label = react_controls.read_selected_label(page, locator)
    except PlaywrightError:
        selected_label = None
    return {
        "value": (value or "")[:_COUNTRY_TRACE_TEXT_LIMIT],
        "aria_expanded": expanded,
        "aria_activedescendant": activedescendant,
        "selected_label": (selected_label or "")[:_COUNTRY_TRACE_TEXT_LIMIT] or None,
    }


def _fill_combobox(
    page: Page,
    locator: Locator,
    value: str,
    *,
    trace_field: DiscoveredField | None = None,
) -> bool:
    """Type into a custom searchable combobox and commit the exact live
    option, not merely the typed text.

    Option discovery/matching/click go through the shared
    `react_controls` helpers (`click_option` / `matching_option`), which
    query `[role='option']` at page scope -- never rooted under the input
    or its immediate control container -- and filter to currently visible
    elements. That is required here: Greenhouse's custom React-selects can
    render their live option menu in a portal appended elsewhere in the
    DOM (not a descendant of the combobox markup), and stale/off-screen
    option nodes left over from a previous open must not be matched.

    A single typed value is matched against a single rendered menu
    (never re-typed to force a retry): if the exact value has no live
    match, the comma-separated head (e.g. "Tashkent, Uzbekistan" ->
    "Tashkent") is tried once against that same menu, since some
    combobox options are only worded as the leading component. No blind
    Enter is ever pressed and no other option is ever substituted -- once
    resolved, only that exact live option locator is clicked (up to twice,
    to tolerate a real async-search quirk where a freshly filtered
    option's first click is swallowed), and the fill fails closed with no
    visible match or no persisted selection.
    """
    trace = trace_field is not None
    if trace:
        logger.info("%s stage=start %s", _COUNTRY_TRACE_PREFIX, _trace_field_meta(trace_field))
    click_exception: str | None = None
    try:
        locator.click(timeout=3_000)
    except PlaywrightError as exc:
        click_exception = type(exc).__name__
        try:
            locator.click(force=True, timeout=3_000)
        except PlaywrightError as exc2:
            if trace:
                logger.info(
                    "%s stage=focus %s ok=False exception=%s,force:%s",
                    _COUNTRY_TRACE_PREFIX,
                    _trace_field_meta(trace_field),
                    click_exception,
                    type(exc2).__name__,
                )
            raise
    if trace:
        logger.info(
            "%s stage=focus %s ok=True exception=%s",
            _COUNTRY_TRACE_PREFIX,
            _trace_field_meta(trace_field),
            click_exception,
        )
    try:
        locator.fill("", timeout=5_000)
        locator.press_sequentially(value, delay=20, timeout=10_000)
    except PlaywrightError:
        try:
            locator.fill(value, timeout=5_000)
        except PlaywrightError:
            _dismiss_overlays(page)
            _clear_combobox_input(locator)
            if trace:
                logger.info(
                    "%s stage=final %s committed=False readback=None reason=type_failed",
                    _COUNTRY_TRACE_PREFIX,
                    _trace_field_meta(trace_field),
                )
            return False
    if trace:
        try:
            typed_value = _input_value(locator)
        except PlaywrightError:
            typed_value = ""
        logger.info(
            "%s stage=typed %s input_value=%r",
            _COUNTRY_TRACE_PREFIX,
            _trace_field_meta(trace_field),
            (typed_value or "")[:_COUNTRY_TRACE_TEXT_LIMIT],
        )
    react_controls.wait_visible_options(page)
    if trace:
        option_count = _trace_option_count(page)
        live_options = _trace_live_options(page)
        listbox_visible_count, listbox_summaries = _trace_listbox_summary(page)
        exact_match = any(
            _trace_normalize(str(item["text"])) == _trace_normalize(value) for item in live_options
        )
        logger.info(
            "%s stage=menu %s option_count=%d live=%s listbox_visible=%d listbox=%s exact_match=%s "
            "locator_strategy=%s",
            _COUNTRY_TRACE_PREFIX,
            _trace_field_meta(trace_field),
            option_count,
            live_options,
            listbox_visible_count,
            listbox_summaries,
            exact_match,
            "get_by_role(option,visible)/locator([role=option],exact)",
        )
    option = react_controls.matching_option(page, value)
    matched_value = value
    if option is None:
        head = value.strip().split(",")[0].strip()
        if head and head.lower() != value.strip().lower():
            option = react_controls.matching_option(page, head)
            matched_value = head
    if trace:
        logger.info(
            "%s stage=match %s found=%s matched_value=%r",
            _COUNTRY_TRACE_PREFIX,
            _trace_field_meta(trace_field),
            option is not None,
            matched_value[:_COUNTRY_TRACE_TEXT_LIMIT],
        )
    if option is None:
        _dismiss_overlays(page)
        _clear_combobox_input(locator)
        if trace:
            try:
                no_match_readback = react_controls.read_selected_label(page, locator)
            except PlaywrightError:
                no_match_readback = None
            logger.info(
                "%s stage=final %s committed=False readback=%r",
                _COUNTRY_TRACE_PREFIX,
                _trace_field_meta(trace_field),
                (no_match_readback or "")[:_COUNTRY_TRACE_TEXT_LIMIT] or None,
            )
        return False
    for _click_attempt in range(2):
        click_ok = True
        click_exc_name: str | None = None
        try:
            option.click(timeout=3_000)
        except PlaywrightError as exc1:
            try:
                option.click(force=True, timeout=3_000)
            except PlaywrightError as exc2:
                click_ok = False
                click_exc_name = f"{type(exc1).__name__},force:{type(exc2).__name__}"
            else:
                click_exc_name = f"{type(exc1).__name__},force_recovered"
        if trace:
            logger.info(
                "%s stage=click attempt=%d %s ok=%s exception=%s",
                _COUNTRY_TRACE_PREFIX,
                _click_attempt,
                _trace_field_meta(trace_field),
                click_ok,
                click_exc_name,
            )
        if not click_ok:
            break
        if trace:
            state = _trace_control_state(page, locator)
            logger.info(
                "%s stage=post_click attempt=%d %s state=%s",
                _COUNTRY_TRACE_PREFIX,
                _click_attempt,
                _trace_field_meta(trace_field),
                state,
            )
        # A searchable combobox can filter its live options down to a match
        # while typing without that typing itself committing a selection,
        # and a freshly filtered option's first click can be swallowed --
        # the click above must actually persist before this counts as a
        # success, or the field can silently revert to its placeholder.
        if react_controls.wait_for_selected_label(page, locator, matched_value):
            if trace:
                try:
                    success_readback = react_controls.read_selected_label(page, locator)
                except PlaywrightError:
                    success_readback = None
                logger.info(
                    "%s stage=final %s committed=True readback=%r",
                    _COUNTRY_TRACE_PREFIX,
                    _trace_field_meta(trace_field),
                    (success_readback or "")[:_COUNTRY_TRACE_TEXT_LIMIT] or None,
                )
            _dismiss_overlays(page)
            return True
    _dismiss_overlays(page)
    _clear_combobox_input(locator)
    if trace:
        try:
            final_readback = react_controls.read_selected_label(page, locator)
        except PlaywrightError:
            final_readback = None
        logger.info(
            "%s stage=final %s committed=False readback=%r",
            _COUNTRY_TRACE_PREFIX,
            _trace_field_meta(trace_field),
            (final_readback or "")[:_COUNTRY_TRACE_TEXT_LIMIT] or None,
        )
    return False


def _fill_choice(
    page: Page,
    locator: Locator,
    field: DiscoveredField,
    value: object,
    *,
    kind: QuestionKind | None = None,
    referenced_country: str | None = None,
    resolved: dict[str, str] | None = None,
) -> bool:
    if isinstance(value, bool):
        if kind is QuestionKind.WORK_AUTHORIZATION:
            live = list(field.options or []) or _live_choice_options(page, locator)
            matched = match_work_authorization_option(value, live)
            if matched is None:
                return False
            if field.field_type == "radio":
                return _fill_radio(page, field, matched)
            return react_controls.select_single_option(page, locator, matched)
        if kind is QuestionKind.VISA_SPONSORSHIP:
            live = list(field.options or []) or _live_choice_options(page, locator)
            matched = match_sponsorship_option(value, live, referenced_country)
            if matched is None:
                return False
            if field.field_type == "radio":
                return _fill_radio(page, field, matched)
            return react_controls.select_single_option(page, locator, matched)
        if field.field_type == "radio":
            return _fill_radio(page, field, value)
        return react_controls.select_yes_no(page, locator, value, field.options)
    wanted = _choice_label(value) if not isinstance(value, str) else value
    if field.field_type == "radio":
        return _fill_radio(page, field, wanted)
    if field.field_type == "select" or _is_native_select(locator):
        if kind is QuestionKind.WORK_AUTHORIZATION:
            matched = match_work_authorization_option(
                _semantic_bool(wanted) if _semantic_bool(wanted) is not None else False,
                field.options,
            )
            if matched is None:
                return False
            return react_controls.select_single_option(page, locator, matched)
        return _fill_select(page, locator, wanted, field.options)
    live = react_controls.open_menu(page, locator)
    match = wanted
    if live:
        matched = _live_choice_match(wanted, live, kind, referenced_country)
        if kind is QuestionKind.VISA_SPONSORSHIP and matched is None and _semantic_bool(wanted) is not None:
            react_controls.dismiss_menu(page)
            return False
        if matched is None and (
            kind in (QuestionKind.AGE, QuestionKind.SENSITIVE)
            or (kind is QuestionKind.RELOCATION and wanted in {ALREADY_LOCATED_CHOICE, WOULD_RELOCATE_CHOICE})
        ):
            # `wanted` is a semantic intent/sentinel here (never real option
            # text -- see `_age_decline_value` / `_sensitive_decline_value` /
            # the located-or-relocate branch of `map_question`), so it must
            # never be clicked as a literal label when the live menu has no
            # matching option.
            react_controls.dismiss_menu(page)
            return False
        match = matched or wanted
    react_controls.dismiss_menu(page)
    # `wanted` here is the discovery-time value (e.g. GENDER's non-disclosure
    # placeholder, chosen before any live option text is known -- see
    # `_gender_value`). `match` is what the live open-menu search actually
    # resolved and is about to click, which is often worded differently on
    # a real site. Surface the divergence so the caller can confirm against
    # what was truly selected instead of the stale placeholder.
    if resolved is not None and match != wanted:
        resolved["label"] = match
    return react_controls.select_single_option(page, locator, match)


def _describe_choice_interaction(
    locator: Locator,
    field: DiscoveredField,
    kind: QuestionKind | None,
    ok: bool,
) -> dict[str, object]:
    """Sanitized structural facts about a menu-choice fill attempt, for
    diagnostics only. Never includes option text, the resolved/selected
    value, or any candidate/profile data.
    """
    try:
        control_tag = locator.evaluate("el => el.tagName.toLowerCase()")
    except PlaywrightError:
        control_tag = "unknown"
    control_role = (locator.get_attribute("role") or "").lower() or "none"
    branch = "native_select" if _is_native_select(locator) else "custom_react_select"
    return {
        "control_tag": control_tag,
        "control_role": control_role,
        "branch": branch,
        "kind": kind.value if kind is not None else None,
        "option_count": len(field.options or []),
        "fill_reported_ok": ok,
    }


def _live_choice_match(
    wanted: str,
    live: list[str],
    kind: QuestionKind | None,
    referenced_country: str | None = None,
) -> str | None:
    if kind is QuestionKind.APPLICATION_SOURCE:
        return match_application_source(
            live,
            [wanted, "Company Website", "Careers Page", "LinkedIn", "Other"],
        )
    if kind is QuestionKind.GENDER:
        return match_prefer_not_to_disclose_gender(live) or match_gender_option(wanted, live)
    if kind is QuestionKind.ACADEMIC_LEVEL:
        return match_academic_option(wanted, live)
    if kind in (QuestionKind.AGE, QuestionKind.SENSITIVE):
        # AGE/SENSITIVE never carry any wanted text other than a decline
        # intent (see `_age_decline_value` / `_sensitive_decline_value`), so
        # the live menu is always searched for the explicit decline option
        # regardless of what `wanted` holds.
        return match_decline_to_answer_option(live)
    if kind is QuestionKind.RELOCATION and wanted in {ALREADY_LOCATED_CHOICE, WOULD_RELOCATE_CHOICE}:
        return match_located_or_relocate_option(wanted, live)
    if kind is QuestionKind.VISA_SPONSORSHIP:
        semantic = _semantic_bool(wanted)
        if semantic is None:
            return match_option(wanted, live)
        return match_sponsorship_option(semantic, live, referenced_country)
    if kind is QuestionKind.WORK_AUTHORIZATION:
        semantic = _semantic_bool(wanted)
        if semantic is None:
            return None
        return match_work_authorization_option(semantic, live)
    semantic = _semantic_bool(wanted)
    if semantic is not None:
        from app.application.autofill.options import match_yes_no

        return match_yes_no(semantic, live)
    return match_option(wanted, live)


def _semantic_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    cleaned = str(value).strip().lower()
    if re.match(r"^(yes)\b", cleaned) or cleaned in {"true", "y"}:
        return True
    if re.match(r"^(no)\b", cleaned) or cleaned in {"false", "n"}:
        return False
    return None


def _is_native_select(locator: Locator) -> bool:
    try:
        return locator.evaluate("el => el.tagName.toLowerCase() === 'select'")
    except PlaywrightError:
        return False


def _live_choice_options(page: Page, locator: Locator) -> list[str]:
    """Read real options directly from the control when discovery captured
    none -- e.g. a native `<select>` whose `<option>` elements are populated
    by JS after the initial discovery pass, or a custom React select whose
    options only exist once its menu is opened.
    """
    if _is_native_select(locator):
        try:
            options = locator.evaluate(
                "el => Array.from(el.options || []).map(opt => (opt.textContent || '').trim())"
                ".filter(Boolean)"
            )
        except PlaywrightError:
            return []
        return [str(item) for item in options] if isinstance(options, list) else []
    live = react_controls.open_menu(page, locator)
    react_controls.dismiss_menu(page)
    return live


def _fill_select(page: Page, locator: Locator, value: str | bool, options: list[str]) -> bool:
    semantic = _semantic_bool(value)
    if semantic is not None:
        return react_controls.select_yes_no(page, locator, semantic, options)
    wanted = _choice_label(value)
    labels = [item.strip() for item in options]
    if wanted not in labels and str(value) not in labels:
        lowered = {item.lower(): item for item in labels}
        match = lowered.get(wanted.lower())
        if match is None:
            return react_controls.select_single_option(page, locator, wanted)
        wanted = match
    try:
        locator.select_option(label=wanted)
        visible = react_controls.read_selected_label(page, locator) or ""
        if wanted.lower() in visible.lower() or visible.lower() in wanted.lower():
            return True
    except PlaywrightError:
        pass
    try:
        locator.select_option(value=str(value))
        return True
    except PlaywrightError:
        return react_controls.select_single_option(page, locator, wanted)


def _fill_radio(page: Page, field: DiscoveredField, value: str | bool) -> bool:
    if not field.name:
        return False
    from app.application.autofill.options import match_yes_no

    wanted = _choice_label(value)
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
        if wanted.lower() in haystack.lower() or haystack.lower().startswith(wanted.lower()):
            radio.check()
            return True
        if semantic is not None and react_controls.semantic_choice_matches(semantic, haystack):
            radio.check()
            return True
    return False


_YEARS_NATIVE_INPUT_TYPES = frozenset({"text", "number"})


def _fill_years(page: Page, locator: Locator, field: DiscoveredField, value: object) -> bool:
    raw = str(value)
    years: float | None = None
    try:
        years = float(raw.split()[0])
    except ValueError:
        years = None
    options = list(field.options)
    if field.field_type == "select":
        if years is not None:
            matched = match_years_option(years, options)
            if matched:
                return _fill_select(page, locator, matched, options)
        return _fill_select(page, locator, raw, options)
    if field.field_type in _YEARS_NATIVE_INPUT_TYPES and not _is_combobox(locator, field):
        return _fill_text(page, locator, raw)
    if years is not None:
        try:
            locator.click(timeout=3_000)
        except PlaywrightError:
            locator.click(force=True, timeout=3_000)
        visible = _visible_option_texts(page)
        matched = match_years_option(years, visible or options)
        _dismiss_overlays(page)
        if matched:
            return _fill_combobox(page, locator, matched)
    return _fill_combobox(page, locator, raw)


def _fill_academic(page: Page, locator: Locator, field: DiscoveredField, value: str) -> bool:
    options = list(field.options)
    if field.field_type == "select" or _is_native_select(locator):
        if not options:
            options = list(_select_options(locator) or [])
        matched = match_academic_option(value, options)
        if not matched:
            return False
        if not _fill_select(page, locator, matched, options):
            return False
        visible = react_controls.read_selected_label(page, locator) or ""
        return canonical_academic_level(visible) == canonical_academic_level(value)
    live = react_controls.open_menu(page, locator)
    option_pool = live or options
    react_controls.dismiss_menu(page)
    matched = match_academic_option(value, option_pool)
    if not matched:
        return False
    if not react_controls.select_single_option(page, locator, matched):
        return False
    visible = react_controls.read_selected_label(page, locator) or ""
    return canonical_academic_level(visible) == canonical_academic_level(value)


def _fill_multiselect(
    page: Page,
    locator: Locator,
    field: DiscoveredField,
    values: list[str],
    max_choices: int | None,
) -> list[str]:
    wanted = [str(item).strip() for item in values if str(item).strip()]
    if not wanted:
        return []
    live = react_controls.open_menu(page, locator)
    option_pool = live or list(field.options)
    if option_pool:
        wanted = select_listed_options(wanted, option_pool, max_choices=max_choices)
        react_controls.dismiss_menu(page)
    elif max_choices is not None and max_choices > 0:
        wanted = wanted[:max_choices]
    if not wanted:
        return []
    if not react_controls.select_multi_options(page, locator, wanted, max_choices=max_choices):
        return []
    chips = react_controls.read_selected_chips(page, locator)
    return chips if chips else wanted


def _visible_option_texts(page: Page) -> list[str]:
    try:
        page.wait_for_selector("[role='option']", timeout=2_000)
    except PlaywrightTimeoutError:
        pass
    options = page.get_by_role("option")
    texts: list[str] = []
    for index in range(options.count()):
        text = " ".join((options.nth(index).inner_text() or "").split())
        if text:
            texts.append(text)
    return texts


def _multiselect_values(page: Page, locator: Locator) -> list[str]:
    try:
        selected = locator.evaluate(
            """el => {
                if (el.tagName && el.tagName.toLowerCase() === 'select') {
                    return Array.from(el.selectedOptions || []).map(opt => (opt.textContent || '').trim()).filter(Boolean);
                }
                return [];
            }"""
        )
    except PlaywrightError:
        selected = []
    if isinstance(selected, list) and selected:
        return [str(item) for item in selected if item]
    chips = page.locator(
        "[class*='multi-value__label'], [class*='multiValue'], [class*='select__multi-value']"
    )
    texts: list[str] = []
    for index in range(chips.count()):
        text = " ".join((chips.nth(index).inner_text() or "").split())
        if text:
            texts.append(text)
    _ = locator
    return texts


def _is_cover_letter_label(label: str, name: str | None, element_id: str | None) -> bool:
    ident = " ".join(part for part in (label, name or "", element_id or "") if part).lower()
    return "cover" in ident and "letter" in ident


def _is_cover_letter_field(field: DiscoveredField) -> bool:
    return field.field_type == "cover_letter" or _is_cover_letter_label(
        field.label, field.name, field.element_id
    )


def _has_cover_letter_section(page: Page) -> bool:
    if page.get_by_role("heading", name=re.compile(r"cover letter", re.I)).count() > 0:
        return True
    if page.get_by_text(re.compile(r"^cover letter$", re.I)).count() > 0:
        return True
    return False


def _is_file_input(locator: Locator) -> bool:
    try:
        return locator.evaluate(
            "el => el.tagName && el.tagName.toLowerCase() === 'input' && (el.type || '').toLowerCase() === 'file'"
        )
    except PlaywrightError:
        return False


def _section_has_cover_letter_controls(section: Locator) -> bool:
    if _enter_manually_control(section) is not None:
        return True
    return section.locator("textarea, [contenteditable='true']").count() > 0


def _cover_letter_container(page: Page) -> Locator | None:
    heading = page.get_by_role("heading", name=re.compile(r"^cover letter$", re.I))
    if heading.count() == 0:
        heading = page.get_by_role("heading", name=re.compile(r"cover letter", re.I))
    if heading.count() == 0:
        heading = page.get_by_text(re.compile(r"^cover letter$", re.I))
    if heading.count() > 0:
        section = heading.first.locator("xpath=ancestor::section[1]")
        if section.count() > 0:
            return section.first
        fieldset = heading.first.locator("xpath=ancestor::fieldset[1]")
        if fieldset.count() > 0:
            return fieldset.first
        current = heading.first
        for _depth in range(8):
            parent = current.locator("xpath=ancestor::div[1]")
            if parent.count() == 0:
                break
            current = parent.first
            if _section_has_cover_letter_controls(current):
                return current
    for selector in ("#cover-letter-section", "#cover-letter"):
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        target = locator.first
        if _is_file_input(target):
            continue
        return target
    locator = page.locator("#cover_letter")
    if locator.count() > 0 and not _is_file_input(locator.first):
        return locator.first
    return None


def _enter_manually_control(section: Locator) -> Locator | None:
    for locator in (
        section.get_by_role("button", name=re.compile(r"enter manually", re.I)),
        section.get_by_role("link", name=re.compile(r"enter manually", re.I)),
        section.get_by_role("tab", name=re.compile(r"enter manually", re.I)),
        section.locator("button, a, [role='button'], [role='tab']").filter(
            has_text=re.compile(r"enter manually", re.I)
        ),
        section.get_by_text(re.compile(r"^\s*enter manually\s*$", re.I)),
    ):
        if locator.count() == 0:
            continue
        target = locator.first
        try:
            if not target.is_visible():
                continue
        except PlaywrightError:
            continue
        return target
    return None


def _activate_cover_letter_manual(page: Page, section: Locator) -> tuple[bool, str]:
    existing = _cover_letter_editor(page, section)
    if existing is not None:
        try:
            if existing.is_visible():
                return True, ""
        except PlaywrightError:
            pass
    button = _enter_manually_control(section)
    if button is None:
        if existing is not None:
            return True, ""
        return False, (
            "Cover letter not filled: Enter manually was not found in the Cover Letter section."
        )
    try:
        button.click(timeout=3_000)
    except PlaywrightError:
        try:
            button.click(force=True, timeout=3_000)
        except PlaywrightError:
            return False, (
                "Cover letter not filled: Enter manually was found but the click failed."
            )
    if _wait_cover_letter_editor(page, section) is None:
        return False, (
            "Cover letter not filled: Enter manually was clicked but the editor did not appear."
        )
    return True, ""


def _wait_cover_letter_editor(page: Page, section: Locator | None) -> Locator | None:
    editor = _cover_letter_editor(page, section)
    if editor is not None:
        try:
            editor.wait_for(state="visible", timeout=5_000)
            return editor
        except PlaywrightTimeoutError:
            pass
    root = section if section is not None else page.locator("body")
    try:
        root.locator("textarea:visible, [contenteditable='true']:visible").first.wait_for(
            state="visible", timeout=5_000
        )
    except PlaywrightTimeoutError:
        pass
    return _cover_letter_editor(page, section)


def _cover_letter_visible_text(editor: Locator) -> str:
    typed = _input_value(editor).strip()
    if typed:
        return typed
    try:
        return (editor.inner_text() or "").strip()
    except PlaywrightError:
        return ""


def _wait_cover_letter_text(page: Page, editor: Locator, text: str) -> bool:
    snippet = text.strip()[:40]
    try:
        page.wait_for_function(
            """(payload) => {
                const snippet = payload.snippet;
                const el = document.querySelector(payload.selector);
                if (!el) return false;
                const value = String(el.value || el.innerText || el.textContent || '').trim();
                const ready = el.getAttribute('data-react-value') || '';
                return (value && value.indexOf(snippet) !== -1) || (ready && ready.indexOf(snippet) !== -1);
            }""",
            arg={
                "snippet": snippet,
                "selector": _cover_letter_editor_selector(editor),
            },
            timeout=5_000,
        )
        return True
    except PlaywrightError:
        visible = _cover_letter_visible_text(editor)
        return bool(snippet) and snippet in visible


def _cover_letter_editor_selector(editor: Locator) -> str:
    element_id = editor.get_attribute("id") or ""
    if element_id:
        return f"#{element_id}"
    name = editor.get_attribute("name") or ""
    if name:
        return f"[name='{name}']"
    return "textarea:visible, [contenteditable='true']:visible"


def _cover_letter_editor(page: Page, section: Locator | None) -> Locator | None:
    cover_specific = (
        "#cover_letter_text",
        "textarea#cover_letter_text",
        "textarea[name*='cover_letter']",
        "textarea[id*='cover_letter']",
        "#cover_letter textarea",
        "textarea[aria-label*='cover letter' i]",
        "[contenteditable='true'][aria-label*='cover letter' i]",
    )
    generic = (
        "textarea:visible",
        "[contenteditable='true']:visible",
    )
    scopes: list[Locator] = []
    if section is not None:
        scopes.append(section)
    scopes.append(page.locator("body"))
    for root_index, root in enumerate(scopes):
        page_level = root_index == len(scopes) - 1 and section is not None
        selectors = cover_specific if page_level else cover_specific + generic
        for selector in selectors:
            locator = root.locator(selector)
            if locator.count() == 0:
                continue
            target = locator.first
            try:
                if not target.is_visible():
                    continue
            except PlaywrightError:
                continue
            element_id = (target.get_attribute("id") or "").lower()
            name = (target.get_attribute("name") or "").lower()
            if "resume" in element_id or "resume" in name:
                continue
            if selector in generic and "cover" not in element_id and "cover" not in name:
                if page_level:
                    continue
            return target
    return None


def _choice_label(value: str | bool) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _resume_is_attached(page: Page, field: DiscoveredField, filename: str | None) -> bool:
    # Scoped to the resume field's own file input (falls back to "#resume"
    # inside _file_names) rather than every file input on the page, so an
    # unrelated file input (e.g. a portfolio upload) can never be mistaken
    # for a successful resume attachment.
    names = _file_names(page, field)
    if filename and filename in names:
        return True
    if filename is None:
        return bool(names)
    if not filename:
        return False
    try:
        text = page.locator("body").inner_text(timeout=2_000)
    except PlaywrightError:
        return False
    return filename in text


def _file_names(page: Page, field: DiscoveredField) -> list[str]:
    element_id = field.element_id or "resume"
    try:
        found = page.evaluate(
            """id => {
                const exact = document.querySelector(`input[type="file"][id="${id}"]`);
                const fallback = document.querySelector('input[type="file"][id="resume"]');
                const el = exact || fallback;
                return el ? Array.from(el.files || []).map(file => file.name) : [];
            }""",
            element_id,
        )
    except PlaywrightError:
        return []
    if not isinstance(found, list):
        return []
    return [str(item) for item in found]




def _input_value(locator: Locator) -> str:
    try:
        typed = locator.input_value()
    except PlaywrightError:
        typed = ""
    if typed:
        return typed
    try:
        react = locator.get_attribute("data-react-value") or ""
    except PlaywrightError:
        react = ""
    return react


def _visible_control_value(page: Page, locator: Locator) -> str | None:
    displayed = locator.evaluate(
        """el => {
            const combo = el.closest('[role="combobox"]') || (el.getAttribute('role') === 'combobox' ? el : null);
            const root = el.closest('.select__control, [class*="select__control"], [class*="combobox"]') || el.parentElement;
            const selected = root && root.querySelector(
                '[class*="single-value"], [class*="singleValue"], [class*="value-container"]'
            );
            const iti = el.closest('.iti, .intl-tel-input');
            const flag = iti && iti.querySelector('.iti__selected-country, .iti__selected-flag, .iti__selected-country-primary');
            const parts = [
                selected && selected.textContent,
                combo && (combo.getAttribute('aria-valuetext') || ''),
                flag && flag.getAttribute('aria-label'),
            ];
            return parts.filter(Boolean).map(text => String(text).trim()).filter(Boolean).join(' ');
        }"""
    )
    typed = _input_value(locator).strip()
    chunks: list[str] = []
    if isinstance(displayed, str) and displayed.strip():
        chunks.append(" ".join(displayed.split()))
    if typed:
        chunks.append(typed)
    _ = page
    if not chunks:
        return None
    return " ".join(dict.fromkeys(chunks))
