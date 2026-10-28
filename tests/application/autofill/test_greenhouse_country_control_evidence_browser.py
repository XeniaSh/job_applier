from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.greenhouse import (
    GreenhouseAdapter,
    _combobox_field_help,
    _country_other_instructed,
    _is_phone_dial_country_combobox,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

FIXTURE = Path("tests/fixtures/autofill/greenhouse_country_control_evidence.html")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)

# These tests drive a real Chromium page (via `BrowserSession`/Playwright),
# never a Python re-implementation of `_COMBOBOX_FIELD_HELP_JS` or of
# `_is_phone_dial_country_combobox`'s `locator.evaluate` call -- unlike
# `test_greenhouse_country_fill_diagnostics_fake_dom.py`, which proves the
# extractor's *own* scoping logic by mirroring it in a fake-DOM harness, but
# can never prove that logic actually behaves the same way once it runs as
# real JavaScript against a real rendered DOM (the nested Stripe shape, a
# dynamically unmounted node, a genuinely swallowed click, a later-reverting
# selection). That gap is exactly what this module closes.


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "professional_links": {"linkedin": "https://www.linkedin.com/in/ada-example-test"},
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        "work_eligibility": {
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "sensitive": {"gender": "female"},
        "fill_sensitive_fields": False,
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def _open() -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(FIXTURE)
    return session


def _field(fields: list[DiscoveredField], element_id: str) -> DiscoveredField:
    for item in fields:
        if item.element_id == element_id:
            return item
    raise AssertionError(f"Field not found: {element_id} in {[item.element_id for item in fields]}")


# --- Stripe-shaped help text, against a real nested DOM -------------------


def test_stripe_help_sibling_is_extracted_over_real_nested_dom() -> None:
    """`.field-wrapper > .select > .select__container > label[for] +
    .select-shell > ... > input`, with the question's own help text a
    sibling of `.select` several levels above the label, and
    `aria-describedby` resolving only to the placeholder -- the exact
    Stripe shape evidenced live. A real browser's own DOM/CSSOM must
    resolve this the same way the fake-DOM mirror asserts.
    """
    session = _open()
    try:
        locator = session.page.locator("#stripe_country")
        context = _combobox_field_help(locator)
        assert context == "If you do not see your country in the list, please select Other."
    finally:
        session.close()


def test_stripe_help_without_sibling_never_returns_placeholder_or_bare_label() -> None:
    session = _open()
    try:
        locator = session.page.locator("#standalone_stripe_no_help")
        context = _combobox_field_help(locator)
        assert context == ""
        assert "Select" not in context
    finally:
        session.close()


def test_shared_wrapper_with_other_independent_control_is_rejected() -> None:
    """A direct-child label alone must never authorize a shared wrapper's
    full aggregate text when that wrapper also holds a second, genuinely
    different control that simply has no `label[for]` of its own -- the
    gap a `label[for]`-only check (looking only for an other *labelled*
    field) would miss.
    """
    session = _open()
    try:
        locator = session.page.locator("#standalone_shared_other_control")
        context = _combobox_field_help(locator)
        assert context == ""
    finally:
        session.close()


def test_shared_wrapper_with_hidden_dummy_input_is_still_trusted() -> None:
    """React-select's own hidden accessibility/form-sync input living in
    the same wrapper is part of this exact control, not a different one --
    it must never itself trigger the rejection above.
    """
    session = _open()
    try:
        locator = session.page.locator("#standalone_shared_hidden_dummy")
        context = _combobox_field_help(locator)
        assert context == "Country"
    finally:
        session.close()


def test_shared_wrapper_with_nested_label_help_text_and_other_control_is_rejected() -> None:
    """The otherControls guard must gate *every* ancestor-text return path,
    not only the older direct-child-label aggregate fallback: a genuine
    help-text sibling several levels above this control's own nested
    `label[for]` (the Stripe shape) must still be rejected when that same
    shared wrapper also holds a second, genuinely different control that
    simply carries no `label[for]` of its own. Before this guard ran
    early enough, that help text would have been collected into `parts`
    and returned regardless of the second control's presence.
    """
    session = _open()
    try:
        locator = session.page.locator("#standalone_nested_help_second_control")
        context = _combobox_field_help(locator)
        assert context == ""
        assert _country_other_instructed(context) is False
    finally:
        session.close()


# --- Own-instruction "Other" fallback, scoped confirmation -----------------


def test_other_fallback_confirms_locally_and_ignores_unrelated_other_sibling() -> None:
    """No live option matches an explicit, unmatched country value; this
    question's own help text instructs the "Other" fallback. An unrelated
    sibling control has shown "Other" the entire time -- the own-control
    confirmation must never be satisfied by it; only this control's own
    committed selection counts.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "stripe_country")
        assert "please select Other" in field.context
        classified = classify_field(field, _profile(identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "country": "Atlantis",
        }))
        assert classified.kind is QuestionKind.COUNTRY
        assert classified.value == "Atlantis"

        assert adapter.fill_field(session.page, classified) is True
        visible = session.page.locator("#stripe-country-wrapper .select__single-value").inner_text().strip()
        assert visible == "Other"
        assert adapter.read_back(session.page, classified.field) == "Other"

        sibling_visible = session.page.locator(
            "#other-sibling-field .select__single-value"
        ).inner_text().strip()
        assert sibling_visible == "Other"
    finally:
        session.close()


# --- Swallowed click must never be confirmed by an unrelated sibling ------


def test_swallowed_click_is_never_confirmed_by_sibling_showing_same_country() -> None:
    """The target control's own click handler never commits (models a
    genuinely swallowed click, not the bounded-retry quirk already covered
    elsewhere), while a sibling control already displays the exact
    requested country ("France") the whole time. The old page-wide
    `wait_for_selected_label` primary signal would scan every single-value
    node on the page and could be satisfied by that sibling; the fix must
    fail this fill closed instead.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "swallow_target_country")
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "France",
            }),
        )
        assert classified.kind is QuestionKind.COUNTRY
        assert classified.value == "France"

        assert adapter.fill_field(session.page, classified) is False

        own_visible = session.page.locator(
            "#swallow-target-field .select__single-value"
        ).inner_text().strip()
        assert own_visible == ""
        assert adapter.read_back(session.page, classified.field) in (None, "")

        sibling_visible = session.page.locator(
            "#swallow-sibling-field .select__single-value"
        ).inner_text().strip()
        assert sibling_visible == "France"
    finally:
        session.close()


# --- A control dynamically unmounted mid-search must never read back its
# own typed, uncommitted search text ---------------------------------------


def test_unmounted_single_value_during_search_never_reads_back_typed_text() -> None:
    """Typing removes this control's own single-value display node
    entirely while a real committed value ("Brazil") stays stuck,
    unexposed, in the widget's internal state -- the shape a generic
    typed-text fallback would misread as a committed "France". The scoped
    country reader must fail closed instead.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "stale_unmount_country")

        locator = session.page.locator("#stale_unmount_country")
        before = session.page.locator(
            "#stale-unmount-field .select__single-value"
        )
        assert before.count() == 1
        assert before.inner_text().strip() == "Brazil"

        locator.click()
        locator.fill("France")

        assert session.page.locator("#stale-unmount-field .select__single-value").count() == 0
        assert adapter.read_back(session.page, field) is None
    finally:
        session.close()


# --- A later, independent read-back must reflect a reverted selection -----


def test_committed_selection_that_later_reverts_is_not_falsely_reported_persisted() -> None:
    """A selection genuinely commits and is confirmed immediately after the
    click (within the scoped confirmation's own poll window), but later
    reverts (observed live, never implemented as an automated keyboard
    clear here -- see the evidence doc). A later, independent read-back
    call must reflect the live DOM truthfully at that later time, never a
    cached "it was confirmed once" result.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "revert_country")
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Uzbekistan",
            }),
        )
        assert classified.kind is QuestionKind.COUNTRY

        assert adapter.fill_field(session.page, classified) is True
        assert adapter.read_back(session.page, classified.field) == "Uzbekistan"

        session.page.wait_for_timeout(3300)

        assert adapter.read_back(session.page, classified.field) != "Uzbekistan"
    finally:
        session.close()


# --- A selection that reverts within the fill's own bounded window must
# never be confirmed in the first place -- not only caught later by an
# independent read-back ---------------------------------------------------


def test_blur_triggered_revert_fails_the_fill_itself_not_only_a_later_readback() -> None:
    """The exact option genuinely commits on click, but this control's own
    blur handler resets the display back to a stale prior country
    ("Brazil") -- `_fill_combobox`'s own bounded persistence recheck
    (which deliberately blurs the control before its final recheck) must
    observe this within the very same `fill_field` call and report the
    fill as unresolved, never confirmed. Unlike the slow 3-second revert
    above, this must never depend on a later, separate read-back call to
    notice anything is wrong.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "blur_revert_country")
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Uzbekistan",
            }),
        )
        assert classified.kind is QuestionKind.COUNTRY

        assert adapter.fill_field(session.page, classified) is False

        assert adapter.read_back(session.page, classified.field) != "Uzbekistan"
    finally:
        session.close()


def test_immediate_async_rerender_revert_fails_the_fill_itself_not_only_a_later_readback() -> None:
    """Same requirement as above, for a revert that is triggered by an
    immediate async re-render shortly after the click rather than a blur
    handler -- the bounded persistence recheck's own short poll window
    must still observe it within the same `fill_field` call.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "async_revert_country")
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Uzbekistan",
            }),
        )
        assert classified.kind is QuestionKind.COUNTRY

        assert adapter.fill_field(session.page, classified) is False

        assert adapter.read_back(session.page, classified.field) != "Uzbekistan"
    finally:
        session.close()


def test_normal_country_selection_and_other_fallback_are_unaffected_by_persistence_recheck() -> None:
    """The new bounded persistence recheck must never regress a normal,
    genuinely durable selection, nor the "Other" fallback: both must still
    confirm exactly as before.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        profile = _profile(identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "country": "Uzbekistan",
        })

        residence_field = _field(fields, "candidate_location")
        residence_classified = classify_field(residence_field, profile)
        assert adapter.fill_field(session.page, residence_classified) is True
        assert adapter.read_back(session.page, residence_classified.field) == "Uzbekistan"

        other_profile = _profile(identity={
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "country": "Atlantis",
        })
        stripe_field = _field(fields, "stripe_country")
        stripe_classified = classify_field(stripe_field, other_profile)
        assert adapter.fill_field(session.page, stripe_classified) is True
        assert adapter.read_back(session.page, stripe_classified.field) == "Other"
    finally:
        session.close()


def test_other_fallback_blur_revert_fails_the_fill_and_stays_manual() -> None:
    """The "Other" fallback's own click genuinely commits ("Other" is this
    control's own committed single-value display right after the click),
    but this control's own blur handler then resets that display back to
    a stale prior country ("Brazil") -- the SAME bounded
    persistence-after-blur recheck the ordinary country-match path already
    applies above must also gate "Other"'s own success, never report a
    confirmed fill it cannot verify survives a blur. The field must remain
    unresolved/manual, never silently left showing "Other" while actually
    reverted.
    """
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)
        field = _field(fields, "other_blur_revert_country")
        assert "please select Other" in field.context
        classified = classify_field(
            field,
            _profile(identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Mongolia",
            }),
        )
        assert classified.kind is QuestionKind.COUNTRY
        assert classified.value == "Mongolia"

        assert adapter.fill_field(session.page, classified) is False

        visible = session.page.locator(
            "#other-blur-revert-wrapper .select__single-value"
        ).inner_text().strip()
        assert visible == "Brazil"
        assert adapter.read_back(session.page, classified.field) != "Other"
    finally:
        session.close()


# --- Phone dialing-country widget: real structural discovery, required
# asterisk preserved without a native required attribute, never filled ----


def test_phone_dial_country_widget_is_discovered_required_and_never_filled() -> None:
    session = _open()
    try:
        adapter = GreenhouseAdapter()
        fields = adapter.discover_fields(session.page)

        phone_locator = session.page.locator("#phone_dial_country_input")
        assert _is_phone_dial_country_combobox(phone_locator) is True
        residence_locator = session.page.locator("#candidate_location")
        assert _is_phone_dial_country_combobox(residence_locator) is False

        phone_field = _field(fields, "phone_dial_country_input")
        assert phone_field.field_type == "phone_dial_country"
        # No native required/aria-required attribute exists on this input
        # at all -- only the visible "Country(*)" asterisk in its own
        # label communicates the requirement.
        assert phone_field.required is True
        assert "phone dialing country" in phone_field.label.lower()

        residence_field = _field(fields, "candidate_location")
        assert residence_field.field_type == "combobox"

        profile = _profile(
            identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Uzbekistan",
            }
        )
        classified_phone = classify_field(phone_field, profile)
        assert classified_phone.fill is False
        assert classified_phone.value is None

        classified_residence = classify_field(residence_field, profile)
        assert classified_residence.kind is QuestionKind.COUNTRY
        assert classified_residence.value == "Uzbekistan"

        # No fill was ever attempted for the phone dial-country field --
        # its own display must stay exactly as rendered, never touched.
        assert session.page.locator(
            "#phone-widget .select__single-value"
        ).inner_text().strip() == ""
        assert session.page.evaluate("window.__submitClicked") is False
        assert session.page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()


# --- Service-level boundary: the required phone dialing-country picker
# must stay unsupported/unfilled through the *whole* real service pipeline,
# never only `classify_field` in isolation -- even with a generator
# available and willing to answer anything --------------------------------

_MINIMAL_PHONE_WIDGET_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Minimal phone widget</title></head>
<body>
  <form id="application_form" data-ats="greenhouse" action="/job_applications" method="post">
    <div class="phone-input" id="phone-widget">
      <div class="phone-input__country">
        <div class="select">
          <div class="select__container">
            <label for="phone_dial_country_input">Country(*)</label>
            <div class="select__control">
              <div class="select__value-container">
                <div class="select__single-value"></div>
                <div class="select__placeholder">Select...</div>
                <input id="phone_dial_country_input" role="combobox" aria-autocomplete="list">
              </div>
            </div>
          </div>
        </div>
      </div>
      <div id="phone-dial-country-listbox" role="listbox" hidden></div>
    </div>
    <button type="submit" id="submit_app">Submit Application</button>
  </form>
  <script>
    window.__submitClicked = false;
    window.__formSubmitted = false;
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


class _UnusedResolver:
    """Never actually called: the test below drives
    `AutofillService._fill_open_page` directly (same private-method-call
    precedent `service._fill_and_confirm` already uses elsewhere), which
    never touches `self._resolver` -- only `AutofillService.run` does.
    """

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        raise AssertionError("resolver must never be called: _fill_open_page is invoked directly")


class _AnyAnswerGenerator:
    """Stands in for a real LLM-backed `ApplicationAnswerGenerator`:
    returns a plausible-looking answer for *any* field it is asked about,
    including the phone dialing-country picker -- so this test proves a
    generator merely being *available* (and willing to answer anything)
    can never leak a fabricated value into that control. `classify_field`
    already routes it to UNSUPPORTED/`fill=False` before
    `_enrich_unresolved` is ever reached for it, and the service's own
    UNSUPPORTED-classification branch never reaches the generic DOM-fill
    path regardless of what `_enrich_unresolved` does to
    `item.fill`/`item.value` afterwards.
    """

    def generate(
        self,
        field: DiscoveredField,
        profile: object,
        vacancy: object = None,
        *,
        markdown_profile: str | None = None,
        sibling_kinds: frozenset[QuestionKind] = frozenset(),
        sibling_has_dedicated_new_zealand_question: bool = False,
    ) -> str | None:
        _ = field, profile, vacancy, markdown_profile, sibling_kinds, sibling_has_dedicated_new_zealand_question
        return "United States"


def test_required_phone_dial_country_stays_unsupported_and_unfilled_through_full_service_with_generator(
    tmp_path: Path,
) -> None:
    """End-to-end through the real service pipeline
    (`AutofillService._fill_open_page` -> `discover_fields` ->
    `classify_field` -> `_enrich_unresolved` -> the fill loop's own
    UNSUPPORTED-classification branch), never only `classify_field` in
    isolation: even with a generator available and willing to answer any
    field, the required phone dialing-country picker must still be
    reported as both unsupported and unresolved-required, must never
    receive a DOM fill, and the form must never be submitted. Uses a
    minimal, test-local page (just the phone widget) rather than the
    shared multi-field evidence fixture, so this generator can never be
    handed, and so never leak a fabricated value into, any other field.
    """
    html_path = tmp_path / "minimal_phone_widget.html"
    html_path.write_text(_MINIMAL_PHONE_WIDGET_HTML, encoding="utf-8")
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(html_path)
    try:
        vacancy = ResolvedVacancy(
            source="target_company:greenhouse:evidence",
            external_id="1",
            title="Senior Backend Engineer",
            company="Evidence Co",
            url=html_path.resolve().as_uri(),
            application_url=html_path.resolve().as_uri(),
        )
        profile = _profile(
            identity={
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "country": "Uzbekistan",
            }
        )
        service = AutofillService(resolver=_UnusedResolver(), answer_generator=_AnyAnswerGenerator())

        result = service._fill_open_page(
            vacancy, profile, Path("unused_resume.pdf"), session, GreenhouseAdapter()
        )

        phone_unsupported = [
            record
            for record in result.unsupported_fields
            if record.note and "Phone dialing country code selector" in record.note
        ]
        assert len(phone_unsupported) == 1
        assert phone_unsupported[0].required is True

        phone_unresolved = [
            record
            for record in result.unresolved_required_fields
            if record.note and "Phone dialing country code selector" in record.note
        ]
        assert len(phone_unresolved) == 1

        assert session.page.locator(
            "#phone-widget .select__single-value"
        ).inner_text().strip() == ""
        assert session.page.evaluate("window.__submitClicked") is False
        assert session.page.evaluate("window.__formSubmitted") is False
    finally:
        session.close()
