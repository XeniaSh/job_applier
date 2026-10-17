"""Service-level regression tests for the Lever required-resume reliability
fix: `AutofillService._fill_open_page` must downgrade its result to
`NEEDS_MANUAL_INTERVENTION` (never silently stay `READY_FOR_REVIEW`) when a
*required* resume control cannot be confirmed attached -- either because
both upload attempts failed outright, because a later, end-of-Prepare
re-check (`LeverAdapter.verify_resume_attached`, the optional hook only
Lever defines) finds the attachment is no longer there, or because the
control was discovered as a required resume field but never queued for
upload at all (e.g. `adjust_classified_field` or the label-mapping heuristic
decided it was unfillable/ambiguous).

These drive `AutofillService` directly with a fake, duck-typed adapter --
no Playwright, no real browser -- so they run even where Chromium cannot
launch in this sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillStatus, FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

_RESUME_PATH = "tests/fixtures/autofill/resume.txt"
_RESUME_FIELD_LABEL = "Resume/CV"


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": _RESUME_PATH},
        }
    )


@dataclass
class _FakeSession:
    keep_open: bool = False
    closed: bool = False
    page: object = object()

    def open(self, url: str, *, is_usable: object = None) -> object:
        _ = url, is_usable
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Example",
            url="https://jobs.lever.co/example/1",
            application_url="https://jobs.lever.co/example/1",
        )


class _FakeLeverResumeAdapter:
    """A single required resume file control. `upload_ok` controls both
    upload attempts in `AutofillService`'s own retry loop; `verify_results`
    is consumed one bool per `verify_resume_attached` call (clamped to the
    last entry), modeling the end-of-Prepare re-check. Omit
    `verify_resume_attached` entirely (see `_FakeLeverResumeAdapterNoVerifyHook`
    below) to model an adapter that never defines this optional hook.
    """

    def __init__(self, *, upload_ok: bool, verify_results: list[bool]) -> None:
        self.upload_ok = upload_ok
        self._verify_results = verify_results
        self.upload_resume_calls = 0
        self.verify_calls = 0

    def detect_challenge(self, page: object) -> str | None:
        _ = page
        return None

    def recognize(self, page: object) -> bool:
        _ = page
        return True

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(
                label=_RESUME_FIELD_LABEL,
                name="resume",
                field_type="file",
                required=True,
                element_id="resume-upload-input",
            )
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        return bool(getattr(classified, "fill", False))

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        _ = page, resume_path, field
        self.upload_resume_calls += 1
        return self.upload_ok

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page, field
        return None

    def verify_resume_attached(self, page: object, field: DiscoveredField, filename: str) -> bool:
        _ = page, field, filename
        self.verify_calls += 1
        index = min(self.verify_calls - 1, len(self._verify_results) - 1)
        return self._verify_results[index]


class _FakeLeverResumeAdapterNoVerifyHook(_FakeLeverResumeAdapter):
    """Same shape, but with `verify_resume_attached` removed entirely --
    models Greenhouse/Ashby, which never define this hook.
    """

    verify_resume_attached = None


def _run(adapter: object) -> object:
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=adapter,
        browser_factory=lambda: _FakeSession(),
        wait_for_review=lambda: None,
    )
    return service.run("target_company:lever:example", "1", keep_open=False)


def test_resume_confirmed_at_end_of_prepare_stays_ready_for_review() -> None:
    adapter = _FakeLeverResumeAdapter(upload_ok=True, verify_results=[True])
    result = _run(adapter)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.resume_uploaded is True
    assert any(item.label == _RESUME_FIELD_LABEL for item in result.filled_fields)
    assert not any(item.label == _RESUME_FIELD_LABEL for item in result.unresolved_required_fields)
    assert adapter.verify_calls == 1


def test_resume_disappearing_after_upload_triggers_needs_manual_intervention() -> None:
    """Upload succeeded and passed its own upload-time check, but the
    end-of-Prepare re-check finds the control empty again -- this must
    downgrade the whole result, not just leave a warning on an otherwise
    `READY_FOR_REVIEW` run.
    """
    adapter = _FakeLeverResumeAdapter(upload_ok=True, verify_results=[False])
    result = _run(adapter)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert not any(item.label == _RESUME_FIELD_LABEL for item in result.filled_fields)
    assert any(item.label == _RESUME_FIELD_LABEL for item in result.unresolved_required_fields)
    assert any("could not be confirmed" in warning for warning in result.warnings)
    # Keeping the browser open for manual review (not closing it outright)
    # is the whole point of NEEDS_MANUAL_INTERVENTION over FAILED.
    assert result.submit_performed is False


def test_resume_upload_failure_itself_triggers_needs_manual_intervention() -> None:
    """Both upload attempts failing outright (never even reaching the
    end-of-Prepare re-check) must still downgrade the result -- the
    end-of-Prepare hook is a second safety net, not the only path to
    `NEEDS_MANUAL_INTERVENTION`.
    """
    adapter = _FakeLeverResumeAdapter(upload_ok=False, verify_results=[True])
    result = _run(adapter)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert any(item.label == _RESUME_FIELD_LABEL for item in result.unresolved_required_fields)
    # Already recorded as failed by the upload loop itself, so the
    # end-of-Prepare re-check must not double-count it.
    assert adapter.verify_calls == 0
    assert adapter.upload_resume_calls == 2


def test_adapter_without_verify_hook_is_unaffected() -> None:
    """Greenhouse/Ashby never define `verify_resume_attached`; a
    successful upload for them must behave exactly as before this fix --
    `READY_FOR_REVIEW`, no end-of-Prepare re-check attempted at all.
    """
    adapter = _FakeLeverResumeAdapterNoVerifyHook(upload_ok=True, verify_results=[True])
    result = _run(adapter)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.resume_uploaded is True
    assert any(item.label == _RESUME_FIELD_LABEL for item in result.filled_fields)


class _FakeLeverResumeAdapterUnqueuedRequiredResume(LeverAdapter):
    """A real `LeverAdapter` subclass (so `isinstance(adapter, LeverAdapter)`
    holds, matching real Lever runs) whose `adjust_classified_field` -- a
    normal, optional adapter hook `AutofillService` already applies to every
    adapter -- models a required resume control the label/field mapping
    could not confidently resolve (ambiguous/unfillable), forcing it back to
    `fill=False` after `classify_field` had initially resolved it to
    `QuestionKind.RESUME`. Never calls into real Playwright: every hook
    `AutofillService` unconditionally invokes when present on the adapter
    (`prepare_page`, `detect_challenge`, `recognize`, `discover_fields`,
    `adjust_classified_field`) is overridden as a no-op/fake here, since the
    real `LeverAdapter` implementations of those call real Playwright `Page`
    methods this test's plain `object()` page stand-in does not have.
    `upload_resume`/`verify_resume_attached`/`read_back` are left inherited
    from the real `LeverAdapter` but must never actually be called in this
    test, since the field is never queued for upload at all.
    """

    def __init__(self) -> None:
        self.adjust_calls = 0

    def prepare_page(self, page: object) -> None:
        _ = page
        return None

    def detect_challenge(self, page: object) -> str | None:
        _ = page
        return None

    def recognize(self, page: object) -> bool:
        _ = page
        return True

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(
                label=_RESUME_FIELD_LABEL,
                name="resume",
                field_type="file",
                required=True,
                element_id="resume-upload-input",
            )
        ]

    def adjust_classified_field(
        self, item: ClassifiedField, *, profile: object, vacancy: object
    ) -> ClassifiedField:
        _ = profile, vacancy
        if item.kind is QuestionKind.RESUME and item.field.required:
            self.adjust_calls += 1
            return replace(
                item,
                fill=False,
                classification=FieldClassification.UNKNOWN_REQUIRED,
                unresolved_reason="ambiguous resume field mapping",
            )
        return item


def test_discovered_required_resume_field_left_unqueued_triggers_needs_manual_intervention() -> None:
    """A required Lever resume control that `classify_field` initially
    resolved to `QuestionKind.RESUME` but that a later adapter hook decided
    was unfillable/ambiguous (so it is never added to the upload queue, and
    `upload_resume` is never called for it) must still downgrade the result
    -- it must not be allowed to silently stay `READY_FOR_REVIEW` just
    because it never reached the upload-attempt loop at all.
    """
    adapter = _FakeLeverResumeAdapterUnqueuedRequiredResume()
    result = _run(adapter)
    assert adapter.adjust_calls == 1
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.resume_uploaded is False
    assert any(item.label == _RESUME_FIELD_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _RESUME_FIELD_LABEL for item in result.filled_fields)
    assert any(
        "could not be mapped to an uploadable resume" in warning for warning in result.warnings
    )
