from __future__ import annotations

from dataclasses import dataclass

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy, VacancyResolveError
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


@dataclass
class _FakeSession:
    keep_open: bool = False
    closed: bool = False
    opened_url: str | None = None
    page: object = object()

    def open(self, url: str) -> object:
        self.opened_url = url
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
            url="https://job-boards.greenhouse.io/agoda/jobs/1",
            application_url="https://job-boards.greenhouse.io/agoda/jobs/1",
        )


class _FakeAdapter:
    submit_called = False

    def detect_challenge(self, page: object) -> str | None:
        _ = page
        return None

    def recognize(self, page: object) -> bool:
        _ = page
        return True

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(label="What is your favorite IDE?", name="favorite_ide", required=True),
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        return bool(getattr(classified, "fill", False))

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        _ = page, resume_path, field
        return False

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        return None


class _UnsupportedResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        raise VacancyResolveError(f"Unsupported vacancy source: {source}")


def test_service_fills_known_fields_and_never_submits() -> None:
    session = _FakeSession()
    waited: list[bool] = []
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: waited.append(True),
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert any(item.label == "First Name" for item in result.filled_fields)
    assert any("favorite IDE" in item.label for item in result.unresolved_required_fields)
    assert waited == [True]
    assert session.closed is True


def test_service_calls_on_ready_before_keep_open_wait() -> None:
    session = _FakeSession()
    order: list[str] = []
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: order.append("wait"),
        on_ready=lambda result: order.append(f"ready:{result.status.value}"),
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert order == ["ready:READY_FOR_REVIEW", "wait"]


def test_service_keep_open_false_closes_without_wait() -> None:
    session = _FakeSession()
    waited: list[bool] = []
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: waited.append(True),
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert waited == []
    assert session.closed is True


def test_service_returns_failed_for_unsupported_source() -> None:
    service = AutofillService(
        resolver=_UnsupportedResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=_FakeSession,
        wait_for_review=lambda: None,
    )
    result = service.run("linkedin-email", "99", keep_open=False)
    assert result.status is AutofillStatus.FAILED
    assert result.submit_performed is False
    assert any("Unsupported vacancy source" in item for item in result.warnings)
