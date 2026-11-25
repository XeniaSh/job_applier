"""SmartRecruiters OneClick adapter, UNREGISTERED (Stages 1 and opt-in 2).

Scope (see docs/smartrecruiters-stage1.md and the inspection report): safe
public-posting -> application-link navigation, hydrated page-one discovery
through open shadow roots (native controls only), supported identity/email/
residence-city/phone fills with committed read-back, and the Resume upload with
local byte-digest proof. Stage 2 explicitly enables bounded Next/Back and
Screening, with employer semantics separated from DOM interaction. There is
no Submit/import interaction. Exceptions and logs carry fixed tokens only: never candidate
values, filenames/paths, queries or raw page text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import AutofillFieldResult, AutofillResult, AutofillStatus, FieldClassification
from app.application.autofill.smartrecruiters_screening import ScreeningControls
from app.application.autofill.wise_screening import map_wise_screening, wise_screening_inventory_complete
from app.application.autofill.options import match_option_exact_normalized
from app.application.autofill.phone import (
    digits_only,
    international_phone_digits,
    resolve_dial_option,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.smartrecruiters_url import (
    SUPPORTED_COMPANIES,
    ApplicationRef,
    PostingRef,
    parse_application_url,
    parse_posting_url,
    same_company_application_ref,
)
from app.application.candidate_profile import CandidateProfile

logger = logging.getLogger(__name__)


class SmartRecruitersFormError(Exception):
    """Raised when a SmartRecruiters page cannot be used. Messages are fixed,
    sanitized tokens (never page text, URLs with queries or candidate data)."""


PARTIAL_STAGE_REASON = (
    "SmartRecruiters Stage 1 prepared page one only: the Screening stage was not inspected or "
    "filled, so the whole form is NOT prepared. Resume proof is UI-only (local byte digest plus "
    "a settled file row; server acknowledgement was not observed). Review every field manually."
)

_ROOT = "oc-oneclick-form"
_APPLY_LINK_RE = re.compile(r"^\s*(i'?m interested|i’m interested|apply( now)?)\s*$", re.IGNORECASE)
_CHALLENGE_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='challenges.cloudflare']",
    ".g-recaptcha",
    "#challenge-form",
)
_CHALLENGE_TITLE_RE = re.compile(r"just a moment|verify you are human|attention required|access denied", re.I)

# key -> (closest data-test hook, native control selector under it, default label, field_type)
_KNOWN: dict[str, tuple[str, str, str, str]] = {
    "firstName": ("personal-info-first-name-input", "input", "First name", "text"),
    "lastName": ("personal-info-last-name-input", "input", "Last name", "text"),
    "email": ("personal-info-email-input", "input", "Email", "email"),
    "emailConfirmation": ("personal-info-email-confirm-input", "input", "Confirm your email", "email"),
    "location": ("personal-info-location", 'input[role="combobox"]', "City", "combobox_location"),
    "phone": ("personal-info-phone", 'input[type="tel"]', "Phone number", "tel"),
    "linkedIn": ("web-profiles-linkedin", "input", "LinkedIn", "text"),
    "facebook": ("web-profiles-facebook", "input", "Facebook", "text"),
    "twitter": ("web-profiles-twitter", "input", "X (fka Twitter)", "text"),
    "website": ("web-profiles-website", "input", "Website", "text"),
    "message": (
        "hiring-manager-message-text",
        "textarea",
        "Let the company know about your interest working there",
        "textarea",
    ),
    "resume": ("resume-upload", 'input[type="file"]', "Resume", "file"),
}
_TEXT_KEYS = frozenset({"firstName", "lastName", "email", "emailConfirmation", "linkedIn"})
_DIAL_KEY = "countryCode"
# Only these recognised controls, and only with the generic classifier's
# expected kind, are ever filled. Facebook/X/website/message, every unknown,
# duplicated or history control stays manual (UNSUPPORTED).
_EXPECTED_KINDS = {
    "firstName": QuestionKind.FIRST_NAME,
    "lastName": QuestionKind.LAST_NAME,
    "email": QuestionKind.EMAIL,
    "location": QuestionKind.LOCATION,
    "phone": QuestionKind.PHONE,
    "linkedIn": QuestionKind.LINKEDIN,
    _DIAL_KEY: QuestionKind.PHONE_DIAL_COUNTRY,
}
_UNSUPPORTED_REASON = "SmartRecruiters Stage 1 does not fill this control; complete it manually."
_PHONE_FORMAT_RE = re.compile(r"^[0-9 ()\-]*$")
_DIAL_BUTTON = 'oc-phone-number button[role="combobox"][aria-label="Country code"]'
_DIAL_SEARCH = 'oc-phone-number spl-dropdown-search input[role="combobox"]'
_DIAL_OPTIONS = 'oc-phone-number [role="listbox"] [role="option"]'
_DIAL_FLAG = 'oc-phone-number [slot="triggerPrefix"] spl-country-flag'
_DIAL_CODE = 'oc-phone-number [slot="triggerPrefix"] spl-typography-body'
_PHONE_INPUT = 'oc-phone-number [data-test="personal-info-phone"] input[type="tel"]'
_CITY = '[data-test="personal-info-location"]'

_DEEP = """
const deepAll = (root, sel) => {
  const out = [];
  const walk = (node) => {
    for (const el of node.querySelectorAll('*')) {
      if (el.matches(sel)) out.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  if (root.shadowRoot) walk(root.shadowRoot);
  walk(root);
  return out;
};
const parentOf = (el) => el.parentElement || (el.getRootNode() && el.getRootNode().host) || null;
const closest = (el, sel) => { for (let p = el; p; p = parentOf(p)) { if (p.matches && p.matches(sel)) return p; } return null; };
"""

_SNAPSHOT_JS = (
    "(args) => { const rules = Array.isArray(args) ? args : args.rules; const includeHidden = !Array.isArray(args) && args.includeHidden;"
    + _DEEP
    + """
  const rootHit = deepAll(document, 'oc-oneclick-form');
  if (rootHit.length !== 1) return null;
  const excluded = 'oc-easy-apply, [data-test="apply-with-resume-container"], spl-dropdown-search, '
    + 'spl-calendar, [role="dialog"]';
  const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const labelOf = (el) => {
    let label = el.getAttribute('aria-label') || '';
    const rn = el.getRootNode();
    if (!label && el.id && rn.querySelector) {
      const l = rn.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) label = l.textContent || '';
    }
    for (let r = rn, i = 0; !label && r && r.host && i < 3; r = r.host.getRootNode(), i++) {
      label = r.host.getAttribute('label') || '';
    }
    return label.replace(/\\s+/g, ' ').trim().slice(0, 120);
  };
  const hostRequired = (el) => {
    for (let r = el.getRootNode(), i = 0; r && r.host && i < 3; r = r.host.getRootNode(), i++) {
      if (r.host.getAttribute('aria-required') === 'true' || r.host.hasAttribute('required')) return true;
    }
    return false;
  };
  const out = [];
  const controls = deepAll(rootHit[0], 'input, textarea, select, button[role="combobox"]');
  for (const el of controls) {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || closest(el, excluded)) continue;
    if (type !== 'file' && !visible(el) && !includeHidden) continue;
    const label = labelOf(el);
    let key = null;
    for (const [k, hook, tag, typeRule] of rules) {
      if (el.tagName.toLowerCase() === tag && closest(el, '[data-test="' + hook + '"]')
          && (!typeRule || type === typeRule || (typeRule === 'combobox' && el.getAttribute('role') === 'combobox')
              || (typeRule === 'tel' && type === 'tel') || (typeRule === 'texty' && ['', 'text', 'email', 'url'].includes(type)))) {
        key = k; break;
      }
    }
    if (!key && el.tagName === 'BUTTON' && label === 'Country code' && closest(el, 'oc-phone-number')) key = 'countryCode';
    const resumeSection = closest(el, 'oc-resume-upload');
    const sectionMark = !!(resumeSection && deepAll(resumeSection, '[data-test="section-required-mark"]').length);
    const section = closest(el, '[data-test="resume-upload"], oc-experience, oc-education, oc-personal-information, oc-web, oc-hiring-manager-message');
    out.push({
      key, tag: el.tagName.toLowerCase(), type, role: el.getAttribute('role') || '', label,
      autocomplete: el.getAttribute('autocomplete') || '',
      required: el.getAttribute('aria-required') === 'true' || el.required === true
        || el.hasAttribute('required') || hostRequired(el) || sectionMark || /\\*\\s*$/.test(label),
      value: type === 'file' ? '' : (el.value || ''),
      section: section ? section.tagName.toLowerCase() : '',
    });
  }
  return out;
}"""
)

_RESUME_STATE_JS = (
    "() => {"
    + _DEEP
    + """
  const forms = deepAll(document, 'oc-oneclick-form');
  if (forms.length !== 1) return null;
  const hosts = deepAll(forms[0], '[data-test="resume-upload"]');
  if (hosts.length !== 1) return null;
  const host = hosts[0];
  const inputs = deepAll(host, 'input[type="file"]');
  const rows = deepAll(host, '.c-spl-file-list-item').map((row) => ({
    name: ((deepAll(row, '.c-spl-file-list-item-name')[0] || {}).textContent || '').trim(),
    stale: row.__srStale === true, own: row.__srOwn === true,
  }));
  const alerts = deepAll(host, '[role="alert"]').filter((el) => (el.textContent || '').trim().length > 0);
  return {
    inputs: inputs.length,
    inputDisabled: inputs.length === 1 ? inputs[0].disabled : null,
    inputMultiple: inputs.length === 1 ? inputs[0].multiple : null,
    rows,
    deletes: deepAll(host, 'button').map((b) => b.getAttribute('aria-label') || '').filter((t) => t.startsWith('Delete file')),
    pending: deepAll(host, '[role="progressbar"], [aria-busy="true"], spl-spinner, spl-loader, '
      + '[class*="loading" i], [class*="uploading" i], [class*="progress" i]').length > 0,
    error: alerts.length > 0 || deepAll(host, '[aria-invalid="true"], [class*="error" i]').length > 0,
    uploads: (window.__srUploads || []).map((u) => u.digest),
  };
}"""
)

_MARK_ROWS_JS = (
    "(mode) => {"
    + _DEEP
    + """
  const forms = deepAll(document, 'oc-oneclick-form');
  if (forms.length !== 1) return false;
  const hosts = deepAll(forms[0], '[data-test="resume-upload"]');
  if (hosts.length !== 1) return false;
  for (const row of deepAll(hosts[0], '.c-spl-file-list-item')) {
    if (mode === 'stale') row.__srStale = true; else row.__srOwn = true;
  }
  return true;
}"""
)

# Installed at most once per input element. Every `change` appends one entry
# to `window.__srUploads` (digest filled in asynchronously from the selected
# File's own bytes), so the number of writes and each write's digest are
# independent of any earlier attempt.
_HOOK_WRITES_JS = """
(el) => {
  window.__srUploads = window.__srUploads || [];
  if (el.__srHooked === true) return;
  el.__srHooked = true;
  el.addEventListener('change', () => {
    const entry = {digest: null};
    window.__srUploads.push(entry);
    const file = el.files && el.files[0];
    if (!file || !(window.crypto && window.crypto.subtle)) { entry.digest = 'unavailable'; return; }
    file.arrayBuffer().then((buf) => window.crypto.subtle.digest('SHA-256', buf)).then((digest) => {
      const hex = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
      entry.digest = hex + ':' + file.size;
    }).catch(() => { entry.digest = 'unavailable'; });
  });
}"""

_APPLY_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]')).map((a, index) => ({
  index, visible: a.getClientRects().length > 0 && getComputedStyle(a).visibility !== 'hidden',
  text: (a.textContent || '').trim().slice(0, 60), href: a.href, target: a.getAttribute('target') || ''}))
  .filter((item) => item.visible)
"""

_LINK_STILL_JS = """
(a, href) => a.href === href && a.getClientRects().length > 0 && getComputedStyle(a).visibility !== 'hidden'
  && ['', '_self'].includes(a.getAttribute('target') || '')
"""


def _norm(text: str) -> str:
    return " ".join((text or "").split())


@dataclass(frozen=True)
class _UploadProof:
    page: object
    url: str
    path: Path
    digest: str
    name: str
    seq: int  # total number of file writes observed once this attempt's write landed


class SmartRecruitersAdapter:
    """OneClick adapter. One instance serves one Prepare run; Stage 2 is opt-in."""

    # Independent of global configuration and completeness: Prepare never submits.
    allow_auto_submit = False

    def __init__(
        self,
        *,
        expected_posting_id: str | None = None,
        hydration_timeout_ms: int = 15_000,
        navigation_timeout_ms: int = 15_000,
        settle_timeout_ms: int = 4_000,
        upload_timeout_ms: int = 15_000,
        poll_ms: int = 150,
        stable_samples: int = 3,
        prepare_screening: bool = False,
    ) -> None:
        self._expected_posting_id = expected_posting_id
        self._hydration_timeout_ms = hydration_timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._settle_timeout_ms = settle_timeout_ms
        self._upload_timeout_ms = upload_timeout_ms
        self._poll_ms = poll_ms
        self._stable_samples = stable_samples
        self._prepare_screening = prepare_screening
        self._reset()

    def _reset(self) -> None:
        """Forget every per-run proof so a reused instance can never inherit one."""
        self._publication: ApplicationRef | None = None
        self._writes: dict[str, tuple[object, str, str]] = {}
        self._city_proof: tuple[object, str, str] | None = None
        # (page, url, wanted digits, code, country name)
        self._dial_proof: tuple[object, str, str, str, str] | None = None
        self._upload_proof: _UploadProof | None = None
        self._screening = ScreeningControls(self._stable, self._screening_ok)
        self.last_choice_selected = None
        self._stage_complete = False

    # ------------------------------------------------------------- recognition
    def partial_stage_reason(self) -> str | None:
        return None if self._stage_complete else PARTIAL_STAGE_REASON

    def detect_challenge(self, page: Page) -> str | None:
        try:
            if _CHALLENGE_TITLE_RE.search(page.title() or ""):
                return "challenge page"
            for selector in _CHALLENGE_SELECTORS:
                if page.locator(selector).count():
                    return "captcha"
        except PlaywrightError:
            return None
        return None

    def recognize(self, page: Page) -> bool:
        ref = parse_application_url(page.url)
        if ref is not None and ref.screening:
            return self._screening_ok(page) and self._screening.hydrated(page)
        if ref is None or ref.screening:
            return False
        if self._publication is not None and ref != self._publication:
            return False
        return self._hydrated(page)

    def _hydrated(self, page: Page) -> bool:
        try:
            snapshot = page.evaluate(_SNAPSHOT_JS, self._rules())
        except PlaywrightError:
            return False
        if not snapshot:
            return False
        labelled = {item["key"] for item in snapshot if item["key"] and item["label"]}
        if not {"firstName", "lastName", "email", "emailConfirmation"} <= labelled:
            return False
        if not any(item["key"] == "resume" for item in snapshot):
            return False
        try:
            return (
                page.locator("oc-nav-first-page").count() == 1
                and page.locator('[data-test="footer-next"]').count() == 1
            )
        except PlaywrightError:
            return False

    @staticmethod
    def _rules() -> list[list[str]]:
        typed = {"text": "texty", "email": "texty", "tel": "tel", "combobox_location": "combobox", "file": "file", "textarea": ""}
        return [[key, hook, selector.split("[")[0], typed[ftype]] for key, (hook, selector, _l, ftype) in _KNOWN.items()]

    # --------------------------------------------------------------- preparation
    def prepare_page(self, page: Page) -> None:
        """Reach the page-one application form. Only an evidenced, validated
        same-company application link is ever followed, in the same tab."""
        self._reset()
        if self.detect_challenge(page):
            return
        try:
            current = parse_application_url(page.url)
            if current is not None:
                if current.screening:
                    raise SmartRecruitersFormError("unsupported stage: screening")
                self._publication = current
            else:
                self._publication = self._navigate_from_posting(page)
            self._wait_hydrated(page)
        except PlaywrightError:
            raise SmartRecruitersFormError("page interaction failed") from None

    def _navigate_from_posting(self, page: Page) -> ApplicationRef:
        posting = parse_posting_url(page.url)
        if posting is None:
            raise SmartRecruitersFormError("unsupported page url")
        if self._expected_posting_id is not None and posting.posting_id != self._expected_posting_id:
            raise SmartRecruitersFormError("posting id mismatch")
        self._check_canonical(page, posting)
        wanted, index, href = self._apply_ref(page, posting)
        # The click is bound to the one validated anchor (same element, same
        # href, still visible/same-tab) and the posting identity is re-checked
        # immediately before it, so a link that changed meanwhile is never followed.
        link = page.locator("a[href]").nth(index)
        self._check_canonical(page, posting)
        if not link.evaluate(_LINK_STILL_JS, href) or parse_posting_url(page.url) != posting:
            raise SmartRecruitersFormError("application link changed")
        link.click(timeout=self._navigation_timeout_ms)
        try:
            page.wait_for_url(
                lambda url: parse_application_url(url) is not None,
                timeout=self._navigation_timeout_ms,
            )
        except PlaywrightError:
            raise SmartRecruitersFormError("application navigation timed out") from None
        landed = parse_application_url(page.url)
        if landed != wanted:
            raise SmartRecruitersFormError("application publication changed")
        return wanted

    def _check_canonical(self, page: Page, posting: PostingRef) -> None:
        hrefs = page.locator('link[rel="canonical"]').evaluate_all("els => els.map(e => e.href)")
        canonical = parse_posting_url(hrefs[0]) if len(hrefs) == 1 else None
        if canonical != posting:
            raise SmartRecruitersFormError("canonical posting mismatch")

    def _apply_ref(self, page: Page, posting: PostingRef) -> tuple[ApplicationRef, int, str]:
        links = [item for item in page.evaluate(_APPLY_LINKS_JS) if _APPLY_LINK_RE.match(item["text"])]
        if not links:
            raise SmartRecruitersFormError("no application link")
        refs = set()
        for item in links:
            ref = same_company_application_ref(posting, item["href"])
            if ref is None or item["target"] not in ("", "_self"):
                raise SmartRecruitersFormError("untrusted application link")
            refs.add(ref)
        if len(refs) != 1:
            raise SmartRecruitersFormError("ambiguous application links")
        return next(iter(refs)), links[0]["index"], links[0]["href"]

    def _wait_hydrated(self, page: Page) -> None:
        waited = 0
        while waited < self._hydration_timeout_ms:
            if self._hydrated(page):
                return
            page.wait_for_timeout(self._poll_ms)
            waited += self._poll_ms
        logger.warning("smartrecruiters_prepare stage=hydration_timeout")

    # ----------------------------------------------------------------- discovery
    def discover_fields(self, page: Page, *, include_hidden: bool = False) -> list[DiscoveredField]:
        if self._screening_ok(page) and not include_hidden:
            return self._screening.discover(page)
        snapshot = page.evaluate(_SNAPSHOT_JS, {"rules": self._rules(), "includeHidden": include_hidden}) or []
        counts: dict[str, int] = {}
        for item in snapshot:
            counts[item["key"] or ""] = counts.get(item["key"] or "", 0) + 1
        fields: list[DiscoveredField] = []
        unknown = 0
        for item in snapshot:
            key = item["key"]
            if key and counts[key] == 1:
                default_label, ftype = (
                    ("Country code", "phone_dial_country")
                    if key == _DIAL_KEY
                    else (_KNOWN[key][2], _KNOWN[key][3])
                )
                fields.append(
                    DiscoveredField(
                        label=item["label"] or default_label,
                        name=key,
                        field_type=ftype,
                        required=bool(item["required"]),
                        current_value=item["value"] or None,
                        autocomplete=item["autocomplete"] or None,
                        element_id=key,
                        context=item["section"],
                    )
                )
                continue
            unknown += 1
            fields.append(
                DiscoveredField(
                    label=item["label"] or f"Unlabelled {item['tag']} control",
                    name=f"unknown-{unknown}",
                    field_type="unknown",
                    required=bool(item["required"]),
                    element_id=f"unknown-{unknown}",
                    context=item["section"],
                )
            )
        logger.info("smartrecruiters_prepare stage=discovered fields=%d unknown=%d", len(fields), unknown)
        return fields

    def adjust_classified_field(
        self, item: ClassifiedField, *, profile: CandidateProfile, vacancy: object
    ) -> ClassifiedField:
        """Deterministic mapping the shared labels cannot give: the separate
        confirmation email and the Resume control (its path is always the
        service's validated selection, never a profile value). Every other
        control -- unknown, duplicated, Facebook/X/website/message, history --
        is UNSUPPORTED so neither the generic classifier's guess nor generated
        answers can ever fill it; a required one stays unresolved."""
        name = item.field.name or ""
        if name.startswith("sr:"):
            return map_wise_screening(item, profile=profile, vacancy=vacancy)
        if name == "emailConfirmation":
            if not profile.identity.email:
                return self._unsupported(item)
            return replace(
                item,
                kind=QuestionKind.EMAIL,
                value=profile.identity.email,
                fill=True,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            )
        if name == "resume":
            return replace(
                item,
                kind=QuestionKind.RESUME,
                value=str(item.value or "resume"),
                fill=True,
                classification=FieldClassification.SUPPORTED_DETERMINISTIC,
            )
        if _EXPECTED_KINDS.get(name) is item.kind and item.classification is not FieldClassification.UNSUPPORTED:
            # Missing factual profile data is manual, never generated prose.
            return item if item.fill else self._unsupported(item)
        return self._unsupported(item)

    @staticmethod
    def _unsupported(item: ClassifiedField) -> ClassifiedField:
        return replace(
            item,
            kind=QuestionKind.UNKNOWN,
            value=None,
            fill=False,
            generated=False,
            classification=FieldClassification.UNSUPPORTED,
            unresolved_reason=_UNSUPPORTED_REASON,
        )

    # ------------------------------------------------------------------- writes
    def _page_ok(self, page: Page) -> bool:
        ref = parse_application_url(page.url)
        return ref is not None and not ref.screening and ref == self._publication

    def _screening_ok(self, page: Page) -> bool:
        ref = parse_application_url(page.url)
        return bool(self._prepare_screening and ref and ref.screening
                    and replace(ref, screening=False) == self._publication)

    @staticmethod
    def _in(page: Page, selector: str):
        """`selector` scoped to the one recognised form root. Without exactly
        one root nothing matches, so a same-hook control outside the form can
        never be used as a field, dial or attachment."""
        root = page.locator(_ROOT)
        if root.count() != 1:
            return page.locator(":not(*)")
        return root.locator(selector)

    def _one(self, page: Page, key: str):
        hook, selector, _label, _type = _KNOWN[key]
        locator = self._in(page, f'[data-test="{hook}"] {selector}')
        return locator if locator.count() == 1 else None

    def fill_field(self, page: Page, classified: ClassifiedField) -> bool:
        key = classified.field.name or ""
        self.last_choice_selected = None
        try:
            if self._screening_ok(page):
                ok = self._screening.fill(page, classified)
                self.last_choice_selected = self._screening.last_choice_selected
                return ok
            if not self._page_ok(page):
                return False
            if key in _TEXT_KEYS:
                return self._fill_text(page, key, str(classified.value or ""))
            if key == "location":
                return self._fill_city(page, str(classified.value or ""))
            if key == _DIAL_KEY:
                return self._fill_dial(page, str(classified.value or ""))
            if key == "phone":
                return self._fill_phone(page, str(classified.value or ""))
        except PlaywrightError:
            logger.warning("smartrecruiters_fill stage=error key=%s", key if key in _KNOWN or key == _DIAL_KEY else "other")
        return False

    def _bound(self, page: Page, proof_page: object, proof_url: str) -> bool:
        return proof_page is page and (
            (proof_url == page.url and self._page_ok(page))
            or (self._screening_ok(page) and parse_application_url(proof_url) == self._publication)
        )

    def _fill_text(self, page: Page, key: str, value: str) -> bool:
        locator = self._one(page, key)
        self._writes.pop(key, None)
        if locator is None or not value:
            return False
        locator.click()
        locator.fill("")
        if locator.input_value() != "":
            return False
        locator.fill(value)
        locator.evaluate("el => el.blur()")
        if not self._page_ok(page):
            return False
        self._writes[key] = (page, page.url, value)
        return True

    @staticmethod
    def _own_valid(locator) -> bool:
        """The input's own validation state: not aria-invalid and natively valid."""
        return bool(
            locator.evaluate("el => el.getAttribute('aria-invalid') !== 'true' && el.checkValidity()")
        )

    def _stable(self, page: Page, sample, *, timeout_ms: int | None = None) -> bool:
        """True once `sample()` held for `stable_samples` consecutive polls
        within the bounded timeout (default: the settle timeout)."""
        limit = self._settle_timeout_ms if timeout_ms is None else timeout_ms
        stable = 0
        waited = 0
        while True:
            stable = stable + 1 if sample() else 0
            if stable >= self._stable_samples:
                return True
            if waited >= limit:
                return False
            page.wait_for_timeout(self._poll_ms)
            waited += self._poll_ms

    def _settled_options(self, page: Page, selector: str, accept) -> list[str] | None:
        """Option texts once `accept(texts)` holds and the list is unchanged for
        `stable_samples` consecutive samples; None on timeout."""
        last: list[str] | None = None
        stable = 0
        waited = 0
        while waited <= self._settle_timeout_ms:
            texts = [_norm(t) for t in self._in(page, selector).all_inner_texts()]
            stable = stable + 1 if texts == last else 1
            last = texts
            if texts and accept(texts) and stable >= self._stable_samples:
                return texts
            page.wait_for_timeout(self._poll_ms)
            waited += self._poll_ms
        return None

    def _fill_city(self, page: Page, wanted: str) -> bool:
        locator = self._one(page, "location")
        if locator is None or not wanted:
            return False
        self._city_proof = None
        locator.click()
        locator.fill("")
        locator.fill(wanted.split(",")[0].strip())
        options_selector = f'{_CITY} [role="listbox"] [role="option"]'
        texts = self._settled_options(page, options_selector, lambda t: True)
        if texts is None:
            locator.press("Tab")
            return False
        matched = match_option_exact_normalized(wanted, texts)
        indexes = [i for i, text in enumerate(texts) if matched is not None and text == matched]
        if len(indexes) != 1:
            locator.press("Tab")
            return False
        self._in(page, options_selector).nth(indexes[0]).click()
        locator.press("Tab")
        self._city_proof = (page, page.url, _norm(texts[indexes[0]]))
        return True

    def _city_committed(self, page: Page) -> str | None:
        proof = self._city_proof
        if proof is None or not self._bound(page, proof[0], proof[1]):
            return None

        def committed() -> bool:
            locator = self._one(page, "location")
            return (
                locator is not None
                and _norm(locator.input_value()) == proof[2]
                and locator.get_attribute("aria-expanded") != "true"
                and self._own_valid(locator)
                and self._in(page, f'{_CITY} button[aria-label="Clear City value"]').count() == 1
            )

        return proof[2] if self._stable(page, committed) else None

    def _dial_display(self, page: Page) -> tuple[str, str] | None:
        flag, code = self._in(page, _DIAL_FLAG), self._in(page, _DIAL_CODE)
        if flag.count() != 1 or code.count() != 1:
            return None
        name, text = _norm(flag.get_attribute("alt") or ""), _norm(code.inner_text())
        match = re.fullmatch(r"\+(\d{1,4})", text)
        return (name, match[1]) if name and match else None

    def _dial_closed_display(self, page: Page) -> tuple[str, str] | None:
        """The slotted (name, code) only while the one own selector is closed."""
        button = self._in(page, _DIAL_BUTTON)
        if button.count() != 1 or button.get_attribute("aria-expanded") == "true":
            return None
        return self._dial_display(page)

    def _fill_dial(self, page: Page, wanted: str) -> bool:
        digits = international_phone_digits(wanted)
        proof = self._dial_proof
        if (
            proof is not None
            and digits is not None
            and proof[2] == digits
            and self._bound(page, proof[0], proof[1])
            and self._dial_ok(page) == proof[3]
        ):
            return True  # already selected for this exact number (phone filled first)
        self._dial_proof = None
        button = self._in(page, _DIAL_BUTTON)
        if digits is None or button.count() != 1 or self._in(page, _PHONE_INPUT).count() != 1:
            return False
        button.click()
        full = self._settled_options(page, _DIAL_OPTIONS, lambda t: True)
        option = resolve_dial_option(full, digits) if full else None
        if option is None:
            button.click()
            return False
        self._in(page, _DIAL_SEARCH).fill(option.name)
        needle = option.name.lower()

        def filtered(texts: list[str]) -> bool:
            return len(texts) < len(full) and all(needle in t.lower() or f"+{option.code}" in t for t in texts)

        texts = self._settled_options(page, _DIAL_OPTIONS, filtered)
        exact = [i for i, text in enumerate(texts or []) if text == option.text]
        if len(exact) != 1:
            button.click()
            return False
        self._in(page, _DIAL_OPTIONS).nth(exact[0]).click()
        self._in(page, _PHONE_INPUT).focus()
        self._in(page, _PHONE_INPUT).evaluate("el => el.blur()")
        expected = (option.name, option.code)
        if not self._stable(page, lambda: self._dial_closed_display(page) == expected):
            return False
        self._dial_proof = (page, page.url, digits, option.code, option.name)
        return True

    def _dial_ok(self, page: Page) -> str | None:
        """The proven dial code iff the own selector is closed and still shows
        the proven country/code for `stable_samples` consecutive samples."""
        proof = self._dial_proof
        if proof is None or not self._bound(page, proof[0], proof[1]):
            return None
        expected = (proof[4], proof[3])
        return proof[3] if self._stable(page, lambda: self._dial_closed_display(page) == expected) else None

    def _fill_phone(self, page: Page, wanted: str) -> bool:
        """Supports both DOM orders: when the dial selector was not processed
        yet, it is selected (and proven) first for this exact number."""
        self._writes.pop("phone", None)
        digits = international_phone_digits(wanted)
        if digits is None:
            return False
        code = self._dial_ok(page)
        if code is None:
            if not self._fill_dial(page, wanted):
                return False
            code = self._dial_ok(page)
        locator = self._in(page, _PHONE_INPUT)
        if code is None or not digits.startswith(code) or locator.count() != 1:
            return False
        locator.click()
        locator.fill("")
        if locator.input_value() != "":
            return False
        locator.fill(digits[len(code):])
        locator.evaluate("el => el.blur()")
        if not self._page_ok(page):
            return False
        self._writes["phone"] = (page, page.url, wanted)
        return True

    def final_phone_dial_check(
        self,
        page: Page,
        dial_field: DiscoveredField,
        phone_field: DiscoveredField | None,
        phone_value: str | None,
    ) -> tuple[bool, bool | None]:
        _ = dial_field
        try:
            code = self._dial_ok(page)
            if code is None or not self._page_ok(page):
                return False, None
            if phone_field is None:
                return True, None
            ok = self._phone_composed(page, code) == international_phone_digits(phone_value)
            return ok, ok
        except PlaywrightError:
            return False, None

    def _phone_composed(self, page: Page, code: str) -> str | None:
        """Digits shown in the phone input, composed with the proven dial code.
        Exactly the national digits (or the full code+national digits) -- never
        a duplicated or partial code."""
        proof = self._writes.get("phone")
        locator = self._in(page, _PHONE_INPUT)
        if proof is None or not self._bound(page, proof[0], proof[1]) or locator.count() != 1:
            return None
        raw = locator.input_value()
        expected = international_phone_digits(proof[2])
        if expected is None or not expected.startswith(code) or not _PHONE_FORMAT_RE.match(raw):
            return None
        if digits_only(raw) != expected[len(code):] or not self._own_valid(locator):
            return None
        return expected

    def read_back(self, page: Page, field: DiscoveredField) -> str | None:
        key = field.name or ""
        try:
            if self._screening_ok(page) and key.startswith("sr:"):
                return self._screening.read_back(page, field)
            if not self._page_ok(page) and not self._screening_ok(page):
                return None
            if key in _TEXT_KEYS:
                proof = self._writes.get(key)
                locator = self._one(page, key)
                if proof is None or not self._bound(page, proof[0], proof[1]) or locator is None:
                    return None
                if not self._stable(page, lambda: self._text_committed(page, key, proof[2])):
                    return None
                return proof[2]
            if key == "location":
                return self._city_committed(page)
            if key == _DIAL_KEY:
                code = self._dial_ok(page)
                return f"+{code}" if code else None
            if key == "phone":
                code = self._dial_ok(page)
                composed = self._phone_composed(page, code) if code else None
                return f"+{composed}" if composed else None
            if key == "resume":
                state = self._resume_stable_state(page, self._upload_proof)
                return state if state else None
        except PlaywrightError:
            return None
        return None

    def _text_committed(self, page: Page, key: str, expected: str) -> bool:
        """Exact expected value and the input's own validation state; the
        confirmation additionally needs the email input to hold the same value
        and be valid too."""
        locator = self._one(page, key)
        if locator is None or locator.input_value() != expected or not self._own_valid(locator):
            return False
        if key != "emailConfirmation":
            return True
        email = self._one(page, "email")
        return email is not None and email.input_value() == expected and self._own_valid(email)

    # ------------------------------------------------------------------- resume
    def upload_resume(self, page: Page, resume_path: Path, field: DiscoveredField) -> bool:
        _ = field
        self._upload_proof = None
        try:
            if not self._page_ok(page):
                return False
            data = Path(resume_path).read_bytes()
            digest = f"{hashlib.sha256(data).hexdigest()}:{len(data)}"
            locator = self._one(page, "resume")
            state = page.evaluate(_RESUME_STATE_JS)
            if locator is None or not state or state["inputs"] != 1 or state["inputMultiple"]:
                return False
            if state["inputDisabled"] or state["pending"] or state["error"]:
                # A prior upload may still be in flight or failed: never write into the widget, or
                # its late settling could make this attempt's same-basename row look stable.
                logger.warning("smartrecruiters_upload stage=widget_busy")
                return False
            page.evaluate(_MARK_ROWS_JS, "stale")
            locator.evaluate(_HOOK_WRITES_JS)  # idempotent per input element
            hooked = page.evaluate(_RESUME_STATE_JS)
            if not hooked:
                return False
            before = len(hooked["uploads"])
            locator.set_input_files(str(resume_path))
            if not self._wait_digest(page, before, digest):
                logger.warning("smartrecruiters_upload stage=no_write_evidence")
                return False
            proof = _UploadProof(
                page, page.url, Path(resume_path), digest, Path(resume_path).name, before + 1
            )
            if self._resume_stable_state(page, proof, timeout_ms=self._upload_timeout_ms) is None:
                logger.warning("smartrecruiters_upload stage=completion_not_observed")
                return False
            page.evaluate(_MARK_ROWS_JS, "own")
            self._upload_proof = proof
            return True
        except (PlaywrightError, OSError):
            logger.warning("smartrecruiters_upload stage=error")
            return False

    def _wait_digest(self, page: Page, writes_before: int, digest: str) -> bool:
        """Exactly one new write landed since this attempt began and its own
        File bytes hash to `digest` (the bytes read from the selected path)."""
        waited = 0
        while waited <= self._settle_timeout_ms:
            state = page.evaluate(_RESUME_STATE_JS)
            if state and len(state["uploads"]) == writes_before + 1 and state["uploads"][-1] == digest:
                return True
            page.wait_for_timeout(self._poll_ms)
            waited += self._poll_ms
        return False

    def _resume_sample_ok(self, state: dict | None, proof: _UploadProof, *, require_own: bool) -> bool:
        if not state or state["inputs"] != 1 or state["inputDisabled"] or state["pending"] or state["error"]:
            return False
        rows = state["rows"]
        if len(rows) != 1 or rows[0]["name"] != proof.name or rows[0]["stale"]:
            return False
        if require_own and not rows[0]["own"]:
            return False
        if len(state["deletes"]) != 1 or proof.name not in state["deletes"][0]:
            return False
        # No later write (late clear/replacement) and the last write is ours.
        return len(state["uploads"]) == proof.seq and state["uploads"][-1] == proof.digest

    def _resume_stable_state(
        self, page: Page, proof: _UploadProof | None, *, timeout_ms: int | None = None
    ) -> str | None:
        """The attached filename once `stable_samples` consecutive samples all
        satisfy the full predicate (own row, delete action, enabled input, no
        pending/error, digest of the selected bytes); None otherwise."""
        if proof is None or not self._bound(page, proof.page, proof.url):
            return None
        require_own = timeout_ms is None
        waited = 0
        stable = 0
        while True:
            ok = self._resume_sample_ok(page.evaluate(_RESUME_STATE_JS), proof, require_own=require_own)
            stable = stable + 1 if ok else 0
            if stable >= self._stable_samples:
                return proof.name
            # Verification mode fails on the first bad sample; upload mode
            # polls until the bounded timeout.
            if (require_own and not ok) or (timeout_ms is not None and waited >= timeout_ms):
                return None
            page.wait_for_timeout(self._poll_ms)
            waited += self._poll_ms

    def verify_resume_attached(self, page: Page, field: DiscoveredField, filename: str) -> bool:
        _ = field
        proof = self._upload_proof
        try:
            return proof is not None and filename == proof.name and self._resume_stable_state(page, proof) == filename
        except PlaywrightError:
            return False

    # ----------------------------------------------------- bounded two-stage flow
    def _navigate_stage(self, page: Page, *, forward: bool) -> None:
        """Only observed Next/Back controls. Never infer safety from enabled/type alone."""
        if self.detect_challenge(page) or not (self._page_ok(page) if forward else self._screening_ok(page)):
            raise SmartRecruitersFormError("stage navigation refused")
        container, hook, name = (
            ("oc-nav-first-page", "footer-next", "Next") if forward
            else ("oc-nav-screening-questions", "footer-back", "Back")
        )
        nav = page.locator(container)
        if nav.count() != 1 or nav.locator(f'[data-test="{hook}"]').count() != 1:
            raise SmartRecruitersFormError("ambiguous stage navigation")
        button = nav.get_by_role("button", name=name, exact=True)
        if button.count() != 1 or not button.is_visible() or not button.is_enabled():
            raise SmartRecruitersFormError("unverified stage navigation")
        element = button.element_handle()
        guard = "(e, args) => {" + _DEEP + """
          return e.isConnected && e.tagName==='BUTTON' && e.type==='button'
            && !e.hasAttribute('formaction') && !e.hasAttribute('formmethod')
            && !e.hasAttribute('form') && !e.disabled && e.getAttribute('aria-disabled')!=='true'
            && !!closest(e, args.container) && !!closest(e, '[data-test="'+args.hook+'"]')
            && !/submit|finish|send|apply/i.test([e.id,e.name,e.value].join(' '));
        }"""
        if element is None or not element.evaluate(guard, {"container": container, "hook": hook}):
            raise SmartRecruitersFormError("unsafe stage navigation")
        parts = urlsplit(page.url)
        path = parts.path.rstrip("/")
        path = path + "/screening" if forward else path.removesuffix("/screening")
        expected = urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
        # Recheck accessible identity/route immediately before clicking the
        # captured node; a changed locator must never select another action.
        if (button.count() != 1 or not button.evaluate("(e, original) => e===original", element)
                or not (self._page_ok(page) if forward else self._screening_ok(page))):
            raise SmartRecruitersFormError("stage navigation changed")
        element.click(timeout=self._navigation_timeout_ms)
        page.wait_for_url(expected, timeout=self._navigation_timeout_ms)
        if self.detect_challenge(page) or not (self._screening_ok(page) if forward else self._page_ok(page)):
            raise SmartRecruitersFormError("unexpected stage destination")
        hydrated = self._screening.hydrated if forward else self._hydrated
        if not self._stable(page, lambda: hydrated(page), timeout_ms=self._hydration_timeout_ms):
            raise SmartRecruitersFormError("stage hydration failed")

    @staticmethod
    def _signature(fields):
        return sorted((f.name or "", f.label, f.field_type, f.required, f.context) for f in fields)

    @staticmethod
    def _demote(result: AutofillResult, records, reason: str) -> None:
        for record in list(records):
            identity = (record.name, record.label)
            result.filled_fields[:] = [r for r in result.filled_fields if (r.name, r.label) != identity]
            target = result.unresolved_required_fields if record.required else result.unresolved_optional_fields
            if not any((r.name, r.label) == identity for r in target):
                target.append(record.model_copy(update={"note": reason}))
            if record.name == "resume":
                result.resume_uploaded = False
                result.resume_attachment_incomplete = True

    def _check_stage(self, page: Page, result: AutofillResult, *, include_hidden: bool = False) -> bool:
        """Re-read every successful value and account for newly required fields."""
        if self.detect_challenge(page):
            self._demote(result, result.filled_fields, "Challenge prevents final verification.")
            return False
        fields = self.discover_fields(page, include_hidden=include_hidden)
        failed = []
        for record in list(result.filled_fields):
            matches = [f for f in fields if (f.name, f.label, f.field_type, f.required) ==
                       (record.name, record.label, record.field_type, record.required)]
            if len(matches) != 1 or self.read_back(page, matches[0]) is None:
                failed.append(record)
        # Read-back can span asynchronous rendering. Account for the schema
        # again afterwards, including fields added during these samples.
        fields = self.discover_fields(page, include_hidden=include_hidden)
        for record in result.filled_fields:
            matches = [f for f in fields if (f.name, f.label, f.field_type, f.required) ==
                       (record.name, record.label, record.field_type, record.required)]
            if len(matches) != 1 and record not in failed:
                failed.append(record)
        self._demote(result, failed, "Value or own validation could not be confirmed after stage transition.")
        accounted = {(r.name, r.label) for r in result.filled_fields + result.unresolved_required_fields}
        for field in fields:
            if field.required and (field.name, field.label) not in accounted:
                result.unresolved_required_fields.append(AutofillFieldResult(
                    label=field.label, name=field.name, field_type=field.field_type,
                    required=True, classification=FieldClassification.UNSUPPORTED,
                    note="New or changed required field needs manual review.",
                ))
        return not failed

    @staticmethod
    def _combine(first: AutofillResult, second: AutofillResult | None) -> AutofillResult:
        merged = first.model_copy(deep=True)
        if second is not None:
            for key in ("filled_fields", "unresolved_required_fields", "unresolved_optional_fields", "sensitive_fields",
                        "unsupported_fields", "generated_fields", "warnings"):
                getattr(merged, key).extend(getattr(second, key))
        merged.warnings[:] = list(dict.fromkeys(w for w in merged.warnings if w != PARTIAL_STAGE_REASON))
        return merged

    def prepare_remaining_stages(self, page: Page, result: AutofillResult, *, fill_stage) -> AutofillResult:
        if not self._prepare_screening:
            return result
        first, second = result.model_copy(deep=True), None
        reason = "candidate-information preflight is incomplete"
        self._stage_complete = False
        try:
            if not self._page_ok(page) or not self._check_stage(page, first) or first.unresolved_required_fields:
                return self._stage_result(first, None, reason)
            signature = self._signature(self.discover_fields(page))
            self._navigate_stage(page, forward=True)
            second = fill_stage()
            reason = "Screening read-back is incomplete"
            if second.status is AutofillStatus.FAILED or not self._check_stage(page, second):
                return self._stage_result(first, second, reason)
            screening_signature = self._signature(self.discover_fields(page))
            self._navigate_stage(page, forward=False)
            reason = "candidate information or resume did not survive Back"
            candidate_ok = self._check_stage(page, first)
            if self._signature(self.discover_fields(page)) != signature or not candidate_ok or first.unresolved_required_fields:
                self._demote(second, second.filled_fields, "Screening persistence has not been verified.")
                return self._stage_result(first, second, reason)
            self._navigate_stage(page, forward=True)
            reason = "Screening answers or requirements did not survive Next"
            answers_ok = self._check_stage(page, second)
            if self._signature(self.discover_fields(page)) != screening_signature or not answers_ok:
                return self._stage_result(first, second, reason)
            # The final Next can itself mutate candidate values or the upload.
            # Only a retained, own widget can prove this on the final route;
            # a recreated/name-only row never replaces original attempt proof.
            candidate_ok = self._check_stage(page, first, include_hidden=True)
            final_candidate_fields = self.discover_fields(page, include_hidden=True)
            if not candidate_ok or self._signature(final_candidate_fields) != signature:
                return self._stage_result(first, second, "final-route candidate/resume persistence could not be proven")
            if (not self._check_stage(page, second)
                    or self._signature(self.discover_fields(page)) != screening_signature):
                return self._stage_result(first, second, "final Screening state changed during verification")
            if not wise_screening_inventory_complete(self.discover_fields(page)):
                return self._stage_result(first, second, "observed required Screening inventory is incomplete or changed")
            self._stage_complete = (not first.unresolved_required_fields and not second.unresolved_required_fields
                                    and not self.detect_challenge(page))
            return self._stage_result(first, second, "required Screening answers remain manual")
        except (PlaywrightError, SmartRecruitersFormError):
            # No raw Playwright exception (it may contain values/paths).
            if self._page_ok(page):
                self._check_stage(page, first)
            else:
                self._demote(first, first.filled_fields, "Candidate-information persistence is unverified.")
            if second is not None:
                self._demote(second, second.filled_fields, "Screening persistence is unverified.")
            return self._stage_result(first, second, "verified stage navigation did not complete")

    def _stage_result(self, first, second, reason):
        merged = self._combine(first, second)
        complete = self._stage_complete and not merged.unresolved_required_fields and not merged.resume_attachment_incomplete
        merged.status = AutofillStatus.READY_FOR_REVIEW if complete else AutofillStatus.NEEDS_MANUAL_INTERVENTION
        merged.submit_performed = False
        merged.warnings.append(
            "SmartRecruiters preparation is complete for manual review; Submit was not activated."
            if complete else "SmartRecruiters preparation is partial: " + reason + ". Submit was not activated."
        )
        merged.warnings.append(
            "Resume proof is UI-only: selected bytes and the original settled widget; server acknowledgement is unverified."
        )
        return merged


__all__ = ["PARTIAL_STAGE_REASON", "SUPPORTED_COMPANIES", "SmartRecruitersAdapter", "SmartRecruitersFormError"]
