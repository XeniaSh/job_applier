from __future__ import annotations

from app.company_watch.wise_eligibility import (
    CORRECTION_EXPLICIT_REFUSAL,
    CORRECTION_LOCAL_TRANSFER_ONLY,
    WISE_SMARTRECRUITERS_SOURCE,
    detect_wise_sponsorship_correction,
    is_wise_smartrecruiters_source,
)


def test_is_wise_smartrecruiters_source_matches_exact_source_only() -> None:
    assert is_wise_smartrecruiters_source(WISE_SMARTRECRUITERS_SOURCE) is True
    assert is_wise_smartrecruiters_source("target_company:smartrecruiters:canva") is False
    assert is_wise_smartrecruiters_source("target_company:ashby:wise") is False


def test_explicit_refusal_detected_for_real_wise_wording() -> None:
    text = "We are unable to support relocation or visa sponsorship for this role."
    correction = detect_wise_sponsorship_correction(text)
    assert correction is not None
    assert correction.category == CORRECTION_EXPLICIT_REFUSAL
    assert correction.visa_override == "no"
    assert correction.relocation_override == "no"


def test_local_transfer_only_detected_for_real_wise_wording() -> None:
    text = (
        "We are currently only considering local candidates as relocation support is not "
        "provided. For local candidates we are able to support transfer of visa sponsorship."
    )
    correction = detect_wise_sponsorship_correction(text)
    assert correction is not None
    assert correction.category == CORRECTION_LOCAL_TRANSFER_ONLY
    assert correction.visa_override == "unknown"
    assert correction.relocation_override == "no"
    assert "local status" in correction.warning or "local" in correction.warning


def test_visa_refusal_detected_with_relocation_refusal_bundled() -> None:
    text = "Please note we do not offer relocation support or VISA sponsorship for this role."
    correction = detect_wise_sponsorship_correction(text)
    assert correction is not None
    assert correction.category == CORRECTION_EXPLICIT_REFUSAL
    assert correction.visa_override == "no"
    assert correction.relocation_override == "no"


def test_visa_refusal_does_not_require_or_invent_relocation_refusal() -> None:
    correction = detect_wise_sponsorship_correction("We do not offer visa sponsorship for this role.")
    assert correction is not None
    assert correction.category == CORRECTION_EXPLICIT_REFUSAL
    assert correction.visa_override == "no"
    assert correction.relocation_override is None


def test_explicit_refusal_beats_local_only_wording() -> None:
    text = "We are only considering local candidates. We do not offer visa sponsorship."
    correction = detect_wise_sponsorship_correction(text)
    assert correction is not None
    assert correction.category == CORRECTION_EXPLICIT_REFUSAL


def test_conditional_warning_never_claims_general_sponsorship() -> None:
    text = (
        "We are currently only considering local candidates as relocation support is not "
        "provided. For local candidates we are able to support transfer of visa sponsorship."
    )
    correction = detect_wise_sponsorship_correction(text)
    assert correction is not None
    assert correction.visa_override == "unknown"
    assert "unknown" in correction.warning
    assert "not confirmed" in correction.warning
    assert "not offered" not in correction.warning


def test_visa_not_required_for_some_is_not_a_refusal() -> None:
    text = "Visa sponsorship is not required for EU citizens; we provide visa support for others."
    assert detect_wise_sponsorship_correction(text) is None


def test_negative_transfer_is_refusal_not_conditional() -> None:
    correction = detect_wise_sponsorship_correction("We do not support transfer of visa sponsorship.")
    assert correction is not None
    assert correction.category == CORRECTION_EXPLICIT_REFUSAL


def test_genuine_positive_wording_is_not_corrected() -> None:
    text = "Hybrid working in Tallinn, Estonia (with relocation and visa sponsorship support, if needed)."
    assert detect_wise_sponsorship_correction(text) is None


def test_plain_unrelated_text_is_not_corrected() -> None:
    assert detect_wise_sponsorship_correction("Build backend services in Java and Kotlin.") is None
