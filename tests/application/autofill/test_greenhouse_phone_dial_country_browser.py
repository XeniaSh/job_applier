from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)

# Offline, test-local model of Greenhouse's modern `.phone-input` widget: a
# react-select-style dialing-country combobox (flag + code committed display,
# options worded "<Country> +<code>") beside a plain phone input. Behaviour
# variants are switched through `window.__mode`:
#   ok            click commits "<flag> +<code>"
#   swallow       click is ignored (display keeps whatever it showed)
#   blur_revert   commit, then blur resets the display to a stale value
#   async_revert  commit, then a later async re-render reverts it
#   prepend       commit also prepends the code onto the phone input
#   phone_blur_clears_country  commit is fine, but blurring the phone input
#                 afterwards clears the committed country display
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Phone dial country</title></head>
<body>
  <form id="application_form" data-ats="greenhouse" action="/job_applications" method="post">
    %(residence)s
    <div class="phone-input" id="phone-widget">
      %(phone_top)s
      <div class="phone-input__country">
        <div class="select">
          <div class="select__container">
            <label for="country">Country(*)</label>
            <div class="select__control">
              <div class="select__value-container">
                <div class="select__single-value" id="dial-display">%(initial)s</div>
                <div class="select__placeholder">Select...</div>
                <input id="country" role="combobox" aria-autocomplete="list" aria-controls="country-listbox">
              </div>
            </div>
          </div>
        </div>
      </div>
      <div id="country-listbox" role="listbox" hidden>
        %(options)s
      </div>
      %(phone_bottom)s
    </div>
    <div class="select__control" id="decoy">
      <div class="select__single-value" id="decoy-display">🇺🇿 +998</div>
    </div>
    <button type="submit" id="submit_app">Submit Application</button>
  </form>
  <script>
    window.__mode = "ok";
    window.__submitClicked = false;
    window.__formSubmitted = false;
    var STALE = %(stale)s;
    var input = document.getElementById("country");
    var listbox = document.getElementById("country-listbox");
    var display = document.getElementById("dial-display");
    var phone = document.getElementById("phone");
    phone.addEventListener("blur", function () {
      if (window.__mode === "phone_blur_clears_country") { display.textContent = ""; }
    });
    input.addEventListener("click", function () { listbox.hidden = false; });
    input.addEventListener("blur", function () {
      if (window.__mode === "blur_revert") { display.textContent = STALE; }
    });
    listbox.querySelectorAll("[role=option]").forEach(function (option) {
      option.addEventListener("click", function () {
        var mode = window.__mode;
        if (mode === "swallow") { return; }
        display.textContent = option.dataset.flag + " +" + option.dataset.code;
        if (mode === "async_revert") {
          setTimeout(function () { display.textContent = STALE; }, 150);
        }
        if (mode === "prepend") { phone.value = "+" + option.dataset.code + phone.value; }
        input.value = "";
        input.focus();
        listbox.hidden = true;
      });
    });
    document.getElementById("submit_app").addEventListener("click", function () {
      window.__submitClicked = true;
    });
    document.getElementById("application_form").addEventListener("submit", function (event) {
      event.preventDefault();
      window.__formSubmitted = true;
    });
  </script>
</body>
</html>
"""

_PHONE_INPUT = """<label for="phone">Phone</label>
      <input id="phone" name="job_application[phone]" type="tel">"""

_ALL_OPTIONS = """<div role="option" data-flag="🇩🇪" data-code="49">Germany +49</div>
        <div role="option" data-flag="🇺🇿" data-code="998">Uzbekistan +998</div>
        <div role="option" data-flag="🇨🇦" data-code="1">Canada +1</div>
        <div role="option" data-flag="🇺🇸" data-code="1">United States +1</div>"""

_RESIDENCE = """<label for="residence_country">What is your country of residence?</label>
    <select id="residence_country" name="residence_country" required>
      <option value="">Select...</option>
      <option value="Germany">Germany</option>
      <option value="Uzbekistan">Uzbekistan</option>
    </select>"""


def _profile(phone: str = "+998901234567", country: str | None = None) -> CandidateProfile:
    identity: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": phone,
    }
    if country:
        identity["country"] = country
    return CandidateProfile.model_validate(
        {
            "identity": identity,
            "professional_links": {"linkedin": "https://www.linkedin.com/in/ada-example-test"},
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
            "work_eligibility": {
                "work_authorizations": [{"country": "Germany", "authorized": True}],
                "requires_visa_sponsorship": False,
            },
            "sensitive": {"gender": "female"},
            "fill_sensitive_fields": False,
        }
    )


def _open(
    tmp_path: Path,
    *,
    initial: str = "",
    stale: str = "",
    residence: bool = False,
    mode: str = "ok",
    options: str = _ALL_OPTIONS,
    phone_first: bool = False,
    name: str = "phone_dial_country.html",
) -> BrowserSession:
    html = _PAGE % {
        "initial": initial,
        "stale": repr(stale).replace("'", '"'),
        "residence": _RESIDENCE if residence else "",
        "options": options,
        "phone_top": _PHONE_INPUT if phone_first else "",
        "phone_bottom": "" if phone_first else _PHONE_INPUT,
    }
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(path)
    session.page.evaluate(f"window.__mode = {mode!r}")
    return session


def _field(fields: list[DiscoveredField], element_id: str) -> DiscoveredField:
    return next(item for item in fields if item.element_id == element_id)


def _display(session: BrowserSession) -> str:
    return session.page.locator("#dial-display").inner_text().strip()


def _fill_dial(session: BrowserSession, profile: CandidateProfile) -> tuple[GreenhouseAdapter, DiscoveredField, bool]:
    adapter = GreenhouseAdapter()
    field = _field(adapter.discover_fields(session.page), "country")
    assert field.field_type == "phone_dial_country"
    classified = classify_field(field, profile)
    assert classified.kind is QuestionKind.PHONE_DIAL_COUNTRY
    return adapter, field, adapter.fill_field(session.page, classified)


def _assert_no_submit(session: BrowserSession) -> None:
    assert session.page.evaluate("window.__submitClicked") is False
    assert session.page.evaluate("window.__formSubmitted") is False


def test_selects_uzbekistan_for_plus_998(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+998901234567"))
        assert ok is True
        assert _display(session) == "🇺🇿 +998"
        assert adapter.read_back(session.page, field) == "+998"
        assert session.page.locator("#country").input_value() == ""
        _assert_no_submit(session)
    finally:
        session.close()


def test_other_unique_code_is_resolved_from_displayed_options(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+4915123456789"))
        assert ok is True
        assert _display(session) == "🇩🇪 +49"
        assert adapter.read_back(session.page, field) == "+49"
    finally:
        session.close()


def test_ambiguous_shared_plus_one_fails_closed(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+15555550100"))
        assert ok is False
        assert _display(session) == ""
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_code_missing_from_options_fails_closed(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+996555012345"))
        assert ok is False
        assert _display(session) == ""
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_swallowed_click_is_not_confirmed_by_decoy_display(tmp_path: Path) -> None:
    session = _open(tmp_path, mode="swallow")
    try:
        adapter, field, ok = _fill_dial(session, _profile("+998901234567"))
        assert ok is False
        assert _display(session) == ""
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_stale_wrong_country_is_rejected(tmp_path: Path) -> None:
    session = _open(tmp_path, initial="🇩🇪 +49", mode="swallow")
    try:
        adapter, field, ok = _fill_dial(session, _profile("+998901234567"))
        assert ok is False
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


@pytest.mark.parametrize("mode", ["blur_revert", "async_revert"])
def test_reverted_selection_is_not_confirmed(tmp_path: Path, mode: str) -> None:
    session = _open(tmp_path, stale="🇩🇪 +49", mode=mode)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+998901234567"))
        assert ok is False
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_read_back_without_fill_proof_is_none(tmp_path: Path) -> None:
    session = _open(tmp_path, initial="🇺🇿 +998")
    try:
        adapter = GreenhouseAdapter()
        field = _field(adapter.discover_fields(session.page), "country")
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_later_revert_after_fill_is_caught_by_independent_read_back(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+998901234567"))
        assert ok is True
        session.page.evaluate("document.getElementById('dial-display').textContent = '🇩🇪 +49'")
        assert adapter.read_back(session.page, field) is None
        session.page.evaluate("document.getElementById('dial-display').textContent = ''")
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_plain_phone_keeps_full_international_digits_without_duplicate_code(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        profile = _profile("+998901234567")
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        dial = _field(fields, "country")
        phone = classify_field(_field(fields, "phone"), profile)
        assert phone.kind is QuestionKind.PHONE
        assert adapter.fill_field(session.page, classify_field(dial, profile)) is True
        assert adapter.fill_field(session.page, phone) is True
        assert session.page.locator("#phone").input_value() == "+998901234567"
        assert adapter.read_back(session.page, dial) == "+998"
    finally:
        session.close()


def test_formatter_prepend_is_restored_when_phone_filled_before_country(tmp_path: Path) -> None:
    session = _open(tmp_path, mode="prepend", phone_first=True)
    try:
        profile = _profile("+998901234567")
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        dial = _field(fields, "country")
        phone = classify_field(_field(fields, "phone"), profile)
        assert adapter.fill_field(session.page, phone) is True
        assert adapter.fill_field(session.page, classify_field(dial, profile)) is True
        assert session.page.locator("#phone").input_value() == "+998901234567"
        assert _display(session) == "🇺🇿 +998"
        assert adapter.read_back(session.page, dial) == "+998"
        _assert_no_submit(session)
    finally:
        session.close()


def test_formatter_prepend_does_not_double_code_when_country_filled_first(tmp_path: Path) -> None:
    session = _open(tmp_path, mode="prepend")
    try:
        profile = _profile("+998901234567")
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        dial = _field(fields, "country")
        phone = classify_field(_field(fields, "phone"), profile)
        assert adapter.fill_field(session.page, classify_field(dial, profile)) is True
        assert session.page.locator("#phone").input_value() == "+998"
        assert adapter.fill_field(session.page, phone) is True
        assert session.page.locator("#phone").input_value() == "+998901234567"
        assert adapter.read_back(session.page, dial) == "+998"
    finally:
        session.close()


@pytest.mark.parametrize("phone_first", [False, True])
def test_full_service_formatter_confirms_both_fields_with_exact_digits(
    tmp_path: Path, phone_first: bool
) -> None:
    session = _open(tmp_path, mode="prepend", phone_first=phone_first)
    try:
        service = AutofillService(resolver=_UnusedResolver())
        result = service._fill_open_page(
            _vacancy(tmp_path / "phone_dial_country.html"),
            _profile("+998901234567"),
            Path("unused_resume.pdf"),
            session,
            GreenhouseAdapter(),
        )
        filled_labels = [record.label.lower() for record in result.filled_fields]
        assert any("phone dialing country" in label for label in filled_labels)
        assert any(label.startswith("phone") and "dialing" not in label for label in filled_labels)
        assert not any("phone" in record.label.lower() for record in result.unresolved_required_fields)
        assert session.page.locator("#phone").input_value() == "+998901234567"
        assert _display(session) == "🇺🇿 +998"
        _assert_no_submit(session)
    finally:
        session.close()


def test_single_visible_us_plus_one_fails_closed(tmp_path: Path) -> None:
    option = '<div role="option" data-flag="🇺🇸" data-code="1">United States +1</div>'
    session = _open(tmp_path, options=option)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+15555550100"))
        assert ok is False
        assert _display(session) == ""
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_single_visible_kazakhstan_plus_seven_fails_closed(tmp_path: Path) -> None:
    option = '<div role="option" data-flag="🇰🇿" data-code="7">Kazakhstan +7</div>'
    session = _open(tmp_path, options=option)
    try:
        adapter, field, ok = _fill_dial(session, _profile("+77011234567"))
        assert ok is False
        assert _display(session) == ""
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


def test_one_adapter_does_not_carry_proof_or_phone_state_across_pages(tmp_path: Path) -> None:
    # One adapter, two pages opened strictly one after the other (a second
    # sync_playwright runtime cannot start while the first session is open).
    profile = _profile("+998901234567")
    adapter = GreenhouseAdapter()
    first = _open(tmp_path, name="first.html")
    try:
        first_dial = _field(adapter.discover_fields(first.page), "country")
        first_phone = classify_field(_field(adapter.discover_fields(first.page), "phone"), profile)
        assert adapter.fill_field(first.page, classify_field(first_dial, profile)) is True
        assert adapter.fill_field(first.page, first_phone) is True
        assert adapter.read_back(first.page, first_dial) == "+998"
    finally:
        first.close()

    second = _open(tmp_path, name="second.html")
    try:
        # Second page: blank phone, no selection yet. The first page's proof
        # must not verify it, and the dial fill must still work (a prior phone
        # write must not make the blank phone fail it).
        second_dial = _field(adapter.discover_fields(second.page), "country")
        assert adapter.read_back(second.page, second_dial) is None
        assert adapter.fill_field(second.page, classify_field(second_dial, profile)) is True
        assert adapter.read_back(second.page, second_dial) == "+998"
        assert second.page.locator("#phone").input_value() == ""
    finally:
        second.close()


class _UnusedResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        raise AssertionError("resolver must never be called: _fill_open_page is invoked directly")


def _vacancy(path: Path) -> ResolvedVacancy:
    uri = path.resolve().as_uri()
    return ResolvedVacancy(
        source="target_company:greenhouse:evidence",
        external_id="1",
        title="Senior Backend Engineer",
        company="Evidence Co",
        url=uri,
        application_url=uri,
    )


def test_full_service_confirms_phone_country_and_phone_with_differing_residence(tmp_path: Path) -> None:
    session = _open(tmp_path, residence=True)
    try:
        profile = _profile("+998901234567", country="Germany")
        service = AutofillService(resolver=_UnusedResolver())
        result = service._fill_open_page(
            _vacancy(tmp_path / "phone_dial_country.html"),
            profile,
            Path("unused_resume.pdf"),
            session,
            GreenhouseAdapter(),
        )

        filled_labels = [record.label.lower() for record in result.filled_fields]
        assert any("phone dialing country" in label for label in filled_labels)
        assert not any(
            "phone dialing country" in record.label.lower() for record in result.unresolved_required_fields
        )
        assert _display(session) == "🇺🇿 +998"
        assert session.page.locator("#phone").input_value() == "+998901234567"
        # Residence stays Germany: the phone country never leaks into it, nor
        # residence into the phone country.
        assert session.page.locator("#residence_country").input_value() == "Germany"
        _assert_no_submit(session)
    finally:
        session.close()


def test_full_service_reports_unconfirmed_phone_country_manually(tmp_path: Path) -> None:
    session = _open(tmp_path, mode="swallow")
    try:
        service = AutofillService(resolver=_UnusedResolver())
        result = service._fill_open_page(
            _vacancy(tmp_path / "phone_dial_country.html"),
            _profile("+998901234567", country="Uzbekistan"),
            Path("unused_resume.pdf"),
            session,
            GreenhouseAdapter(),
        )
        assert not any("phone dialing country" in record.label.lower() for record in result.filled_fields)
        assert any("phone dialing country" in record.label.lower() for record in result.unresolved_required_fields)
        assert any("Phone dialing country selection" in warning for warning in result.warnings)
        _assert_no_submit(session)
    finally:
        session.close()


def test_full_service_phone_blur_clearing_country_is_not_reported_filled(tmp_path: Path) -> None:
    session = _open(tmp_path, mode="phone_blur_clears_country")
    try:
        service = AutofillService(resolver=_UnusedResolver())
        result = service._fill_open_page(
            _vacancy(tmp_path / "phone_dial_country.html"),
            _profile("+998901234567"),
            Path("unused_resume.pdf"),
            session,
            GreenhouseAdapter(),
        )
        assert _display(session) == ""
        assert not any("phone dialing country" in record.label.lower() for record in result.filled_fields)
        assert any("phone dialing country" in record.label.lower() for record in result.unresolved_required_fields)
        assert any("Phone dialing country" in warning for warning in result.warnings)
        _assert_no_submit(session)
    finally:
        session.close()


@pytest.mark.parametrize("breakage", ["removed", "duplicated"])
def test_final_check_fails_closed_when_own_phone_missing_or_ambiguous(tmp_path: Path, breakage: str) -> None:
    session = _open(tmp_path)
    try:
        profile = _profile("+998901234567")
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        dial = _field(fields, "country")
        phone_field = _field(fields, "phone")
        assert adapter.fill_field(session.page, classify_field(dial, profile)) is True
        assert adapter.fill_field(session.page, classify_field(phone_field, profile)) is True
        assert adapter.final_phone_dial_check(session.page, dial, phone_field, "+998901234567") == (True, True)

        if breakage == "removed":
            session.page.evaluate("document.getElementById('phone').remove()")
        else:
            session.page.evaluate(
                "document.getElementById('phone-widget').insertAdjacentHTML("
                "'beforeend', '<input id=\"phone2\" type=\"tel\" value=\"+998901234567\">')"
            )
        # Supplied-field and no-phone-field fallback paths both stay unresolved.
        assert adapter.final_phone_dial_check(session.page, dial, phone_field, "+998901234567")[0] is False
        assert adapter.final_phone_dial_check(session.page, dial, None, None) == (False, None)
        _assert_no_submit(session)
    finally:
        session.close()


def test_full_service_phone_removed_after_fill_leaves_dial_unresolved(tmp_path: Path) -> None:
    class _RemovingAdapter(GreenhouseAdapter):
        def read_back(self, page, field):  # type: ignore[no-untyped-def]
            result = super().read_back(page, field)
            # Disappear after the initial successful read-back, so this
            # exercises final verification rather than initial input lookup.
            if field.element_id == "phone" and result:
                page.evaluate("document.getElementById('phone').remove()")
            return result

    session = _open(tmp_path)
    try:
        service = AutofillService(resolver=_UnusedResolver())
        result = service._fill_open_page(
            _vacancy(tmp_path / "phone_dial_country.html"),
            _profile("+998901234567"),
            Path("unused_resume.pdf"),
            session,
            _RemovingAdapter(),
        )
        assert not any("phone dialing country" in record.label.lower() for record in result.filled_fields)
        assert any("phone dialing country" in record.label.lower() for record in result.unresolved_required_fields)
        assert any("Phone dialing country" in warning for warning in result.warnings)
        _assert_no_submit(session)
    finally:
        session.close()


def test_final_check_rejects_phone_cleared_after_both_were_verified(tmp_path: Path) -> None:
    session = _open(tmp_path)
    try:
        profile = _profile("+998901234567")
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        dial = _field(fields, "country")
        phone_field = _field(fields, "phone")
        phone = classify_field(phone_field, profile)
        assert adapter.fill_field(session.page, classify_field(dial, profile)) is True
        assert adapter.fill_field(session.page, phone) is True
        assert adapter.final_phone_dial_check(session.page, dial, phone_field, "+998901234567") == (True, True)

        for leftover in ("", "998", "+998"):
            session.page.locator("#phone").fill(leftover)
            # The permissive read-back still accepts an empty/code-only sibling...
            assert adapter.read_back(session.page, dial) == "+998"
            # ...but the strict final check never calls that phone complete.
            assert adapter.final_phone_dial_check(session.page, dial, phone_field, "+998901234567") == (
                False,
                False,
            )
        _assert_no_submit(session)
    finally:
        session.close()
