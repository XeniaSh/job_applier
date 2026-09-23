from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.application.autofill.acknowledgements import (
    AcknowledgementClass,
    classify_acknowledgement,
    is_safe_required_privacy_acknowledgement,
)
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import (
    ALREADY_LOCATED_CHOICE,
    WOULD_RELOCATE_CHOICE,
    find_known_technologies_in_text,
    is_located_or_relocate_choice,
    label_matches,
    match_application_source,
    match_decline_to_answer_option,
    match_interest_option,
    match_located_or_relocate_option,
    match_named_skill_set,
    match_option,
    match_option_exact_normalized,
    match_prefer_not_to_disclose_gender,
    match_remote_work_arrangement_option,
    match_years_option,
    match_yes_no,
    match_affirmative_option,
    match_sponsorship_option,
    parse_located_in_places,
    parse_located_or_relocate_places,
    parse_max_choices,
    parse_named_skill_set,
    parse_relocation_destination,
    parse_sponsorship_scope,
    parse_years_experience_technology,
    split_technology_scope_terms,
)
from app.application.candidate_profile import CandidateProfile

_WORK_IN_RE = re.compile(
    r"(?:authorized|authorised|eligible|right)\s+to\s+work\s+in(?:\s+the)?\s+(.+?)\??$",
    re.IGNORECASE,
)
_GENERIC_COUNTRY_RELATIVE_WORK_AUTH_RE = re.compile(
    r"^(?:"
    r"(?:the\s+)?countr(?:y|ies)\s+(?:"
    r"for which you (?:have\s+)?applied"
    r"|in which you (?:are applying|have applied)"
    r"|(?:for|in) which (?:this|the) (?:role|position|job) is located"
    r")"
    r"|(?:the\s+)?location\(s\) you selected in your previous response"
    r")\s*$",
    re.IGNORECASE,
)

QuestionValue = str | bool | list[str] | None


class QuestionKind(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    COUNTRY = "country"
    ANTICIPATED_WORK_COUNTRY = "anticipated_work_country"
    SCHOOL = "school"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    GITLAB_USERNAME = "gitlab_username"
    WEBSITE = "website"
    OPEN_SOURCE_LINKS = "open_source_links"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    VISA_SPONSORSHIP = "visa_sponsorship"
    WORK_AUTHORIZATION = "work_authorization"
    SALARY = "salary"
    YEARS_EXPERIENCE = "years_experience"
    ACADEMIC_LEVEL = "academic_level"
    TECH_STACK = "tech_stack"
    PRIMARY_LANGUAGE = "primary_language"
    FIELD_OF_INTEREST = "field_of_interest"
    RELOCATION = "relocation"
    OFFICE_WORK = "office_work"
    REMOTE_WORK_ARRANGEMENT = "remote_work_arrangement"
    CURRENT_EMPLOYER = "current_employer"
    CURRENT_TITLE = "current_title"
    SKILL_SET_CHOICE = "skill_set_choice"
    EMPLOYEE_RELATIONSHIP = "employee_relationship"
    EMPLOYEE_RELATIONSHIP_DETAILS = "employee_relationship_details"
    PRIOR_AFFILIATION = "prior_affiliation"
    EMPLOYMENT_RESTRICTIONS = "employment_restrictions"
    LOCATED_IN = "located_in"
    APPLICATION_SOURCE = "application_source"
    PRIVACY_CONSENT = "privacy_consent"
    NEWSLETTER = "newsletter"
    SMS_UPDATES = "sms_updates"
    QUESTION_OVERRIDE = "question_override"
    WHY_COMPANY = "why_company"
    PROFESSIONAL_FREE_TEXT = "professional_free_text"
    GENDER = "gender"
    AGE = "age"
    NATIONALITY = "nationality"
    SENSITIVE = "sensitive"
    UNKNOWN = "unknown"


LLM_FORBIDDEN_KINDS = frozenset(
    {
        QuestionKind.VISA_SPONSORSHIP,
        QuestionKind.WORK_AUTHORIZATION,
        QuestionKind.SALARY,
        QuestionKind.SENSITIVE,
        QuestionKind.EMPLOYEE_RELATIONSHIP,
        QuestionKind.EMPLOYEE_RELATIONSHIP_DETAILS,
        QuestionKind.PRIOR_AFFILIATION,
        QuestionKind.EMPLOYMENT_RESTRICTIONS,
        QuestionKind.LOCATED_IN,
        QuestionKind.APPLICATION_SOURCE,
        QuestionKind.PRIVACY_CONSENT,
        QuestionKind.NEWSLETTER,
        QuestionKind.SMS_UPDATES,
        QuestionKind.QUESTION_OVERRIDE,
        QuestionKind.GENDER,
        QuestionKind.AGE,
        QuestionKind.NATIONALITY,
        QuestionKind.FIRST_NAME,
        QuestionKind.LAST_NAME,
        QuestionKind.FULL_NAME,
        QuestionKind.EMAIL,
        QuestionKind.PHONE,
        QuestionKind.LOCATION,
        QuestionKind.COUNTRY,
        QuestionKind.ANTICIPATED_WORK_COUNTRY,
        QuestionKind.SCHOOL,
        QuestionKind.LINKEDIN,
        QuestionKind.GITHUB,
        QuestionKind.GITLAB_USERNAME,
        QuestionKind.WEBSITE,
        QuestionKind.OPEN_SOURCE_LINKS,
        QuestionKind.RESUME,
        QuestionKind.COVER_LETTER,
        QuestionKind.YEARS_EXPERIENCE,
        QuestionKind.ACADEMIC_LEVEL,
        QuestionKind.TECH_STACK,
        QuestionKind.PRIMARY_LANGUAGE,
        QuestionKind.FIELD_OF_INTEREST,
        QuestionKind.RELOCATION,
        QuestionKind.OFFICE_WORK,
        QuestionKind.REMOTE_WORK_ARRANGEMENT,
        QuestionKind.CURRENT_EMPLOYER,
        QuestionKind.CURRENT_TITLE,
        QuestionKind.SKILL_SET_CHOICE,
    }
)


@dataclass(frozen=True)
class MappedQuestion:
    kind: QuestionKind
    value: QuestionValue = None
    country: str | None = None
    fillable: bool = False
    max_choices: int | None = None
    inactive_conditional: bool = False
    unresolved_reason: str | None = None


def map_question(field: DiscoveredField, profile: CandidateProfile) -> MappedQuestion:
    """Map a discovered field to an explicit profile value. Never guesses."""
    text = _search_text(field)

    if _is_gender(text):
        value = _gender_value(field, profile)
        return MappedQuestion(kind=QuestionKind.GENDER, value=value, fillable=bool(value))

    if _is_age(text):
        value = _age_decline_value(field)
        return MappedQuestion(kind=QuestionKind.AGE, value=value, fillable=bool(value))

    if _is_nationality_of_vacancy_country(text):
        # No vacancy context is available here; the resolved vacancy's work
        # country and the candidate's citizenship are only known once the
        # service layer enriches this against `ResolvedVacancy`.
        return MappedQuestion(
            kind=QuestionKind.NATIONALITY,
            fillable=False,
            unresolved_reason=(
                "nationality relative to the vacancy's work country requires "
                "vacancy-aware enrichment"
            ),
        )

    if _is_visa_relocation_elaboration(text, field):
        return MappedQuestion(
            kind=QuestionKind.VISA_SPONSORSHIP,
            fillable=False,
            unresolved_reason=(
                "combined visa/relocation elaboration requires an explicit "
                "truthful vacancy-country-relative answer"
            ),
        )

    if _is_sensitive(text):
        value = _sensitive_decline_value(field)
        return MappedQuestion(kind=QuestionKind.SENSITIVE, value=value, fillable=bool(value))

    if _is_cover_letter(text, field):
        return MappedQuestion(kind=QuestionKind.COVER_LETTER, fillable=False)

    if _is_resume(text, field):
        path = profile.application_files.default_resume
        return MappedQuestion(kind=QuestionKind.RESUME, value=path, fillable=bool(path))

    if _is_linkedin_specific(text):
        value = profile.professional_links.linkedin
        return MappedQuestion(kind=QuestionKind.LINKEDIN, value=value, fillable=bool(value))

    if _is_github_specific(text):
        value = profile.professional_links.github
        return MappedQuestion(kind=QuestionKind.GITHUB, value=value, fillable=bool(value))

    if _is_gitlab_username(text):
        value = profile.gitlab_username_for_autofill()
        return MappedQuestion(
            kind=QuestionKind.GITLAB_USERNAME,
            value=value,
            fillable=bool(value),
        )

    # A checkbox option such as "Careers Website" carries the source-group
    # wording, so classify the group before treating the option as a profile
    # website field.
    if _is_application_source(text):
        source_options = field.options
        if field.field_type == "checkbox" and not source_options:
            source_options = _source_checkbox_options(field)
            preferred = match_application_source(
                source_options,
                profile.application_source_preference(),
            )
            value = field.label if preferred and label_matches(preferred, field.label) else None
        else:
            value = match_application_source(source_options, profile.application_source_preference())
        if not value and not field.options and field.field_type != "checkbox":
            value = (
                profile.application_source_preference()[0]
                if profile.application_source_preference()
                else None
            )
        return MappedQuestion(
            kind=QuestionKind.APPLICATION_SOURCE,
            value=value,
            fillable=bool(value),
        )

    if _is_website(text):
        value = profile.website_for_autofill()
        return MappedQuestion(kind=QuestionKind.WEBSITE, value=value, fillable=bool(value))

    if _is_open_source_links(text, field):
        urls = profile.open_source_urls_for_autofill()
        value = "\n".join(urls) if urls else None
        return MappedQuestion(
            kind=QuestionKind.OPEN_SOURCE_LINKS,
            value=value,
            fillable=bool(value),
        )

    if _is_marketing(text):
        answer = profile.newsletter_opt_in_answer()
        return MappedQuestion(
            kind=QuestionKind.NEWSLETTER,
            value=_mapped_choice(answer, field.options),
            fillable=True,
        )

    acknowledgement_text = _acknowledgement_search_text(field, text)
    acknowledgement = classify_acknowledgement(acknowledgement_text)
    if acknowledgement is AcknowledgementClass.MARKETING:
        answer = profile.newsletter_opt_in_answer()
        return MappedQuestion(
            kind=QuestionKind.NEWSLETTER,
            value=_mapped_choice(answer, field.options),
            fillable=True,
        )
    if acknowledgement is AcknowledgementClass.UNSAFE_LEGAL:
        return MappedQuestion(kind=QuestionKind.UNKNOWN, fillable=False)

    if acknowledgement is AcknowledgementClass.APPLICATION_PRIVACY or _is_privacy_consent(text):
        answer = profile.privacy_acknowledgement_answer(required=field.required)
        if answer is None and is_safe_required_privacy_acknowledgement(field, acknowledgement_text):
            answer = True
        value: QuestionValue = None
        if answer is True:
            value = match_affirmative_option(True, field.options) if field.options else True
        elif answer is False:
            value = _mapped_choice(False, field.options)
        return MappedQuestion(
            kind=QuestionKind.PRIVACY_CONSENT,
            value=value,
            fillable=answer is not None and value is not None,
        )

    if _is_sms_updates(text):
        answer = profile.sms_interview_updates_answer()
        return MappedQuestion(
            kind=QuestionKind.SMS_UPDATES,
            value=_mapped_choice(answer, field.options),
            fillable=True,
        )

    if _is_employee_relationship_details(text):
        relationship = profile.employee_relationship_answer()
        if relationship is not True:
            return MappedQuestion(
                kind=QuestionKind.EMPLOYEE_RELATIONSHIP_DETAILS,
                fillable=False,
                inactive_conditional=True,
            )
        value = _relationship_detail_value(text, profile)
        return MappedQuestion(
            kind=QuestionKind.EMPLOYEE_RELATIONSHIP_DETAILS,
            value=value,
            fillable=bool(value),
        )

    if _is_employee_relationship(text):
        answer = profile.employee_relationship_answer()
        return MappedQuestion(
            kind=QuestionKind.EMPLOYEE_RELATIONSHIP,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_prior_affiliation(text):
        answer = profile.prior_affiliation_answer(_search_text(field))
        return MappedQuestion(
            kind=QuestionKind.PRIOR_AFFILIATION,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_employment_restriction(text):
        answer = profile.employment_restrictions_answer()
        return MappedQuestion(
            kind=QuestionKind.EMPLOYMENT_RESTRICTIONS,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_sponsorship(text):
        scope, named = parse_sponsorship_scope(_search_text(field))
        answer, country = profile.sponsorship_answer_for_scope(scope, named)
        value: QuestionValue = None
        if answer is not None:
            if field.options:
                value = match_sponsorship_option(
                    answer,
                    field.options,
                    country,
                    relocation_willing=profile.relocation_willingness(),
                )
            else:
                value = answer
        return MappedQuestion(
            kind=QuestionKind.VISA_SPONSORSHIP,
            value=value,
            country=country,
            fillable=value is not None,
            unresolved_reason=_sponsorship_unresolved_reason(scope, country, answer, value),
        )

    if _is_work_authorization(text):
        country = _country_from_work_auth_label(field.label)
        if country is None:
            return MappedQuestion(kind=QuestionKind.WORK_AUTHORIZATION, fillable=False)
        if _is_generic_country_relative_work_auth_phrase(country):
            explicit = profile.work_eligibility.work_authorizations
            if len(explicit) == 1 and explicit[0].authorized is False:
                # The profile's sole explicit current-work-authorization fact
                # is a deterministic negative answer.  This preserves the
                # candidate's confirmed No for generic country-relative
                # controls when the ATS omits its vacancy country from the
                # discovered field and resolver metadata.
                return MappedQuestion(
                    kind=QuestionKind.WORK_AUTHORIZATION,
                    value=match_yes_no(False, field.options) if field.options else False,
                    country=explicit[0].country,
                    fillable=True,
                )
            # No named country in the label itself (e.g. "...in the country
            # for which you applied"); only the service layer, which has
            # `ResolvedVacancy`, can resolve this -- see
            # `_single_vacancy_work_country` / `_enrich_work_authorization`.
            return MappedQuestion(
                kind=QuestionKind.WORK_AUTHORIZATION,
                fillable=False,
                unresolved_reason=(
                    "work authorization relative to the role's country requires "
                    "vacancy-aware enrichment"
                ),
            )
        answer = profile.work_authorization_for(country)
        return MappedQuestion(
            kind=QuestionKind.WORK_AUTHORIZATION,
            value=answer,
            country=country,
            fillable=answer is not None,
        )

    if _is_salary(text):
        if profile.may_fill_salary() and profile.employment.salary_expectations is not None:
            amount = profile.employment.salary_expectations.amount
            currency = profile.employment.salary_expectations.currency or ""
            value = f"{amount} {currency}".strip() if amount is not None else None
            return MappedQuestion(kind=QuestionKind.SALARY, value=value, fillable=bool(value))
        return MappedQuestion(kind=QuestionKind.SALARY, fillable=False)

    if _is_years_experience(text):
        named_technologies = find_known_technologies_in_text(
            f"{field.label} {field.context}", profile.known_technology_names()
        )
        raw_technology_phrase = parse_years_experience_technology(
            field.label
        ) or parse_years_experience_technology(field.context)
        scope_terms = split_technology_scope_terms(raw_technology_phrase)
        if len(named_technologies) > 1 or len(scope_terms) > 1:
            named = named_technologies if len(named_technologies) > 1 else scope_terms
            return MappedQuestion(
                kind=QuestionKind.YEARS_EXPERIENCE,
                fillable=False,
                unresolved_reason=(
                    "years of experience question names multiple technologies "
                    f"({', '.join(named)}); a single combined answer "
                    "cannot be truthfully inferred"
                ),
            )
        technology = (named_technologies[0] if named_technologies else None) or raw_technology_phrase
        if technology:
            tech_years = profile.technology_years_for(technology)
            if tech_years is None:
                return MappedQuestion(
                    kind=QuestionKind.YEARS_EXPERIENCE,
                    fillable=False,
                    unresolved_reason=(
                        f"years of experience in {technology} requires an explicit "
                        "configured technology_years value"
                    ),
                )
            value = match_years_option(tech_years, field.options)
            return MappedQuestion(kind=QuestionKind.YEARS_EXPERIENCE, value=value, fillable=bool(value))
        years = profile.relevant_experience_years()
        if years is None:
            return MappedQuestion(kind=QuestionKind.YEARS_EXPERIENCE, fillable=False)
        value = match_years_option(years, field.options)
        return MappedQuestion(kind=QuestionKind.YEARS_EXPERIENCE, value=value, fillable=bool(value))

    if _is_named_skill_set(text):
        named = parse_named_skill_set(field.label) or parse_named_skill_set(field.context)
        if not named:
            return MappedQuestion(kind=QuestionKind.SKILL_SET_CHOICE, fillable=False)
        matches = match_named_skill_set(named, profile.known_technology_names())
        if len(matches) > 1:
            primary = profile.primary_programming_language()
            if primary and any(item.strip().lower() == primary.strip().lower() for item in matches):
                matches = [item for item in matches if item.strip().lower() == primary.strip().lower()]
        if len(matches) != 1:
            return MappedQuestion(
                kind=QuestionKind.SKILL_SET_CHOICE,
                fillable=False,
                unresolved_reason=(
                    f"named skill set ({', '.join(named)}) has no single explicit "
                    "matching configured technology"
                ),
            )
        value = matches[0]
        if field.options:
            value = match_option(value, field.options) or value
        return MappedQuestion(kind=QuestionKind.SKILL_SET_CHOICE, value=value, fillable=True)

    if _is_academic_level(text):
        level = profile.awarded_academic_level()
        if not level:
            return MappedQuestion(kind=QuestionKind.ACADEMIC_LEVEL, fillable=False)
        return MappedQuestion(kind=QuestionKind.ACADEMIC_LEVEL, value=level, fillable=True)

    if _is_school(field):
        school = profile.employment.school
        if school and field.options:
            value = match_option_exact_normalized(school, field.options)
        else:
            value = school
        return MappedQuestion(kind=QuestionKind.SCHOOL, value=value, fillable=bool(value))

    if _is_anticipated_work_country(text):
        return MappedQuestion(
            kind=QuestionKind.ANTICIPATED_WORK_COUNTRY,
            fillable=False,
            unresolved_reason="anticipated work country requires one unambiguous vacancy work country",
        )

    if _is_primary_language(text):
        language = profile.primary_programming_language()
        if not language:
            return MappedQuestion(kind=QuestionKind.PRIMARY_LANGUAGE, fillable=False)
        value = match_option(language, field.options) if field.options else language
        return MappedQuestion(
            kind=QuestionKind.PRIMARY_LANGUAGE,
            value=value,
            fillable=bool(value),
        )

    if _is_tech_stack(text):
        max_choices = parse_max_choices(text)
        stack = profile.employment.professional_tech_stack
        if not stack:
            return MappedQuestion(
                kind=QuestionKind.TECH_STACK,
                fillable=False,
                max_choices=max_choices,
            )
        # Pass the ranked profile stack. The adapter intersects with live form
        # options so incomplete discovery-time option lists cannot drop a
        # truthful match such as Spring Boot.
        return MappedQuestion(
            kind=QuestionKind.TECH_STACK,
            value=list(stack),
            fillable=True,
            max_choices=max_choices,
        )

    if _is_field_of_interest(text):
        interests = profile.employment.preferred_fields_of_interest
        value = match_interest_option(interests, field.options)
        return MappedQuestion(
            kind=QuestionKind.FIELD_OF_INTEREST,
            value=value,
            fillable=bool(value),
        )

    if is_located_or_relocate_choice(text):
        places = parse_located_or_relocate_places(field.label) or parse_located_or_relocate_places(text)
        residing = profile.resides_in_any(places) if places else None
        willing = profile.relocation_willingness()
        choice: str | None = None
        if residing is True:
            choice = ALREADY_LOCATED_CHOICE
        elif residing is False and willing is True:
            choice = WOULD_RELOCATE_CHOICE
        if choice and field.options:
            value = match_located_or_relocate_option(choice, field.options)
        else:
            # A custom React-select has no readable `field.options` at
            # discovery time; the semantic choice is carried through as-is
            # for `greenhouse._live_choice_match` to resolve against the
            # opened menu's live (non-remote) options instead.
            value = choice
        return MappedQuestion(
            kind=QuestionKind.RELOCATION,
            value=value,
            fillable=value is not None,
        )

    if _is_relocation(text):
        destination = parse_relocation_destination(field.label) or parse_relocation_destination(text)
        answer = profile.relocation_answer_for(destination)
        return MappedQuestion(
            kind=QuestionKind.RELOCATION,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_office_work(text):
        answer = profile.office_work_answer()
        return MappedQuestion(
            kind=QuestionKind.OFFICE_WORK,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_remote_work_arrangement(text):
        preference = profile.remote_work_arrangement_preference()
        if preference is None:
            return MappedQuestion(kind=QuestionKind.REMOTE_WORK_ARRANGEMENT, fillable=False)
        value = match_remote_work_arrangement_option(preference, field.options) if field.options else None
        return MappedQuestion(
            kind=QuestionKind.REMOTE_WORK_ARRANGEMENT,
            value=value,
            fillable=bool(value),
        )

    if _is_current_or_previous_employer(text):
        value = profile.employment.current_employer if field.required else None
        return MappedQuestion(kind=QuestionKind.CURRENT_EMPLOYER, value=value, fillable=bool(value))

    if _is_current_or_previous_title(text):
        value = profile.employment.current_title if field.required else None
        return MappedQuestion(kind=QuestionKind.CURRENT_TITLE, value=value, fillable=bool(value))

    if _is_current_employer(text):
        value = profile.current_employer_for_autofill()
        return MappedQuestion(kind=QuestionKind.CURRENT_EMPLOYER, value=value, fillable=bool(value))

    override = profile.question_override_answer(_search_text(field))
    if override is not None:
        value = _override_value(override, field)
        return MappedQuestion(
            kind=QuestionKind.QUESTION_OVERRIDE,
            value=value,
            fillable=value is not None,
        )

    if _is_why_company(text) or _is_professional_free_text(text, field):
        kind = QuestionKind.WHY_COMPANY if _is_why_company(text) else QuestionKind.PROFESSIONAL_FREE_TEXT
        return MappedQuestion(kind=kind, fillable=False)

    if _is_first_name(text, field):
        value = profile.identity.first_name
        return MappedQuestion(kind=QuestionKind.FIRST_NAME, value=value, fillable=bool(value))

    if _is_last_name(text, field):
        value = profile.identity.last_name
        return MappedQuestion(kind=QuestionKind.LAST_NAME, value=value, fillable=bool(value))

    if _is_full_name(text, field):
        value = f"{profile.identity.first_name} {profile.identity.last_name}".strip()
        return MappedQuestion(kind=QuestionKind.FULL_NAME, value=value, fillable=bool(value))

    if _is_email(text, field):
        value = profile.identity.email
        return MappedQuestion(kind=QuestionKind.EMAIL, value=value, fillable=bool(value))

    if _is_phone(text, field):
        value = profile.identity.phone
        return MappedQuestion(
            kind=QuestionKind.PHONE,
            value=value,
            country=profile.identity.country,
            fillable=bool(value),
        )

    if _is_identity_country(text, field):
        value = _country_value(field, profile)
        return MappedQuestion(kind=QuestionKind.COUNTRY, value=value, fillable=bool(value))

    if _is_located_in(text):
        places = parse_located_in_places(_search_text(field))
        answer = profile.resides_in_any(places) if places else None
        return MappedQuestion(
            kind=QuestionKind.LOCATED_IN,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

    if _is_identity_location(text):
        value = profile.identity.current_location
        return MappedQuestion(kind=QuestionKind.LOCATION, value=value, fillable=bool(value))

    return MappedQuestion(kind=QuestionKind.UNKNOWN, fillable=False)


def is_llm_eligible_question(field: DiscoveredField, mapped: MappedQuestion) -> bool:
    if mapped.fillable or mapped.kind in LLM_FORBIDDEN_KINDS:
        return False
    if field.field_type in {"file", "signature", "captcha"}:
        return False
    text = _search_text(field)
    if _is_llm_forbidden_text(text):
        return False
    if mapped.kind in {QuestionKind.WHY_COMPANY, QuestionKind.PROFESSIONAL_FREE_TEXT}:
        return True
    if mapped.kind is QuestionKind.UNKNOWN and field.field_type in {"text", "textarea", "select", "radio", "combobox"}:
        return _is_professional_free_text(text, field) or field.field_type == "textarea"
    return False


def _mapped_choice(answer: bool | None, options: list[str]) -> str | bool | None:
    if answer is None:
        return None
    if options:
        return match_yes_no(answer, options)
    return answer


def _sponsorship_unresolved_reason(
    scope: str,
    country: str | None,
    answer: bool | None,
    value: QuestionValue,
) -> str | None:
    if value is not None or answer is not None or scope != "current":
        return None
    place = country or "current residence"
    return f"current-location sponsorship requires country-specific fact for {place}"


def _override_value(answer: bool | str, field: DiscoveredField) -> QuestionValue:
    if isinstance(answer, bool):
        return _mapped_choice(answer, field.options)
    if field.options:
        return match_option(str(answer), field.options) or str(answer)
    return str(answer)


def _gender_value(field: DiscoveredField, profile: CandidateProfile) -> str | None:
    """Required gender uses a non-disclosure option. Optional gender is left blank.

    Never fills ``sensitive.gender``.
    """
    _ = profile
    if not field.required:
        return None
    if field.options:
        return match_prefer_not_to_disclose_gender(field.options)
    return "prefer not to disclose"


def _country_value(field: DiscoveredField, profile: CandidateProfile) -> str | None:
    wanted = profile.identity.country
    if not wanted:
        return None
    if field.options:
        return match_option(wanted, field.options)
    return wanted


def _relationship_detail_value(text: str, profile: CandidateProfile) -> str | None:
    if "name" in text:
        return profile.employee_relationship.employee_name
    return profile.employee_relationship.how_known


def _search_text(field: DiscoveredField) -> str:
    parts = [
        field.label,
        field.context,
        field.name or "",
        field.autocomplete or "",
        field.element_id or "",
    ]
    return " ".join(part for part in parts if part).lower()


def _acknowledgement_search_text(field: DiscoveredField, text: str) -> str:
    """`text` plus visible option copy, for acknowledgement classification only.

    A required select/checkbox often carries its actual privacy/consent
    meaning in the option text (e.g. "I understand that my personal data
    will be processed...") rather than in a generic label like "Recruitment
    Privacy Statement". Scoped to acknowledgement classification so it
    cannot broaden unrelated kinds (marketing detection above still uses the
    label-only `text`).
    """
    if not field.options:
        return text
    options_text = " ".join(option.lower() for option in field.options if option)
    return f"{text} {options_text}".strip()


def _is_age(text: str) -> bool:
    if re.search(r"\byour age\b", text):
        return True
    if re.fullmatch(r"age\s*\??", text.strip()):
        return True
    return "how old are you" in text


AGE_DECLINE_INTENT = "decline_to_answer"


def _age_decline_value(field: DiscoveredField) -> str | None:
    """Only ever selects an explicit decline-to-answer option. Never derives age.

    A custom React-select (e.g. Greenhouse's "What's your age?" combobox)
    has no readable `field.options` at discovery time -- only the opened
    menu's live options carry the real "I don't wish to answer" text. When
    required and no discovery-time options exist, the semantic decline
    intent is carried through as `AGE_DECLINE_INTENT` for live-choice
    matching (`greenhouse._live_choice_match`) to resolve against the live
    menu; it is never treated as literal option text to click.
    """
    if not field.required:
        return None
    if not field.options:
        return AGE_DECLINE_INTENT
    return match_decline_to_answer_option(field.options)


SENSITIVE_DECLINE_INTENT = "decline_to_answer"


def _sensitive_decline_value(field: DiscoveredField) -> str | None:
    """Required voluntary demographic self-identification (race/ethnicity,
    veteran/military status, sexual orientation, disability, etc.) only ever
    selects an explicit decline-to-answer option. Never infers or guesses a
    demographic value -- optional fields of this kind stay unfillable
    (`fillable=False` here) so the classifier keeps them sensitive/untouched.

    Mirrors `_age_decline_value`, but only for an actual custom React-select
    (`field_type == "combobox"`): that control has no readable
    `field.options` at discovery time, so the semantic decline intent is
    carried through as `SENSITIVE_DECLINE_INTENT` for live-choice matching
    (`greenhouse._live_choice_match`) to resolve against the opened menu. A
    plain "select" with no discovered options is not that live widget --
    guessing a decline intent there would silently attempt to fill a control
    that has no matching option, so it must stay unresolved/fail-closed
    instead.
    """
    if not field.required:
        return None
    if not field.options:
        return SENSITIVE_DECLINE_INTENT if field.field_type == "combobox" else None
    return match_decline_to_answer_option(field.options)


_NATIONALITY_OF_WORK_COUNTRY_RE = re.compile(
    r"national(?:ity)? of the country (?:where|in which) you (?:are|would be) applying",
    re.IGNORECASE,
)


def _is_nationality_of_vacancy_country(text: str) -> bool:
    """Explicit citizenship relative to the vacancy's work country.

    Never conflated with residence/current-location, work authorization, or
    visa questions, all of which are matched separately.
    """
    return bool(_NATIONALITY_OF_WORK_COUNTRY_RE.search(text))


def _is_visa_relocation_elaboration(text: str, field: DiscoveredField) -> bool:
    """A combined free-text visa-and-relocation explanation, e.g. "Do you need
    visa and/or relocation support for this role? If yes, please, elaborate."

    Distinct from a plain "are you willing to relocate?" yes/no question
    (still handled by `_is_relocation` below): this shape asks for prose
    that would have to weigh both visa and relocation facts together, which
    cannot be truthfully answered without vacancy-country context this
    function does not have, and must never collapse to a boolean.
    """
    if not (_is_sponsorship(text) or "visa" in text) or not _is_relocation(text):
        return False
    if field.field_type == "textarea":
        return True
    return any(term in text for term in ("elaborate", "please describe", "please explain"))


def _is_gender(text: str) -> bool:
    if "sexual orientation" in text:
        return False
    return bool(re.search(r"\b(gender|sex)\b", text))


def _is_sensitive(text: str) -> bool:
    if _is_gender(text):
        return False
    terms = (
        "ethnicity",
        "race",
        "hispanic",
        "latino",
        "disability",
        "disabled",
        "veteran",
        "sexual orientation",
        "pronoun",
    )
    return any(term in text for term in terms)


def _is_cover_letter(text: str, field: DiscoveredField) -> bool:
    ident = " ".join(part for part in (text, field.element_id or "", field.name or "") if part).lower()
    return "cover" in ident and "letter" in ident


def _is_resume(text: str, field: DiscoveredField) -> bool:
    ident = " ".join(part for part in (text, field.element_id or "", field.name or "") if part).lower()
    if "cover" in ident and "letter" in ident:
        return False
    return any(term in ident for term in ("resume", "cv", "curriculum"))


def _is_marketing(text: str) -> bool:
    return any(
        term in text
        for term in (
            "newsletter",
            "email me about",
            "other job openings",
            "marketing",
            "recruitment-related",
        )
    )


def _is_sms_updates(text: str) -> bool:
    if not any(term in text for term in ("sms", "text/sms", "text message", "text updates")):
        return False
    return any(
        term in text
        for term in ("interview", "recruit", "update", "process", "application")
    )


def _is_email(text: str, field: DiscoveredField) -> bool:
    if _is_marketing(text):
        return False
    if field.autocomplete == "email" or field.field_type == "email":
        return True
    element_id = (field.element_id or "").lower()
    name = (field.name or "").lower()
    if element_id in {"email", "job_application_email"} or name.endswith("[email]"):
        return True
    label = field.label.strip().lower().rstrip("*").strip()
    return label in {"email", "email address", "e-mail", "e-mail address", "work email"}


def _is_website(text: str) -> bool:
    if "have you read" in text or "read our" in text:
        return False
    if _is_open_source_links_text(text):
        return False
    return any(term in text for term in ("website", "portfolio", "personal site", "homepage", "blog"))


def _is_github_specific(text: str) -> bool:
    if "github" not in text:
        return False
    return not _is_website(text)


def _is_gitlab_username(text: str) -> bool:
    if "gitlab" not in text:
        return False
    if _is_prior_affiliation(text) or _is_website(text) or _is_github_specific(text):
        return False
    return any(term in text for term in ("username", "user name", "handle", "userid", "user id"))


def _is_open_source_links_text(text: str) -> bool:
    return any(term in text for term in ("open source", "open-source", "opensource")) and any(
        term in text for term in ("link", "url", "project", "contribution", "contributions")
    )


def _is_open_source_links(text: str, field: DiscoveredField) -> bool:
    if not _is_open_source_links_text(text):
        return False
    if field.options and match_yes_no(True, field.options) and match_yes_no(False, field.options):
        return False
    return True


def _is_primary_language(text: str) -> bool:
    if _is_tech_stack(text):
        return False
    normalized = " ".join(text.replace("/", " ").replace("&", " ").split())
    if "primary" not in normalized:
        return False
    if "programming language" in normalized:
        return True
    if "language" in normalized and "framework" in normalized:
        return True
    return any(
        term in normalized
        for term in (
            "language and or framework",
            "language or framework",
        )
    )


def _is_linkedin_specific(text: str) -> bool:
    if "linkedin" not in text:
        return False
    return not _is_website(text)


def _is_sponsorship(text: str) -> bool:
    return "sponsor" in text


def _is_work_authorization(text: str) -> bool:
    if "sponsor" in text:
        return False
    return any(
        term in text
        for term in (
            "authorized to work",
            "authorised to work",
            "legally authorized",
            "legally authorised",
            "work authorization",
            "work authorisation",
            "right to work",
            "eligible to work",
        )
    )


def _country_from_work_auth_label(label: str) -> str | None:
    # Some ATSes (e.g. Lever) render the required-field asterisk directly
    # appended to the question text with no separating space, which would
    # otherwise defeat the trailing `$` anchor below.
    cleaned = label.strip().rstrip("*").strip()
    match = _WORK_IN_RE.search(cleaned)
    if not match:
        return None
    country = match.group(1).strip().rstrip("?.")
    return country or None


def _is_generic_country_relative_work_auth_phrase(phrase: str) -> bool:
    """True for a work-authorization label naming the vacancy's country only
    relatively (e.g. "...in the country for which you applied", "...in the
    country in which this role is located"), never an actual country name.

    Distinct from the named-country form (e.g. "...in Germany"), which is
    used as-is against `CandidateProfile.work_authorization_for` below.
    """
    cleaned = " ".join(phrase.strip().lower().split())
    return bool(_GENERIC_COUNTRY_RELATIVE_WORK_AUTH_RE.match(cleaned))


def _is_salary(text: str) -> bool:
    return any(term in text for term in ("salary", "compensation", "expected pay", "desired pay"))


def _is_years_experience(text: str) -> bool:
    if "year" not in text:
        return False
    return any(term in text for term in ("experience", "relevant"))


def _is_academic_level(text: str) -> bool:
    return any(
        term in text
        for term in (
            "highest academic",
            "education level",
            "academic level",
            "highest level of education",
            "degree obtained",
        )
    ) or text.strip().rstrip("*").strip() == "degree" or bool(re.search(r"\bdegree\b", text))


def _is_school(field: DiscoveredField) -> bool:
    label = " ".join(field.label.lower().rstrip("*").split())
    return label in {"school", "school name", "university", "university name"}


def _is_anticipated_work_country(text: str) -> bool:
    return "anticipate" in text and "work" in text and "countr" in text


def _is_named_skill_set(text: str) -> bool:
    """A single-choice question naming a closed set of skills/languages after
    a colon, e.g. "Which of these languages are you most proficient in: Go,
    Ruby or Python?". See ``parse_named_skill_set`` for the actual parse.
    """
    return parse_named_skill_set(text) is not None


def _is_tech_stack(text: str) -> bool:
    return any(
        term in text
        for term in (
            "tech stack",
            "technologies",
            "professional experience with",
            "which of the following",
        )
    ) and any(term in text for term in ("tech", "technolog", "language", "framework", "stack"))


def _is_field_of_interest(text: str) -> bool:
    return "interest" in text and any(
        term in text for term in ("field", "preferred", "area", "discipline")
    )


def _is_relocation(text: str) -> bool:
    if _is_work_authorization(text):
        return False
    return any(term in text for term in ("relocate", "relocation", "open to relocation"))


def _is_office_work(text: str) -> bool:
    """Stated office/hybrid attendance, not current location or work authorization."""
    if _is_work_authorization(text) or _is_sponsorship(text):
        return False
    if any(term in text for term in ("relocate", "relocation")):
        return False
    if any(term in text for term in ("current location", "currently based", "where do you live")):
        return False
    hybrid = any(
        term in text
        for term in (
            "hybrid schedule",
            "hybrid work",
            "comfortable with a hybrid",
            "open to hybrid",
            "willing to work hybrid",
        )
    )
    office_days = any(
        term in text
        for term in (
            "days per week in the office",
            "days a week in the office",
            "days per week at the office",
            "days a week at the office",
            "days per week from the office",
            "in the office",
            "from our office",
            "from the office",
            "attend the office",
            "office regularly",
            "office attendance",
        )
    ) and any(
        term in text
        for term in ("willing", "comfortable", "can you", "able to", "accept", "okay with", "agree")
    )
    onsite = any(
        term in text
        for term in (
            "work onsite",
            "work on-site",
            "work on site",
            "onsite / hybrid",
            "on-site / hybrid",
            "onsite/hybrid",
            "hybrid at the",
            "work from our office",
        )
    )
    return hybrid or office_days or onsite


def _is_remote_work_arrangement(text: str) -> bool:
    """A single question offering onsite/hybrid vs remote-in-listed-countries
    vs remote-outside-listed-countries vs open-to-either, e.g. "What working
    arrangement are you ideally looking for?". Generic phrasing, not tied to
    any specific company's option wording.
    """
    if _is_relocation(text) or _is_work_authorization(text) or _is_sponsorship(text):
        return False
    return "working arrangement" in text or "work arrangement" in text


def _is_current_employer(text: str) -> bool:
    """Optional current-employer/current-company name field. Never confused
    with prior-affiliation, employment-restriction, or employee-relationship
    questions, which are matched earlier and take precedence.
    """
    if (
        _is_prior_affiliation(text)
        or _is_employment_restriction(text)
        or _is_employee_relationship(text)
        or _is_employee_relationship_details(text)
        or _is_work_authorization(text)
        or _is_sponsorship(text)
    ):
        return False
    return any(
        term in text
        for term in (
            "current company",
            "current employer",
            "present employer",
            "current organization",
            "current organisation",
        )
    )


def _is_current_or_previous_employer(text: str) -> bool:
    return "current or previous employer" in text or "current/previous employer" in text


def _is_current_or_previous_title(text: str) -> bool:
    return (
        "current or previous job title" in text
        or "current/previous job title" in text
        or "current or previous title" in text
    )


def _is_employee_relationship_details(text: str) -> bool:
    if "if yes" in text:
        return True
    if "employee" in text and any(term in text for term in ("how do you know", "employee's name", "employee name")):
        return True
    return False


def _is_employee_relationship(text: str) -> bool:
    if _is_employee_relationship_details(text) or _is_application_source(text):
        return False
    return any(
        term in text
        for term in (
            "current employee",
            "personal relationship",
            "know anyone who works",
            "referred by",
            "relationship with a current",
        )
    )


def _is_prior_affiliation(text: str) -> bool:
    if _is_employee_relationship(text) or _is_employee_relationship_details(text):
        return False
    if _is_work_authorization(text) or _is_sponsorship(text) or _is_sensitive(text) or _is_gender(text):
        return False
    if _is_years_experience(text) or "how many" in text:
        return False
    return any(
        term in text
        for term in (
            "associated with",
            "formerly associated",
            "currently or formerly",
            "current or former",
            "ex-employee",
            "ex employee",
            "conflict of interest",
            "subsidiary",
            "presently employed by",
            "currently employed by",
            "employed by any",
            "employed by a",
            "holdings group",
            "group of companies",
            "previously worked at",
            "previously worked for",
            "worked at or consulted",
            "consulted for",
            "have you previously worked",
            "former employee",
            "previously employed",
            "ever worked for",
            "ever been employed",
        )
    )


def _is_employment_restriction(text: str) -> bool:
    """Employment agreements / post-employment restrictions only. Not criminal or certifications."""
    if _is_work_authorization(text) or _is_sponsorship(text) or _is_sensitive(text):
        return False
    if any(
        term in text
        for term in (
            "criminal",
            "background check",
            "export control",
            "i certify",
            "true and complete",
            "conviction",
        )
    ):
        return False
    return any(
        term in text
        for term in (
            "employment agreement",
            "employment agreements",
            "post-employment restriction",
            "post-employment restrictions",
            "post employment restriction",
            "post employment restrictions",
            "post-employment",
        )
    )


def _is_located_in(text: str) -> bool:
    """Factual current-residence presence, not relocation or work authorization."""
    if _is_work_authorization(text) or _is_sponsorship(text) or _is_relocation(text):
        return False
    if any(
        term in text
        for term in (
            "authoriz",
            "eligible to work",
            "right to work",
            "citizen",
            "nationality",
            "visa",
        )
    ):
        return False
    return bool(parse_located_in_places(text))


def _is_application_source(text: str) -> bool:
    return any(
        term in text
        for term in (
            "how did you hear",
            "how did you find",
            "where did you hear",
            "how did you learn about",
            "hear about this job",
            "hear about this opportunity",
            "hear about us",
            "source of this application",
            "how did you come to apply",
        )
    )


def _source_checkbox_options(field: DiscoveredField) -> list[str]:
    """Recover known source choices from a checkbox group's bounded context.

    Greenhouse checkbox groups expose each option as its own control rather
    than as a field option list. Only explicit, known source labels are
    considered; an unknown option stays manual.
    """
    known = (
        "Company Website",
        "Careers Website",
        "Careers Page",
        "Direct Application",
        "LinkedIn",
        "Other",
    )
    text = f"{field.label} {field.context}".lower()
    return [label for label in known if label.lower() in text]


def _is_privacy_consent(text: str) -> bool:
    if _is_marketing(text):
        return False
    return classify_acknowledgement(text) is AcknowledgementClass.APPLICATION_PRIVACY


def _is_why_company(text: str) -> bool:
    return "why" in text and any(term in text for term in ("company", "role", "work here", "join"))


def _is_professional_free_text(text: str, field: DiscoveredField) -> bool:
    if _is_llm_forbidden_text(text) or _is_years_experience(text):
        return False
    cues = (
        "why",
        "motivation",
        "interest you",
        "tell us",
        "describe",
        "technical background",
        "relevant experience",
        "cover note",
        "what interests",
        "what excites",
        "in your own words",
        "briefly",
    )
    if any(cue in text for cue in cues):
        return True
    return field.field_type == "textarea" and not _is_cover_letter(text, field)


def _is_llm_forbidden_text(text: str) -> bool:
    terms = (
        "sponsor",
        "authoriz",
        "authoris",
        "visa",
        "salary",
        "compensation",
        "gender",
        "ethnicity",
        "disability",
        "veteran",
        "race",
        "privacy",
        "gdpr",
        "consent",
        "personal data",
        "employee",
        "referral",
        "relationship with",
        "associated with",
        "how did you hear",
        "hear about this",
        "citizenship",
        "date of birth",
        "social security",
        "newsletter",
        "sms",
        "other job openings",
        "engineering blog",
        "past 6 months",
        "past six months",
        "hybrid",
        "in the office",
        "onsite",
        "on-site",
        "data transfer",
        "background check",
        "criminal",
        "employment agreement",
        "post-employment",
        "consulted for",
        "previously worked",
        "located in",
        "country of residence",
        "open source",
        "open-source",
        "primary programming",
        "gitlab username",
        "current company",
        "current employer",
        "present employer",
        "working arrangement",
        "work arrangement",
    )
    return any(term in text for term in terms)


def _is_first_name(text: str, field: DiscoveredField) -> bool:
    if field.autocomplete in {"given-name", "fname"}:
        return True
    return "first name" in text or "first_name" in text


def _is_last_name(text: str, field: DiscoveredField) -> bool:
    if field.autocomplete in {"family-name", "lname"}:
        return True
    return "last name" in text or "last_name" in text or "family name" in text


def _is_full_name(text: str, field: DiscoveredField) -> bool:
    """A single combined name field (e.g. Lever's `name`), not first/last split."""
    _ = text
    if field.autocomplete == "name":
        return True
    element_id = (field.element_id or "").lower()
    name_attr = (field.name or "").lower()
    if element_id == "name" or name_attr == "name":
        return True
    label = field.label.strip().lower().rstrip("*").strip()
    return label in {"full name", "name", "your name", "candidate name"}


def _is_phone(text: str, field: DiscoveredField) -> bool:
    if field.autocomplete in {"tel", "tel-national"} or field.field_type == "tel":
        return True
    return any(term in text for term in ("phone", "mobile", "telephone"))


def _is_identity_location(text: str) -> bool:
    if _is_office_work(text):
        return False
    if any(term in text for term in ("authoriz", "sponsor", "remote", "job location", "relocate", "based in")):
        return False
    if "country" in text and "city" not in text:
        return False
    return any(term in text for term in ("city", "current location", "location"))


def _is_identity_country(text: str, field: DiscoveredField) -> bool:
    if _is_work_authorization(text) or _is_sponsorship(text) or _is_relocation(text):
        return False
    if any(
        term in text
        for term in (
            "authoriz",
            "eligible to work",
            "job location",
            "work in",
            "citizen",
            "nationality",
        )
    ):
        return False
    element_id = (field.element_id or "").lower()
    autocomplete = (field.autocomplete or "").lower()
    if element_id in {"country", "candidate-country"} or autocomplete in {"country", "country-name"}:
        return True
    label = field.label.strip().lower().rstrip("*").strip()
    if label in {"country", "country/region", "country of residence", "phone country"}:
        return True
    if "country of residence" in text or "current country of residence" in text:
        return True
    if "country" in text and any(
        term in text for term in ("reside", "residence", "currently live", "currently living")
    ):
        return True
    if "country" in text and ("currently based" in text or ("current" in text and "based" in text)):
        return True
    if "country/region" in text and ("current" in text or "based" in text):
        return True
    return False
