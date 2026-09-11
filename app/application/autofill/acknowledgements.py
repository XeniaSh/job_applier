from __future__ import annotations

from enum import StrEnum

from app.application.autofill.fields import DiscoveredField


class AcknowledgementClass(StrEnum):
    """Semantic purpose of a consent / acknowledgement control."""

    APPLICATION_PRIVACY = "application_privacy"
    MARKETING = "marketing"
    UNSAFE_LEGAL = "unsafe_legal"
    OTHER = "other"


_MARKETING_TERMS = (
    "newsletter",
    "email me about",
    "other job openings",
    "marketing email",
    "marketing communication",
    "promotional",
    "job alert",
    "job alerts",
    "talent pool",
    "future opportunities",
    "future job",
    "third-party marketing",
    "unrelated third-party",
    "keep my cv",
    "keep my resume",
    "retain my data",
    "retain my information",
    "retain my profile",
    "keep my data on file",
)

_UNSAFE_LEGAL_TERMS = (
    "criminal",
    "conviction",
    "felony",
    "misdemeanor",
    "background check",
    "background investigation",
    "credit check",
    "drug test",
    "export control",
    "sanction",
    "denied party",
    "embargo",
    "i certify",
    "i attest",
    "under penalty",
    "true and complete",
    "information is accurate",
    "information provided is true",
    "legally binding",
)

_STRONG_PRIVACY_CUES = (
    "privacy notice",
    "privacy policy",
    "applicant privacy",
    "data processing",
    "personal data",
    "gdpr",
    "consent to process",
    "process my data",
    "processing of my personal data",
    "processing my application data",
    "data transfer",
    "point of data transfer",
    "for considering the application",
    "for considering this application",
    "held and used for considering",
    "held/used for considering",
    "application data",
)


def classify_acknowledgement(text: str) -> AcknowledgementClass:
    """Classify a control from its label, option text, and nearby section copy."""
    haystack = " ".join((text or "").lower().split())
    if not haystack:
        return AcknowledgementClass.OTHER
    if _is_unsafe_legal(haystack):
        return AcknowledgementClass.UNSAFE_LEGAL
    if _is_marketing(haystack):
        return AcknowledgementClass.MARKETING
    if _is_application_privacy(haystack):
        return AcknowledgementClass.APPLICATION_PRIVACY
    return AcknowledgementClass.OTHER


def is_safe_required_privacy_acknowledgement(field: DiscoveredField, text: str) -> bool:
    """True only for required application/recruitment privacy acknowledgements."""
    if not field.required:
        return False
    return classify_acknowledgement(text) is AcknowledgementClass.APPLICATION_PRIVACY


def _is_marketing(text: str) -> bool:
    return any(term in text for term in _MARKETING_TERMS)


def _is_unsafe_legal(text: str) -> bool:
    return any(term in text for term in _UNSAFE_LEGAL_TERMS)


def _is_application_privacy(text: str) -> bool:
    if any(term in text for term in _STRONG_PRIVACY_CUES):
        return True
    if "recruitment purposes" in text and any(
        term in text for term in ("consent", "process", "personal data", "acknowledge")
    ):
        return True
    if "privacy" in text and any(
        term in text for term in ("acknowledge", "consent", "notice", "policy", "process")
    ):
        return True
    return False
