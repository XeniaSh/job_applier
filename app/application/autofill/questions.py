from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import (
    match_application_source,
    match_gender_option,
    match_interest_option,
    match_option,
    match_prefer_not_to_disclose_gender,
    match_years_option,
    match_yes_no,
    parse_max_choices,
    parse_relocation_destination,
)
from app.application.candidate_profile import CandidateProfile

_WORK_IN_RE = re.compile(
    r"(?:authorized|authorised|eligible|right)\s+to\s+work\s+in(?:\s+the)?\s+(.+?)\??$",
    re.IGNORECASE,
)

QuestionValue = str | bool | list[str] | None


class QuestionKind(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    COUNTRY = "country"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    WEBSITE = "website"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    VISA_SPONSORSHIP = "visa_sponsorship"
    WORK_AUTHORIZATION = "work_authorization"
    SALARY = "salary"
    YEARS_EXPERIENCE = "years_experience"
    ACADEMIC_LEVEL = "academic_level"
    TECH_STACK = "tech_stack"
    FIELD_OF_INTEREST = "field_of_interest"
    RELOCATION = "relocation"
    EMPLOYEE_RELATIONSHIP = "employee_relationship"
    EMPLOYEE_RELATIONSHIP_DETAILS = "employee_relationship_details"
    PRIOR_AFFILIATION = "prior_affiliation"
    APPLICATION_SOURCE = "application_source"
    PRIVACY_CONSENT = "privacy_consent"
    NEWSLETTER = "newsletter"
    SMS_UPDATES = "sms_updates"
    QUESTION_OVERRIDE = "question_override"
    WHY_COMPANY = "why_company"
    PROFESSIONAL_FREE_TEXT = "professional_free_text"
    GENDER = "gender"
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
        QuestionKind.APPLICATION_SOURCE,
        QuestionKind.PRIVACY_CONSENT,
        QuestionKind.NEWSLETTER,
        QuestionKind.SMS_UPDATES,
        QuestionKind.QUESTION_OVERRIDE,
        QuestionKind.GENDER,
        QuestionKind.FIRST_NAME,
        QuestionKind.LAST_NAME,
        QuestionKind.EMAIL,
        QuestionKind.PHONE,
        QuestionKind.LOCATION,
        QuestionKind.COUNTRY,
        QuestionKind.LINKEDIN,
        QuestionKind.GITHUB,
        QuestionKind.WEBSITE,
        QuestionKind.RESUME,
        QuestionKind.COVER_LETTER,
        QuestionKind.YEARS_EXPERIENCE,
        QuestionKind.ACADEMIC_LEVEL,
        QuestionKind.TECH_STACK,
        QuestionKind.FIELD_OF_INTEREST,
        QuestionKind.RELOCATION,
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


def map_question(field: DiscoveredField, profile: CandidateProfile) -> MappedQuestion:
    """Map a discovered field to an explicit profile value. Never guesses."""
    text = _search_text(field)

    if _is_gender(text):
        value = _gender_value(field, profile)
        return MappedQuestion(kind=QuestionKind.GENDER, value=value, fillable=bool(value))

    if _is_sensitive(text):
        return MappedQuestion(kind=QuestionKind.SENSITIVE, fillable=False)

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

    if _is_website(text):
        value = profile.website_for_autofill()
        return MappedQuestion(kind=QuestionKind.WEBSITE, value=value, fillable=bool(value))

    if _is_marketing(text):
        answer = profile.newsletter_opt_in_answer()
        return MappedQuestion(
            kind=QuestionKind.NEWSLETTER,
            value=_mapped_choice(answer, field.options),
            fillable=True,
        )

    if _is_sms_updates(text):
        answer = profile.sms_interview_updates_answer()
        return MappedQuestion(
            kind=QuestionKind.SMS_UPDATES,
            value=_mapped_choice(answer, field.options),
            fillable=True,
        )

    if _is_application_source(text):
        value = match_application_source(field.options, profile.application_source_preference())
        if not value and not field.options:
            value = profile.application_source_preference()[0] if profile.application_source_preference() else None
        return MappedQuestion(
            kind=QuestionKind.APPLICATION_SOURCE,
            value=value,
            fillable=bool(value),
        )

    if _is_privacy_consent(text):
        answer = profile.application_consent.privacy_data_processing
        return MappedQuestion(
            kind=QuestionKind.PRIVACY_CONSENT,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is True,
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

    if _is_sponsorship(text):
        answer = profile.explicit_requires_visa_sponsorship()
        return MappedQuestion(
            kind=QuestionKind.VISA_SPONSORSHIP,
            value=answer,
            fillable=answer is not None,
        )

    if _is_work_authorization(text):
        country = _country_from_work_auth_label(field.label)
        if country is None:
            return MappedQuestion(kind=QuestionKind.WORK_AUTHORIZATION, fillable=False)
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
        years = profile.relevant_experience_years()
        if years is None:
            return MappedQuestion(kind=QuestionKind.YEARS_EXPERIENCE, fillable=False)
        value = match_years_option(years, field.options)
        return MappedQuestion(kind=QuestionKind.YEARS_EXPERIENCE, value=value, fillable=bool(value))

    if _is_academic_level(text):
        level = profile.awarded_academic_level()
        if not level:
            return MappedQuestion(kind=QuestionKind.ACADEMIC_LEVEL, fillable=False)
        return MappedQuestion(kind=QuestionKind.ACADEMIC_LEVEL, value=level, fillable=True)

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

    if _is_relocation(text):
        destination = parse_relocation_destination(field.label) or parse_relocation_destination(text)
        answer = profile.relocation_answer_for(destination)
        return MappedQuestion(
            kind=QuestionKind.RELOCATION,
            value=_mapped_choice(answer, field.options) if answer is not None else None,
            fillable=answer is not None,
        )

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


def _override_value(answer: bool | str, field: DiscoveredField) -> QuestionValue:
    if isinstance(answer, bool):
        return _mapped_choice(answer, field.options)
    if field.options:
        return match_option(str(answer), field.options) or str(answer)
    return str(answer)


def _gender_value(field: DiscoveredField, profile: CandidateProfile) -> str | None:
    wanted = profile.gender_for_autofill()
    if not wanted:
        return None
    if field.options:
        if profile.application_policy.prefer_not_to_disclose_gender:
            return match_prefer_not_to_disclose_gender(field.options) or wanted
        return match_gender_option(wanted, field.options)
    return wanted


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
    parts = [field.label, field.name or "", field.autocomplete or "", field.element_id or ""]
    return " ".join(parts).lower()


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
    return any(term in text for term in ("website", "portfolio", "personal site", "homepage", "blog"))


def _is_github_specific(text: str) -> bool:
    if "github" not in text:
        return False
    return not _is_website(text)


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
    match = _WORK_IN_RE.search(label.strip())
    if not match:
        return None
    country = match.group(1).strip().rstrip("?.")
    return country or None


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
    )


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
        )
    )


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


def _is_privacy_consent(text: str) -> bool:
    if _is_marketing(text):
        return False
    return any(
        term in text
        for term in (
            "privacy",
            "data processing",
            "personal data",
            "gdpr",
            "consent to process",
            "process my data",
            "privacy policy",
        )
    )


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


def _is_phone(text: str, field: DiscoveredField) -> bool:
    if field.autocomplete in {"tel", "tel-national"} or field.field_type == "tel":
        return True
    return any(term in text for term in ("phone", "mobile", "telephone"))


def _is_identity_location(text: str) -> bool:
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
    if "country" in text and ("currently based" in text or "current" in text and "based" in text):
        return True
    if "country/region" in text and ("current" in text or "based" in text):
        return True
    return False
