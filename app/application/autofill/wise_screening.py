"""Wise questionnaire semantics. No selectors, browser access or invented facts."""

from __future__ import annotations

from dataclasses import replace
import re

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind, map_question
from app.application.autofill.salary import _multiple_locations, resolve_salary_band_acceptance
from app.application.candidate_profile import CandidateProfile, normalize_country_name

NOTICE = "What is your notice period?"
ELIGIBILITY = "How are you eligible to work in this location?"
OFFICE = (
    "We have a flexible working model at Wise. As part of our guidance, we expect Wisers to be in "
    "the office most days to build connections and learn from colleagues, particularly in their "
    "first six months. Do you live in a commutable distance from the office and are you willing "
    "to work from the office regularly?"
)
CUSTOMER = "Are you currently a Wise customer?"
SALARY = "Does the advertised salary band meet your expectations?"
PWC = "Have you been employed by our external audit firm, PWC?"
PRIVACY = "You declare that you have read and agree to the privacy notice of Wise."
ELIGIBILITY_OPTIONS = (
    "I hold citizenship or permanent residency (No visa/sponsorship required)",
    "I hold a temporary work visa / permit that allows me to work full-time",
    "I hold a student or temporary visa with work-hour restrictions",
    "I do not currently have work authorization and will require visa sponsorship",
    "Other",
)
OFFICE_OPTIONS = (
    "Yes - I'm willing to work from the office regularly",
    "Yes - I am willing to relocate and I'm willing to work from the office regularly",
    "No - I'm not able to work from the office regularly",
)
DECLINE = "decline_to_answer"


def _norm(text: str) -> str:
    return " ".join(text.replace("’", "'").casefold().split()).rstrip(" *")


def wise_work_country(vacancy: object) -> str | None:
    """Only an explicit single country from the observed Wise markets.

    Deliberately no geocoding, city-only inference, phone default or global
    sponsorship fallback. Additional Wise markets require their own evidence.
    """
    normalized = getattr(vacancy, "vacancy", None)
    if _norm(getattr(normalized, "company", "") or "") != "wise":
        return None
    location = getattr(normalized, "location", None) or ""
    if _multiple_locations(location) or re.search(r"[;/|&+]|\b(?:and|or)\b", location, re.I):
        return None
    parts = [_norm(p) for p in location.split(",") if p.strip()]
    if not parts:
        return None
    country = normalize_country_name(parts[-1])
    known = {"estonia", "united kingdom"}
    if country not in known:
        return None
    mentioned = {normalize_country_name(p) for p in parts if normalize_country_name(p) in known}
    return country if mentioned == {country} else None


def manual(item: ClassifiedField, reason: str) -> ClassifiedField:
    # UNSUPPORTED also excludes LLM enrichment in the shared pipeline.
    return replace(item, classification=FieldClassification.UNSUPPORTED,
                   kind=QuestionKind.UNKNOWN, value=None, fill=False, generated=False,
                   unresolved_reason=reason)


def _answer(item: ClassifiedField, value, *, kind=QuestionKind.UNKNOWN, reason=None) -> ClassifiedField:
    if value is None or value == "":
        return manual(item, reason or "Wise question requires an explicit applicable candidate answer.")
    return replace(item, classification=FieldClassification.SUPPORTED_DETERMINISTIC,
                   kind=kind, value=value, fill=True, generated=False, unresolved_reason=reason)


def _override(profile: CandidateProfile, label: str):
    answers = [o.answer for o in profile.application_policy.question_overrides if o.matches(label)]
    if not answers or any(type(a) is not type(answers[0]) or a != answers[0] for a in answers):
        return None
    return answers[0]


def _eligibility(profile: CandidateProfile, vacancy: object, override):
    country = wise_work_country(vacancy)
    if country is None:
        return None
    facts = [f for f in profile.work_eligibility.work_authorizations
             if normalize_country_name(f.country) == country]
    if len(facts) > 1:
        return None
    authorized = facts[0].authorized if facts else None
    sponsored = facts[0].requires_sponsorship if facts else None
    citizen = profile.is_national_of(country)
    if authorized is False and citizen is True:
        return None  # conflicting configured facts, not an inferred repair
    if isinstance(override, str) and override in ELIGIBILITY_OPTIONS:
        index = ELIGIBILITY_OPTIONS.index(override)
        # Foreign citizenship does not contradict permanent residency. The
        # full override supplies that assertion; we never infer it ourselves.
        if index == 0 and (authorized is False or sponsored is True):
            return None
        if index in (1, 2) and (authorized is False or citizen is True):
            return None
        if index == 3 and (authorized is True or sponsored is False or citizen is True):
            return None
        return override
    if citizen is True and authorized is True and sponsored is False:
        return ELIGIBILITY_OPTIONS[0]
    if authorized is False and sponsored is True:
        return ELIGIBILITY_OPTIONS[3]
    # Authorization does not identify a temporary/student visa or permanent
    # residency. Neither residence nor relocation supplies the missing subtype.
    return None


def _office(profile: CandidateProfile, vacancy: object, override):
    if wise_work_country(vacancy) is None:
        return None
    willing = profile.application_policy.office_work.willing
    relocate = profile.relocation_willingness()
    if isinstance(override, str) and override in OFFICE_OPTIONS:
        if override != OFFICE_OPTIONS[2] and willing is False:
            return None
        if override == OFFICE_OPTIONS[1] and relocate is False:
            return None
        return override
    if willing is False:
        return OFFICE_OPTIONS[2]
    country = wise_work_country(vacancy)
    destinations = {normalize_country_name(d) for d in profile.employment.relocation_destinations}
    if willing is True and relocate is True and country in destinations:
        return OFFICE_OPTIONS[1]
    # Living in the same city does not prove a commutable distance. Generic
    # relocation willingness without this destination does not supply it either.
    return None


def _survey(label: str) -> bool:
    return label in {_norm(x) for x in (
        "What's your age?",
        "Are you part of the first generation of your family to study further education? e.g. college or university?",
        "What gender do you most closely identify with?",
        "Do you identify with a gender that is different to the gender assigned to you at birth?",
        "I identify my sexual orientation as (mark all that apply)",
        "What race/ethnicity do you most closely identify with (mark all that apply):",
    )} or label.startswith((
        "do you have a disability or neurodiversity", "do you have, or have you ever had, a disability",
        "are you hispanic/latin american?", "if you are applying from the us", "us-applicant veteran status",
    ))


def _audit(label: str) -> bool:
    return (label.startswith("were you involved, in any capacity, in the audit of wise's financial statements,")
            and re.search(r"\b(?:last|past)\s+(?:12|twelve)\s+months\b", label) is not None)


def wise_screening_inventory_complete(fields) -> bool:
    """A partially hydrated/substantially changed Wise form is not complete.

    These are required semantic groups observed on both inspected vacancies,
    not hardcoded question IDs. Unknown wording remains a manual boundary.
    """
    labels = {_norm(f.label) for f in fields if f.required}
    if not {_norm(x) for x in (NOTICE, ELIGIBILITY, OFFICE, CUSTOMER, SALARY, PRIVACY)} <= labels:
        return False
    predicates = (
        _audit,
        lambda s: s == "what's your age?",
        lambda s: s.startswith("are you part of the first generation of your family to study further education?"),
        lambda s: s.startswith(("do you have a disability or neurodiversity", "do you have, or have you ever had, a disability")),
        lambda s: s == "what gender do you most closely identify with?",
        lambda s: s == "do you identify with a gender that is different to the gender assigned to you at birth?",
        lambda s: s == "i identify my sexual orientation as (mark all that apply)",
        lambda s: s.startswith("are you hispanic/latin american?"),
        lambda s: s == "what race/ethnicity do you most closely identify with (mark all that apply):",
        lambda s: s.startswith(("if you are applying from the us", "us-applicant veteran status")),
    )
    return all(any(predicate(label) for label in labels) for predicate in predicates)


def map_wise_screening(item: ClassifiedField, *, profile: CandidateProfile, vacancy: object) -> ClassifiedField:
    if _norm(getattr(vacancy, "company", "") or "") != "wise":
        return manual(item, "Wise screening context is not verified.")
    field = item.field
    label = _norm(field.label)
    if field.field_type not in {"text", "textarea", "combobox", "multiselect", "radio", "checkbox"}:
        return manual(item, "Unsupported SmartRecruiters screening control.")
    if label == _norm(NOTICE) and field.field_type in {"text", "textarea"}:
        return _answer(item, profile.employment.notice_period, kind=QuestionKind.NOTICE_PERIOD)
    if label == _norm(SALARY) and field.field_type == "radio":
        normalized = getattr(vacancy, "vacancy", None)
        if (normalized is None or _norm(getattr(normalized, "company", "") or "") != "wise"
                or _norm(normalized.title) != _norm(getattr(vacancy, "title", ""))):
            return manual(item, "Published compensation context conflicts with the resolved Wise vacancy.")
        answer, reason = resolve_salary_band_acceptance(
            profile.employment.salary_expectations, title=getattr(vacancy, "title", ""),
            vacancy=getattr(vacancy, "vacancy", None),
        )
        # Not QuestionKind.SALARY: the shared numeric resolver must never
        # override a Yes/No judgement with an amount or midpoint.
        return _answer(item, answer, reason=reason)
    override = _override(profile, field.label)
    if label == _norm(ELIGIBILITY) and field.field_type == "combobox":
        return _answer(item, _eligibility(profile, vacancy, override))
    if label == _norm(OFFICE) and field.field_type == "combobox":
        return _answer(item, _office(profile, vacancy, override))
    if (label == _norm(CUSTOMER) or _audit(label)) and field.field_type == "radio":
        return _answer(item, override if isinstance(override, bool) else None)
    if label == _norm(PWC) and field.field_type == "combobox":
        return _answer(item, override if isinstance(override, bool) else None)
    if label == _norm(PRIVACY) and field.field_type == "checkbox" and field.required:
        mapped = map_question(field, profile)
        if (profile.privacy_acknowledgement_answer(required=True) is True
                and mapped.kind is QuestionKind.PRIVACY_CONSENT and mapped.fillable and mapped.value is True):
            return _answer(item, mapped.value, kind=mapped.kind)
        return manual(item, "Required Wise privacy acknowledgement needs manual review.")
    if _survey(label) and field.field_type in {"combobox", "multiselect"}:
        if not field.required:
            return manual(item, "Optional demographic survey stays untouched.")
        mapped = map_question(field, profile)
        if mapped.fillable and mapped.kind in {QuestionKind.GENDER, QuestionKind.AGE, QuestionKind.SENSITIVE}:
            return _answer(item, mapped.value, kind=mapped.kind)
        return _answer(item, DECLINE, kind=QuestionKind.SENSITIVE)
    # Includes optional privacy/marketing/talent-pool and unknown legal consent,
    # languages, self-description and any unexpected employer question.
    return manual(item, "Unknown, ambiguous or unsupported Wise question; answer manually.")
