from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.application.autofill.ashby import AshbyAdapter, AshbyFormError, is_ashby_application_page
from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.classifier import ClassifiedField, classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy

ASHBY_FIXTURE = Path("tests/fixtures/autofill/ashby_application.html")
ASHBY_OVERVIEW_FIXTURE = Path("tests/fixtures/autofill/ashby_overview.html")
UNRELATED_FIXTURE = Path("tests/fixtures/autofill/unrelated.html")
RESUME_FIXTURE = Path("tests/fixtures/autofill/resume.txt")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(), reason="Playwright Chromium is not installed"
)


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
            "current_location": "Berlin, Germany",
            "country": "Germany",
        },
        "professional_links": {
            "linkedin": "https://www.linkedin.com/in/ada-example-test",
        },
        "application_files": {"default_resume": str(RESUME_FIXTURE)},
        "work_eligibility": {
            "work_authorizations": [{"country": "Germany", "authorized": True}],
            "requires_visa_sponsorship": False,
        },
        "sensitive": {"gender": "female"},
        "fill_sensitive_fields": False,
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


def _open(path: Path) -> BrowserSession:
    session = BrowserSession(headed=False, keep_open=False)
    session.open_html_file(path)
    return session


def _field(fields: list[DiscoveredField], label_substring: str) -> DiscoveredField:
    needle = label_substring.lower()
    for item in fields:
        if needle in item.label.lower():
            return item
    raise AssertionError(f"Field not found: {label_substring} in {[item.label for item in fields]}")


def test_recognizes_ashby_fixture() -> None:
    session = _open(ASHBY_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is True
    assert is_ashby_application_page(session.page) is True
    session.close()


def test_rejects_overview_page() -> None:
    session = _open(ASHBY_OVERVIEW_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_rejects_unrelated_page() -> None:
    session = _open(UNRELATED_FIXTURE)
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_rejects_survey_only_form_container_without_main_application_panel() -> None:
    # Ashby wraps its EEOC/demographic survey section in the very same
    # `.ashby-application-form-container` class as the real application
    # panel (see the shared fixture's own sibling survey container). A page
    # that only has that survey container -- no tabpanel with both a submit
    # button and name/email systemfields -- must not be recognized.
    session = BrowserSession(headed=False, keep_open=False)
    session.start()
    session.page.set_content(
        """
        <div class="ashby-survey-form-container">
          <div class="ashby-application-form-container">
            <fieldset data-field-path="_systemfield_eeoc_gender">
              <div class="ashby-application-form-question-title">Gender</div>
              <div class="ashby-application-form-input-radio-group">
                <label><input type="radio" name="eeoc-gender" value="Man" /> Man</label>
                <label><input type="radio" name="eeoc-gender" value="Woman" /> Woman</label>
              </div>
            </fieldset>
          </div>
        </div>
        """
    )
    assert AshbyAdapter().recognize(session.page) is False
    session.close()


def test_detects_visible_active_captcha_even_on_a_recognized_application_page() -> None:
    session = _open(ASHBY_FIXTURE)
    session.page.evaluate(
        """() => {
            const iframe = document.createElement('iframe');
            // A self-contained data: URI keeps this test offline -- only the
            // "recaptcha" substring in the src (matched case-insensitively)
            // and the iframe's visible layout box matter here, not that
            // anything actually loads from google.com.
            iframe.src = 'data:text/html;charset=utf-8,recaptcha-challenge';
            iframe.style.width = '300px';
            iframe.style.height = '80px';
            document.body.appendChild(iframe);
        }"""
    )
    assert AshbyAdapter().detect_challenge(session.page) == "captcha"
    session.close()


def test_discover_fields_raises_on_unrecognized_page() -> None:
    session = _open(UNRELATED_FIXTURE)
    with pytest.raises(AshbyFormError):
        AshbyAdapter().discover_fields(session.page)
    session.close()


def test_discover_fields_ignores_data_field_path_outside_application_panel() -> None:
    # The fixture has a decoy `[data-field-path]` element as a sibling of
    # the `#form` tabpanel (representing a different tab/section of the same
    # page) -- discovery must be scoped to the recognized panel so it is
    # never surfaced or drivable.
    session = _open(ASHBY_FIXTURE)
    fields = AshbyAdapter().discover_fields(session.page)
    assert not any(item.context == "decoy-outside-panel-uuid" for item in fields)
    assert not any("should never be discovered" in item.label.lower() for item in fields)
    session.close()


def test_offscreen_recaptcha_badge_iframe_is_not_a_blocking_challenge() -> None:
    # The fixture's badge iframe is positioned off-screen rather than
    # visibility:hidden -- the same way the real widget behaves -- so it can
    # report `is_visible() == True`. It must still be excluded, by ancestry
    # (`.grecaptcha-badge`), not by visibility.
    session = _open(ASHBY_FIXTURE)
    badge_iframe = session.page.locator(".grecaptcha-badge iframe[title='recaptcha']")
    assert badge_iframe.count() == 1
    assert badge_iframe.is_visible() is True
    assert AshbyAdapter().detect_challenge(session.page) is None
    session.close()


def test_fills_name_email_linkedin_and_uploads_resume() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    classified = [classify_field(item, profile) for item in fields]

    name = next(item for item in classified if item.kind is QuestionKind.FULL_NAME)
    email = next(item for item in classified if item.kind is QuestionKind.EMAIL)
    linkedin = next(item for item in classified if item.kind is QuestionKind.LINKEDIN)
    resume = next(item for item in classified if item.kind is QuestionKind.RESUME)

    assert name.fill is True
    assert email.fill is True
    assert linkedin.fill is True
    assert resume.fill is True

    assert adapter.fill_field(session.page, name) is True
    assert adapter.fill_field(session.page, email) is True
    assert adapter.fill_field(session.page, linkedin) is True
    assert adapter.upload_resume(session.page, Path(profile.application_files.default_resume), resume.field) is True

    assert adapter.read_back(session.page, name.field) == "Ada Example"
    assert adapter.read_back(session.page, email.field) == "ada.example@example.test"
    assert adapter.read_back(session.page, linkedin.field) == "https://www.linkedin.com/in/ada-example-test"
    assert adapter.read_back(session.page, resume.field) == "resume.txt"

    session.close()


def test_never_uploads_to_the_autofill_uploader_input() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    resume = next(classify_field(item, profile) for item in fields if item.field_type == "file")
    adapter.upload_resume(session.page, Path(profile.application_files.default_resume), resume.field)

    uploader_files = session.page.evaluate(
        "() => { const el = document.querySelector('.ashby-application-form-autofill-uploader input[type=file]');"
        " return el ? el.files.length : -1; }"
    )
    assert uploader_files == 0
    session.close()


def test_submit_button_is_never_clicked_during_a_full_fill_pass() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    session.page.evaluate(
        "() => { window.__submitClicked = false;"
        " document.querySelector('.ashby-application-form-submit-button')"
        ".addEventListener('click', () => { window.__submitClicked = true; }); }"
    )
    fields = adapter.discover_fields(session.page)
    for item in [classify_field(field, profile) for field in fields]:
        if item.kind is QuestionKind.RESUME and item.fill:
            adapter.upload_resume(session.page, Path(profile.application_files.default_resume), item.field)
        elif item.fill:
            adapter.fill_field(session.page, item)

    assert session.page.evaluate("() => window.__submitClicked") is False
    session.close()


def test_unassociated_autocomplete_stays_unsupported_not_typed_into() -> None:
    # "Where did you hear..." has no resolvable listbox association in this
    # snapshot (no aria-controls, no rendered listbox at all) -- it must stay
    # an unsupported control, never typed into blindly. "Current Location" is
    # covered separately by the combobox_location tests below.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)

    source_field = _field(fields, "Where did you hear")
    assert source_field.field_type == "unknown"

    source_classified = classify_field(source_field, profile)
    assert source_classified.classification is FieldClassification.UNSUPPORTED
    assert source_classified.fill is False
    session.close()


def test_employee_relationship_checkbox_group_is_not_discovered() -> None:
    session = _open(ASHBY_FIXTURE)
    fields = AshbyAdapter().discover_fields(session.page)
    assert not any("related to any current employees" in item.label.lower() for item in fields)
    session.close()


def test_consent_checkbox_is_discovered_but_never_auto_checked() -> None:
    # The live consent checkbox's own id/name carry no stable semantic
    # meaning (id is an Ashby-generated UUID, name is the literal "I agree"
    # label text) -- only the wrapper's data-field-path (`context`)
    # reliably identifies it.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    consent = next(item for item in fields if item.context == "_systemfield_data_consent_ack")
    assert consent.field_type == "checkbox"
    assert consent.name == "I agree"
    assert consent.required is False

    forced = ClassifiedField(
        field=consent,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.NEWSLETTER,
    )
    assert adapter.fill_field(session.page, forced) is False
    assert adapter.read_back(session.page, consent) == "false"
    session.close()


def test_which_location_radio_fills_and_reads_back_when_explicitly_resolved() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Which location are you applying for")
    assert location.field_type == "radio"
    assert location.required is True

    classified = ClassifiedField(
        field=location,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Boston",
        fill=True,
        kind=QuestionKind.LOCATED_IN,
    )
    assert adapter.fill_field(session.page, classified) is True
    assert adapter.read_back(session.page, location) == "Boston"
    session.close()


def test_which_location_radio_fill_fails_closed_on_unmatched_prefix() -> None:
    # "Bo" is a prefix of the live "Boston" option but not an exact match --
    # must never guess. The ambiguous-match case (more than one live option
    # matching) is a synthetic scenario not observed on this snapshot's
    # single-option field, covered browser-free instead by
    # `_matching_radio_indices` in test_ashby_helpers.py.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Which location are you applying for")

    prefix_classified = ClassifiedField(
        field=location,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Bo",
        fill=True,
        kind=QuestionKind.LOCATED_IN,
    )
    assert adapter.fill_field(session.page, prefix_classified) is False
    assert adapter.read_back(session.page, location) is None
    session.close()


def test_which_location_radio_stays_unresolved_by_default() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = classify_field(_field(fields, "Which location are you applying for"), profile)
    assert location.fill is False
    session.close()


def test_legal_authorization_yesno_fills_and_reads_back_yes_and_no() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    fields = adapter.discover_fields(session.page)
    legal = _field(fields, "legal authorisation")
    assert legal.field_type == "yesno"
    assert legal.required is True
    assert set(legal.options) == {"Yes", "No"}

    classified_yes = ClassifiedField(
        field=legal,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value=True,
        fill=True,
        kind=QuestionKind.WORK_AUTHORIZATION,
    )
    assert adapter.fill_field(session.page, classified_yes) is True
    assert adapter.read_back(session.page, legal) == "Yes"

    classified_no = replace(classified_yes, value=False)
    assert adapter.fill_field(session.page, classified_no) is True
    assert adapter.read_back(session.page, legal) == "No"
    session.close()


def test_legal_authorization_yesno_stays_unresolved_by_default() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    legal = classify_field(_field(fields, "legal authorisation"), profile)
    # This exact phrasing ("legal authorisation to work in...") is not one
    # of the shared classifier's recognized work-authorization patterns, so
    # it falls through to the generic unresolved-required bucket -- it must
    # stay unresolved/manual, never guessed by classify_field alone.
    assert legal.fill is False
    session.close()


def test_legal_authorization_yesno_resolves_from_single_vacancy_work_country() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()  # has an explicit work_authorizations fact for Germany
    fields = adapter.discover_fields(session.page)
    legal_field = _field(fields, "legal authorisation")
    legal = classify_field(legal_field, profile)
    assert legal.fill is False  # the shared classifier itself never resolves this

    vacancy = _vacancy(location="Berlin, Germany")
    adjusted = adapter.adjust_classified_field(legal, profile=profile, vacancy=vacancy)
    assert adjusted.fill is True
    assert adjusted.value is True
    assert adjusted.kind is QuestionKind.WORK_AUTHORIZATION

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, legal_field) == "Yes"
    session.close()


def test_legal_authorization_yesno_stays_manual_without_an_explicit_country_fact() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()  # only has an explicit work_authorizations fact for Germany
    fields = adapter.discover_fields(session.page)
    legal = classify_field(_field(fields, "legal authorisation"), profile)

    # No explicit work_authorizations fact for the Netherlands -- must never
    # be inferred from citizenship, current_location, or relocation
    # willingness even though this profile is otherwise fully filled in.
    vacancy = _vacancy(location="Remote, Netherlands")
    adjusted = adapter.adjust_classified_field(legal, profile=profile, vacancy=vacancy)
    assert adjusted.fill is False
    session.close()


def test_legal_authorization_yesno_stays_manual_with_ambiguous_vacancy_countries() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    legal = classify_field(_field(fields, "legal authorisation"), profile)

    vacancy = _vacancy(location="Germany or Netherlands (Remote)")
    adjusted = adapter.adjust_classified_field(legal, profile=profile, vacancy=vacancy)
    assert adjusted.fill is False
    session.close()


def test_office_arrangement_yesno_stays_unresolved_by_default() -> None:
    # The shared `office_work` policy's class default is `willing=True`
    # (indistinguishable from a real candidate fact), so the raw shared
    # classifier alone would incorrectly resolve this to "Yes". The real
    # pipeline (`AutofillService`, mirrored here via `adjust_classified_field`)
    # must revert that generic default back to manual -- see
    # `_adjust_office_work_feasibility`.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    classified = classify_field(_field(fields, "3 days per week"), profile)
    assert classified.fill is True  # the raw shared classifier trusts the generic default
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is False
    session.close()


def test_eeoc_gender_survey_group_is_optional_and_never_guessed() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    gender = _field(fields, "Gender")
    assert gender.field_type == "radio"
    assert gender.required is False

    classified = classify_field(gender, profile)
    assert classified.fill is False
    session.close()


def test_phone_number_tel_field_fills_and_reads_back() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    phone = _field(fields, "Phone Number")
    assert phone.field_type == "tel"

    classified = classify_field(phone, profile)
    assert classified.kind is QuestionKind.PHONE
    assert classified.fill is True

    assert adapter.fill_field(session.page, classified) is True
    assert adapter.read_back(session.page, phone) == profile.identity.phone
    session.close()


def test_whatsapp_optional_consent_yesno_is_always_declined() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    whatsapp = _field(fields, "WhatsApp")
    assert whatsapp.field_type == "yesno"

    classified = classify_field(whatsapp, profile)
    assert classified.fill is False  # the shared classifier itself never resolves this

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is True
    assert adjusted.value is False

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, whatsapp) == "No"
    session.close()


def test_whatsapp_consent_is_never_answered_yes_even_with_recruiting_contact_true() -> None:
    # Opting into optional messaging contact must never be automatic --
    # not even when the profile has an otherwise-affirmative recruiting
    # consent fact, since that fact answers a different (privacy/marketing)
    # question, never this one.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile(application_consent={"recruiting_contact": True})
    fields = adapter.discover_fields(session.page)
    whatsapp = _field(fields, "WhatsApp")
    classified = classify_field(whatsapp, profile)
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.value is False
    session.close()


def test_whatsapp_worded_sms_updates_question_is_never_answered_yes_via_shared_classifier() -> None:
    # A WhatsApp-worded consent question that also happens to match the
    # shared classifier's own `SMS_UPDATES` cues ("text updates",
    # "application") gets mapped to `QuestionKind.SMS_UPDATES` and, with an
    # explicit opt-in profile fact, resolved to Yes by the shared classifier
    # alone -- before this Ashby-specific override ever runs. It must still
    # be forced back to No, never left at the shared classifier's Yes.
    profile = _profile(application_policy={"sms_interview_updates": True})
    field = DiscoveredField(
        label="Would you like text updates about your application on WhatsApp? (optional)",
        field_type="yesno",
        options=["Yes", "No"],
    )
    classified = classify_field(field, profile)
    assert classified.kind is QuestionKind.SMS_UPDATES
    assert classified.fill is True
    assert classified.value is True

    adjusted = AshbyAdapter().adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.value is False


def test_java_spring_boot_conjunctive_skill_yesno_is_yes_when_both_are_configured() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile(employment={"professional_tech_stack": ["Java", "Spring Boot"]})
    fields = adapter.discover_fields(session.page)
    skill = _field(fields, "Java and Spring Boot")
    assert skill.field_type == "yesno"

    classified = classify_field(skill, profile)
    assert classified.fill is False  # the shared classifier itself never resolves this

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is True
    assert adjusted.value is True

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, skill) == "Yes"
    session.close()


def test_java_spring_boot_conjunctive_skill_yesno_stays_manual_when_spring_boot_is_missing() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile(employment={"professional_tech_stack": ["Java"]})
    fields = adapter.discover_fields(session.page)
    skill = _field(fields, "Java and Spring Boot")
    classified = classify_field(skill, profile)
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    # Absence of a configured "Spring Boot" entry is not truthful evidence the
    # candidate lacks it -- this must stay manual, never answered "No".
    assert adjusted.fill is False
    session.close()


def test_notice_period_text_field_fills_from_explicit_profile_value() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile(employment={"notice_period": "4 weeks"})
    fields = adapter.discover_fields(session.page)
    notice = _field(fields, "Notice period")

    classified = classify_field(notice, profile)
    assert classified.fill is False  # no generic shared mapping for this key

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is True
    assert adjusted.value == "4 weeks"

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, notice) == "4 weeks"
    session.close()


def test_gross_annual_salary_local_currency_stays_manual_even_with_currency_configured() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
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
    fields = adapter.discover_fields(session.page)
    salary = _field(fields, "local currency")

    classified = classify_field(salary, profile)
    assert classified.fill is True  # the shared classifier itself has no currency-applicability gate

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is False
    session.close()


def test_application_source_checkbox_group_checks_only_company_website() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    source = _field(fields, "How did you hear about our company")
    assert source.field_type == "checkbox_group"
    assert set(source.options) == {"Company Website", "LinkedIn", "Employee Referral"}

    classified = classify_field(source, profile)
    assert classified.fill is True
    assert classified.value == "Company Website"

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is True

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, source) == "Company Website"

    # Never checks LinkedIn or Employee Referral -- only the one exact option.
    other_checked = session.page.evaluate(
        "() => Array.from(document.querySelectorAll("
        "'[data-field-path=\"a1b2c3d4-source-checkbox-uuid\"] input[type=checkbox]'))"
        ".filter(el => el.value !== 'Company Website').some(el => el.checked)"
    )
    assert other_checked is False
    session.close()


def test_application_source_checkbox_group_stays_manual_without_a_verified_ashby_board_url() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    source = _field(fields, "How did you hear about our company")
    classified = classify_field(source, profile)
    assert classified.fill is True

    unverified_vacancy = _vacancy(application_url="https://embed.example.com/perk/abc-123/application")
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=unverified_vacancy)
    assert adjusted.fill is False
    session.close()


def test_application_source_checkbox_group_stays_manual_without_a_target_company_ashby_source() -> None:
    # A canonical-shaped Ashby board URL alone is never sufficient -- it must
    # never be inferred as proof of direct discovery without the vacancy
    # also having actually been discovered through this module's own Target
    # Company Ashby pipeline.
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    source = _field(fields, "How did you hear about our company")
    classified = classify_field(source, profile)
    assert classified.fill is True

    non_target_company_vacancy = _vacancy(source="job_board:aggregator")
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=non_target_company_vacancy)
    assert adjusted.fill is False
    session.close()


def test_employee_relationship_group_is_still_never_discovered_as_checkbox_group() -> None:
    # The narrow "...hear about..." checkbox-group discovery must never widen
    # to an unrelated multi-option group such as employee relationship.
    session = _open(ASHBY_FIXTURE)
    fields = AshbyAdapter().discover_fields(session.page)
    assert not any(item.field_type == "checkbox_group" and "employee" in item.label.lower() for item in fields)
    session.close()


def test_current_location_combobox_selects_exact_listbox_option() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Current Location")
    assert location.field_type == "combobox_location"

    classified = classify_field(location, profile)
    assert classified.kind is QuestionKind.LOCATION
    assert classified.fill is True
    assert classified.value == profile.identity.current_location

    assert adapter.fill_field(session.page, classified) is True
    assert adapter.read_back(session.page, location) == "Berlin, Germany"
    session.close()


def test_current_location_combobox_fails_closed_on_no_matching_option() -> None:
    session = _open(ASHBY_FIXTURE)
    adapter = AshbyAdapter()
    profile = _profile(identity={
        "first_name": "Ada",
        "last_name": "Example",
        "email": "ada.example@example.test",
        "phone": "+15555550100",
        "current_location": "Nowhere, Atlantis",
    })
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Current Location")
    classified = classify_field(location, profile)
    assert classified.fill is True

    # No live option matches -- never falls back to a bare typed value or a
    # blind Enter; the service layer must see this as an unconfirmed fill.
    assert adapter.fill_field(session.page, classified) is False
    session.close()


def test_current_location_combobox_fails_closed_with_no_resolvable_listbox() -> None:
    # A combobox input with no aria-controls/aria-owns association and no
    # visible listbox anywhere on the page must never be typed-and-Entered
    # into blindly.
    session = BrowserSession(headed=False, keep_open=False)
    session.start()
    session.page.set_content(
        """
        <div id="ashby_app">
          <div id="form" role="tabpanel">
            <div class="ashby-application-form-container">
              <fieldset data-field-path="_systemfield_name">
                <input id="_systemfield_name" type="text" />
              </fieldset>
              <fieldset data-field-path="_systemfield_email">
                <input id="_systemfield_email" type="email" />
              </fieldset>
              <fieldset data-field-path="loc-uuid">
                <label class="ashby-application-form-question-title" for="loc-input">Current Location</label>
                <input id="loc-input" type="text" role="combobox" />
              </fieldset>
            </div>
            <button type="button" class="ashby-application-form-submit-button">Submit Application</button>
          </div>
        </div>
        """
    )
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Current Location")
    assert location.field_type == "combobox_location"
    classified = classify_field(location, profile)
    assert adapter.fill_field(session.page, classified) is False
    session.close()


def test_current_location_combobox_discovered_and_filled_without_an_element_id() -> None:
    # The live combobox input has no stable id on every observed Ashby form
    # -- discovery and fill must both work purely from the wrapper's unique
    # `data-field-path`, never require an id.
    session = BrowserSession(headed=False, keep_open=False)
    session.start()
    session.page.set_content(
        """
        <div id="ashby_app">
          <div id="form" role="tabpanel">
            <div class="ashby-application-form-container">
              <fieldset data-field-path="_systemfield_name">
                <input id="_systemfield_name" type="text" />
              </fieldset>
              <fieldset data-field-path="_systemfield_email">
                <input id="_systemfield_email" type="email" />
              </fieldset>
              <fieldset data-field-path="loc-no-id-uuid">
                <label class="ashby-application-form-question-title">Current Location</label>
                <input type="text" role="combobox" aria-controls="loc-no-id-listbox" />
                <ul id="loc-no-id-listbox" role="listbox" style="display:none">
                  <li role="option">Berlin, Germany</li>
                  <li role="option">Remote</li>
                </ul>
              </fieldset>
            </div>
            <button type="button" class="ashby-application-form-submit-button">Submit Application</button>
          </div>
        </div>
        <script>
          (function () {
            var input = document.querySelector('[data-field-path="loc-no-id-uuid"] input[role="combobox"]');
            var listbox = document.getElementById("loc-no-id-listbox");
            input.addEventListener("input", function () {
              listbox.style.display = input.value.trim() ? "block" : "none";
            });
            listbox.querySelectorAll('[role="option"]').forEach(function (option) {
              option.addEventListener("click", function () {
                input.value = option.textContent.trim();
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
                listbox.style.display = "none";
              });
            });
          })();
        </script>
        """
    )
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Current Location")
    assert location.field_type == "combobox_location"
    assert location.element_id is None

    classified = classify_field(location, profile)
    assert adapter.fill_field(session.page, classified) is True
    assert adapter.read_back(session.page, location) == "Berlin, Germany"
    session.close()


def test_current_location_combobox_fills_live_currently_based_wording_via_adjuster() -> None:
    # "Where are you currently based?" is real, observed Ashby wording that
    # discovery already recognizes as `combobox_location` (see
    # `_looks_like_current_location_label`), but the shared classifier's own
    # `_is_identity_location` does not match it -- only the Ashby-only
    # `adjust_classified_field` adjuster resolves it.
    session = BrowserSession(headed=False, keep_open=False)
    session.start()
    session.page.set_content(
        """
        <div id="ashby_app">
          <div id="form" role="tabpanel">
            <div class="ashby-application-form-container">
              <fieldset data-field-path="_systemfield_name">
                <input id="_systemfield_name" type="text" />
              </fieldset>
              <fieldset data-field-path="_systemfield_email">
                <input id="_systemfield_email" type="email" />
              </fieldset>
              <fieldset data-field-path="loc-uuid">
                <label class="ashby-application-form-question-title" for="loc-input">Where are you currently based?</label>
                <input id="loc-input" type="text" role="combobox" aria-controls="loc-listbox" />
                <ul id="loc-listbox" role="listbox" style="display:none">
                  <li role="option">Berlin, Germany</li>
                  <li role="option">Remote</li>
                </ul>
              </fieldset>
            </div>
            <button type="button" class="ashby-application-form-submit-button">Submit Application</button>
          </div>
        </div>
        <script>
          (function () {
            var input = document.getElementById("loc-input");
            var listbox = document.getElementById("loc-listbox");
            input.addEventListener("input", function () {
              listbox.style.display = input.value.trim() ? "block" : "none";
            });
            listbox.querySelectorAll('[role="option"]').forEach(function (option) {
              option.addEventListener("click", function () {
                input.value = option.textContent.trim();
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
                listbox.style.display = "none";
              });
            });
          })();
        </script>
        """
    )
    adapter = AshbyAdapter()
    profile = _profile()
    fields = adapter.discover_fields(session.page)
    location = _field(fields, "Where are you currently based?")
    assert location.field_type == "combobox_location"

    classified = classify_field(location, profile)
    assert classified.fill is False
    assert classified.kind is QuestionKind.UNKNOWN

    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=_vacancy())
    assert adjusted.fill is True
    assert adjusted.kind is QuestionKind.LOCATION
    assert adjusted.value == "Berlin, Germany"

    assert adapter.fill_field(session.page, adjusted) is True
    assert adapter.read_back(session.page, location) == "Berlin, Germany"
    session.close()
