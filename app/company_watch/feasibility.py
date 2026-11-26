from __future__ import annotations

from dataclasses import dataclass
import re

FEASIBILITY_LIKELY = "LIKELY"
FEASIBILITY_UNCLEAR = "UNCLEAR"
FEASIBILITY_UNLIKELY = "UNLIKELY"
FEASIBILITY_LABELS = (FEASIBILITY_LIKELY, FEASIBILITY_UNCLEAR, FEASIBILITY_UNLIKELY)

_YES_VALUES = frozenset({"yes", "y", "true", "available", "provided", "supported", "offered"})
_NO_VALUES = frozenset({"no", "n", "false", "unavailable", "unsupported", "none"})
_UNKNOWN_VALUES = frozenset({"", "unknown", "null", "none", "n/a", "na", "unspecified", "not specified"})

_VISA_YES_MARKERS = (
    "visa sponsorship",
    "sponsorship available",
    "sponsorship provided",
    "we sponsor",
    "will sponsor",
    "can sponsor",
    "visa support",
    "sponsors visas",
    "sponsorship for",
)
_VISA_NO_MARKERS = (
    "no visa sponsorship",
    "no sponsorship",
    "cannot sponsor",
    "can't sponsor",
    "unable to sponsor",
    "not able to sponsor",
    "does not sponsor",
    "do not sponsor",
    "will not sponsor",
    "won't sponsor",
    "not offering visa",
    "without visa sponsorship",
    "without the need for visa sponsorship",
    "without the need for sponsorship",
    "visa sponsorship is not available",
    "visa sponsorship is not provided",
    "visa sponsorship is not offered",
    "sponsorship not available",
    "do not provide visa sponsorship",
    "cannot offer visa sponsorship",
    "do not offer relocation support or visa sponsorship",
    "unable to support relocation or visa sponsorship",
    "визовая поддержка не предоставляется",
    "не предоставляем визовую поддержку",
)
_RELOCATION_YES_MARKERS = (
    "relocation support",
    "relocation package",
    "relocation assistance",
    "relocation provided",
    "relocation bonus",
    "help with relocation",
    "we offer relocation",
    "relocation is provided",
    "full relocation",
)
_REMOTE_WORLDWIDE_MARKERS = (
    "remote worldwide",
    "worldwide remote",
    "work from anywhere",
    "anywhere in the world",
    "globally remote",
    "remote-first globally",
    "remote globally",
    "remote contractors worldwide",
)
_WORK_AUTH_REQUIRED_MARKERS = (
    "must be authorized to work",
    "must have authorization to work",
    "must have the right to work",
    "must already be eligible to work",
    "must be eligible to work",
    "legal right to work",
    "us work authorization required",
    "work authorization required",
    "work authorisation required",
    "citizens only",
    "permanent residents only",
    "permanent resident only",
)

# Only explicit restrictions, never a job's address or the candidate's location.
# Unknown country compatibility is a manual check, not a factual rejection.
_GEOGRAPHIC_RESTRICTIONS = re.compile(
    r"\b(?:must|need to|required to) (?:already |currently )?"
    r"(?:be based|be located|live|reside|be resident|be physically located) (?:in|within)\b|"
    r"\b(?:only (?:hire|hiring|employ|accept)|hiring is limited to|"
    r"remote (?:work|working) (?:is )?(?:limited|restricted) to)\b|"
    r"\b(?:remote|work(?:ing)? remotely) (?:only (?:in|from)|within)\b|"
    r"\b(?:us|usa|uk|eu|eea|europe|russia|new zealand|australia|canada) (?:residents? )?only\b|"
    r"\b(?:allowed|eligible|excluded|supported) countries\b|"
    r"\b(?:residents|local candidates) only\b|"
    r"\b(?:except|excluding) (?:the )?(?:us|usa|uk|eu|russia|belarus)\b|"
    r"\b(?:not (?:worldwide|globally remote)|cannot work from anywhere)\b|"
    r"(?:удал[её]н\w* (?:работа )?только (?:из|в|по)|"
    r"только (?:на территории|из|в) (?:рф|россии|казахстана|узбекистана)|"
    r"(?:необходимо|нужно|обязательно) прожива\w*|кроме (?:рф|россии|рб|беларуси))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ApplicationFeasibility:
    label: str
    visa_sponsorship: str
    relocation_support: str
    remote_type: str
    work_authorization_requirement: str
    language_requirements: list[str]
    location_restrictions: list[str]
    warnings: list[str]
    alternative_remote_route: bool = False


def assess_application_feasibility(
    *,
    vacancy_text: str = "",
    location: str | None = None,
    visa_sponsorship: str = "unknown",
    relocation_support: str = "unknown",
    remote_type: str = "unknown",
    work_authorization_requirement: str = "unknown",
    language_requirements: list[str] | None = None,
    location_restrictions: list[str] | None = None,
    alternative_remote_route: bool | None = None,
) -> ApplicationFeasibility:
    combined = " ".join(
        part
        for part in (
            vacancy_text,
            location or "",
            " ".join(location_restrictions or []),
        )
        if part
    ).lower()

    visa = _merge_ternary(visa_sponsorship, _scan_ternary(combined, _VISA_YES_MARKERS, _VISA_NO_MARKERS))
    relocation = _merge_ternary(
        relocation_support,
        _scan_ternary(combined, _RELOCATION_YES_MARKERS, ()),
    )
    remote = _merge_remote(remote_type, combined)
    work_auth = _merge_work_auth(work_authorization_requirement, combined)
    languages = [item for item in (language_requirements or []) if item.strip()]
    restrictions = [item for item in (location_restrictions or []) if item.strip()]
    if alternative_remote_route is None:
        alternative_remote_route = (
            _normalize_remote(remote_type) in {"worldwide", "country_limited"}
            or bool(re.search(r"\bremote (?:worldwide|contractors?|work|role|within|only|from)\b|"
                              r"\bwork (?:remotely|from anywhere)\b|удал[её]н\w* работ", combined))
        )

    label, warnings = _classify(
        visa=visa,
        relocation=relocation,
        remote=remote,
        work_auth=work_auth,
        languages=languages,
        location=location,
        location_limited=has_location_limits(restrictions),
        alternative_remote_route=alternative_remote_route,
    )
    return ApplicationFeasibility(
        label=label,
        visa_sponsorship=visa,
        relocation_support=relocation,
        remote_type=remote,
        work_authorization_requirement=work_auth,
        language_requirements=languages,
        location_restrictions=restrictions,
        warnings=warnings,
        alternative_remote_route=alternative_remote_route,
    )


def _classify(
    *,
    visa: str,
    relocation: str,
    remote: str,
    work_auth: str,
    languages: list[str],
    location: str | None,
    location_limited: bool = False,
    alternative_remote_route: bool = False,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    explicit_local_block = visa == "no" and remote != "worldwide" and not alternative_remote_route

    if explicit_local_block:
        if work_auth == "required":
            warnings.append("local work authorization required")
        if visa == "no":
            warnings.append("visa sponsorship not offered")
        if location:
            warnings.append(f"check eligibility for {location.strip()}")
        return FEASIBILITY_UNLIKELY, warnings

    if (work_auth == "required" or remote == "country_limited" or location_limited
            or (visa == "no" and alternative_remote_route and remote != "worldwide")):
        # No country-specific candidate facts are available in this layer.
        # Even positive marketing/sponsorship cannot settle a conflicting limit.
        warnings.append(
            "visa/relocation not mentioned; check manually" if visa == "unknown" and relocation == "unknown"
            else "country/work-authorization compatibility requires confirmation"
        )
        if remote == "country_limited":
            warnings.append("explicit geographic hiring restriction; check compatibility manually")
        if work_auth == "required":
            warnings.append("existing local work authorization requires confirmation")
        return FEASIBILITY_UNCLEAR, warnings

    likely_signals: list[str] = []
    if visa == "yes":
        likely_signals.append("visa sponsorship mentioned")
    if relocation == "yes":
        likely_signals.append("relocation support mentioned")
    if remote == "worldwide":
        likely_signals.append("remote worldwide")
    if likely_signals:
        return FEASIBILITY_LIKELY, likely_signals

    warnings.append("visa/relocation not mentioned; check manually")
    if location:
        warnings.append(f"location is {location.strip()}")
    if languages:
        warnings.append("language requirements: " + ", ".join(languages[:3]))
    return FEASIBILITY_UNCLEAR, warnings


def has_location_limits(restrictions: list[str]) -> bool:
    """Unparsed location/timezone limits need confirmation, not invented rights."""
    unrestricted = {"worldwide", "remote worldwide", "worldwide remote", "global", "anywhere"}
    return any(item.strip().casefold() not in unrestricted for item in restrictions if item.strip())


def _normalize_ternary(value: str | None) -> str:
    text = " ".join((value or "").strip().lower().replace("_", " ").split())
    if text in _UNKNOWN_VALUES:
        return "unknown"
    if text in _YES_VALUES:
        return "yes"
    if text in _NO_VALUES:
        return "no"
    if text.startswith("yes"):
        return "yes"
    if text.startswith("no"):
        return "no"
    return "unknown"


def _normalize_remote(value: str | None) -> str:
    text = " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())
    if text in _UNKNOWN_VALUES:
        return "unknown"
    if "worldwide" in text or "anywhere" in text or text == "global":
        return "worldwide"
    if "hybrid" in text:
        return "hybrid"
    if "onsite" in text or "on site" in text or "office" == text:
        return "onsite"
    if "country" in text or "region" in text:
        return "country_limited"
    if text == "remote":
        return "unknown"
    return "unknown"


def _normalize_work_auth(value: str | None) -> str:
    text = " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())
    if text in _UNKNOWN_VALUES:
        return "unknown"
    if text in {"required", "must", "yes"}:
        return "required"
    if text in {"not required", "not_required", "no"}:
        return "not_required"
    if "not required" in text:
        return "not_required"
    if "required" in text:
        return "required"
    return "unknown"


def _scan_ternary(text: str, yes_markers: tuple[str, ...], no_markers: tuple[str, ...]) -> str:
    if any(marker in text for marker in no_markers):
        return "no"
    if any(marker in text for marker in yes_markers):
        return "yes"
    return "unknown"


def _merge_ternary(extracted: str, scanned: str) -> str:
    extracted_norm = _normalize_ternary(extracted)
    if scanned == "no" or extracted_norm == "no":
        return "no"
    if extracted_norm == "yes" or scanned == "yes":
        return "yes"
    return "unknown"


def _merge_remote(extracted: str, text: str) -> str:
    extracted_norm = _normalize_remote(extracted)
    if _GEOGRAPHIC_RESTRICTIONS.search(text):
        return "country_limited"
    if extracted_norm in {"country_limited", "hybrid", "onsite"}:
        return extracted_norm
    if extracted_norm == "worldwide" or any(marker in text for marker in _REMOTE_WORLDWIDE_MARKERS):
        return "worldwide"
    if extracted_norm != "unknown":
        return extracted_norm
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in ("on-site", "onsite", "on site", "in-office")):
        return "onsite"
    return "unknown"


def _merge_work_auth(extracted: str, text: str) -> str:
    extracted_norm = _normalize_work_auth(extracted)
    scanned = "required" if any(marker in text for marker in _WORK_AUTH_REQUIRED_MARKERS) else "unknown"
    if extracted_norm == "required" or scanned == "required":
        return "required"
    if extracted_norm == "not_required":
        return "not_required"
    return "unknown"
