"""Stable batch ordering only: no quota, queue, eligibility inference or score edit."""
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.feasibility import ApplicationFeasibility, assess_application_feasibility, has_location_limits
from app.company_watch.models import TargetCompany
from app.company_watch.wise_eligibility import detect_wise_sponsorship_correction, is_wise_smartrecruiters_source
from app.resume_policy import ResumePolicyInput, decide_resume_variant


def employment_priority(
    vacancy: NormalizedVacancy, *, company: TargetCompany | None = None,
    feasibility: ApplicationFeasibility | None = None,
) -> tuple[int, int]:
    assessment = feasibility or assess_application_feasibility(
        vacancy_text=vacancy.to_analysis_text(), location=vacancy.location,
    )
    company_priority = {"A": 0, "B": 1, "C": 2}.get(company.priority.upper(), 3) if company else 3
    supported = (
        assessment.label == "LIKELY"
        and assessment.remote_type != "country_limited"
        and assessment.work_authorization_requirement != "required"
        and not has_location_limits(assessment.location_restrictions)
    )
    if is_wise_smartrecruiters_source(vacancy.source) and detect_wise_sponsorship_correction(vacancy.to_analysis_text()):
        supported = False  # local transfer/refusal must not gain a raw-text boost
    if not supported:
        return 3, company_priority
    worldwide = assessment.remote_type == "worldwide"
    russian_or_cis = company is not None and (
        company.russian_speaking_signal is True or company.cis_employer
        or company.language.casefold() in {"russian", "english_russian_signal"}
    )
    # Reuse the same structured vacancy-location interpretation as resume policy;
    # this call reads no CV/config/profile and does not alter resume selection.
    nz = decide_resume_variant(ResumePolicyInput(location=vacancy.location)).is_nz
    if (worldwide and russian_or_cis) or (nz and assessment.visa_sponsorship == "yes"):
        return 0, company_priority
    if worldwide or (assessment.visa_sponsorship == "yes" and assessment.relocation_support == "yes"):
        return 1, company_priority
    return 2, company_priority
