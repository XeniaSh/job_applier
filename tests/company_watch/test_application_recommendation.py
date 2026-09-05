from app.company_watch.application_recommendation import recommend_application
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.models import TargetCompany
from app.models import Decision


def _constraints() -> CandidateConstraints:
    return CandidateConstraints(
        known_languages=["english", "russian"],
        requires_visa_sponsorship=True,
        open_to_relocation=True,
        open_to_remote_worldwide=True,
    )


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Adyen",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "greenhouse",
        "hiring_modes": ["relocation", "hybrid"],
        "known_hiring_locations": ["Amsterdam", "Netherlands"],
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _feasibility(
    *,
    label: str = "UNCLEAR",
    visa_sponsorship: str = "unknown",
    relocation_support: str = "unknown",
    remote_type: str = "unknown",
    work_authorization_requirement: str = "unknown",
    language_requirements: list[str] | None = None,
) -> ApplicationFeasibility:
    return ApplicationFeasibility(
        label=label,
        visa_sponsorship=visa_sponsorship,
        relocation_support=relocation_support,
        remote_type=remote_type,
        work_authorization_requirement=work_authorization_requirement,
        language_requirements=language_requirements or [],
        location_restrictions=[],
        warnings=["visa/relocation not mentioned; check manually"],
    )


def test_ignore_is_skip() -> None:
    result = recommend_application(
        decision=Decision.IGNORE,
        feasibility=_feasibility(),
        constraints=_constraints(),
        company=_company(),
        location="Amsterdam",
    )
    assert result.label == "SKIP"
    assert "technical decision is IGNORE" in result.reasons


def test_language_blocker_is_skip() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(language_requirements=["portuguese"]),
        constraints=_constraints(),
        company=_company(),
        location="Amsterdam",
    )
    assert result.label == "SKIP"
    assert any("portuguese" in reason for reason in result.reasons)


def test_empty_language_requirements_are_not_skip() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(language_requirements=[]),
        constraints=_constraints(),
        company=_company(),
        location="Amsterdam",
    )
    assert result.label != "SKIP"


def test_work_authorization_required_without_sponsorship_is_skip() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(work_authorization_requirement="required"),
        constraints=_constraints(),
        company=_company(),
        location="Chicago",
    )
    assert result.label == "SKIP"
    assert any("work authorization" in reason for reason in result.reasons)


def test_strong_match_with_known_hiring_location_is_apply_now() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(),
        constraints=_constraints(),
        company=_company(),
        location="Amsterdam",
    )
    assert result.label == "APPLY_NOW"
    assert "location matches known hiring locations" in result.reasons


def test_strong_match_without_location_signal_is_check_manually() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(),
        constraints=_constraints(),
        company=_company(known_hiring_locations=[]),
        location="Sao Jose dos Campos",
    )
    assert result.label == "CHECK_MANUALLY"
    assert "sponsorship/relocation/location is unclear" in result.reasons


def test_strong_match_likely_feasibility_is_apply_now() -> None:
    result = recommend_application(
        decision=Decision.STRONG_MATCH,
        feasibility=_feasibility(label="LIKELY", visa_sponsorship="yes"),
        constraints=_constraints(),
        company=_company(known_hiring_locations=[]),
        location="Chicago",
    )
    assert result.label == "APPLY_NOW"
    assert "feasibility is LIKELY" in result.reasons
