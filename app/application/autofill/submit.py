"""Real Submit-click capability for TASK-048 auto-submit execution.

Kept out of `GreenhouseAdapter`/`LeverAdapter` on purpose: those adapters must
never gain a `submit` method (see `test_submit_control_is_not_clicked` in
`test_greenhouse_adapter.py` and the equivalent Lever test), so the only way a
real Submit click can happen is through `attempt_auto_submit` below, and only
when `AutofillService` is constructed with `auto_submit_enabled=True`.

`attempt_auto_submit` is the sole gate for a real submission. It requires,
all at once:
  (a) `evaluate_auto_submit_policy(result)` returns `AUTO_SUBMIT_SAFE`
      (READY_FOR_REVIEW, no unresolved required fields, no generated fields,
      no unsupported required fields, no captcha/security-challenge or
      validation warning captured during Stage 1 fill);
  (b) `enabled=True` was passed explicitly (wired from config, default off);
  (c) implied by (a): all required fields already passed fill + read-back
      confirmation in Stage 1, otherwise they would be unresolved and policy
      would block;
  (d) no challenge is visible on the page immediately before the click, per
      `adapter.detect_challenge` (captcha iframe / password field);
  (e) a last-moment scan of `result.warnings` and, when the page exposes a
      readable body, of the pre-submit page text finds no OTP, email
      verification, login/authentication, consent/privacy/acknowledgement,
      captcha/cloudflare, or validation marker (see `_final_challenge_marker`
      below). This is deliberately broader than (a)/(d): it also catches
      challenges that only ever show up as page text, not as a warning or a
      DOM element `detect_challenge` looks for.
Any other outcome returns `result` unchanged, so the existing manual-review
browser handoff (see `AutofillService.run`) still applies untouched.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.application.autofill.models import AutofillResult
from app.application.autofill.policy import AutoSubmitDecision, evaluate_auto_submit_policy
from app.application.autofill.resolver import TARGET_COMPANY_ASHBY_PREFIX, TARGET_COMPANY_LEVER_PREFIX

logger = logging.getLogger(__name__)

_SUBMIT_BUTTON_SELECTOR = "button[type='submit'], input[type='submit']"
_CONFIRMATION_TEXT_MARKERS = (
    "thank you",
    "application received",
    "application submitted",
    "successfully submitted",
)

# Markers for the TASK-048 last-moment guard. `_WARNING_MARKERS` scan our own
# internal `AutofillResult.warnings` strings, so plain single words are fine
# (low false-positive risk: we control that text). `_PAGE_MARKERS` scan raw
# page body text instead, so they are deliberately multi-word phrases —
# generic single words like "privacy" or "validation" show up in ordinary
# legal footers / inline field hints on real application pages and would
# block a normal successful confirmation flow.
_OTP_WARNING_MARKERS = ("otp", "one-time", "passcode")
_EMAIL_VERIFICATION_WARNING_MARKERS = ("email verification", "verify email")
_LOGIN_WARNING_MARKERS = ("login", "authentication")
_CONSENT_WARNING_MARKERS = ("consent", "privacy", "acknowledg")
_CAPTCHA_WARNING_MARKERS = ("captcha", "cloudflare", "security challenge")
_VALIDATION_WARNING_MARKERS = ("validation",)
_WARNING_MARKERS = (
    _OTP_WARNING_MARKERS
    + _EMAIL_VERIFICATION_WARNING_MARKERS
    + _LOGIN_WARNING_MARKERS
    + _CONSENT_WARNING_MARKERS
    + _CAPTCHA_WARNING_MARKERS
    + _VALIDATION_WARNING_MARKERS
)

_PAGE_MARKERS = (
    "one-time password",
    "one-time code",
    "enter otp",
    "passcode",
    "verify your email",
    "email verification",
    "confirm your email",
    "please log in",
    "please sign in",
    "log in to continue",
    "authentication required",
    "please confirm your consent",
    "consent is required",
    "acknowledge to continue",
    "captcha",
    "cloudflare",
    "verify you are human",
    "security challenge",
)


class ChallengeDetector(Protocol):
    """The subset of `AutofillAdapter` needed for a last-moment challenge check."""

    def detect_challenge(self, page: object) -> str | None: ...


class SubmitAdapter(Protocol):
    """Real submit surface. Never constructed/used unless auto-submit is enabled."""

    def submit(self, page: object) -> bool: ...
    def submit_confirmed(self, page: object) -> bool: ...


class _ClickSubmitButtonAdapter:
    """Clicks the first native Submit control and looks for a confirmation page."""

    def submit(self, page: object) -> bool:
        button = page.locator(_SUBMIT_BUTTON_SELECTOR)
        if button.count() == 0:
            return False
        try:
            button.first.click(timeout=5_000)
        except Exception:
            logger.exception("Auto-submit click failed")
            return False
        return True

    def submit_confirmed(self, page: object) -> bool:
        try:
            body = page.locator("body").inner_text(timeout=5_000).lower()
        except Exception:
            logger.exception("Auto-submit confirmation read-back failed")
            return False
        return any(marker in body for marker in _CONFIRMATION_TEXT_MARKERS)


class GreenhouseSubmitAdapter(_ClickSubmitButtonAdapter):
    """Real Submit-button click for Greenhouse. See module docstring for gating."""


class LeverSubmitAdapter(_ClickSubmitButtonAdapter):
    """Real Submit-button click for Lever. See module docstring for gating."""


class AshbySubmitAdapter:
    """Never submits. There is no Ashby submit capability yet -- `AshbyAdapter`
    only discovers/fills fields (see `app.application.autofill.ashby`), and
    this class exists solely so that an Ashby-sourced vacancy can never fall
    through `default_submit_adapter_for_source`'s generic default and end up
    driven by `GreenhouseSubmitAdapter`'s click-based logic. `submit` always
    returns False, so `attempt_auto_submit` always leaves `result` unchanged
    for Ashby regardless of `auto_submit_enabled`.
    """

    def submit(self, page: object) -> bool:
        _ = page
        return False

    def submit_confirmed(self, page: object) -> bool:
        _ = page
        return False


def default_submit_adapter_for_source(source: str) -> SubmitAdapter:
    """Source-aware submit adapter selection, mirroring `default_adapter_for_source`."""
    cleaned = source.strip()
    if cleaned.startswith(TARGET_COMPANY_LEVER_PREFIX):
        return LeverSubmitAdapter()
    if cleaned.startswith(TARGET_COMPANY_ASHBY_PREFIX):
        return AshbySubmitAdapter()
    return GreenhouseSubmitAdapter()


def _final_challenge_marker(result: AutofillResult, page: object) -> str | None:
    """Last-moment, fail-closed scan for a challenge the earlier gates miss.

    Checks `result.warnings` first, then — only if `page` actually exposes a
    `locator` (real Playwright pages always do; the bare placeholder objects
    used by unrelated tests don't, and are skipped rather than treated as a
    challenge) — the pre-submit page body text. A page that fails to read is
    treated as a challenge (fail closed): we have no evidence it's safe.
    """
    warning_text = " ".join(result.warnings).lower()
    for marker in _WARNING_MARKERS:
        if marker in warning_text:
            return marker

    locator = getattr(page, "locator", None)
    if locator is None:
        return None
    try:
        body_text = locator("body").inner_text(timeout=5_000).lower()
    except Exception:
        logger.exception("Final challenge scan failed to read page body")
        return "page-unreadable"
    for marker in _PAGE_MARKERS:
        if marker in body_text:
            return marker
    return None


def attempt_auto_submit(
    result: AutofillResult,
    *,
    enabled: bool,
    challenge_detector: ChallengeDetector,
    submit_adapter: SubmitAdapter,
    page: object,
) -> AutofillResult:
    """Perform a real Submit click only when every safeguard clears.

    Returns `result` unchanged whenever: auto-submit is disabled; the policy is
    not `AUTO_SUBMIT_SAFE`; a challenge (CAPTCHA/login/etc.) is visible right
    before submitting per `adapter.detect_challenge`; a last-moment scan of
    warnings/page text finds an OTP, email verification, login, consent, or
    other challenge marker (see `_final_challenge_marker`); the click itself
    fails; or no post-submit confirmation is found. `result` is never mutated
    in place.
    """
    if not enabled:
        return result
    decision = evaluate_auto_submit_policy(result)
    if decision.decision is not AutoSubmitDecision.AUTO_SUBMIT_SAFE:
        return result
    if challenge_detector.detect_challenge(page):
        return result
    if _final_challenge_marker(result, page):
        return result
    if not submit_adapter.submit(page):
        return result
    if not submit_adapter.submit_confirmed(page):
        return result
    return result.model_copy(update={"submit_performed": True})
