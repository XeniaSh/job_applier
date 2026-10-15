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
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever_url import (
    is_canonical_lever_hosted_url,
    is_same_job_apply_url,
)
from app.application.autofill.models import FieldClassification
from app.application.autofill.options import match_option, match_option_exact_normalized, match_yes_no
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile

_TEXT_TYPES = frozenset({"text", "email", "tel", "url", "search", "number"})
_FORM_READY_SELECTOR = "#application-form, form.application-form, input#name, input[name='name']"

# Lever's own "Which location are you applying for?" control
# (`<select name="opportunityLocationId">`). Its options are the hiring
# team's own office choices for *this posting*, not a fact about the
# candidate, so it is recognized purely by this stable, Lever-generated
# field name -- never by the field's label or option text, which would
# otherwise read as a generic "location" question about the candidate (see
# `LeverAdapter.adjust_classified_field`).
_OPPORTUNITY_LOCATION_FIELD_NAME = "opportunityLocationId"
_OPPORTUNITY_LOCATION_SELECT_TIMEOUT_MS = 5_000
_PLACEHOLDER_OPTION_TEXTS = (
    "select",
    "please select",
    "select one",
    "select an option",
    "choose",
    "choose one",
    "choose an option",
)

# Older Lever posting templates render "Apply for this job" as an
# <a data-qa="btn-apply-top|btn-apply-bottom">. That `data-qa` attribute is
# specific to Lever's own template (unlike a generic "click the button that
# says Apply" rule), and the submit control on the application form itself
# never carries it, so this selector cannot accidentally match a submit
# button. Current Lever markup (observed live, e.g. Qonto's postings) drops
# `data-qa` entirely -- `_same_job_apply_action` below is the primary
# detection path and this selector is kept only for that older template
# shape and existing fixtures built against it.
_JOB_DETAIL_APPLY_SELECTOR = "a[data-qa='btn-apply-top'], a[data-qa='btn-apply-bottom']"

# A supervised live run showed the optional "Current company" and optional
# "Other website" fields still ending up populated even though
# `CandidateProfile.current_employer_for_autofill` always returns None (it is
# shared across every ATS, so it never discloses `employment.current_employer`
# on any optional "Current company"-shaped field -- see its docstring).
# Whatever upstream path produced a value for these two fields on that live
# run -- a future change to that shared resolver, or a generated/LLM fallback
# answer that was even misclassified into the wrong semantic `kind` -- this is
# the final boundary before a Playwright write, so it is the one place that
# can make the "never write a value this adapter did not itself trust"
# guarantee hold regardless of how the value was produced upstream or what
# `classified.kind` says. Recognition therefore happens on the discovered
# field's own identity, never on `classified.kind` alone:
#
# - "org" and "urls[Other]" are Lever's own stable `name` attributes for the
#   current-employer and "Other website" custom-url fields, present whenever
#   a posting includes them, independent of label wording.
# - The normalized-label fallback below is deliberately an exact, narrow
#   allowlist (not the classifier's broader substring matching) so it cannot
#   suppress an unrelated optional website field (e.g. "Personal website",
#   "Portfolio URL").
#
# "Other website" has no configured profile fact at all and stays blocked
# unconditionally whenever optional, exactly as before. "Current company" is
# the one exception: `adjust_classified_field`/`_adjust_current_company` below
# opts this adapter's own stable "org" field back in, but only from the
# explicit `employment.current_employer` fact -- never a previous-employer
# inference, never a generated/LLM value -- so the guard here still only lets
# that one trusted case through: `classified.kind` must be the real
# `CURRENT_EMPLOYER` kind (not misclassified), `classified.generated` must be
# False (not an LLM/generated fallback that merely landed on that kind), and
# `classified.value` must equal the exact value `adjust_classified_field`
# itself read from that explicit fact and cached on the instance -- so a
# correctly-classified, non-generated field carrying some other, wrong value
# (e.g. a hypothetical future resolver path that lands on this same
# kind/generated combination without actually reading the explicit fact)
# still cannot slip through on kind/generated alone.
#
# This is intentionally narrow: only these two fields, and only when the
# field is optional. Required fields (e.g. a required "Website" a posting
# could theoretically add) are unaffected, and no other optional field is
# touched.
_FINAL_WRITE_BLOCKED_NAMES: dict[str, str] = {
    "org": "current_company",
    "urls[Other]": "other_website",
}
_FINAL_WRITE_BLOCKED_LABELS: dict[str, str] = {
    "current company": "current_company",
    "other website": "other_website",
}

logger = logging.getLogger(__name__)


def _final_write_normalized_label(field: DiscoveredField) -> str | None:
    """The normalized diagnostic token for the two known live-failure optional
    fields, recognized from the field's own stable Lever `name` or its exact
    normalized label -- never from `classified.kind`, which a classifier bug
    or LLM fallback path could get wrong for this exact field.
    """
    by_name = _FINAL_WRITE_BLOCKED_NAMES.get(field.name or "")
    if by_name is not None:
        return by_name
    normalized_label = " ".join(field.label.strip().lower().rstrip("*").split())
    return _FINAL_WRITE_BLOCKED_LABELS.get(normalized_label)


def _log_final_write_diagnostic(classified: ClassifiedField, *, normalized_label: str, action: str) -> None:
    """Lever-only diagnostic at the final write boundary for the two known
    live-failure fields. Logs only the normalized label token identified from
    the field itself, required flag, the *actual* `classified.kind` (even if
    it disagrees with the recognized field -- that disagreement is itself the
    signal a misclassification happened), whether the answer came from the
    deterministic resolver or a generated/LLM fallback, and the fill/skip
    action -- never the field's value, URL, name, raw page text, or any other
    candidate data. Skipped fields are logged too, so a later run can tell a
    code-level skip apart from a value that appears to have been filled by
    the browser itself (e.g. native autofill) before our code even runs.
    """
    logger.warning(
        "lever_final_write field=%s required=%s kind=%s source=%s action=%s",
        normalized_label,
        classified.field.required,
        classified.kind.value,
        "generated" if classified.generated else "resolver",
        action,
    )


def _adjust_current_company(item: ClassifiedField, profile: CandidateProfile) -> ClassifiedField:
    """Opt Lever's own stable optional "Current company" field (`name="org"`)
    back into filling, but only from the explicit `employment.current_employer`
    fact -- never a previous-employer inference, never a generated/LLM value.

    `CandidateProfile.current_employer_for_autofill` is shared across every
    ATS and always returns None (see its docstring), so the shared
    `map_question` never resolves a value for this field on its own. This is
    the one place, scoped to this single field identity, that reads the raw
    explicit fact directly -- no other ATS and no other field is affected.
    A no-op unless the shared classifier left the field unfilled (`item.fill`
    is False) and still classified it as `CURRENT_EMPLOYER`; a misclassified
    kind must never be force-corrected here. `fill_field`'s own final-write
    guard still re-checks `kind`/`generated` immediately before the actual
    Playwright write, so this hook is defense-in-depth, not the only gate.
    """
    if item.fill or item.field.required:
        return item
    if _final_write_normalized_label(item.field) != "current_company":
        return item
    if item.kind is not QuestionKind.CURRENT_EMPLOYER:
        return item
    value = profile.employment.current_employer
    if not value:
        return item
    return replace(
        item,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        generated=False,
        unresolved_reason=None,
    )


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


def _application_page_check(page: Page) -> tuple[bool, str]:
    """Same recognition logic as `is_lever_application_page`, plus a stable
    reason token for diagnostics. Kept as a single implementation so the
    logged reason can never diverge from the actual recognition result.
    """
    if page.locator("#application-form, form.application-form").count() > 0:
        return True, "application_form_selector"
    if (
        page.locator('input[name="resume"]').count() > 0
        and page.locator('input#name, input[name="name"]').count() > 0
    ):
        return True, "resume_and_name_inputs"
    url = page.url.lower()
    if "lever.co" in url and page.locator("form").count() > 0:
        return True, "lever_co_generic_form"
    return False, "no_match"


def is_lever_application_page(page: Page) -> bool:
    matched, _ = _application_page_check(page)
    return matched


def _job_detail_apply_action(page: Page) -> Locator | None:
    """The "Apply for this job" action on a Lever job-detail page, if present.

    Tries the legacy `data-qa` template first (still used by some fixtures/
    older postings). On a canonical `jobs.lever.co` page its href is validated
    the same way as the modern path -- via `_same_job_apply_action` -- so a
    legacy-shaped anchor can never carry a wrong-job or external `/apply`
    link. Non-canonical pages (e.g. local file fixtures) keep the original,
    unvalidated legacy match, since `is_same_job_apply_url` cannot resolve a
    same-job URL for those anyway.
    """
    legacy = page.locator(_JOB_DETAIL_APPLY_SELECTOR)
    if legacy.count() > 0:
        if not is_canonical_lever_hosted_url(page.url):
            return legacy.first
        validated = _same_job_apply_action(page, legacy)
        if validated is not None:
            return validated
    return _same_job_apply_action(page)


def _same_job_apply_action(page: Page, anchors: Locator | None = None) -> Locator | None:
    """An anchor whose resolved href is exactly this job's own `/apply` link.

    Only applies to a canonical `jobs.lever.co/<company>/<posting-id>` page;
    matching is done on the anchor's browser-resolved absolute URL (`el.href`),
    never on text/class/styling, so it cannot match an unrelated Lever page,
    a different job's apply link, or an external `/apply` URL.
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
    # Set by `adjust_classified_field` only when `_adjust_current_company`
    # actually opts the field back in from the explicit
    # `employment.current_employer` fact; `fill_field`'s final-write guard
    # re-checks `classified.value` against this before ever trusting a
    # non-generated `CURRENT_EMPLOYER`-kind field for this one field identity.
    _trusted_current_employer_value: str | None = None

    def recognize(self, page: Page) -> bool:
        result, reason = _application_page_check(page)
        logger.warning(
            "lever_prepare stage=form_recognition result=%s reason=%s page_host=%s",
            result,
            reason,
            _safe_host(page.url),
        )
        return result

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
        is_job_detail = is_lever_job_detail_page(page)
        logger.warning(
            "lever_prepare stage=detail_detection is_job_detail=%s page_host=%s",
            is_job_detail,
            _safe_host(page.url),
        )
        if is_job_detail:
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
        """Attempt the job-detail -> application-form transition and always log a
        navigation-stage record at the end, even when the Apply locator is absent
        or the click raises, so a single run identifies every failure stage.
        """
        pre_url = page.url
        apply_locator_count = page.locator(_JOB_DETAIL_APPLY_SELECTOR).count()
        logger.warning(
            "lever_prepare stage=apply_action apply_locator_count=%s",
            apply_locator_count,
        )
        action = _job_detail_apply_action(page)
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
                "lever_prepare stage=apply_action click_attempted=%s click_raised=%s",
                click_attempted,
                click_raised,
            )
            if click_raised is None:
                timeout = 2_000 if page.url.startswith("file:") else 15_000
                try:
                    page.wait_for_selector(_FORM_READY_SELECTOR, timeout=timeout)
                    form_ready_selector_found = True
                except PlaywrightTimeoutError:
                    form_ready_selector_found = False
        post_url = page.url
        logger.warning(
            "lever_prepare stage=navigation pre=%s post=%s url_changed=%s "
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
            raise LeverFormError("UNSUPPORTED_FORM")
        # A reused adapter instance must never carry trust from a previous
        # Prepare run into this one -- each discovery starts a fresh run, so
        # a second run with no explicit `employment.current_employer` can
        # never inherit a first run's cached trusted value.
        self._trusted_current_employer_value = None
        discovery_start = time.monotonic()
        logger.warning("lever_prepare stage=discovery_start")
        form = _application_form(page)
        fields: list[DiscoveredField] = []
        seen_radio_names: set[str] = set()
        seen_ids: set[str] = set()
        controls = form.locator("input, textarea, select")
        control_count = controls.count()
        for index in range(control_count):
            # Logged before `_discover_control`'s own per-control Playwright
            # calls (several `locator.evaluate`/`get_attribute` round-trips
            # that can each stall) -- the last index seen here is the one
            # diagnostic a hung discovery leaves behind.
            logger.warning("lever_prepare stage=discover_field index=%d control_count=%d", index, control_count)
            locator = controls.nth(index)
            field = _discover_control(page, locator, seen_radio_names)
            if field is None:
                continue
            if field.element_id:
                if field.element_id in seen_ids:
                    continue
                seen_ids.add(field.element_id)
            fields.append(field)
        logger.warning(
            "lever_prepare stage=discovery_complete field_count=%d elapsed_ms=%d",
            len(fields),
            int((time.monotonic() - discovery_start) * 1000),
        )
        return fields

    def reconcile_discovered_fields(
        self,
        page: Page,
        *,
        profile: CandidateProfile,
        fields: list[DiscoveredField],
    ) -> list[str]:
        """Clears a stale, untrusted "Current company" value left over from
        the browser's own autofill (a previous employer, typically) before
        this adapter's own field-fill loop ever runs.

        Only acts when `employment.current_employer` has no explicit value:
        when it does, the normal `adjust_classified_field` /
        `fill_field` path fills the field from that trusted fact later in
        this same run, overwriting whatever was prefilled anyway, so there
        is nothing to reconcile here. Recognized the same narrow,
        name/label-based way `fill_field`'s own final-write guard recognizes
        this one field (`_final_write_normalized_label`) -- never by
        `classified.kind`, which this hook runs ahead of classification
        entirely. Required fields, and every other field, are untouched.

        Clearing is verified via `read_back`, the same read-back this
        adapter uses everywhere else -- never assumed to have worked just
        because no exception was raised. A clear that leaves the field
        non-blank is reported as a warning for manual review rather than
        silently left showing the wrong employer.
        """
        if profile.employment.current_employer:
            return []
        warnings: list[str] = []
        for field in fields:
            if field.required:
                continue
            if _final_write_normalized_label(field) != "current_company":
                continue
            if not (field.current_value or "").strip():
                continue
            locator = _field_locator(page, field)
            try:
                locator.fill("", timeout=3_000)
            except PlaywrightError:
                pass
            if self.read_back(page, field):
                logger.warning("lever_prefill_reconcile field=current_company action=clear_failed")
                warnings.append(
                    "Current company field appears to already contain a value (likely carried "
                    "over by browser autofill) and could not be cleared automatically; please "
                    "review and clear it manually before submitting."
                )
            else:
                logger.warning("lever_prefill_reconcile field=current_company action=cleared")
        return warnings

    def observe_final_fill_plan(self, item: ClassifiedField) -> None:
        """Lever-only diagnostic hook: the service calls this for every
        classified field right after `_enrich_unresolved`, before deciding
        whether to call `fill_field`. `fill_field`'s own final-write guard
        already logs `lever_final_write` for every field it is actually
        invoked for -- including the case where it fires only to block a
        write -- so this hook is a no-op whenever `fill_field` will run
        (`item.fill=True`), which avoids a duplicate log. The one case
        `fill_field` can never observe is a field the service has already
        classified as unfillable (`item.fill=False`, e.g. "Current company"
        when no explicit `employment.current_employer` fact resolved it):
        the service never calls `fill_field` for it at all, so without this
        hook that skip would go silently unlogged. This hook exists only to
        close that gap.
        """
        if item.fill:
            return
        normalized_label = _final_write_normalized_label(item.field)
        if normalized_label is None:
            return
        _log_final_write_diagnostic(item, normalized_label=normalized_label, action="skip")

    def fill_field(self, page: Page, classified: ClassifiedField) -> bool:
        self.last_multiselect_selected = None
        final_write_label = _final_write_normalized_label(classified.field)
        if final_write_label is not None:
            if classified.field.required:
                blocked = False
            elif final_write_label == "current_company":
                # The one trusted exception: `adjust_classified_field` only
                # ever sets this exact kind/generated combination -- and
                # caches the value -- from the explicit
                # `employment.current_employer` fact, so re-checking kind,
                # generated, and the cached value here (rather than trusting
                # `item.fill` alone) still blocks a generated/LLM value, a
                # misclassified field that merely landed on `fill=True` some
                # other way, or a correctly-classified, non-generated field
                # that nonetheless carries a value other than the one this
                # adapter itself read from the explicit fact.
                blocked = not (
                    classified.kind is QuestionKind.CURRENT_EMPLOYER
                    and not classified.generated
                    and classified.value is not None
                    and classified.value == self._trusted_current_employer_value
                )
            else:
                # "Other website" (and any other field this allowlist names)
                # has no configured profile fact at all; stays blocked
                # unconditionally whenever optional.
                blocked = True
            _log_final_write_diagnostic(classified, normalized_label=final_write_label, action="skip" if blocked else "fill")
            if blocked:
                return False
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
                if field.name == _OPPORTUNITY_LOCATION_FIELD_NAME:
                    # `adjust_classified_field` only ever lets this control
                    # reach `fill_field` with an exact, discovered option
                    # label, so `select_option` should resolve almost
                    # immediately -- a short, explicit timeout bounds that in
                    # case it does not, instead of falling back to
                    # Playwright's default 30s wait (twice, once per
                    # `_fill_and_confirm` attempt). This is a dedicated path,
                    # not a change to `_fill_select`'s own shared behavior --
                    # every other Lever select keeps Playwright's default.
                    return _fill_opportunity_location_select(locator, classified.value)
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
            visible = _radio_option_label(checked.first)
            value = checked.first.get_attribute("value")
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

    def adjust_classified_field(
        self,
        item: ClassifiedField,
        *,
        profile: CandidateProfile,
        vacancy: ResolvedVacancy,
    ) -> ClassifiedField:
        """Lever-only narrow post-classification adjustment.

        `AutofillService` calls this (when present) right after the shared
        `classifier.classify_field` pass, before `_enrich_unresolved`. Two
        controls are ever touched, each independently and narrowly scoped:

        - `opportunityLocationId` -- Lever's own "Which location are you
          applying for?" select, whose options are the hiring team's office
          choices for *this posting*, not a fact about the candidate. The
          shared classifier can map this control to `LOCATION` (via its
          generic "location"-worded label, filling the candidate's own
          residence -- a value absent from every real option) or to an
          unresolved `QUESTION_OVERRIDE`; both are replaced here.

          The only safe source of an answer is an explicit
          `application_policy.question_overrides` string that matches
          exactly one real, non-placeholder discovered option (case/
          whitespace/punctuation differences tolerated, never a fuzzy/
          partial match). Anything else -- no override, a boolean override,
          no match, or an ambiguous match -- is forced to `UNSUPPORTED`:
          `AutofillService`'s existing `UNSUPPORTED` branch skips the field
          before any fill attempt (even if generic answer-generation
          enrichment would otherwise re-enable `fill`), so this can never
          reach `fill_field` with a value absent from the live control.

        - "Current company" (`name="org"`) -- see `_adjust_current_company`.
          Caches the exact value read from the explicit
          `employment.current_employer` fact whenever that adjustment
          actually fires, so `fill_field`'s final-write guard can later tie
          its trust of a `CURRENT_EMPLOYER`-kind, non-generated field to
          this specific value, not just to the kind/generated combination.
        """
        if _is_opportunity_location_select(item.field):
            matched = _matched_opportunity_location_override(item.field, profile)
            if matched is not None:
                return replace(
                    item,
                    classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                    kind=QuestionKind.QUESTION_OVERRIDE,
                    value=matched,
                    fill=True,
                    generated=False,
                    unresolved_reason=None,
                )
            return replace(
                item,
                classification=FieldClassification.UNSUPPORTED,
                kind=QuestionKind.QUESTION_OVERRIDE,
                value=None,
                fill=False,
                generated=False,
                unresolved_reason=(
                    "Lever application-location choice requires an explicit "
                    "application_policy.question_overrides string answer that exactly matches "
                    "one discovered, non-placeholder option; never inferred from current "
                    "residence, relocation willingness, or fuzzy option matching"
                ),
            )
        adjusted = _adjust_current_company(item, profile)
        if adjusted is not item:
            self._trusted_current_employer_value = adjusted.value
        return adjusted


def _is_opportunity_location_select(field: DiscoveredField) -> bool:
    return field.name == _OPPORTUNITY_LOCATION_FIELD_NAME and field.field_type == "select"


def _is_placeholder_option(option: str) -> bool:
    return any(match_option_exact_normalized(text, [option]) is not None for text in _PLACEHOLDER_OPTION_TEXTS)


def _matched_opportunity_location_override(field: DiscoveredField, profile: CandidateProfile) -> str | None:
    override = profile.question_override_answer(field.label)
    if not isinstance(override, str):
        return None
    # A list, not a set: two distinct `<option>` elements with identical
    # label text (e.g. two offices both rendered "Buenos Aires") are two
    # real, separately selectable choices -- deduplicating by label would
    # silently accept an ambiguous match as if only one had matched.
    matches: list[str] = []
    for option in field.options:
        if not option or _is_placeholder_option(option):
            continue
        matched = match_option_exact_normalized(override, [option])
        if matched is not None:
            matches.append(matched)
    if len(matches) != 1:
        return None
    return matches[0]


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


def _radio_option_label(radio: Locator) -> str:
    """Visible option text: a wrapping `<label>` first, then `label[for=id]`.

    Lever renders custom-question option groups both ways depending on
    template/posting; without the `label[for]` fallback, a non-wrapped radio
    would read back its raw `value` attribute (which is not always the
    visible text) instead of the option a human actually sees and picks.
    """
    try:
        text = radio.evaluate(_RADIO_OPTION_LABEL_JS)
    except PlaywrightError:
        return ""
    return " ".join(str(text or "").split())


def _radio_options(page: Page, name: str) -> list[str]:
    radios = page.locator(f'input[type="radio"][name="{name}"]')
    options: list[str] = []
    for index in range(radios.count()):
        radio = radios.nth(index)
        value = _radio_option_label(radio) or radio.get_attribute("value")
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


def _fill_opportunity_location_select(locator: Locator, value: object) -> bool:
    """Fill the `opportunityLocationId` control via a native, single
    `select_option(label=...)` call with a bounded secondary timeout.

    `value` is always the exact, discovered option label already verified
    by `LeverAdapter.adjust_classified_field` -- never a semantic yes/no or
    a fuzzy/partial match -- so this never shares `_fill_select`'s
    multi-attempt fallback logic.
    """
    try:
        locator.select_option(label=str(value), timeout=_OPPORTUNITY_LOCATION_SELECT_TIMEOUT_MS)
        return True
    except PlaywrightError:
        return False


def _fill_radio(page: Page, field: DiscoveredField, value: object) -> bool:
    if not field.name:
        return False
    if isinstance(value, bool):
        # A raw boolean (e.g. work authorization) has no pre-resolved option
        # text yet; map it to the visible Yes/No-style option here.
        wanted = match_yes_no(value, field.options) or ("Yes" if value else "No")
    else:
        # A string value (e.g. from `_mapped_choice`/`match_sponsorship_option`)
        # is already the exact intended option text. Re-deriving it from
        # `_semantic_bool` here would discard that choice and fall back to
        # the first "Yes"/"No"-prefixed option by list position -- wrong
        # whenever a question offers more than one such option (e.g. sponsorship
        # "Yes, want to relocate" vs "Yes, already relocated").
        wanted = str(value)
    radios = page.locator(f'input[type="radio"][name="{field.name}"]')
    for index in range(radios.count()):
        radio = radios.nth(index)
        option_value = (radio.get_attribute("value") or "").strip()
        label_text = _radio_option_label(radio)
        haystack = f"{option_value} {label_text}".strip()
        if wanted.lower() == option_value.lower() or wanted.lower() == label_text.lower():
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
