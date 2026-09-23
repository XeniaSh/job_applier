"""Browser-free regression coverage for Ashby's pure/duck-typed helpers.

Playwright Chromium cannot launch in this sandbox (MachPortRendezvousServer
permission denial), so `test_ashby_adapter.py`'s fixture-driven tests are
skipped here. These tests instead exercise the extracted pure decision
functions directly, and drive `detect_security_challenge` through a minimal
fake `Page`/`Locator` double that implements only the subset of the
Playwright API the function actually calls.
"""

from __future__ import annotations

from app.application.autofill.ashby import (
    _ACTIVE_CAPTCHA_SELECTOR,
    _APPLICATION_PANEL_SELECTOR,
    _classify_application_page,
    _has_visible_active_captcha,
    _label_or_value_candidates,
    _matching_radio_indices,
    detect_security_challenge,
)
from app.application.autofill.options import match_option_exact_normalized


class _FakeLocator:
    def __init__(
        self,
        count: int = 0,
        visible: list[bool] | None = None,
        text: str = "",
        in_badge: list[bool] | None = None,
    ) -> None:
        self._count = count
        self._visible = visible or []
        self._text = text
        self._in_badge = in_badge or []

    def count(self) -> int:
        return self._count

    def inner_text(self) -> str:
        return self._text

    def nth(self, index: int) -> "_FakeLocator":
        is_visible = self._visible[index] if index < len(self._visible) else True
        is_in_badge = self._in_badge[index] if index < len(self._in_badge) else False
        return _FakeLocator(count=1, visible=[is_visible], in_badge=[is_in_badge])

    def is_visible(self) -> bool:
        return self._visible[0] if self._visible else True

    def evaluate(self, script: str) -> bool:
        # Only ever called with `_BADGE_ANCESTOR_JS` in production code --
        # this fake just reports the ancestry this test double was told to
        # simulate for the candidate at hand.
        return self._in_badge[0] if self._in_badge else False


class _FakePage:
    """Duck-types just `.title()` / `.locator(selector)` -- the only Page
    surface `detect_security_challenge` and `_has_visible_active_captcha`
    touch. Unregistered selectors resolve to a locator matching nothing, the
    same as a real page where that selector is absent from the DOM.
    """

    def __init__(self, *, title: str = "", body: str = "", locators: dict[str, _FakeLocator] | None = None) -> None:
        self._title = title
        self._locators = dict(locators or {})
        self._locators.setdefault("body", _FakeLocator(count=1, text=body))

    def title(self) -> str:
        return self._title

    def locator(self, selector: str) -> _FakeLocator:
        return self._locators.get(selector, _FakeLocator())


_CLOUDFLARE_SELECTOR = "#cf-challenge, .cf-browser-verification"
_PASSWORD_SELECTOR = 'input[type="password"]'


def _recognized_ashby_page(locator_overrides: dict[str, _FakeLocator] | None = None) -> _FakePage:
    locators = {_APPLICATION_PANEL_SELECTOR: _FakeLocator(count=1)}
    locators.update(locator_overrides or {})
    return _FakePage(locators=locators)


# -- _classify_application_page (pure) --------------------------------------


def test_classify_application_page_recognizes_real_panel() -> None:
    assert _classify_application_page(True) == (True, "application_form_container")


def test_classify_application_page_rejects_missing_panel() -> None:
    # This covers both the survey-only-container case (Ashby wraps the EEOC
    # section in the same `.ashby-application-form-container` class as the
    # real panel, so `has_panel` must already be False by the time it reaches
    # here -- the selector that produces it requires the submit button and
    # the name/email systemfields both as descendants of the same tabpanel)
    # and a page carrying bare name/email systemfield wrappers with no
    # tabpanel at all: there is deliberately no fallback that recognizes
    # those wrappers on their own, since recognition gates what
    # `discover_fields` is allowed to scope its scan to.
    assert _classify_application_page(False) == (False, "no_match")


# -- _matching_radio_indices (pure) ------------------------------------------


def test_matching_radio_indices_exact_label_match() -> None:
    options = [("Yes", ""), ("No", "")]
    assert _matching_radio_indices("Yes", options) == [0]


def test_matching_radio_indices_prefix_is_not_a_match() -> None:
    options = [("Boston", ""), ("Boston Remote", "")]
    assert _matching_radio_indices("Bo", options) == []
    assert _matching_radio_indices("Boston", options) == [0]
    assert _matching_radio_indices("Boston Remote", options) == [1]


def test_matching_radio_indices_substring_is_not_a_match() -> None:
    options = [("Yes, I have a criminal record", "yes-record")]
    assert _matching_radio_indices("Yes", options) == []


def test_matching_radio_indices_visible_label_wins_even_when_value_alone_would_match() -> None:
    # A "Yes, I have a criminal record" option with value="yes" must never
    # match a plain "Yes" wanted -- the visible label is what the candidate
    # is actually attesting to, and the hidden `value` is irrelevant once a
    # visible label is present.
    options = [("Yes, I have a criminal record", "yes")]
    assert _matching_radio_indices("Yes", options) == []


def test_matching_radio_indices_ignores_differing_value_when_label_matches() -> None:
    options = [("Affirmative", "yes")]
    assert _matching_radio_indices("Yes", options) == []
    assert _matching_radio_indices("Affirmative", options) == [0]


def test_matching_radio_indices_falls_back_to_value_when_no_visible_label() -> None:
    options = [("", "yes")]
    assert _matching_radio_indices("Yes", options) == [0]


def test_matching_radio_indices_ambiguous_near_duplicate_labels_are_not_unique() -> None:
    # Punctuation-insensitive normalization means "Yes" and "Yes." collide --
    # callers must see both indices and refuse to guess between them.
    options = [("Yes", ""), ("Yes.", "")]
    assert _matching_radio_indices("Yes", options) == [0, 1]


# -- _label_or_value_candidates (pure) ---------------------------------------
#
# `_fill_radio`'s post-click readback feeds the just-checked option's
# (label, value) pair through this same helper before matching `wanted`
# against it -- these regressions pin the visible-label-first rule so a
# hidden `value` can never rescue a readback whose visible label mismatches.


def test_label_or_value_candidates_prefers_visible_label() -> None:
    assert _label_or_value_candidates("Yes, I have a criminal record", "yes") == [
        "Yes, I have a criminal record"
    ]


def test_label_or_value_candidates_falls_back_to_value_when_no_visible_label() -> None:
    assert _label_or_value_candidates("", "yes") == ["yes"]


def test_label_or_value_candidates_empty_when_neither_present() -> None:
    assert _label_or_value_candidates("", "") == []


def test_label_or_value_candidates_never_lets_value_rescue_a_mismatching_label() -> None:
    # A checked option whose visible label is "Yes, I have a criminal
    # record" but whose `value="yes"` must never be reported as a match for
    # a plain "Yes" readback via its hidden value.
    candidates = _label_or_value_candidates("Yes, I have a criminal record", "yes")
    assert match_option_exact_normalized("Yes", candidates) is None


# -- detect_security_challenge / _has_visible_active_captcha ----------------


def test_detect_challenge_cloudflare_via_title() -> None:
    page = _FakePage(title="Just a moment...")
    assert detect_security_challenge(page) == "cloudflare_challenge"  # type: ignore[arg-type]


def test_detect_challenge_cloudflare_via_body_text() -> None:
    page = _FakePage(body="Checking your browser before accessing example.com")
    assert detect_security_challenge(page) == "cloudflare_challenge"  # type: ignore[arg-type]


def test_detect_challenge_cloudflare_via_marker_element() -> None:
    page = _FakePage(locators={_CLOUDFLARE_SELECTOR: _FakeLocator(count=1)})
    assert detect_security_challenge(page) == "cloudflare_challenge"  # type: ignore[arg-type]


def test_detect_challenge_invisible_recaptcha_badge_on_recognized_page_is_none() -> None:
    page = _recognized_ashby_page({_ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(count=1, visible=[False])})
    assert detect_security_challenge(page) is None  # type: ignore[arg-type]


def test_detect_challenge_visible_captcha_on_recognized_page_is_captcha() -> None:
    page = _recognized_ashby_page({_ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(count=1, visible=[True])})
    assert detect_security_challenge(page) == "captcha"  # type: ignore[arg-type]


def test_detect_challenge_recognized_page_with_no_captcha_is_none() -> None:
    page = _recognized_ashby_page()
    assert detect_security_challenge(page) is None  # type: ignore[arg-type]


def test_detect_challenge_unrecognized_page_with_password_is_login() -> None:
    page = _FakePage(locators={_PASSWORD_SELECTOR: _FakeLocator(count=1)})
    assert detect_security_challenge(page) == "login"  # type: ignore[arg-type]


def test_detect_challenge_unrecognized_page_with_nothing_is_none() -> None:
    page = _FakePage()
    assert detect_security_challenge(page) is None  # type: ignore[arg-type]


def test_has_visible_active_captcha_ignores_hidden_candidates() -> None:
    page = _FakePage(locators={_ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(count=1, visible=[False])})
    assert _has_visible_active_captcha(page) is False  # type: ignore[arg-type]


def test_has_visible_active_captcha_detects_visible_candidate() -> None:
    page = _FakePage(locators={_ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(count=2, visible=[False, True])})
    assert _has_visible_active_captcha(page) is True  # type: ignore[arg-type]


def test_has_visible_active_captcha_ignores_visible_iframe_inside_grecaptcha_badge() -> None:
    # Mirrors a real snapshot: the badge's own challenge iframe is
    # positioned off-screen rather than hidden, so `is_visible()` reports
    # True -- it must still be excluded because it descends from
    # `.grecaptcha-badge`.
    page = _FakePage(
        locators={_ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(count=1, visible=[True], in_badge=[True])}
    )
    assert _has_visible_active_captcha(page) is False  # type: ignore[arg-type]


def test_has_visible_active_captcha_flags_visible_candidate_outside_the_badge() -> None:
    page = _FakePage(
        locators={
            _ACTIVE_CAPTCHA_SELECTOR: _FakeLocator(
                count=2, visible=[True, True], in_badge=[True, False]
            )
        }
    )
    assert _has_visible_active_captcha(page) is True  # type: ignore[arg-type]
