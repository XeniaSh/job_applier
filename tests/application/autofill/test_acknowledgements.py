from __future__ import annotations

from app.application.autofill.acknowledgements import (
    AcknowledgementClass,
    classify_acknowledgement,
    is_safe_required_privacy_acknowledgement,
)
from app.application.autofill.fields import DiscoveredField


def test_required_privacy_notice_is_application_privacy() -> None:
    text = "I have read and acknowledge the Applicant Privacy Notice"
    assert classify_acknowledgement(text) is AcknowledgementClass.APPLICATION_PRIVACY
    field = DiscoveredField(label=text, field_type="checkbox", required=True)
    assert is_safe_required_privacy_acknowledgement(field, text) is True


def test_point_of_data_transfer_acknowledge_is_application_privacy() -> None:
    text = (
        "Point of Data Transfer Acknowledge/Confirm Information submitted during "
        "the application will be held and used for considering the application "
        "and handled according to the Applicant Privacy Notice."
    )
    assert classify_acknowledgement(text) is AcknowledgementClass.APPLICATION_PRIVACY
    field = DiscoveredField(
        label="Acknowledge/Confirm",
        field_type="checkbox",
        required=True,
        context=text,
    )
    combined = f"{field.label} {field.context}"
    assert is_safe_required_privacy_acknowledgement(field, combined) is True


def test_acknowledge_confirm_alone_is_not_privacy() -> None:
    assert classify_acknowledgement("Acknowledge/Confirm") is AcknowledgementClass.OTHER
    field = DiscoveredField(label="Acknowledge/Confirm", field_type="checkbox", required=True)
    assert is_safe_required_privacy_acknowledgement(field, "acknowledge/confirm") is False


def test_newsletter_is_marketing_not_privacy() -> None:
    text = "Subscribe to newsletter and other job openings"
    assert classify_acknowledgement(text) is AcknowledgementClass.MARKETING
    field = DiscoveredField(label=text, field_type="checkbox", required=False)
    assert is_safe_required_privacy_acknowledgement(field, text) is False


def test_talent_pool_is_marketing_not_privacy() -> None:
    text = "Keep my profile in the talent pool for future opportunities"
    assert classify_acknowledgement(text) is AcknowledgementClass.MARKETING


def test_criminal_declaration_is_unsafe_legal() -> None:
    text = "I declare that I have no criminal convictions"
    assert classify_acknowledgement(text) is AcknowledgementClass.UNSAFE_LEGAL
    field = DiscoveredField(label=text, field_type="checkbox", required=True)
    assert is_safe_required_privacy_acknowledgement(field, text) is False


def test_true_and_complete_certification_is_unsafe_legal() -> None:
    text = "I certify that the information provided is true and complete"
    assert classify_acknowledgement(text) is AcknowledgementClass.UNSAFE_LEGAL


def test_optional_privacy_is_classified_but_not_safe_required() -> None:
    text = "I consent to the processing of my personal data for recruiting purposes"
    assert classify_acknowledgement(text) is AcknowledgementClass.APPLICATION_PRIVACY
    field = DiscoveredField(label=text, field_type="checkbox", required=False)
    assert is_safe_required_privacy_acknowledgement(field, text) is False
