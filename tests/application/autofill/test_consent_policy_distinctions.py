"""Confirms the three consent shapes are never conflated:

- Applicant/recruitment privacy acknowledgement: auto-acknowledgeable when
  required (`ApplicationPolicy.privacy_acknowledgement.auto_acknowledge_required`).
- AI Responsible Use Policy acknowledgement: always manual -- never an
  application/recruitment privacy notice, so it must never be classified as
  `PRIVACY_CONSENT` and auto-filled.
- Demographic survey processing consent: always manual unless an explicit
  exact preference authorizes it -- never inferred from the generic privacy
  acknowledgement policy.
"""

from __future__ import annotations

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import FieldClassification
from app.application.autofill.questions import QuestionKind, map_question
from app.application.candidate_profile import CandidateProfile


def _profile(**overrides: object) -> CandidateProfile:
    payload: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
    }
    payload.update(overrides)
    return CandidateProfile.model_validate(payload)


def test_required_privacy_acknowledgement_still_auto_acknowledges() -> None:
    """Sanity check that the ordinary privacy-notice path is untouched."""
    field = DiscoveredField(
        label="I have read and acknowledge the Applicant Privacy Notice",
        field_type="checkbox",
        required=True,
    )
    mapped = map_question(field, _profile())
    assert mapped.kind is QuestionKind.PRIVACY_CONSENT
    assert mapped.fillable is True
    assert mapped.value is True


def test_ai_responsible_use_policy_stays_manual_even_when_required() -> None:
    field = DiscoveredField(
        label="I acknowledge the AI Responsible Use Policy",
        field_type="checkbox",
        required=True,
        context=(
            "By checking this box, you acknowledge that this company may use "
            "artificial intelligence tools as part of the recruiting process, "
            "as described in our AI Responsible Use Policy."
        ),
    )
    mapped = map_question(field, _profile())
    assert mapped.kind is not QuestionKind.PRIVACY_CONSENT
    assert mapped.fillable is False

    classified = classify_field(field, _profile())
    assert classified.kind is not QuestionKind.PRIVACY_CONSENT
    assert classified.fill is False
    assert classified.classification is FieldClassification.UNKNOWN_REQUIRED


def test_demographic_survey_consent_stays_manual_without_explicit_preference() -> None:
    field = DiscoveredField(
        label="I consent to my demographic survey responses being processed for diversity reporting",
        field_type="checkbox",
        required=True,
    )
    mapped = map_question(field, _profile())
    assert mapped.kind is not QuestionKind.PRIVACY_CONSENT
    assert mapped.fillable is False

    classified = classify_field(field, _profile())
    assert classified.fill is False
    assert classified.classification is FieldClassification.UNKNOWN_REQUIRED

    # Even a permissive privacy-acknowledgement policy must not leak into
    # this distinct demographic-survey consent -- only an explicit,
    # narrowly-scoped preference could ever authorize it, and none exists.
    permissive = _profile(
        application_consent={"privacy_data_processing": True},
        application_policy={"privacy_acknowledgement": {"auto_acknowledge_required": True}},
    )
    still_manual = map_question(field, permissive)
    assert still_manual.fillable is False
