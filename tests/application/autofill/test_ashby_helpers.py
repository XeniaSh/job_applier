"""Browser-free regression coverage for Ashby's pure/duck-typed helpers.

Playwright Chromium cannot launch in this sandbox (MachPortRendezvousServer
permission denial), so `test_ashby_adapter.py`'s fixture-driven tests are
skipped here. These tests instead exercise the extracted pure decision
functions directly, and drive `detect_security_challenge` through a minimal
fake `Page`/`Locator` double that implements only the subset of the
Playwright API the function actually calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill import ashby as ashby_module
from app.application.autofill.ashby import (
    _ACTIVE_CAPTCHA_SELECTOR,
    _APPLICATION_PANEL_SELECTOR,
    AshbyAdapter,
    _adjust_application_source_checkbox_group,
    _adjust_conjunctive_skill_experience,
    _adjust_country_relative_work_authorization,
    _adjust_current_location_combobox,
    _adjust_notice_period,
    _adjust_office_work_feasibility,
    _adjust_optional_messaging_consent,
    _adjust_salary_applicability,
    _classify_application_page,
    _cover_letter_ui_file_item_visible,
    _discover_wrapper,
    _has_visible_active_captcha,
    _is_yes_no_control,
    _label_or_value_candidates,
    _looks_like_application_source_group_label,
    _looks_like_country_relative_work_auth_yesno,
    _looks_like_current_location_label,
    _looks_like_optional_whatsapp_consent,
    _matching_radio_indices,
    _parse_conjunctive_tech_question,
    _revert_to_manual,
    _single_vacancy_work_country,
    _text_field_locator,
    detect_security_challenge,
)
from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.options import match_option_exact_normalized
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "application_files": {"default_resume": "resume.txt"},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def _vacancy(
    application_url: str = "https://jobs.ashbyhq.com/perk/abc-123/application",
    *,
    source: str = "target_company:ashby:perk",
    location: str | None = None,
) -> ResolvedVacancy:
    normalized = None
    if location is not None:
        normalized = NormalizedVacancy(
            source=source,
            external_id="abc-123",
            title="Backend Engineer",
            company="Perk",
            location=location,
            employment=None,
            description="",
            url=application_url,
            published_at=None,
        )
    return ResolvedVacancy(
        source=source,
        external_id="abc-123",
        title="Backend Engineer",
        company="Perk",
        url=application_url,
        application_url=application_url,
        vacancy=normalized,
    )


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


# -- _text_field_locator (pure -- records the selector it builds) ----------
#
# A bare page-wide `[id="..."]`/`[name="..."]` lookup (the prior behavior)
# can also match an unrelated node elsewhere on the page that happens to
# reuse the same id/name outside this field's own wrapper (e.g. a masked
# phone-input widget's own mirrored/hidden value input) -- these pin that
# the selector is always scoped to the field's own `data-field-path`
# wrapper and its live input tag, never a bare id/name.


class _RecordingPage:
    def __init__(self) -> None:
        self.selectors: list[str] = []

    def locator(self, selector: str) -> str:
        self.selectors.append(selector)
        return selector

    def get_by_label(self, label: str) -> str:
        return f"label:{label}"


def test_text_field_locator_scopes_id_lookup_to_the_field_wrapper() -> None:
    page = _RecordingPage()
    field = DiscoveredField(
        label="Phone Number",
        field_type="tel",
        element_id="a1b2c3d4-phone-uuid",
        context="a1b2c3d4-phone-uuid",
    )
    _text_field_locator(page, field)  # type: ignore[arg-type]
    assert page.selectors == ['[data-field-path="a1b2c3d4-phone-uuid"] input[id="a1b2c3d4-phone-uuid"]']


def test_text_field_locator_scopes_name_lookup_to_the_field_wrapper_when_no_id() -> None:
    page = _RecordingPage()
    field = DiscoveredField(label="Phone Number", field_type="tel", name="phone", context="phone-wrapper")
    _text_field_locator(page, field)  # type: ignore[arg-type]
    assert page.selectors == ['[data-field-path="phone-wrapper"] input[name="phone"]']


def test_text_field_locator_uses_textarea_tag_for_textarea_fields() -> None:
    page = _RecordingPage()
    field = DiscoveredField(
        label="Cover letter", field_type="textarea", element_id="cover", context="cover-wrapper"
    )
    _text_field_locator(page, field)  # type: ignore[arg-type]
    assert page.selectors == ['[data-field-path="cover-wrapper"] textarea[id="cover"]']


def test_text_field_locator_falls_back_to_label_with_no_id_name_or_context() -> None:
    page = _RecordingPage()
    field = DiscoveredField(label="Phone Number", field_type="tel")
    result = _text_field_locator(page, field)  # type: ignore[arg-type]
    assert result == "label:Phone Number"
    assert page.selectors == []


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


# -- label cue matchers (pure) -----------------------------------------------


def test_looks_like_current_location_label_matches_observed_wording() -> None:
    assert _looks_like_current_location_label("Current Location") is True
    assert _looks_like_current_location_label("Where are you currently based?") is True


def test_looks_like_current_location_label_rejects_unrelated_labels() -> None:
    assert _looks_like_current_location_label("Where did you hear about this opportunity?") is False
    assert _looks_like_current_location_label("Country") is False


def test_looks_like_application_source_group_label_matches_observed_wording() -> None:
    assert _looks_like_application_source_group_label("How did you hear about our company?") is True
    assert _looks_like_application_source_group_label("Where did you hear about this opportunity?") is True


def test_looks_like_application_source_group_label_rejects_unrelated_labels() -> None:
    assert _looks_like_application_source_group_label("Are you related to any current employees?") is False


def test_looks_like_optional_whatsapp_consent_requires_yesno_and_whatsapp_and_contact_cue() -> None:
    field = DiscoveredField(label="Can we contact you on WhatsApp about your application?", field_type="yesno")
    assert _looks_like_optional_whatsapp_consent(field) is True


def test_looks_like_optional_whatsapp_consent_rejects_non_yesno_control() -> None:
    field = DiscoveredField(label="Can we contact you on WhatsApp about your application?", field_type="checkbox")
    assert _looks_like_optional_whatsapp_consent(field) is False


def test_looks_like_optional_whatsapp_consent_rejects_missing_whatsapp_mention() -> None:
    field = DiscoveredField(label="Can we contact you about your application?", field_type="yesno")
    assert _looks_like_optional_whatsapp_consent(field) is False


def test_looks_like_optional_whatsapp_consent_rejects_whatsapp_mention_without_contact_cue() -> None:
    # "whatsapp" alone, with no contact/message cue, is not enough --
    # deliberately narrow so an unrelated question that merely names the
    # channel is never swept in.
    field = DiscoveredField(label="Do you use WhatsApp for work?", field_type="yesno")
    assert _looks_like_optional_whatsapp_consent(field) is False


def test_looks_like_optional_whatsapp_consent_recognizes_identity_verified_field_regardless_of_label() -> None:
    # A field discovered via `_discover_nested_whatsapp_consent` carries the
    # phone wrapper's own "Phone Number" label (or the consent block's own
    # free-form text) -- neither of which need pass the generic cue-phrase
    # check -- so this must be recognized from the platform's own exact
    # `whatsAppConsent` radio name plus its own exact safe negative option,
    # both already verified at discovery time.
    field = DiscoveredField(
        label="Phone Number",
        field_type="radio",
        name="whatsAppConsent",
        options=[
            "Yes - I consent to receiving WhatsApp messages",
            "No - I do not consent to receiving WhatsApp messages",
        ],
    )
    assert _looks_like_optional_whatsapp_consent(field) is True


def test_looks_like_optional_whatsapp_consent_still_rejects_required_identity_verified_field() -> None:
    field = DiscoveredField(
        label="Phone Number",
        field_type="radio",
        name="whatsAppConsent",
        required=True,
        options=[
            "Yes - I consent to receiving WhatsApp messages",
            "No - I do not consent to receiving WhatsApp messages",
        ],
    )
    assert _looks_like_optional_whatsapp_consent(field) is False


def test_looks_like_optional_whatsapp_consent_rejects_name_match_without_the_exact_safe_negative_option() -> None:
    # A same-named `whatsAppConsent` radio group could in principle reach
    # this function via the generic radio-group discovery branch instead of
    # `_discover_nested_whatsapp_consent` -- that branch never checks live
    # option wording, so the name alone (`_is_yes_no_control` only requires
    # a Yes-prefixed/No-prefixed pair, not the platform's own exact "do not
    # consent" wording) must never be enough to authorize an automatic
    # decline. Regression for a gap where the name-based path trusted
    # identity alone.
    field = DiscoveredField(
        label="Phone Number",
        field_type="radio",
        name="whatsAppConsent",
        options=["Yes", "No thanks"],
    )
    assert _looks_like_optional_whatsapp_consent(field) is False


# -- _discover_wrapper: phone `tel` input with a nested WhatsApp-consent
# component sharing the same `[data-field-path]` wrapper -----------------
#
# A live posting was observed rendering the Phone Number field's own
# wrapper with a second, nested `.ashby-application-form-texting-consent-
# description` component holding a distinct optional WhatsApp-consent radio
# group (`name="whatsAppConsent"`), sometimes alongside an unrelated
# same-block SMS-consent radio group under a different name. `_discover_
# wrapper` must still discover the phone `tel` input as its own text field
# (never misclassified as a single "radio" field labeled "Phone Number")
# and, only when the WhatsApp group's exact name and a safe negative option
# are both present, discover it as a second, separate field scoped to only
# that radio group. These fakes model the wrapper-info dict a real
# `_WRAPPER_INFO_JS` evaluation would produce for that DOM shape, and the
# nested radios `_discover_nested_whatsapp_consent` reads from it --
# validating this module's own decision logic without a real browser.

_PHONE_WRAPPER_PATH = "3f6a8b2c-0a92-4d5e-9c33-7a1b6e2f5d40"


def _phone_with_consent_info(*, consent_radio_names: list[str], consent_required: bool = False) -> dict:
    return {
        "dataFieldPath": _PHONE_WRAPPER_PATH,
        "label": "Phone Number",
        "hasFileInput": False,
        "isAutocomplete": False,
        "hasYesno": False,
        "radioCount": 2,
        "radioName": "whatsAppConsent",
        "checkboxCount": 0,
        "hasTextInput": True,
        "textInputTag": "input",
        "textInputType": "tel",
        "textInputId": _PHONE_WRAPPER_PATH,
        "textInputName": _PHONE_WRAPPER_PATH,
        "required": True,
        "hasConsentBlock": bool(consent_radio_names),
        "consentRadioNames": consent_radio_names,
        "consentText": "Can we contact you on WhatsApp about your application?",
        "consentRequired": consent_required,
    }


class _FakeConsentRadio:
    def __init__(self, label: str, value: str | None = None) -> None:
        self._label = label
        self._value = value if value is not None else label

    def evaluate(self, script: str) -> str:
        return self._label

    def get_attribute(self, name: str) -> str | None:
        return self._value if name == "value" else None


class _FakeConsentRadioLocator:
    def __init__(self, radios: list[_FakeConsentRadio]) -> None:
        self._radios = radios

    def count(self) -> int:
        return len(self._radios)

    def nth(self, index: int) -> _FakeConsentRadio:
        return self._radios[index]


class _FakePhoneWrapper:
    """`.locator(selector)` asserts the selector is always scoped to exactly
    `name="whatsAppConsent"` -- never the unrelated `smsConsent` group also
    modeled by `info["consentRadioNames"]` in the contamination test below,
    and never a bare unscoped radio query.
    """

    def __init__(self, info: dict, whatsapp_radios: list[_FakeConsentRadio]) -> None:
        self._info = info
        self._whatsapp_radios = whatsapp_radios

    def evaluate(self, script: str) -> dict:
        return self._info

    def locator(self, selector: str) -> _FakeConsentRadioLocator:
        assert "ashby-application-form-texting-consent-description" in selector
        assert 'name="whatsAppConsent"' in selector
        assert "smsConsent" not in selector
        return _FakeConsentRadioLocator(self._whatsapp_radios)


class _NoLocatorProbeWrapper:
    """Raises if `.locator(...)` is ever called -- pins that discovery never
    even probes for consent radios when the `whatsAppConsent` identity is
    absent from `consentRadioNames` (e.g. only an unrelated SMS-consent
    group was nested in the nested consent block).
    """

    def __init__(self, info: dict) -> None:
        self._info = info

    def evaluate(self, script: str) -> dict:
        return self._info

    def locator(self, selector: str):
        raise AssertionError("must not probe consent radios without the whatsAppConsent identity")


def test_discover_wrapper_phone_with_nested_whatsapp_consent_yields_both_fields() -> None:
    info = _phone_with_consent_info(consent_radio_names=["whatsAppConsent"])
    whatsapp_radios = [
        _FakeConsentRadio("Yes - I consent to receiving WhatsApp messages"),
        _FakeConsentRadio("No - I do not consent to receiving WhatsApp messages"),
    ]
    wrapper = _FakePhoneWrapper(info, whatsapp_radios)

    fields = _discover_wrapper(wrapper, set())  # type: ignore[arg-type]

    assert len(fields) == 2
    phone_field, consent_field = fields
    assert phone_field.field_type == "tel"
    assert phone_field.element_id == _PHONE_WRAPPER_PATH
    assert phone_field.context == _PHONE_WRAPPER_PATH
    assert consent_field.field_type == "radio"
    assert consent_field.name == "whatsAppConsent"
    assert consent_field.required is False
    assert consent_field.context == _PHONE_WRAPPER_PATH
    assert consent_field.options == [
        "Yes - I consent to receiving WhatsApp messages",
        "No - I do not consent to receiving WhatsApp messages",
    ]


def test_discover_wrapper_ignores_sms_consent_contamination_in_the_same_block() -> None:
    # `consentRadioNames` models both groups being present in the nested
    # consent block -- `_FakePhoneWrapper.locator` itself asserts the SMS
    # name never leaks into the selector used to read live options.
    info = _phone_with_consent_info(consent_radio_names=["whatsAppConsent", "smsConsent"])
    whatsapp_radios = [
        _FakeConsentRadio("Yes - I consent to receiving WhatsApp messages"),
        _FakeConsentRadio("No - I do not consent to receiving WhatsApp messages"),
    ]
    wrapper = _FakePhoneWrapper(info, whatsapp_radios)

    fields = _discover_wrapper(wrapper, set())  # type: ignore[arg-type]

    assert len(fields) == 2
    assert fields[1].name == "whatsAppConsent"


def test_discover_wrapper_fails_closed_without_the_exact_safe_negative_option() -> None:
    # A live options list that no longer offers the platform's own exact
    # "do not consent" negative wording must never be guessed at -- only the
    # phone text field is discovered, never a consent field built from an
    # unverified options list.
    info = _phone_with_consent_info(consent_radio_names=["whatsAppConsent"])
    whatsapp_radios = [_FakeConsentRadio("Yes"), _FakeConsentRadio("No thanks")]
    wrapper = _FakePhoneWrapper(info, whatsapp_radios)

    fields = _discover_wrapper(wrapper, set())  # type: ignore[arg-type]

    assert len(fields) == 1
    assert fields[0].field_type == "tel"


def test_discover_wrapper_never_probes_consent_radios_without_whatsapp_identity() -> None:
    # Only an unrelated SMS-consent group is nested here -- no `whatsAppConsent`
    # name at all -- so discovery must recognize the phone text field and
    # never even attempt to read the nested radios.
    info = _phone_with_consent_info(consent_radio_names=["smsConsent"])
    wrapper = _NoLocatorProbeWrapper(info)

    fields = _discover_wrapper(wrapper, set())  # type: ignore[arg-type]

    assert len(fields) == 1
    assert fields[0].field_type == "tel"


def test_discover_wrapper_required_nested_consent_is_still_discovered_but_stays_manual() -> None:
    info = _phone_with_consent_info(consent_radio_names=["whatsAppConsent"], consent_required=True)
    whatsapp_radios = [
        _FakeConsentRadio("Yes - I consent to receiving WhatsApp messages"),
        _FakeConsentRadio("No - I do not consent to receiving WhatsApp messages"),
    ]
    wrapper = _FakePhoneWrapper(info, whatsapp_radios)

    fields = _discover_wrapper(wrapper, set())  # type: ignore[arg-type]

    assert len(fields) == 2
    consent_field = fields[1]
    assert consent_field.required is True
    assert _looks_like_optional_whatsapp_consent(consent_field) is False


def test_looks_like_optional_whatsapp_consent_recognizes_native_radio_rendering() -> None:
    # A live posting was observed rendering this exact question as a native
    # radio group -- `.ashby-application-form-input-radio-group` -- instead
    # of the yesno button widget, with the platform's own Yes/No consent
    # wording. Must still be recognized as the same safe-decline shape.
    field = DiscoveredField(
        label="Can we contact you on WhatsApp about your application? (optional)",
        field_type="radio",
        options=[
            "Yes - I consent to receiving WhatsApp messages",
            "No - I do not consent to receiving WhatsApp messages",
        ],
    )
    assert _looks_like_optional_whatsapp_consent(field) is True


def test_looks_like_optional_whatsapp_consent_rejects_radio_with_non_yes_no_options() -> None:
    field = DiscoveredField(
        label="Can we contact you on WhatsApp about your application?",
        field_type="radio",
        options=["Mobile", "Landline"],
    )
    assert _looks_like_optional_whatsapp_consent(field) is False


def test_looks_like_optional_whatsapp_consent_rejects_radio_with_more_than_two_options() -> None:
    field = DiscoveredField(
        label="Can we contact you on WhatsApp about your application?",
        field_type="radio",
        options=["Yes", "No", "Maybe"],
    )
    assert _looks_like_optional_whatsapp_consent(field) is False


# -- _is_yes_no_control (pure) ------------------------------------------------


def test_is_yes_no_control_true_for_yesno_widget() -> None:
    assert _is_yes_no_control(DiscoveredField(label="x", field_type="yesno")) is True


def test_is_yes_no_control_true_for_radio_with_yes_no_prefixed_pair() -> None:
    field = DiscoveredField(
        label="x",
        field_type="radio",
        options=["No - I do not consent", "Yes - I consent"],
    )
    assert _is_yes_no_control(field) is True


def test_is_yes_no_control_false_for_radio_with_three_options() -> None:
    field = DiscoveredField(label="x", field_type="radio", options=["Yes", "No", "Unsure"])
    assert _is_yes_no_control(field) is False


def test_is_yes_no_control_false_for_radio_with_unrelated_two_options() -> None:
    field = DiscoveredField(label="x", field_type="radio", options=["Man", "Woman"])
    assert _is_yes_no_control(field) is False


def test_is_yes_no_control_false_for_other_field_types() -> None:
    assert _is_yes_no_control(DiscoveredField(label="x", field_type="checkbox")) is False
    assert _is_yes_no_control(DiscoveredField(label="x", field_type="text")) is False


# -- _parse_conjunctive_tech_question (pure) ---------------------------------


def test_parse_conjunctive_tech_question_extracts_two_named_technologies() -> None:
    label = "Do you have recent hands-on working experience with Java and Spring Boot?"
    assert _parse_conjunctive_tech_question(label) == ["Java", "Spring Boot"]


def test_parse_conjunctive_tech_question_none_for_single_technology() -> None:
    assert _parse_conjunctive_tech_question("Do you have experience with Kubernetes?") is None


def test_parse_conjunctive_tech_question_none_when_no_experience_phrase() -> None:
    assert _parse_conjunctive_tech_question("Do you know Java and Spring Boot?") is None


# -- _revert_to_manual (pure) -------------------------------------------------


def test_revert_to_manual_uses_unknown_required_for_a_required_field() -> None:
    field = DiscoveredField(label="Salary expectations", field_type="number", required=True)
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="150000",
        fill=True,
        kind=QuestionKind.SALARY,
    )
    reverted = _revert_to_manual(item, reason="test reason")
    assert reverted.classification is FieldClassification.UNKNOWN_REQUIRED
    assert reverted.fill is False
    assert reverted.value is None
    assert reverted.unresolved_reason == "test reason"


def test_revert_to_manual_uses_unknown_optional_for_an_optional_field() -> None:
    field = DiscoveredField(label="Salary expectations", field_type="number", required=False)
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="150000",
        fill=True,
        kind=QuestionKind.SALARY,
        generated=True,
    )
    reverted = _revert_to_manual(item, reason="test reason")
    assert reverted.classification is FieldClassification.UNKNOWN_OPTIONAL
    assert reverted.generated is False


# -- _adjust_office_work_feasibility (pure) ----------------------------------


def _office_work_item(
    *, required: bool = True, options: list[str] | None = None, value: str = "Yes"
) -> ClassifiedField:
    field = DiscoveredField(
        label="This role requires working 5 days per week in the office. Are you comfortable with this arrangement?",
        field_type="yesno",
        required=required,
        options=list(options or ["Yes", "No"]),
    )
    return ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=value,
        fill=True,
        kind=QuestionKind.OFFICE_WORK,
    )


def test_adjust_office_work_feasibility_reverts_generic_default_true_to_manual() -> None:
    # `office_work.willing` defaults to True, indistinguishable here from a
    # real candidate fact -- must never be trusted for a named on-site
    # feasibility question without an explicit override.
    profile = _profile()
    item = _office_work_item()
    adjusted = _adjust_office_work_feasibility(item, profile)
    assert adjusted.fill is False
    assert adjusted.classification is FieldClassification.UNKNOWN_REQUIRED


def test_adjust_office_work_feasibility_keeps_explicit_decline() -> None:
    profile = _profile(application_policy={"office_work": {"willing": False}})
    item = _office_work_item(value="No")
    adjusted = _adjust_office_work_feasibility(item, profile)
    assert adjusted.fill is True
    assert adjusted is item


def test_adjust_office_work_feasibility_uses_explicit_question_override() -> None:
    profile = _profile(
        application_policy={
            "question_overrides": [{"question_contains": ["days per week in the office"], "answer": True}],
        }
    )
    item = _office_work_item()
    adjusted = _adjust_office_work_feasibility(item, profile)
    assert adjusted.fill is True
    assert adjusted.value is True
    assert adjusted.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_adjust_office_work_feasibility_ignores_non_office_work_kind() -> None:
    field = DiscoveredField(label="Salary expectations", field_type="number")
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.UNKNOWN_OPTIONAL,
        fill=False,
        kind=QuestionKind.SALARY,
    )
    assert _adjust_office_work_feasibility(item, _profile()) is item


def test_adjust_office_work_feasibility_leaves_unnamed_office_work_question_unchanged() -> None:
    # The shared classifier's `QuestionKind.OFFICE_WORK` bucket is much
    # broader than the one exact "requires working N days .../fully on-site"
    # shape this adjuster is scoped to -- a bare "willing to work onsite?"
    # question (a shape this module has never verified) must be left exactly
    # as the shared classifier resolved it, never force-reverted to manual.
    field = DiscoveredField(
        label="Are you willing to work onsite?",
        field_type="yesno",
        required=True,
        options=["Yes", "No"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Yes",
        fill=True,
        kind=QuestionKind.OFFICE_WORK,
    )
    assert _adjust_office_work_feasibility(item, _profile()) is item


# -- _adjust_salary_applicability (pure) -------------------------------------


def _salary_item(label: str, *, required: bool = True, fill: bool = True) -> ClassifiedField:
    field = DiscoveredField(label=label, field_type="number", required=required)
    return ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC if fill else FieldClassification.UNKNOWN_REQUIRED,
        value="150000 USD" if fill else None,
        fill=fill,
        kind=QuestionKind.SALARY,
    )


def test_adjust_salary_applicability_local_currency_always_stays_manual() -> None:
    profile = _profile(
        employment={"salary_expectations": {"amount": 150000, "currency": "USD", "period": "annual", "fill_salary": True}}
    )
    item = _salary_item("Gross annual salary (local currency)")
    adjusted = _adjust_salary_applicability(item, profile)
    assert adjusted.fill is False
    assert "local currency" in (adjusted.unresolved_reason or "")


def test_adjust_salary_applicability_requires_explicit_currency() -> None:
    profile = _profile(
        employment={"salary_expectations": {"amount": 150000, "currency": None, "fill_salary": True}}
    )
    item = _salary_item("Salary expectations")
    adjusted = _adjust_salary_applicability(item, profile)
    assert adjusted.fill is False


def test_adjust_salary_applicability_requires_matching_annual_period() -> None:
    profile = _profile(
        employment={
            "salary_expectations": {
                "amount": 150000,
                "currency": "USD",
                "period": "monthly",
                "fill_salary": True,
            }
        }
    )
    item = _salary_item("Annual salary expectations")
    adjusted = _adjust_salary_applicability(item, profile)
    assert adjusted.fill is False


def test_adjust_salary_applicability_keeps_a_fully_provable_annual_salary() -> None:
    profile = _profile(
        employment={
            "salary_expectations": {
                "amount": 150000,
                "currency": "USD",
                "period": "annual",
                "fill_salary": True,
            }
        }
    )
    item = _salary_item("Annual salary expectations")
    adjusted = _adjust_salary_applicability(item, profile)
    assert adjusted.fill is True
    assert adjusted is item


def test_adjust_salary_applicability_keeps_a_plain_currency_only_salary() -> None:
    profile = _profile(
        employment={"salary_expectations": {"amount": 150000, "currency": "USD", "fill_salary": True}}
    )
    item = _salary_item("Salary expectations")
    adjusted = _adjust_salary_applicability(item, profile)
    assert adjusted.fill is True


def test_adjust_salary_applicability_leaves_an_already_unresolved_field_alone() -> None:
    item = _salary_item("Salary expectations", fill=False)
    adjusted = _adjust_salary_applicability(item, _profile())
    assert adjusted.fill is False


# -- _adjust_application_source_checkbox_group (pure) ------------------------


def _source_group_item(*, fill: bool = True) -> ClassifiedField:
    field = DiscoveredField(
        label="How did you hear about our company?",
        field_type="checkbox_group",
        options=["Company Website", "LinkedIn", "Employee Referral"],
    )
    return ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC if fill else FieldClassification.UNKNOWN_OPTIONAL,
        value="Company Website" if fill else None,
        fill=fill,
        kind=QuestionKind.APPLICATION_SOURCE,
    )


def test_adjust_application_source_checkbox_group_keeps_verified_ashby_board() -> None:
    item = _source_group_item()
    adjusted = _adjust_application_source_checkbox_group(item, _vacancy())
    assert adjusted is item


def test_adjust_application_source_checkbox_group_reverts_unverified_url() -> None:
    item = _source_group_item()
    adjusted = _adjust_application_source_checkbox_group(
        item, _vacancy(application_url="https://embed.example.com/perk/abc-123/application")
    )
    assert adjusted.fill is False


def test_adjust_application_source_checkbox_group_reverts_a_non_target_company_source_even_with_canonical_url() -> None:
    # A canonical-shaped Ashby board URL alone is never sufficient -- it can
    # still reach here through an unrelated, non-target-company source (e.g.
    # a generic job aggregator that also links to Ashby-hosted boards), which
    # never proves the candidate found this posting directly on the
    # company's own board.
    item = _source_group_item()
    adjusted = _adjust_application_source_checkbox_group(item, _vacancy(source="job_board:aggregator"))
    assert adjusted.fill is False


def test_adjust_application_source_checkbox_group_reverts_a_non_company_source_even_with_a_verified_board() -> None:
    # When the group offers no "Company Website"/"Careers Website" option at
    # all, the shared classifier's generic preference matching can still
    # resolve a *different* present option (e.g. "LinkedIn") -- a verified
    # direct board URL only proves the candidate found this posting directly
    # on the company's own board, never that they came specifically via
    # LinkedIn, so that resolution must still be reverted to manual.
    field = DiscoveredField(
        label="How did you hear about our company?",
        field_type="checkbox_group",
        options=["LinkedIn", "Other"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="LinkedIn",
        fill=True,
        kind=QuestionKind.APPLICATION_SOURCE,
    )
    adjusted = _adjust_application_source_checkbox_group(item, _vacancy())
    assert adjusted.fill is False


def test_adjust_application_source_checkbox_group_keeps_careers_website() -> None:
    field = DiscoveredField(
        label="How did you hear about our company?",
        field_type="checkbox_group",
        options=["Careers Website", "LinkedIn"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Careers Website",
        fill=True,
        kind=QuestionKind.APPLICATION_SOURCE,
    )
    adjusted = _adjust_application_source_checkbox_group(item, _vacancy())
    assert adjusted is item


def test_adjust_application_source_checkbox_group_ignores_other_field_types() -> None:
    field = DiscoveredField(
        label="How did you hear about our company?",
        field_type="checkbox",
        options=["Company Website"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Company Website",
        fill=True,
        kind=QuestionKind.APPLICATION_SOURCE,
    )
    assert _adjust_application_source_checkbox_group(item, _vacancy()) is item


# -- _adjust_optional_messaging_consent (pure) -------------------------------


def test_adjust_optional_messaging_consent_declines_whatsapp_yesno() -> None:
    field = DiscoveredField(
        label="Can we contact you on WhatsApp about your application? (optional)",
        field_type="yesno",
        options=["Yes", "No"],
    )
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_optional_messaging_consent(item)
    assert adjusted.fill is True
    assert adjusted.value is False


def test_adjust_optional_messaging_consent_overrides_an_already_resolved_yes() -> None:
    # A WhatsApp-worded question that the shared classifier already resolved
    # to Yes (e.g. it also matched the shared `SMS_UPDATES` cues and an
    # explicit `sms_interview_updates` opt-in fact) must still be forced back
    # to No -- that fact answers a different, generic question, never this
    # exact optional WhatsApp control, and opting in must never be automatic.
    field = DiscoveredField(
        label="Can we contact you on WhatsApp?", field_type="yesno", options=["Yes", "No"]
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.SMS_UPDATES,
    )
    adjusted = _adjust_optional_messaging_consent(item)
    assert adjusted.fill is True
    assert adjusted.value is False


def test_adjust_optional_messaging_consent_declines_whatsapp_rendered_as_radio() -> None:
    # See `_is_yes_no_control` -- a live posting renders this exact question
    # as a native radio pair rather than the yesno widget.
    field = DiscoveredField(
        label="Can we contact you on WhatsApp about your application? (optional)",
        field_type="radio",
        options=[
            "Yes - I consent to receiving WhatsApp messages",
            "No - I do not consent to receiving WhatsApp messages",
        ],
    )
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_optional_messaging_consent(item)
    assert adjusted.fill is True
    assert adjusted.value is False
    assert adjusted.field.field_type == "radio"


def test_adjust_optional_messaging_consent_ignores_required_whatsapp_question() -> None:
    # A required control is never eligible -- declining a mandatory question
    # is not the same safe no-op as declining a clearly optional one, so it
    # must stay exactly as the shared classifier resolved it.
    field = DiscoveredField(
        label="Can we contact you on WhatsApp?",
        field_type="yesno",
        required=True,
        options=["Yes", "No"],
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.UNKNOWN_REQUIRED,
        fill=False,
        kind=QuestionKind.UNKNOWN,
    )
    assert _adjust_optional_messaging_consent(item) is item


# -- _adjust_conjunctive_skill_experience (pure) -----------------------------


def _skill_item(label: str) -> ClassifiedField:
    field = DiscoveredField(label=label, field_type="yesno", options=["Yes", "No"])
    return ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)


def test_adjust_conjunctive_skill_experience_yes_when_every_technology_is_known() -> None:
    profile = _profile(employment={"professional_tech_stack": ["Java", "Spring Boot"]})
    item = _skill_item("Do you have recent hands-on working experience with Java and Spring Boot?")
    adjusted = _adjust_conjunctive_skill_experience(item, profile)
    assert adjusted.fill is True
    assert adjusted.value is True


def test_adjust_conjunctive_skill_experience_stays_manual_when_one_technology_is_missing() -> None:
    profile = _profile(employment={"professional_tech_stack": ["Java"]})
    item = _skill_item("Do you have recent hands-on working experience with Java and Spring Boot?")
    adjusted = _adjust_conjunctive_skill_experience(item, profile)
    assert adjusted.fill is False


def test_adjust_conjunctive_skill_experience_stays_manual_with_no_configured_technologies() -> None:
    item = _skill_item("Do you have recent hands-on working experience with Java and Spring Boot?")
    adjusted = _adjust_conjunctive_skill_experience(item, _profile())
    assert adjusted.fill is False


def test_adjust_conjunctive_skill_experience_yes_when_rendered_as_native_radio_pair() -> None:
    # A live posting was observed rendering this exact question as a native
    # radio group (`.ashby-application-form-input-radio-group`) rather than
    # the yesno widget -- see `_is_yes_no_control`.
    field = DiscoveredField(
        label="Do you have recent hands-on working experience with Java and Spring Boot?",
        field_type="radio",
        options=["Yes", "No"],
    )
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    profile = _profile(employment={"professional_tech_stack": ["Java", "Spring Boot"]})
    adjusted = _adjust_conjunctive_skill_experience(item, profile)
    assert adjusted.fill is True
    assert adjusted.value is True
    assert adjusted.kind is QuestionKind.SKILL_SET_CHOICE
    assert adjusted.field.field_type == "radio"


def test_adjust_conjunctive_skill_experience_radio_stays_manual_when_one_technology_is_missing() -> None:
    field = DiscoveredField(
        label="Do you have recent hands-on working experience with Java and Spring Boot?",
        field_type="radio",
        options=["Yes", "No"],
    )
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    profile = _profile(employment={"professional_tech_stack": ["Java"]})
    adjusted = _adjust_conjunctive_skill_experience(item, profile)
    assert adjusted.fill is False


def test_adjust_conjunctive_skill_experience_ignores_radio_with_non_yes_no_options() -> None:
    # A three-plus-option or non-Yes/No radio group is never this control --
    # `_is_yes_no_control` must reject it, never guessing a conjunctive
    # skill answer onto an unrelated radio question that merely names two
    # technologies in its label.
    field = DiscoveredField(
        label="Which of Java and Spring Boot do you prefer?",
        field_type="radio",
        options=["Java", "Spring Boot", "Both"],
    )
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    profile = _profile(employment={"professional_tech_stack": ["Java", "Spring Boot"]})
    assert _adjust_conjunctive_skill_experience(item, profile) is item


# -- _adjust_notice_period (pure) --------------------------------------------


def test_adjust_notice_period_fills_from_explicit_profile_value() -> None:
    profile = _profile(employment={"notice_period": "4 weeks"})
    field = DiscoveredField(label="Notice period / earliest start date", field_type="text")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_notice_period(item, profile)
    assert adjusted.fill is True
    assert adjusted.value == "4 weeks"


def test_adjust_notice_period_stays_manual_when_unset() -> None:
    field = DiscoveredField(label="Notice period / earliest start date", field_type="text")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_notice_period(item, _profile())
    assert adjusted.fill is False


def test_adjust_notice_period_ignores_unrelated_labels() -> None:
    field = DiscoveredField(label="Salary expectations", field_type="text")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    profile = _profile(employment={"notice_period": "4 weeks"})
    assert _adjust_notice_period(item, profile) is item


# -- _adjust_current_location_combobox (pure) --------------------------------


def test_adjust_current_location_combobox_fills_unresolved_live_wording() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
        }
    )
    field = DiscoveredField(label="Where are you currently based?", field_type="combobox_location")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_current_location_combobox(item, profile)
    assert adjusted.fill is True
    assert adjusted.value == "Berlin, Germany"
    assert adjusted.kind is QuestionKind.LOCATION
    assert adjusted.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_adjust_current_location_combobox_stays_manual_when_unset() -> None:
    field = DiscoveredField(label="Where are you currently based?", field_type="combobox_location")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    adjusted = _adjust_current_location_combobox(item, _profile())
    assert adjusted.fill is False
    assert adjusted is item


def test_adjust_current_location_combobox_ignores_other_field_types() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
        }
    )
    field = DiscoveredField(label="Where are you currently based?", field_type="text")
    item = ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_OPTIONAL, kind=QuestionKind.UNKNOWN)
    assert _adjust_current_location_combobox(item, profile) is item


def test_adjust_current_location_combobox_never_overrides_an_already_filled_item() -> None:
    profile = _profile(
        identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
        }
    )
    field = DiscoveredField(label="Current Location", field_type="combobox_location")
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Paris, France",
        fill=True,
        kind=QuestionKind.LOCATION,
    )
    assert _adjust_current_location_combobox(item, profile) is item


# -- _looks_like_country_relative_work_auth_yesno / _single_vacancy_work_country (pure) --


def test_looks_like_country_relative_work_auth_yesno_matches_observed_wording() -> None:
    field = DiscoveredField(
        label="Do you have legal authorisation to work in the country where this role is based?",
        field_type="yesno",
    )
    assert _looks_like_country_relative_work_auth_yesno(field) is True


def test_looks_like_country_relative_work_auth_yesno_rejects_sponsorship_question() -> None:
    field = DiscoveredField(
        label="Will you require sponsorship to work in the country where this role is based?",
        field_type="yesno",
    )
    assert _looks_like_country_relative_work_auth_yesno(field) is False


def test_looks_like_country_relative_work_auth_yesno_rejects_non_yesno_control() -> None:
    field = DiscoveredField(
        label="Do you have legal authorisation to work in the country where this role is based?",
        field_type="radio",
    )
    assert _looks_like_country_relative_work_auth_yesno(field) is False


def test_single_vacancy_work_country_requires_exactly_one_named_country() -> None:
    assert _single_vacancy_work_country(_vacancy(location="Berlin, Germany")) == "germany"
    assert _single_vacancy_work_country(_vacancy(location="Germany or Netherlands")) is None
    assert _single_vacancy_work_country(_vacancy(location="Remote")) is None
    assert _single_vacancy_work_country(_vacancy()) is None


# -- _adjust_country_relative_work_authorization (pure) ----------------------


def _work_auth_item() -> ClassifiedField:
    field = DiscoveredField(
        label="Do you have legal authorisation to work in the country where this role is based?",
        field_type="yesno",
        required=True,
        options=["Yes", "No"],
    )
    return ClassifiedField(field=field, classification=FieldClassification.UNKNOWN_REQUIRED, kind=QuestionKind.UNKNOWN)


def test_adjust_country_relative_work_authorization_resolves_explicit_country_fact() -> None:
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": True}]})
    item = _work_auth_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Berlin, Germany"))
    assert adjusted.fill is True
    assert adjusted.value is True
    assert adjusted.kind is QuestionKind.WORK_AUTHORIZATION
    assert adjusted.country == "germany"


def test_adjust_country_relative_work_authorization_resolves_explicit_negative_fact() -> None:
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": False}]})
    item = _work_auth_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Berlin, Germany"))
    assert adjusted.fill is True
    assert adjusted.value is False


def test_adjust_country_relative_work_authorization_stays_manual_without_a_matching_country_fact() -> None:
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": True}]})
    item = _work_auth_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Remote, Netherlands"))
    assert adjusted.fill is False


def test_adjust_country_relative_work_authorization_never_infers_from_citizenship_or_relocation() -> None:
    # Citizenship and relocation willingness must never substitute for an
    # explicit work_authorizations fact, even when both are set.
    profile = _profile(
        work_eligibility={"citizenship": ["Germany"]},
        application_policy={"relocation": {"willing": True}},
    )
    item = _work_auth_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Berlin, Germany"))
    assert adjusted.fill is False


def test_adjust_country_relative_work_authorization_stays_manual_with_ambiguous_vacancy_country() -> None:
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": True}]})
    item = _work_auth_item()
    adjusted = _adjust_country_relative_work_authorization(
        item, profile, _vacancy(location="Germany or Netherlands (Remote)")
    )
    assert adjusted.fill is False


def test_adjust_country_relative_work_authorization_recomputes_over_prior_resolution() -> None:
    # A prior resolution (e.g. the shared classifier's own sole-explicit-fact
    # fallback) must never be trusted as-is: this exact recognized wording is
    # always re-derived from the vacancy country and a matching explicit
    # fact, so a stale True here is overridden to the recomputed False.
    field = DiscoveredField(
        label="Do you have legal authorisation to work in the country where this role is based?",
        field_type="yesno",
    )
    item = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.WORK_AUTHORIZATION,
        country="germany",
    )
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": False}]})
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Berlin, Germany"))
    assert adjusted.fill is True
    assert adjusted.value is False
    assert adjusted.kind is QuestionKind.WORK_AUTHORIZATION
    assert adjusted.country == "germany"


def _sole_explicit_no_germany_item() -> ClassifiedField:
    # Mirrors the shared classifier's own sole-explicit-negative-fact
    # fallback for a generic country-relative phrase (see
    # `questions._is_work_authorization`): it marks the field filled False
    # from the candidate's only `work_authorizations` fact even though it
    # never checked the vacancy's own work country against that fact's
    # country. This adapter must never let that vacancy-blind answer stand.
    field = DiscoveredField(
        label="Do you have legal authorisation to work in the country where this role is based?",
        field_type="yesno",
        required=True,
        options=["Yes", "No"],
    )
    return ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=False,
        fill=True,
        kind=QuestionKind.WORK_AUTHORIZATION,
        country="germany",
    )


def test_adjust_country_relative_work_authorization_forces_manual_with_ambiguous_vacancy_country_despite_sole_no_fact() -> (
    None
):
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": False}]})
    item = _sole_explicit_no_germany_item()
    adjusted = _adjust_country_relative_work_authorization(
        item, profile, _vacancy(location="Germany or Netherlands (Remote)")
    )
    assert adjusted.fill is False


def test_adjust_country_relative_work_authorization_forces_manual_with_mismatched_vacancy_country_despite_sole_no_fact() -> (
    None
):
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": False}]})
    item = _sole_explicit_no_germany_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Remote, Netherlands"))
    assert adjusted.fill is False


def test_adjust_country_relative_work_authorization_resolves_no_for_matching_vacancy_country() -> None:
    profile = _profile(work_eligibility={"work_authorizations": [{"country": "Germany", "authorized": False}]})
    item = _sole_explicit_no_germany_item()
    adjusted = _adjust_country_relative_work_authorization(item, profile, _vacancy(location="Berlin, Germany"))
    assert adjusted.fill is True
    assert adjusted.value is False
    assert adjusted.kind is QuestionKind.WORK_AUTHORIZATION
    assert adjusted.country == "germany"


# -- upload_resume / upload_cover_letter_file (real AshbyAdapter methods) ---
#
# `test_service.py`'s cover-letter-file-input coverage only ever drives a
# fake adapter double, never these real methods together against two
# distinct file controls -- this exercises `_field_locator`/`_file_names`'s
# own id-scoped selector resolution through `_upload_file_to_field` instead,
# via a minimal page/locator double standing in for Playwright.


class _FakeFileInputLocator:
    """Stands in for the single `[id="..."]` locator `_field_locator`
    resolves a file control to -- just the `set_input_files` surface
    `_upload_file_to_field` actually calls on it."""

    def __init__(self, page: "_FakeFileUploadPage", element_id: str) -> None:
        self._page = page
        self._element_id = element_id

    def set_input_files(self, path: str, timeout: int | None = None) -> None:
        self._page.attach(self._element_id, Path(path).name)


class _FakeGraphQLRequest:
    """Stands in for `Response.request`'s `post_data_json` property (a real
    Playwright one is a property, not a method)."""

    def __init__(self, post_data: dict | None) -> None:
        self._post_data = post_data

    @property
    def post_data_json(self) -> dict | None:
        return self._post_data


class _FakeGraphQLResponse:
    """Stands in for a Playwright `Response` -- just `.ok`, `.json()`, and
    `.request`."""

    def __init__(self, *, post_data: dict | None, ok: bool = True, body: object = None) -> None:
        self.request = _FakeGraphQLRequest(post_data)
        self.ok = ok
        self._body = body if body is not None else {}

    def json(self) -> object:
        return self._body


def _commit_mutation_post_data(field_path: str) -> dict:
    return {"operationName": "ApiSetFormValueToFile", "variables": {"path": field_path}}


def _successful_commit_response(field_path: str) -> _FakeGraphQLResponse:
    return _FakeGraphQLResponse(
        post_data=_commit_mutation_post_data(field_path),
        ok=True,
        body={"data": {"setFormValueToFile": {"path": field_path}}},
    )


def _graphql_error_commit_response(field_path: str) -> _FakeGraphQLResponse:
    return _FakeGraphQLResponse(
        post_data=_commit_mutation_post_data(field_path),
        ok=True,
        body={"errors": [{"message": "upload failed"}]},
    )


class _FakeResponseInfo:
    def __init__(self) -> None:
        self.value: object = None


class _FakeExpectResponseContext:
    """Stands in for Playwright's `expect_response` context manager:
    resolves, at `__exit__` (after the `with`-block body, here
    `set_input_files`, already ran), to the first queued response the real
    predicate matches. Raises `PlaywrightTimeoutError` when none match,
    exactly like a real timed-out wait, exercising the adapter's own
    `except PlaywrightError` handling.
    """

    def __init__(self, page: "_FakeFileUploadPage", predicate, timeout_ms: int) -> None:
        self._page = page
        self._predicate = predicate
        self._timeout_ms = timeout_ms
        self._info = _FakeResponseInfo()

    def __enter__(self) -> _FakeResponseInfo:
        return self._info

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is not None:
            return False
        for response in self._page._queued_responses:
            if self._predicate(response):
                self._info.value = response
                if self._page._watched_path is not None:
                    self._page.watched_path_existed_at_commit = self._page._watched_path.exists()
                return False
        raise PlaywrightTimeoutError(f"Timeout {self._timeout_ms}ms exceeded waiting for response")


class _FakeUiFileItemLocator:
    """Stands in for the two class-scoped locators
    `_cover_letter_ui_file_item_visible` reads: `.count()` /
    `.nth(index).inner_text()` / `.nth(index).is_visible()` for the name
    item, `.count()` / `.first.is_visible()` for the delete control."""

    def __init__(self, count: int, text: str = "", visible: bool = True) -> None:
        self._count = count
        self._text = text
        self._visible = visible

    def count(self) -> int:
        return self._count

    def nth(self, index: int) -> "_FakeUiFileItemLocator":
        return _FakeUiFileItemLocator(1, self._text, self._visible)

    @property
    def first(self) -> "_FakeUiFileItemLocator":
        return self.nth(0)

    def inner_text(self) -> str:
        return self._text

    def is_visible(self) -> bool:
        return self._visible


class _FakeFileUploadPage:
    """Duck-types `.locator(selector)` / `.evaluate(script, arg)` /
    `.expect_response(predicate, timeout=...)` / `.wait_for_timeout(ms)` --
    the full `Page` surface the resume and cover-letter upload/verify paths
    touch. Tracks the attached filename per element id, keyed the same way
    `_file_names`'s readback re-derives the `[id="..."]` selector.
    `_queued_responses` models the network responses `expect_response`'s
    predicate would see; an empty queue models a bounded wait that times
    out with no matching response. `ui_file_item_visible` (True by default,
    so pre-existing success/error/no-response tests pass unmodified) models
    whether the frontend has rendered the committed file item for the
    last-attached filename in a no-longer-loading state; a dedicated test
    below sets it False to reproduce an acknowledged-but-not-yet-rendered
    commit."""

    def __init__(
        self,
        *,
        queued_responses: list[_FakeGraphQLResponse] | None = None,
        watched_path: Path | None = None,
        ui_file_item_visible: bool = True,
    ) -> None:
        self._attached: dict[str, list[str]] = {}
        self._queued_responses: list[_FakeGraphQLResponse] = queued_responses or []
        self._watched_path = watched_path
        self.watched_path_existed_at_commit: bool | None = None
        self._ui_file_item_visible = ui_file_item_visible
        self._last_attached_filename: str | None = None

    def attach(self, element_id: str, filename: str) -> None:
        self._attached[element_id] = [filename]
        self._last_attached_filename = filename

    def locator(self, selector: str) -> object:
        id_match = re.search(r'\[id="([^"]+)"\]', selector)
        if id_match:
            return _FakeFileInputLocator(self, id_match.group(1))
        if "file-item-name" in selector:
            if self._ui_file_item_visible and self._last_attached_filename:
                return _FakeUiFileItemLocator(1, self._last_attached_filename)
            return _FakeUiFileItemLocator(0)
        if "file-item-delete" in selector:
            return _FakeUiFileItemLocator(1 if self._ui_file_item_visible else 0)
        raise AssertionError(f"unexpected file-control selector: {selector}")

    def evaluate(self, script: str, arg: str) -> list[str]:
        match = re.search(r'\[id="([^"]+)"\]', arg)
        assert match, f"unexpected file-control readback selector: {arg}"
        return list(self._attached.get(match.group(1), []))

    def expect_response(self, predicate, timeout: int | None = None) -> _FakeExpectResponseContext:
        return _FakeExpectResponseContext(self, predicate, timeout or 0)

    def wait_for_timeout(self, ms: int) -> None:
        return None


def test_upload_resume_and_upload_cover_letter_file_target_distinct_controls_via_real_adapter_methods() -> None:
    page = _FakeFileUploadPage(queued_responses=[_successful_commit_response("cover-letter-uuid")])
    adapter = AshbyAdapter()
    resume_field = DiscoveredField(
        label="Resume",
        field_type="file",
        element_id="resume-input-id",
        context="_systemfield_resume",
    )
    cover_letter_field = DiscoveredField(
        label="Cover Letter",
        field_type="file",
        element_id="cover-letter-input-id",
        context="cover-letter-uuid",
    )
    resume_path = Path("/tmp/ada-example-resume.txt")
    cover_letter_path = Path("/tmp/ada-example-cover-letter.pdf")

    assert adapter.upload_resume(page, resume_path, resume_field) is True
    assert adapter.upload_cover_letter_file(page, cover_letter_path, cover_letter_field) is True

    # Each control only ever received its own file, under its own element id
    # -- never the other control's file, and never raw prose text.
    assert page._attached["resume-input-id"] == ["ada-example-resume.txt"]
    assert page._attached["cover-letter-input-id"] == ["ada-example-cover-letter.pdf"]
    assert "ada-example-cover-letter.pdf" not in page._attached["resume-input-id"]
    assert "ada-example-resume.txt" not in page._attached["cover-letter-input-id"]

    assert adapter.read_back(page, resume_field) == "ada-example-resume.txt"
    assert adapter.read_back(page, cover_letter_field) == "ada-example-cover-letter.pdf"


def test_upload_resume_returns_false_before_readback_confirms_the_filename() -> None:
    # `_upload_file_to_field` must only ever return True once the page's own
    # readback confirms the exact filename attached -- a `set_input_files`
    # call that silently attaches nothing (modeled here by a page that never
    # records the attachment) must stay False, never trusted merely because
    # the call itself did not raise.
    class _SilentFileUploadPage(_FakeFileUploadPage):
        def attach(self, element_id: str, filename: str) -> None:
            return None

    page = _SilentFileUploadPage()
    adapter = AshbyAdapter()
    resume_field = DiscoveredField(
        label="Resume",
        field_type="file",
        element_id="resume-input-id",
        context="_systemfield_resume",
    )
    assert adapter.upload_resume(page, Path("/tmp/ada-example-resume.txt"), resume_field) is False


# -- upload_cover_letter_file commit-mutation wait: unlike the resume
# control, the cover-letter control's raw `FileList` updates instantly
# regardless of whether the server ever actually commits, so
# `_upload_cover_letter_file_to_field` must wait for this field's own
# `ApiSetFormValueToFile` response and trust only an HTTP- and
# GraphQL-successful body. ---------------------------------------------


def _cover_letter_field(element_id: str = "cover-letter-input-id") -> DiscoveredField:
    return DiscoveredField(
        label="Cover Letter",
        field_type="file",
        element_id=element_id,
        context="cover-letter-uuid",
    )


def test_upload_cover_letter_file_fails_when_no_commit_response_arrives_despite_filelist_update(
    tmp_path: Path,
) -> None:
    # The FileList updates immediately (a bare readback would have falsely
    # reported success), but no matching commit response ever arrives
    # within the bounded wait (an empty response queue).
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    page = _FakeFileUploadPage(watched_path=pdf_path)
    adapter = AshbyAdapter()
    field = _cover_letter_field()

    result = adapter.upload_cover_letter_file(page, pdf_path, field)

    assert result is False
    # Failed due to the missing acknowledgement, not the write itself.
    assert page._attached["cover-letter-input-id"] == ["cover_letter.pdf"]
    assert pdf_path.exists()


def test_upload_cover_letter_file_confirms_only_once_matching_commit_response_arrives(
    tmp_path: Path,
) -> None:
    # Mirror image: a response matching this field's own commit mutation
    # arrives, is HTTP- and GraphQL-successful, and is trusted.
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FakeFileUploadPage(
        queued_responses=[_successful_commit_response(field.context)], watched_path=pdf_path
    )
    adapter = AshbyAdapter()

    result = adapter.upload_cover_letter_file(page, pdf_path, field)

    assert result is True
    assert page.watched_path_existed_at_commit is True
    assert pdf_path.exists()


def test_upload_cover_letter_file_fails_on_graphql_error_in_matching_commit_response(
    tmp_path: Path,
) -> None:
    # A matching response whose GraphQL body carries `errors` must never be
    # trusted just because a matching response arrived at all.
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FakeFileUploadPage(queued_responses=[_graphql_error_commit_response(field.context)])
    adapter = AshbyAdapter()

    assert adapter.upload_cover_letter_file(page, pdf_path, field) is False


def test_upload_cover_letter_file_ignores_a_different_fields_own_commit_response(tmp_path: Path) -> None:
    # A commit response for a *different* field's own path (e.g. the
    # resume control's) must never be mistaken for this field's own.
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FakeFileUploadPage(queued_responses=[_successful_commit_response("_systemfield_resume")])
    adapter = AshbyAdapter()

    assert adapter.upload_cover_letter_file(page, pdf_path, field) is False


class _FileListClearedAfterCommitPage(_FakeFileUploadPage):
    """Models React Dropzone clearing/resetting the native input's
    `FileList` once the upload commits, even though the committed file
    keeps rendering in the UI. `attach` still drives
    `_last_attached_filename` (so the UI file-item fake keeps rendering the
    committed name) but immediately empties the recorded `FileList`."""

    def attach(self, element_id: str, filename: str) -> None:
        super().attach(element_id, filename)
        self._attached[element_id] = []


def test_upload_cover_letter_file_succeeds_when_native_filelist_is_cleared_after_commit(
    tmp_path: Path,
) -> None:
    # A FileList that React Dropzone has since cleared/reset must never
    # turn an otherwise-successful upload into a false negative.
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FileListClearedAfterCommitPage(
        queued_responses=[_successful_commit_response(field.context)], watched_path=pdf_path
    )
    adapter = AshbyAdapter()

    result = adapter.upload_cover_letter_file(page, pdf_path, field)

    assert result is True
    # Left empty -- success here never depended on reading it back.
    assert page._attached["cover-letter-input-id"] == []


def test_upload_cover_letter_file_never_attaches_to_the_resume_controls_element_id(tmp_path: Path) -> None:
    # The two file controls must never be confused under the
    # commit-response-wait path either, mirroring the resume-path guard
    # above.
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FakeFileUploadPage(queued_responses=[_successful_commit_response(field.context)])
    adapter = AshbyAdapter()

    assert adapter.upload_cover_letter_file(page, pdf_path, field) is True
    assert page._attached["cover-letter-input-id"] == ["cover_letter.pdf"]
    assert "resume-input-id" not in page._attached


# -- _cover_letter_ui_file_item_visible: the final UI-visibility proof
# `_upload_cover_letter_file_to_field` requires on top of the server
# commit-mutation acknowledgement. -------------------------------------


class _FakeUiScopedPage:
    """Duck-types `.locator(selector)` -- returns the configured
    `_FakeUiFileItemLocator` fake, keyed by whether the selector targets
    the name-item or delete-control class."""

    def __init__(self, *, name_locator: object, delete_locator: object) -> None:
        self.selectors: list[str] = []
        self._name_locator = name_locator
        self._delete_locator = delete_locator

    def locator(self, selector: str) -> object:
        self.selectors.append(selector)
        if "file-item-name" in selector:
            return self._name_locator
        if "file-item-delete" in selector:
            return self._delete_locator
        raise AssertionError(f"unexpected selector: {selector}")


def test_cover_letter_ui_file_item_visible_true_for_matching_name_and_present_delete_control() -> None:
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "cover_letter.pdf"),
        delete_locator=_FakeUiFileItemLocator(1),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is True  # type: ignore[arg-type]
    assert all('[data-field-path="cover-letter-uuid"]' in selector for selector in page.selectors)


def test_cover_letter_ui_file_item_visible_false_while_still_loading() -> None:
    # Name item rendered, but the delete control (no-longer-loading
    # signal) has not appeared yet.
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "cover_letter.pdf"),
        delete_locator=_FakeUiFileItemLocator(0),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


def test_cover_letter_ui_file_item_visible_false_when_no_name_item_present() -> None:
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(0),
        delete_locator=_FakeUiFileItemLocator(1),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


def test_cover_letter_ui_file_item_visible_false_when_filename_text_mismatches() -> None:
    # A rendered item for an unrelated file (e.g. a leftover previous
    # upload) must never match this filename.
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "old_cover_letter.pdf"),
        delete_locator=_FakeUiFileItemLocator(1),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


def test_cover_letter_ui_file_item_visible_false_without_a_data_field_path_context() -> None:
    field = DiscoveredField(label="Cover Letter", field_type="file", context="")
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "cover_letter.pdf"),
        delete_locator=_FakeUiFileItemLocator(1),
    )
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


def test_cover_letter_ui_file_item_visible_false_when_matching_name_item_is_hidden() -> None:
    # Present in the DOM is not the same as shown to the user: a matching
    # name item with `is_visible() == False` (e.g. mid-transition, or
    # behind an inactive tab) must not pass.
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "cover_letter.pdf", visible=False),
        delete_locator=_FakeUiFileItemLocator(1),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


def test_cover_letter_ui_file_item_visible_false_when_delete_control_is_hidden() -> None:
    # Only a visible delete control counts as the no-longer-loading
    # signal; mere DOM presence is not enough.
    page = _FakeUiScopedPage(
        name_locator=_FakeUiFileItemLocator(1, "cover_letter.pdf"),
        delete_locator=_FakeUiFileItemLocator(1, visible=False),
    )
    field = DiscoveredField(label="Cover Letter", field_type="file", context="cover-letter-uuid")
    assert _cover_letter_ui_file_item_visible(page, field, "cover_letter.pdf") is False  # type: ignore[arg-type]


# -- upload_cover_letter_file: a successful server acknowledgement is still
# not proof the UI shows the file. ------------------------------------------


def test_upload_cover_letter_file_fails_closed_when_ui_never_shows_the_committed_file_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit mutation succeeds and the FileList updates, but the
    frontend never renders the committed file item within the bound --
    must fail closed rather than trust the server acknowledgement alone.
    """
    monkeypatch.setattr(ashby_module, "_COVER_LETTER_UI_FILE_ITEM_WAIT_MS", 10)
    pdf_path = tmp_path / "cover_letter.pdf"
    pdf_path.write_bytes(b"%PDF-1.3\n...\n%%EOF")
    field = _cover_letter_field()
    page = _FakeFileUploadPage(
        queued_responses=[_successful_commit_response(field.context)],
        watched_path=pdf_path,
        ui_file_item_visible=False,
    )
    adapter = AshbyAdapter()

    result = adapter.upload_cover_letter_file(page, pdf_path, field)

    assert result is False
    # Failed solely because the UI never rendered the item, not an earlier stage.
    assert page._attached["cover-letter-input-id"] == ["cover_letter.pdf"]
