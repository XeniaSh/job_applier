from __future__ import annotations

from dataclasses import dataclass

from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.feasibility import ApplicationFeasibility, FEASIBILITY_LIKELY
from app.company_watch.models import TargetCompany
from app.company_watch.seniority import (
    SENIORITY_LEAD_MANAGER,
    SENIORITY_UNKNOWN,
    SeniorityClassification,
)
from app.models import Decision

RECOMMENDATION_APPLY_NOW = "APPLY_NOW"
RECOMMENDATION_CHECK_MANUALLY = "CHECK_MANUALLY"
RECOMMENDATION_SKIP = "SKIP"
RECOMMENDATION_LABELS = (
    RECOMMENDATION_APPLY_NOW,
    RECOMMENDATION_CHECK_MANUALLY,
    RECOMMENDATION_SKIP,
)


def allows_autonomous_application_workflow(
    recommendation: ApplicationRecommendation | str,
) -> bool:
    """True when the vacancy may enter the normal discovery → application path.

    SKIP vacancies stay out of automatic Telegram delivery and must not be
    selected for automatic application. An explicit CLI invocation that
    already names source + external_id is a manual smoke-test path and
    does not use this gate.
    """
    label = (
        recommendation.label
        if isinstance(recommendation, ApplicationRecommendation)
        else str(recommendation).strip().upper()
    )
    return label in {RECOMMENDATION_APPLY_NOW, RECOMMENDATION_CHECK_MANUALLY}


@dataclass(frozen=True)
class ApplicationRecommendation:
    label: str
    reasons: list[str]


def recommend_application(
    *,
    decision: Decision | str,
    feasibility: ApplicationFeasibility | None,
    constraints: CandidateConstraints,
    company: TargetCompany | None = None,
    location: str | None = None,
    seniority: SeniorityClassification | None = None,
) -> ApplicationRecommendation:
    decision_value = decision.value if isinstance(decision, Decision) else str(decision).strip().upper()
    visa = feasibility.visa_sponsorship if feasibility is not None else "unknown"
    relocation = feasibility.relocation_support if feasibility is not None else "unknown"
    work_auth = feasibility.work_authorization_requirement if feasibility is not None else "unknown"
    remote_type = feasibility.remote_type if feasibility is not None else "unknown"
    languages = list(feasibility.language_requirements) if feasibility is not None else []
    feasibility_label = feasibility.label if feasibility is not None else None
    seniority_label = seniority.label if seniority is not None else SENIORITY_UNKNOWN

    if decision_value == Decision.IGNORE.value:
        return ApplicationRecommendation(
            label=RECOMMENDATION_SKIP,
            reasons=["technical decision is IGNORE"],
        )

    skip_reasons = _hard_blockers(
        languages=languages,
        known_languages=constraints.known_languages,
        work_auth=work_auth,
        visa=visa,
        relocation=relocation,
        seniority_label=seniority_label,
        excluded_seniority=constraints.excluded_seniority,
    )
    if skip_reasons:
        return ApplicationRecommendation(label=RECOMMENDATION_SKIP, reasons=skip_reasons)

    location_note = _known_hiring_location_reason(
        location=location,
        company=company,
    )

    if seniority_label in constraints.stretch_seniority:
        reasons = [f"seniority {seniority_label} is stretch level"]
        if location_note:
            reasons.append(location_note)
        return ApplicationRecommendation(label=RECOMMENDATION_CHECK_MANUALLY, reasons=reasons)

    if decision_value == Decision.STRONG_MATCH.value and _seniority_allows_apply_now(
        seniority_label,
        constraints,
    ):
        boost_reasons = _apply_now_signals(
            feasibility_label=feasibility_label,
            visa=visa,
            relocation=relocation,
            remote_type=remote_type,
            constraints=constraints,
        )
        if boost_reasons:
            if location_note:
                boost_reasons.append(location_note)
            return ApplicationRecommendation(label=RECOMMENDATION_APPLY_NOW, reasons=boost_reasons)

    check_reasons = ["sponsorship/relocation/location is unclear"]
    if location_note:
        check_reasons.append(location_note)
    return ApplicationRecommendation(label=RECOMMENDATION_CHECK_MANUALLY, reasons=check_reasons)


def _hard_blockers(
    *,
    languages: list[str],
    known_languages: list[str],
    work_auth: str,
    visa: str,
    relocation: str,
    seniority_label: str,
    excluded_seniority: list[str],
) -> list[str]:
    reasons: list[str] = []
    if seniority_label in excluded_seniority:
        reasons.append(_excluded_seniority_reason(seniority_label))
    missing_languages = _missing_required_languages(languages, known_languages)
    if missing_languages:
        reasons.append(
            "required language not in known languages: " + ", ".join(missing_languages)
        )
    if work_auth == "required" and visa != "yes" and relocation != "yes":
        reasons.append("local work authorization required without visa/relocation support")
    return reasons


def _apply_now_signals(
    *,
    feasibility_label: str | None,
    visa: str,
    relocation: str,
    remote_type: str,
    constraints: CandidateConstraints,
) -> list[str]:
    reasons: list[str] = []
    if visa == "yes":
        reasons.append("visa sponsorship is available")
    if relocation == "yes":
        reasons.append("relocation support is available")
    if constraints.open_to_remote_worldwide and remote_type == "worldwide":
        reasons.append("remote worldwide")
    if feasibility_label == FEASIBILITY_LIKELY:
        reasons.append("feasibility is LIKELY")
    return reasons


def _known_hiring_location_reason(
    *,
    location: str | None,
    company: TargetCompany | None,
) -> str | None:
    if company is None:
        return None
    if _location_matches_known(location, company.known_hiring_locations):
        return "location matches known hiring locations"
    return None


def _seniority_allows_apply_now(seniority_label: str, constraints: CandidateConstraints) -> bool:
    if seniority_label == SENIORITY_UNKNOWN:
        return True
    return seniority_label in constraints.target_seniority


def _excluded_seniority_reason(seniority_label: str) -> str:
    if seniority_label == SENIORITY_LEAD_MANAGER:
        return "lead/manager role is not target IC backend role"
    return f"seniority {seniority_label} is excluded"


def _missing_required_languages(required: list[str], known: list[str]) -> list[str]:
    if not required:
        return []
    known_normalized = [item.casefold() for item in known]
    missing: list[str] = []
    for raw in required:
        text = " ".join(raw.strip().lower().split())
        if not text:
            continue
        if not any(token in text or text in token for token in known_normalized):
            missing.append(text)
    return missing


def _location_matches_known(location: str | None, known_hiring_locations: list[str]) -> bool:
    loc = " ".join((location or "").strip().lower().split())
    if not loc or not known_hiring_locations:
        return False
    for item in known_hiring_locations:
        token = " ".join(item.strip().lower().split())
        if token and (token in loc or loc in token):
            return True
    return False
