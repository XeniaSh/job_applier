from __future__ import annotations

import re
from dataclasses import dataclass

# Only the one approved Wise SmartRecruiters discovery source. This module
# must never widen itself to any other `target_company:smartrecruiters:*`
# board without an explicit separate approval.
WISE_SMARTRECRUITERS_SOURCE = "target_company:smartrecruiters:wise"

WISE_MANUAL_EMPLOYER_SITE_INFO = (
    "Apply manually on the employer's SmartRecruiters page (no autofill for Wise)"
)

WISE_UNKNOWN_SPONSORSHIP_WARNING = (
    "Wise: visa sponsorship not confirmed in the posting - check manually before applying"
)

CORRECTION_EXPLICIT_REFUSAL = "explicit_refusal"
CORRECTION_LOCAL_TRANSFER_ONLY = "local_transfer_only"

# `assess_application_feasibility`'s shared substring scan treats any text
# containing "visa sponsorship" or "relocation support" as a positive
# signal. That is wrong for two real Wise phrasings: an explicit refusal
# ("we do not offer relocation support or visa sponsorship") and a
# local-only / existing-visa-transfer qualifier ("only considering local
# candidates ... transfer of visa sponsorship"). Both contain the generic
# yes-markers even though neither is a general sponsorship offer. These
# patterns exist only to detect those narrow cases for Wise and must never
# be treated as evidence of a general positive signal.
_NEGATION = r"(?:do(?:es)? not|don't|cannot|can't|will not|won't|unable to|not able to)"
_NO_RELOCATION_RE = re.compile(
    r"relocation(?: support)? is not provided"
    r"|no relocation support"
    rf"|{_NEGATION} (?:provide|support|offer) relocation"
)
# Gap words between a denial and "visa sponsorship" must not cross a clause
# boundary ("... but we provide visa sponsorship").
_GAP_WORD = r"(?!but\b|however\b|while\b|although\b)[\w-]+"
_NO_VISA_RE = re.compile(
    # A visa refusal stands on its own: it may be bundled with a relocation
    # refusal ("... relocation support or visa sponsorship") but never needs one.
    # Only denials of the sponsorship being available/provided/offered/supported
    # count: "visa sponsorship is not required" is not a refusal.
    rf"{_NEGATION} (?:provide|support|offer|sponsor|give)(?: {_GAP_WORD}){{0,4}}? visa sponsorship"
    rf"|\bno (?!need\b|requirement)(?:{_GAP_WORD} ){{0,3}}?visa sponsorship"
    rf"|not offering (?:{_GAP_WORD} ){{0,3}}?visa sponsorship"
    r"|visa sponsorship (?:is|are) (?:unavailable|not (?:available|provided|offered|supported))"
    rf"|{_NEGATION} sponsor"
)
_VISA_TRANSFER_ONLY_RE = re.compile(
    r"transfer (?:of |an )?(?:existing )?visa sponsorship"
    r"|support transfer of visa"
)
_LOCAL_ONLY_RE = re.compile(r"only considering local candidates|local candidates only")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class WiseSponsorshipCorrection:
    category: str
    visa_override: str | None
    relocation_override: str | None
    warning: str


def is_wise_smartrecruiters_source(source: str) -> bool:
    return source == WISE_SMARTRECRUITERS_SOURCE


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def detect_wise_sponsorship_correction(vacancy_text: str) -> WiseSponsorshipCorrection | None:
    """Wise-only correction for the shared substring-scan false positive.

    Returns None when neither narrow pattern is present, so normal shared
    feasibility/recommendation behavior is untouched (including genuinely
    positive wording such as "relocation and visa sponsorship support").
    """
    normalized = _normalize(vacancy_text)
    no_relocation = bool(_NO_RELOCATION_RE.search(normalized))
    no_visa = bool(_NO_VISA_RE.search(normalized))
    # Only an affirmative transfer offer is a conditional exception; "we do not
    # support transfer of visa sponsorship" is a denial, judged per sentence.
    transfer_only = any(
        _VISA_TRANSFER_ONLY_RE.search(sentence) and not _NO_VISA_RE.search(sentence)
        for sentence in _SENTENCE_SPLIT_RE.split(normalized)
    )
    local_only = bool(_LOCAL_ONLY_RE.search(normalized))

    if transfer_only or (local_only and not no_visa):
        # Conditional on being local and/or already holding a visa to
        # transfer -- never a general sponsorship offer. Candidate's own
        # local status/authorization is unknown here, so this condition alone
        # requires a manual check. Independent eligibility blockers still SKIP.
        # An explicit refusal beats mere local-only wording (handled below).
        return WiseSponsorshipCorrection(
            category=CORRECTION_LOCAL_TRANSFER_ONLY,
            visa_override="unknown",
            relocation_override="no" if no_relocation else None,
            warning=(
                "Wise: sponsorship is conditional (local candidates / visa transfer only) and "
                "not confirmed for you; candidate local status/authorization unknown "
                "- check manually"
            ),
        )
    if no_visa:
        return WiseSponsorshipCorrection(
            category=CORRECTION_EXPLICIT_REFUSAL,
            visa_override="no",
            relocation_override="no" if no_relocation else None,
            warning="Wise: posting explicitly states visa sponsorship is not offered",
        )
    return None
