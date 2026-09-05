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

_INTERNATIONAL_RELOCATION_STATUSES = frozenset({"confirmed_role_based", "remote_global"})
_INTERNATIONAL_HIRING_MODES = frozenset(
    {
        "relocation",
        "visa_support_possible",
        "visa_sponsorship_possible",
        "remote_global",
    }
)
_INTERNATIONAL_REMOTE = frozenset({"worldwide", "yes_for_some_roles"})


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

    if seniority_label in constraints.stretch_seniority:
        return ApplicationRecommendation(
            label=RECOMMENDATION_CHECK_MANUALLY,
            reasons=[f"seniority {seniority_label} is stretch level"],
        )

    if decision_value == Decision.STRONG_MATCH.value and _seniority_allows_apply_now(
        seniority_label,
        constraints,
    ):
        boost_reasons = _apply_now_signals(
            feasibility_label=feasibility_label,
            location=location,
            company=company,
            remote_type=remote_type,
            constraints=constraints,
        )
        if boost_reasons:
            return ApplicationRecommendation(label=RECOMMENDATION_APPLY_NOW, reasons=boost_reasons)

    return ApplicationRecommendation(
        label=RECOMMENDATION_CHECK_MANUALLY,
        reasons=["sponsorship/relocation/location is unclear"],
    )


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
    location: str | None,
    company: TargetCompany | None,
    remote_type: str,
    constraints: CandidateConstraints,
) -> list[str]:
    reasons: list[str] = []
    if feasibility_label == FEASIBILITY_LIKELY:
        reasons.append("feasibility is LIKELY")
    if constraints.open_to_relocation and _location_matches_known(
        location,
        company.known_hiring_locations if company is not None else [],
    ):
        reasons.append("location matches known hiring locations")
    if constraints.open_to_remote_worldwide and remote_type == "worldwide":
        reasons.append("remote worldwide")
    if (
        company is not None
        and _company_suggests_international_hiring(company)
        and not (location or "").strip()
    ):
        reasons.append("company metadata suggests international hiring")
    return reasons


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


def _company_suggests_international_hiring(company: TargetCompany) -> bool:
    status = company.relocation_status.strip().casefold()
    if status in _INTERNATIONAL_RELOCATION_STATUSES:
        return True
    modes = {item.strip().casefold() for item in company.hiring_modes}
    if modes & _INTERNATIONAL_HIRING_MODES:
        return True
    remote = (company.remote or "").strip().casefold()
    return remote in _INTERNATIONAL_REMOTE
