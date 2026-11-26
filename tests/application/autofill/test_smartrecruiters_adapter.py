"""Offline, real-browser tests for the unregistered SmartRecruiters Stage 1 adapter.

Every `https://jobs.smartrecruiters.com/...` request is intercepted and served from the synthetic
fixture `smartrecruiters_page_one.html`; any other host is aborted and recorded, so there is no
outbound network. Fixture behaviours beyond docs/smartrecruiters-form-inspection.md are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from pydantic import ValidationError

from app.application.autofill import smartrecruiters as sr_module
from app.application.autofill.ashby import AshbyAdapter
from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillStatus, FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService, default_adapter_for_source
from app.application.autofill.smartrecruiters import (
    PARTIAL_STAGE_REASON,
    SmartRecruitersAdapter,
    SmartRecruitersFormError,
)
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy
from app.resume_profiles import ResumeProfile, ResumeProfiles
from app.telegram.client import source_supports_application_prepare
from tests.pdf_fixtures import build_labeled_valid_pdf

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)

FIXTURE = Path("tests/fixtures/autofill/smartrecruiters_page_one.html")
RESUME_TXT = Path("tests/fixtures/autofill/resume.txt")
HOST = "jobs.smartrecruiters.com"
PUBLICATION = "11111111-2222-3333-4444-555555555555"
OTHER_PUBLICATION = "99999999-2222-3333-4444-555555555555"
POSTING_ID = "744000123456789"
POSTING_URL = f"https://{HOST}/Wise/{POSTING_ID}-senior-engineer"
APP_PATH = f"/oneclick-ui/company/Wise/publication/{PUBLICATION}"
APP_URL = f"https://{HOST}{APP_PATH}"
OTHER_APP_URL = f"https://{HOST}/oneclick-ui/company/Wise/publication/{OTHER_PUBLICATION}"
CANVA_APP_URL = f"https://{HOST}/oneclick-ui/company/Canva/publication/{PUBLICATION}"
VARIANTS = ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")


def _posting_html(
    *, canonical: str = POSTING_URL, links: list[tuple[str, str, str]] | None = None, script: str = ""
) -> str:
    links = links if links is not None else [("I'm interested", APP_URL, "")]
    anchors = "".join(
        f'<a href="{href}" target="{target}">{text}</a>' if target else f'<a href="{href}">{text}</a>'
        for text, href, target in links
    )
    return (
        f'<html><head><link rel="canonical" href="{canonical}"></head><body>{anchors}'
        f"<script>{script}</script></body></html>"
    )


class _Site(BrowserSession):
    """BrowserSession with every request intercepted (no outbound network)."""

    def __init__(self, *, posting_html: str | None = None, sr_init: dict | None = None) -> None:
        super().__init__(headed=False, keep_open=False)
        self._posting_html = posting_html
        self._sr_init = sr_init or {}
        self._routed = False
        self.requests: list[str] = []
        self.blocked: list[str] = []

    def start(self) -> None:
        super().start()
        if self._routed:
            return
        self._routed = True
        self._context.add_init_script(f"window.SR_INIT = {json.dumps(self._sr_init)};")
        self._context.route("**/*", self._handle)

    def _handle(self, route, request) -> None:  # noqa: ANN001
        parts = urlsplit(request.url)
        if parts.hostname != HOST:
            self.blocked.append(str(parts.hostname))
            route.abort()
            return
        self.requests.append(parts.path)
        if parts.path.endswith("/screening"):
            body = "<html><body>synthetic screening stage</body></html>"
        elif parts.path.startswith("/oneclick-ui/"):
            body = FIXTURE.read_text()
        elif self._posting_html is not None and parts.path.startswith(f"/Wise/{POSTING_ID}"):
            body = self._posting_html
        else:
            route.fulfill(status=404, content_type="text/plain", body="not found")
            return
        route.fulfill(status=200, content_type="text/html", body=body)


def _adapter(**kwargs: object) -> SmartRecruitersAdapter:
    options: dict[str, object] = {
        "hydration_timeout_ms": 3000,
        "navigation_timeout_ms": 3000,
        "settle_timeout_ms": 700,
        "upload_timeout_ms": 1500,
        "poll_ms": 40,
        "stable_samples": 3,
    }
    options.update(kwargs)
    return SmartRecruitersAdapter(**options)  # type: ignore[arg-type]


def _profile(phone: str = "+37255501234", **overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": phone,
            "current_location": "Tallinn, Estonia",
            "country": "Estonia",
        },
        "professional_links": {"linkedin": "https://www.linkedin.com/in/ada-example-test"},
        "application_files": {"default_resume": str(RESUME_TXT)},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


@pytest.fixture(autouse=True)
def _no_markdown_profile(monkeypatch):
    monkeypatch.setattr("app.application.autofill.service._load_markdown_profile", lambda: "")


@pytest.fixture
def open_form():
    """One live site at a time: sync Playwright cannot start a second instance while another is
    running in the same thread, so each `_open` first checks and closes the previous site."""
    sites: list[_Site] = []

    def _open(sr_init: dict | None = None, *, url: str = APP_URL, adapter: SmartRecruitersAdapter | None = None):
        for previous in sites:
            blocked, previous.blocked = previous.blocked, []
            previous.close()
            assert blocked == []
        site = _Site(sr_init=sr_init)
        sites.append(site)
        site.open(url)
        adapter = adapter or _adapter()
        adapter.prepare_page(site.page)
        return site, adapter

    try:
        yield _open
        for site in sites:
            assert site.blocked == []
    finally:
        for site in sites:
            site.close()


def _classified(adapter: SmartRecruitersAdapter, page, name: str, profile: CandidateProfile | None = None):
    profile = profile or _profile()
    field = next(item for item in adapter.discover_fields(page) if item.name == name)
    return adapter.adjust_classified_field(classify_field(field, profile), profile=profile, vacancy=None)


def _fill(adapter: SmartRecruitersAdapter, page, name: str, profile: CandidateProfile | None = None):
    item = _classified(adapter, page, name, profile)
    return adapter.fill_field(page, item), adapter.read_back(page, item.field), item


def _shadow_js(body: str) -> str:
    return f"document.querySelector('oc-oneclick-form').shadowRoot.{body}"


def _uploads(page) -> list[str]:
    return page.evaluate("(window.__srUploads || []).map((u) => u.digest)")


def _pdf(directory: Path, label: str) -> Path:
    path = directory / "Resume.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_labeled_valid_pdf(label))
    return path


def _digest(path: Path) -> str:
    data = path.read_bytes()
    return f"{hashlib.sha256(data).hexdigest()}:{len(data)}"


# ----------------------------------------------------------------- discovery
def test_discovery_standard_fields_requiredness_and_excluded_import(open_form) -> None:
    site, adapter = open_form({"extraRequired": True})
    page = site.page
    page.evaluate(_shadow_js('querySelector(\'[data-test="web-profiles-facebook"] input\').required = true'))
    fields = {item.name: item for item in adapter.discover_fields(page)}
    for key in ("firstName", "lastName", "email", "emailConfirmation", "location", "countryCode", "phone", "resume"):
        assert key in fields
    assert fields["firstName"].required and fields["location"].required
    assert fields["phone"].required is False  # aria-required="false", no native required
    assert fields["resume"].required is True  # section marker only
    assert fields["facebook"].required is True  # native required without ARIA
    assert fields["countryCode"].field_type == "phone_dial_country"
    # observed duplicate id: the custom host and its native input share one id (2 bare, 1 native input)
    host_js = _shadow_js("querySelector('spl-input[id=\"first-name-input\"]')")
    bare, native = page.evaluate(
        f"""() => {{
  const host = {host_js};
  const inner = host.shadowRoot.querySelectorAll('[id="first-name-input"]').length;
  return [1 + inner, host.shadowRoot.querySelectorAll('input[id="first-name-input"]').length];
}}"""
    )
    assert (bare, native) == (2, 1)
    unknown =[item for item in fields.values() if item.field_type == "unknown"]
    assert {item.label for item in unknown} == {"Employer", "Why us?"}  # import input never discovered
    assert next(item for item in unknown if item.label == "Why us?").required is True


def test_unknown_duplicate_and_unsupported_known_controls_are_not_fillable(open_form) -> None:
    site, adapter = open_form({"extraRequired": True})
    page = site.page
    profile = _profile()
    for field in adapter.discover_fields(page):
        item = classify_field(field, profile)
        if field.name in {"facebook", "twitter", "website", "message"}:
            # even if the generic classifier (or generated answers) wanted to fill it
            item = replace(item, fill=True, value="x", classification=FieldClassification.SUPPORTED_DETERMINISTIC)
            item = replace(item, kind=QuestionKind.WEBSITE)
        adjusted = adapter.adjust_classified_field(item, profile=profile, vacancy=None)
        if field.field_type == "unknown" or field.name in {"facebook", "twitter", "website", "message"}:
            assert adjusted.classification is FieldClassification.UNSUPPORTED
            assert adjusted.fill is False and adjusted.value is None and adjusted.generated is False
            assert adapter.fill_field(page, adjusted) is False
    assert page.evaluate(_shadow_js('querySelector(\'[data-test="web-profiles-facebook"] input\').value')) == ""
    # a mismatched generic kind for a recognised control also stays manual
    field = next(item for item in adapter.discover_fields(page) if item.name == "firstName")
    wrong = replace(classify_field(field, profile), kind=QuestionKind.WEBSITE)
    assert adapter.adjust_classified_field(wrong, profile=profile, vacancy=None).fill is False


def test_profile_without_email_or_phone_is_not_a_valid_profile() -> None:
    # The production model rejects these identities, so no adapter branch for them can ever run.
    with pytest.raises(ValidationError):
        _profile(identity={"first_name": "Ada", "last_name": "Example"})
    with pytest.raises(ValidationError):
        _profile("")


def test_absent_residence_leaves_city_manual_with_a_valid_identity(open_form) -> None:
    site, adapter = open_form()
    page = site.page
    identity = {
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": "+37255501234",
    }
    profile = _profile(identity=identity)  # optional current_location/country absent
    assert profile.identity.current_location is None and profile.identity.country is None
    item = _classified(adapter, page, "location", profile)
    assert adapter.fill_field(page, replace(item, value=None)) is False
    assert adapter.read_back(page, item.field) is None
    assert site.page.evaluate(_shadow_js('querySelector(\'[data-test="personal-info-location"] input\').value')) == ""


# ------------------------------------------------------------- text / email
def test_text_fields_and_email_confirmation_require_exact_value(open_form) -> None:
    site, adapter = open_form()
    for key, expected in (
        ("firstName", "Ada"),
        ("lastName", "Example"),
        ("email", "ada.example@example.test"),
        ("emailConfirmation", "ada.example@example.test"),
    ):
        ok, read, _ = _fill(adapter, site.page, key)
        assert (ok, read) == (True, expected)
    # stale: the email changes after the confirmation was proven
    site.page.evaluate(_shadow_js('querySelector(\'[data-test="personal-info-email-input"] input\').value = "other@example.test"'))
    item = _classified(adapter, site.page, "emailConfirmation")
    assert adapter.read_back(site.page, item.field) is None


@pytest.mark.parametrize(
    "init", [{"confirmSuffix": True}, {"invalidOn": "Confirm your email"}], ids=["mismatch", "own-invalid"]
)
def test_email_confirmation_mismatch_or_invalid_is_not_confirmed(open_form, init) -> None:
    site, adapter = open_form(init)
    assert _fill(adapter, site.page, "email")[:2] == (True, "ada.example@example.test")
    _ok, read, _ = _fill(adapter, site.page, "emailConfirmation")
    assert read is None


def test_invalid_own_state_blocks_text_read_back(open_form) -> None:
    site, adapter = open_form({"invalidOn": "First name"})
    assert _fill(adapter, site.page, "firstName")[1] is None


def test_prepare_page_clears_previous_proofs_on_a_reused_instance(open_form) -> None:
    site, adapter = open_form()
    ok, read, item = _fill(adapter, site.page, "firstName")
    assert (ok, read) == (True, "Ada")
    adapter.prepare_page(site.page)
    assert adapter.read_back(site.page, item.field) is None


# --------------------------------------------------------------------- city
def test_city_exact_option_persists_after_blur(open_form) -> None:
    site, adapter = open_form()
    ok, read, _ = _fill(adapter, site.page, "location")
    assert (ok, read) == (True, "Tallinn, Estonia")


@pytest.mark.parametrize(
    "init",
    [
        {"cityOptions": ["Tallinn, Estonia", "Tallinn, Estonia"]},
        {"cityOptions": ["Tallinn, Virginia, United States"]},
        {"cityOptions": []},
    ],
    ids=["ambiguous", "no-exact", "no-options"],
)
def test_city_without_one_exact_option_is_never_chosen(open_form, init) -> None:
    site, adapter = open_form(init)
    ok, read, _ = _fill(adapter, site.page, "location")
    assert ok is False and read is None
    assert site.page.evaluate(_shadow_js('querySelector(\'[data-test="personal-info-location"] input\').value')) == ""


def test_city_reverted_or_cleared_after_commit_is_not_confirmed(open_form) -> None:
    site, adapter = open_form({"cityRevert": True})
    _ok, read, _ = _fill(adapter, site.page, "location")
    assert read is None
    site, adapter = open_form()
    ok, read, item = _fill(adapter, site.page, "location")
    assert (ok, read) == (True, "Tallinn, Estonia")
    site.page.evaluate(_shadow_js('querySelector(\'[aria-label="Clear City value"]\').click()'))
    assert adapter.read_back(site.page, item.field) is None


# ------------------------------------------------------------ dial / phone
def _display(page) -> str:
    return page.evaluate(
        _shadow_js(
            "querySelector('[slot=\"triggerPrefix\"]').textContent + '|' + "
            "document.querySelector('oc-oneclick-form').shadowRoot.querySelector('spl-country-flag').getAttribute('alt')"
        )
    )


@pytest.mark.parametrize("phone_first", [False, True], ids=["dial-first", "phone-first"])
@pytest.mark.parametrize(
    ("phone", "code", "country"),
    [("+37255501234", "372", "Estonia"), ("+998901234567", "998", "Uzbekistan")],
)
def test_dial_and_phone_in_both_dom_orders(open_form, phone_first, phone, code, country) -> None:
    site, adapter = open_form({"phoneFirst": phone_first})
    page = site.page
    profile = _profile(phone)
    order = ("phone", "countryCode") if phone_first else ("countryCode", "phone")
    results = {name: _fill(adapter, page, name, profile) for name in order}
    assert results["countryCode"][:2] == (True, f"+{code}")
    assert results["phone"][:2] == (True, f"+{phone[1:]}")
    assert _display(page).endswith(f"|{country}")
    fields = {item.name: item for item in adapter.discover_fields(page)}
    assert adapter.final_phone_dial_check(page, fields["countryCode"], fields["phone"], phone) == (True, True)
    assert adapter.final_phone_dial_check(page, fields["countryCode"], None, None) == (True, None)
    # exact national digits only: the typed value never contains the dial code
    assert page.evaluate(_shadow_js("querySelector('input[type=\"tel\"]').value")) == phone[1 + len(code) :]


@pytest.mark.parametrize("phone", ["+14155550100", "+447700900123", "07700900123", ""], ids=["+1", "+44", "national", "none"])
def test_shared_missing_or_non_international_phone_never_guesses_a_dial_code(open_form, phone) -> None:
    site, adapter = open_form()
    page = site.page
    before = _display(page)
    profile = _profile(phone or "+37255501234")  # a missing phone is modelled on the classified item
    dial = replace(
        classify_field(next(f for f in adapter.discover_fields(page) if f.name == "countryCode"), profile),
        value=f"+{phone.lstrip('+')}" if phone else "",
    )
    assert adapter.fill_field(page, dial) is False
    phone_item = _classified(adapter, page, "phone", profile)
    if not phone:
        phone_item = replace(phone_item, value=None)
    assert adapter.fill_field(page, phone_item) is False
    assert _display(page) == before
    assert page.evaluate(_shadow_js("querySelector('input[type=\"tel\"]').value")) == ""


def test_dial_failed_or_reverted_selection_is_not_proven(open_form) -> None:
    site, adapter = open_form({"dialClick": "ignore"})
    ok, read, _ = _fill(adapter, site.page, "countryCode")
    assert (ok, read) == (False, None)
    site, adapter = open_form({"dialRevert": True})
    ok, read, _ = _fill(adapter, site.page, "countryCode")
    assert (ok, read) == (False, None)


def test_phone_formatting_that_changes_digits_stays_manual(open_form) -> None:
    site, adapter = open_form({"phoneMangle": True})
    page = site.page
    assert _fill(adapter, page, "countryCode")[:2] == (True, "+372")
    ok, read, _ = _fill(adapter, page, "phone")
    assert ok is True and read is None
    fields = {item.name: item for item in adapter.discover_fields(page)}
    assert adapter.final_phone_dial_check(page, fields["countryCode"], fields["phone"], "+37255501234") == (False, False)


def test_dial_reverted_after_phone_fill_fails_final_check(open_form) -> None:
    site, adapter = open_form()
    page = site.page
    assert _fill(adapter, page, "countryCode")[1] == "+372"
    assert _fill(adapter, page, "phone")[1] == "+37255501234"
    page.evaluate(
        _shadow_js(
            "querySelector('spl-country-flag').setAttribute('alt', 'United Kingdom')"
        )
    )
    fields = {item.name: item for item in adapter.discover_fields(page)}
    assert adapter.final_phone_dial_check(page, fields["countryCode"], fields["phone"], "+37255501234")[0] is False


# ------------------------------------------------------------------- resume
def _resume_field(adapter, page):
    return next(item for item in adapter.discover_fields(page) if item.name == "resume")


def test_sequential_same_basename_uploads_are_tied_to_each_attempt(open_form, tmp_path) -> None:
    site, adapter = open_form()
    page = site.page
    field = _resume_field(adapter, page)
    first, second = _pdf(tmp_path / "a", "first"), _pdf(tmp_path / "b", "second")
    assert first.name == second.name and first.read_bytes() != second.read_bytes()
    assert adapter.upload_resume(page, first, field) is True
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is True
    assert adapter.read_back(page, field) == "Resume.pdf"
    assert _uploads(page) == [_digest(first)]
    assert adapter.upload_resume(page, second, field) is True  # no accumulated listeners: exactly +1 write
    assert _uploads(page) == [_digest(first), _digest(second)]
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is True
    assert adapter.verify_resume_attached(page, field, "Other.pdf") is False
    # Repeating the identical selection is a browser no-op (no `change` for an unchanged FileList):
    # fail closed, no new upload record, and no fresh attachment is claimed.
    assert adapter.upload_resume(page, second, field) is False
    assert _uploads(page) == [_digest(first), _digest(second)]
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is False


@pytest.mark.parametrize("mode", ["pending", "error", "disabled"])
def test_disabled_pending_or_error_widget_is_never_written(open_form, tmp_path, mode) -> None:
    init = {} if mode == "disabled" else {"upload": mode}
    site, adapter = open_form(init, adapter=_adapter(upload_timeout_ms=900))
    page = site.page
    field = _resume_field(adapter, page)
    if mode == "disabled":
        page.evaluate(_shadow_js("querySelector('[data-test=\"resume-upload\"] input[type=\"file\"]').disabled = true"))
    else:
        # a prior attempt (different content, same basename) is still in flight / failed
        assert adapter.upload_resume(page, _pdf(tmp_path / "prior", "prior"), field) is False
    before = _uploads(page)
    assert len(before) == (0 if mode == "disabled" else 1)
    assert adapter.upload_resume(page, _pdf(tmp_path / "next", "next"), field) is False
    assert _uploads(page) == before  # refused before any file write
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is False
    assert adapter.read_back(page, field) is None


def test_upload_byte_mismatch_is_rejected(open_form, tmp_path, monkeypatch) -> None:
    site, adapter = open_form()
    field = _resume_field(adapter, site.page)
    real = hashlib.sha256
    monkeypatch.setattr(sr_module, "hashlib", SimpleNamespace(sha256=lambda data=b"": real(data + b"x")))
    assert adapter.upload_resume(site.page, _pdf(tmp_path, "one"), field) is False
    assert adapter.verify_resume_attached(site.page, field, "Resume.pdf") is False


@pytest.mark.parametrize("mode", ["noop", "pending", "error"])
def test_upload_noop_pending_or_error_is_not_success(open_form, tmp_path, mode) -> None:
    site, adapter = open_form({"upload": mode}, adapter=_adapter(upload_timeout_ms=900))
    field = _resume_field(adapter, site.page)
    assert adapter.upload_resume(site.page, _pdf(tmp_path, mode), field) is False
    assert adapter.verify_resume_attached(site.page, field, "Resume.pdf") is False


def test_same_row_reuse_stays_manual(open_form, tmp_path) -> None:
    site, adapter = open_form({"upload": "sameRow"}, adapter=_adapter(upload_timeout_ms=900))
    field = _resume_field(adapter, site.page)
    assert adapter.upload_resume(site.page, _pdf(tmp_path / "a", "one"), field) is True
    # the second write only renames the stale row: a stale row is never success
    assert adapter.upload_resume(site.page, _pdf(tmp_path / "b", "two"), field) is False


def test_late_clear_removal_and_external_replacement_fail_verification(open_form, tmp_path) -> None:
    site, adapter = open_form({"lateClear": 500})
    page = site.page
    field = _resume_field(adapter, page)
    adapter.upload_resume(page, _pdf(tmp_path, "one"), field)
    page.wait_for_timeout(900)
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is False

    site, adapter = open_form()
    page = site.page
    field = _resume_field(adapter, page)
    assert adapter.upload_resume(page, _pdf(tmp_path / "x", "one"), field) is True
    page.evaluate(_shadow_js("querySelector('[aria-label^=\"Delete file\"]').click()"))
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is False

    site, adapter = open_form()
    page = site.page
    field = _resume_field(adapter, page)
    assert adapter.upload_resume(page, _pdf(tmp_path / "y", "one"), field) is True
    # another (non-adapter) write of different bytes under the same basename replaces the row
    page.locator('[data-test="resume-upload"] input[type="file"]').set_input_files(str(_pdf(tmp_path / "z", "two")))
    page.wait_for_timeout(500)
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is False


# ------------------------------------------------- lookups scoped to the form
_DECOYS = "document.querySelector('#decoys')"


def _decoy_state(page) -> dict:
    return page.evaluate(
        f"""() => {{
  const d = {_DECOYS};
  return {{
    email: d.querySelector('[data-test="personal-info-email-input"] input').value,
    city: d.querySelector('[data-test="personal-info-location"] input').value,
    tel: d.querySelector('input[type="tel"]').value,
    dial: d.querySelector('button[aria-label="Country code"]').textContent,
    files: d.querySelector('input[type="file"]').files.length,
    rows: d.querySelectorAll('.c-spl-file-list-item').length,
  }};
}}"""
    )


_DECOY_BASELINE = {"email": "ada.example@example.test", "city": "Tallinn, Estonia", "tel": "55501234",
                   "dial": "+372", "files": 0, "rows": 1}


def test_same_hook_decoys_outside_the_form_are_never_written(open_form, tmp_path) -> None:
    site, adapter = open_form({"decoys": True})
    page = site.page
    assert {item.name for item in adapter.discover_fields(page)} >= {"email", "location", "phone", "resume"}
    for name in ("email", "location", "countryCode", "phone"):
        ok, read, _ = _fill(adapter, page, name)
        assert ok is True and read
    field = _resume_field(adapter, page)
    assert adapter.upload_resume(page, _pdf(tmp_path, "one"), field) is True
    assert adapter.verify_resume_attached(page, field, "Resume.pdf") is True
    assert _decoy_state(page) == _DECOY_BASELINE  # the real form took every write, the decoys none


@pytest.mark.parametrize("key", ["email", "location", "countryCode", "phone", "resume"])
def test_decoy_outside_the_form_cannot_confirm_a_removed_real_control(open_form, tmp_path, key) -> None:
    site, adapter = open_form({"decoys": True})
    page = site.page
    field = _resume_field(adapter, page)
    for name in ("email", "location", "countryCode", "phone"):
        assert _fill(adapter, page, name)[1]
    assert adapter.upload_resume(page, _pdf(tmp_path, "one"), field) is True
    items = {name: _classified(adapter, page, name) for name in ("email", "location", "countryCode", "phone", "resume")}
    removed = {
        "email": '[data-test="personal-info-email-input"]',
        "location": '[data-test="personal-info-location"]',
        "countryCode": 'button[aria-label="Country code"]',
        "phone": '[data-test="personal-info-phone"]',
        "resume": '[data-test="resume-upload"]',
    }[key]
    page.evaluate(_shadow_js(f"querySelector('{removed}').remove()"))
    assert adapter.read_back(page, items[key].field) is None
    if key in {"countryCode", "phone"}:
        check = adapter.final_phone_dial_check(page, items["countryCode"].field, items["phone"].field, "+37255501234")
        assert check[0] is False
    if key == "resume":
        assert adapter.verify_resume_attached(page, items["resume"].field, "Resume.pdf") is False
        assert adapter.upload_resume(page, _pdf(tmp_path / "again", "two"), items["resume"].field) is False
    else:
        assert adapter.fill_field(page, items[key]) is False
    assert _decoy_state(page)["files"] == 0


# --------------------------------------------------------------- navigation
def _navigate(sr_init=None, **posting) -> tuple[_Site, SmartRecruitersAdapter]:
    site = _Site(posting_html=_posting_html(**posting), sr_init=sr_init)
    site.open(POSTING_URL)
    return site, _adapter()


def test_posting_navigates_to_validated_same_company_application() -> None:
    site, adapter = _navigate()
    try:
        adapter.prepare_page(site.page)
        assert site.page.url == APP_URL
        assert adapter.recognize(site.page) is True
        assert site.blocked == []
    finally:
        site.close()


@pytest.mark.parametrize(
    "posting",
    [
        {"links": [("I'm interested", CANVA_APP_URL, "")]},
        {"links": [("I'm interested", f"{APP_URL}/screening", "")]},
        {"links": [("I'm interested", "https://example.test/apply", "")]},
        {"links": [("I'm interested", APP_URL, "_blank")]},
        {"links": [("I'm interested", APP_URL, ""), ("Apply", OTHER_APP_URL, "")]},
        {"links": [("Share", APP_URL, "")]},
        {"canonical": f"https://{HOST}/Wise/744000123456000"},
    ],
    ids=["company", "screening", "host", "new-tab", "ambiguous", "no-link", "canonical"],
)
def test_untrusted_posting_or_link_is_rejected_without_navigation(posting) -> None:
    site, adapter = _navigate(**posting)
    try:
        with pytest.raises(SmartRecruitersFormError):
            adapter.prepare_page(site.page)
        assert site.page.url == POSTING_URL
        assert not any(path.startswith("/oneclick-ui/") for path in site.requests)
    finally:
        site.close()


def test_expected_posting_id_mismatch_is_rejected() -> None:
    site = _Site(posting_html=_posting_html())
    site.open(POSTING_URL)
    try:
        with pytest.raises(SmartRecruitersFormError):
            _adapter(expected_posting_id="744000123456000").prepare_page(site.page)
        _adapter(expected_posting_id=POSTING_ID).prepare_page(site.page)
        assert site.page.url == APP_URL
    finally:
        site.close()


def test_apply_href_changed_after_validation_is_never_clicked(monkeypatch) -> None:
    site, adapter = _navigate()
    calls = {"n": 0}
    real = adapter._check_canonical

    def mutate(page, posting) -> None:  # noqa: ANN001
        real(page, posting)
        calls["n"] += 1
        if calls["n"] == 2:  # the recheck right before the click
            page.evaluate(f"document.querySelector('a').href = '{OTHER_APP_URL}'")

    monkeypatch.setattr(adapter, "_check_canonical", mutate)
    try:
        with pytest.raises(SmartRecruitersFormError):
            adapter.prepare_page(site.page)
        assert site.page.url == POSTING_URL
        assert not any(path.startswith("/oneclick-ui/") for path in site.requests)
    finally:
        site.close()


def test_playwright_errors_are_sanitized_and_unchained(monkeypatch) -> None:
    site, adapter = _navigate()

    def boom(page) -> None:  # noqa: ANN001
        raise PlaywrightError("candidate-secret-value")

    monkeypatch.setattr(adapter, "_navigate_from_posting", boom)
    try:
        with pytest.raises(SmartRecruitersFormError) as caught:
            adapter.prepare_page(site.page)
        assert "secret" not in str(caught.value)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True
    finally:
        site.close()


def test_screening_url_hydration_challenge_and_foreign_pages(open_form) -> None:
    site = _Site()
    site.open(f"{APP_URL}/screening")
    try:
        adapter = _adapter()
        with pytest.raises(SmartRecruitersFormError):
            adapter.prepare_page(site.page)
        assert adapter.recognize(site.page) is False
    finally:
        site.close()
    foreign = BrowserSession(headed=False, keep_open=False)
    try:
        foreign.open_html_file(FIXTURE)  # file:// origin is never a trusted application URL
        assert _adapter().recognize(foreign.page) is False
    finally:
        foreign.close()
    site, adapter = open_form({"hydrateDelay": 500})  # prepare waits for hydration
    assert adapter.recognize(site.page) is True
    site.page.evaluate("document.body.append(Object.assign(document.createElement('div'), {className: 'g-recaptcha'}))")
    assert adapter.detect_challenge(site.page) == "captcha"
    slow, slow_adapter = open_form(
        {"hydrateDelay": 20_000}, adapter=_adapter(hydration_timeout_ms=400)
    )
    assert slow_adapter.recognize(slow.page) is False


@pytest.mark.parametrize("src,expected", [
    ("https://ct.captcha-delivery.com/c.js?token=SYNTHETIC-PRIVATE", "captcha"),
    ("https://geo.captcha-delivery.com/captcha/", "captcha"),
    ("https://captcha-delivery.com/c.js", "captcha"),
    ("https://captcha-delivery.com.evil.invalid/c.js", None),
    ("https://example.invalid/c.js?redirect=https://ct.captcha-delivery.com/", None),
])
def test_datadome_challenge_resource_uses_exact_host_and_sanitized_reason(open_form, monkeypatch, src, expected) -> None:
    site, adapter = open_form()
    # An inert resource marker reproduces the blocked loader without network.
    site.page.evaluate("""src => {
        const script=document.createElement('script');
        script.type='application/json'; script.src=src; document.body.append(script);
    }""", src)
    assert adapter.detect_challenge(site.page) == expected
    if expected:
        def forbidden_hydration(_page):
            raise AssertionError("A detected challenge must stop hydration inspection.")

        monkeypatch.setattr(adapter, "_hydrated", forbidden_hydration)
        adapter._wait_hydrated(site.page)


# ------------------------------------------------- service (injected adapter)
class _Resolver:
    def __init__(self, title: str = "Engineer", normalized: NormalizedVacancy | None = None) -> None:
        self._title = title
        self._normalized = normalized

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        return ResolvedVacancy(
            source=source, external_id=external_id, title=self._title, company="Wise", url=POSTING_URL,
            application_url=APP_URL, vacancy=self._normalized,
        )


_HANDOFF_STATE_JS = """() => {
  const values = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches('input, textarea') && el.type !== 'file') values.push(el.value);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return {actions: window.__srActions || [], uploads: (window.__srUploads || []).length, values};
}"""


class _Handoff:
    """Owns the sites the service opens. At handoff it records what a reviewer would see: every
    Next/Submit click, Enter key or form submission seen by the page, the number of file writes and
    all text values."""

    def __init__(self, sr_init: dict | None = None) -> None:
        self.sr_init = sr_init
        self.sites: list[_Site] = []
        self.pages: list[object] = []
        self.states: list[dict] = []

    def factory(self) -> _Site:
        site = _Site(sr_init=self.sr_init)
        self.sites.append(site)
        return site

    def wait(self) -> None:
        page = self.sites[-1].page  # still open: the handoff has not released the browser yet
        assert not page.is_closed()
        self.pages.append(page)
        self.states.append(page.evaluate(_HANDOFF_STATE_JS))

    def assert_closed(self) -> None:
        assert self.sites
        for site in self.sites:
            assert site._page is None and site._context is None and site._browser is None
        assert all(page.is_closed() for page in self.pages)


@pytest.fixture
def handoff():
    created: list[_Handoff] = []

    def _make(sr_init: dict | None = None) -> _Handoff:
        item = _Handoff(sr_init)
        created.append(item)
        return item

    try:
        yield _make
        for item in created:
            assert all(site.blocked == [] for site in item.sites)
    finally:
        for item in created:
            for site in item.sites:
                site.close()


def _service(run: _Handoff, profiles: ResumeProfiles, **kwargs: object) -> AutofillService:
    options: dict[str, object] = {
        "resolver": _Resolver(),
        "profile_loader": _profile,
        "adapter": _SpyAdapter(),
        "browser_factory": run.factory,
        "wait_for_review": run.wait,
        "resume_profiles": profiles,
    }
    options.update(kwargs)
    return AutofillService(**options)  # type: ignore[arg-type]


class _SpyAdapter(SmartRecruitersAdapter):
    def __init__(self) -> None:
        super().__init__(
            hydration_timeout_ms=3000, navigation_timeout_ms=3000, settle_timeout_ms=700,
            upload_timeout_ms=1500, poll_ms=40, stable_samples=3,
        )
        self.uploaded: list[str] = []

    def upload_resume(self, page, resume_path, field) -> bool:  # noqa: ANN001
        ok = super().upload_resume(page, resume_path, field)
        if ok:
            self.uploaded.append(self._upload_proof.digest)
        return ok


class _NeverSubmit:
    def submit(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("Stage 1 must never submit")

    __call__ = submit


def _profiles(tmp_path: Path) -> tuple[ResumeProfiles, dict[str, Path]]:
    paths = {variant: _pdf(tmp_path / variant, variant) for variant in VARIANTS}
    profiles = tuple(ResumeProfile(id=v, description=v, pdf=p) for v, p in paths.items())
    return ResumeProfiles(profiles=profiles, default_profile_id="java_backend"), paths


@pytest.mark.parametrize("variant", VARIANTS)
def test_service_prepares_page_one_as_partial_manual_with_each_resume_variant(tmp_path, handoff, variant) -> None:
    profiles, paths = _profiles(tmp_path)
    run = handoff()
    adapter, ready = _SpyAdapter(), []
    service = _service(
        run,
        profiles,
        adapter=adapter,
        on_ready=ready.append,
        auto_submit_enabled=True,
        submit_adapter=_NeverSubmit(),
        resume_variant_override=variant,
    )
    result = service.run("target_company:smartrecruiters:wise", "1", keep_open=True)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert PARTIAL_STAGE_REASON in result.warnings
    assert result.submit_performed is False
    assert result.resume_variant == variant and result.resume_file == "Resume.pdf"
    assert adapter.uploaded == [_digest(paths[variant])]  # the selected variant's own bytes
    assert len(set(_digest(p) for p in paths.values())) == 4
    labels = {item.label for item in result.filled_fields}
    assert {"First name", "Email", "Confirm your email", "City", "Phone number", "Resume"} <= labels
    assert len(ready) == 1 and len(run.states) == 1  # the review handoff happened while the page was open
    state = run.states[0]
    assert state["actions"] == []  # no Next / Submit (type=button) click, Enter key or form submission
    assert state["uploads"] == 1 and "Ada" in state["values"]
    run.assert_closed()  # the session/page were actually closed after the handoff


def test_service_phone_first_order_and_unknown_required_stay_unresolved(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.application.autofill.service._load_markdown_profile", lambda: "")
    profiles, _paths = _profiles(tmp_path)
    asked: list[str] = []

    class _Generator:
        def generate(self, field, *args: object, **kwargs: object) -> str:  # noqa: ANN001
            asked.append(field.label)
            return "generated"

    service = AutofillService(
        resolver=_Resolver(),
        profile_loader=_profile,
        adapter=_SpyAdapter(),
        browser_factory=lambda: _Site(sr_init={"phoneFirst": True, "extraRequired": True}),
        wait_for_review=lambda: None,
        answer_generator=_Generator(),
        resume_profiles=profiles,
        resume_variant_override="java_backend",
    )
    result = service.run("target_company:smartrecruiters:wise", "1", keep_open=False)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    labels = {item.label for item in result.filled_fields}
    assert {"Country code", "Phone number"} <= labels
    assert "Why us?" in {item.label for item in result.unresolved_required_fields}
    assert "Why us?" not in labels and not any("Why us?" in label for label in asked)
    assert not any("could not be confirmed" in warning for warning in result.warnings)


def test_service_challenge_is_manual_without_writes_and_sanitized(tmp_path, handoff, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    profiles, _paths = _profiles(tmp_path)
    run = handoff({"challenge": True})
    adapter = _SpyAdapter()
    service = _service(
        run, profiles, adapter=adapter, auto_submit_enabled=True, submit_adapter=_NeverSubmit(),
        resume_variant_override="java_backend",
    )
    result = service.run("target_company:smartrecruiters:wise", "1", keep_open=True)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert any("Security challenge detected" in warning for warning in result.warnings)
    assert result.submit_performed is False and not result.filled_fields
    assert adapter.uploaded == []
    assert len(run.states) == 1
    state = run.states[0]
    assert state["actions"] == [] and state["uploads"] == 0
    assert all(value == "" for value in state["values"])  # not a single control was written
    shown = repr(result) + caplog.text
    for private in ("ada.example", "5501234", str(tmp_path)):
        assert private not in shown
    run.assert_closed()


# ------------------------------------------- default resume policy and gates
@pytest.mark.parametrize(
    ("expected", "title", "location", "description"),
    [
        ("java_backend", "Senior Backend Engineer", "Tallinn, Estonia", "Build and maintain Java services."),
        ("ai_backend", "Software Engineer, AI Integration", "London, United Kingdom", "Build LLM integrations."),
        ("java_backend_nz", "Senior Backend Engineer", "Auckland, New Zealand", "Build and maintain Java services."),
        ("ai_backend_nz", "Software Engineer, AI Integration", "Auckland, New Zealand", "Build LLM integrations."),
    ],
)
def test_default_resume_policy_selects_the_variant_without_an_override(
    tmp_path, handoff, expected, title, location, description
) -> None:
    profiles, paths = _profiles(tmp_path)
    normalized = NormalizedVacancy(
        source="target_company:smartrecruiters:wise",
        external_id="1",
        title=title,
        company="Wise",
        location=location,
        employment=None,
        description=description,
        url=POSTING_URL,
        published_at=None,
    )
    run, adapter = handoff(), _SpyAdapter()
    service = _service(run, profiles, adapter=adapter, resolver=_Resolver(title, normalized))  # no override
    result = service.run("target_company:smartrecruiters:wise", "1", keep_open=False)
    assert result.resume_variant == expected
    assert adapter.uploaded == [_digest(paths[expected])]  # the policy-selected variant's own bytes
    run.assert_closed()


def test_production_wise_prepare_gate_and_other_adapters_are_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("SMARTRECRUITERS_PREPARE_ENABLED", "true")
    assert source_supports_application_prepare("target_company:smartrecruiters:wise") is True
    adapter = default_adapter_for_source("target_company:smartrecruiters:wise")
    assert isinstance(adapter, SmartRecruitersAdapter)
    assert adapter._prepare_screening and adapter._require_posting_identity and not adapter.allow_auto_submit
    assert source_supports_application_prepare("target_company:smartrecruiters:canva") is False
    with pytest.raises(SmartRecruitersFormError):
        default_adapter_for_source("target_company:smartrecruiters:canva")
    assert type(default_adapter_for_source("target_company:lever:acme")) is LeverAdapter
    assert type(default_adapter_for_source("target_company:ashby:acme")) is AshbyAdapter
    assert type(default_adapter_for_source("target_company:greenhouse:acme")) is GreenhouseAdapter
    assert type(default_adapter_for_source("linkedin-email")) is GreenhouseAdapter
