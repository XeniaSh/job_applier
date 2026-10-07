"""Browser-free `AutofillService.run` regression for the `opportunityLocationId`
control (Lever's own "Which location are you applying for?" select, see
`app.application.autofill.lever._matched_opportunity_location_override`).

With no explicit `application_policy.question_overrides` answer,
`LeverAdapter.adjust_classified_field` (the real, unfaked method here) forces
this field to `UNSUPPORTED` before `AutofillService._fill_open_page` ever
reaches its fill branch. This proves that end-to-end through the real
`AutofillService.run`: the fake adapter's `fill_field` is never invoked for
this field (so a live run attempts neither a native `select_option` nor the
two-attempt fill/read-back retry that would otherwise wait on Playwright), a
later, ordinary supported text field is still filled normally, the run still
reaches `READY_FOR_REVIEW`, the location field surfaces in
`unsupported_fields` for manual review, and no submit is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

_LOCATION_OPTIONS = [
    "Select...",
    "Taiwan, Taipei",
    "Japan, Tokyo",
]


def _location_field() -> DiscoveredField:
    return DiscoveredField(
        label="Which location are you applying for?",
        name="opportunityLocationId",
        element_id="opportunityLocationId",
        field_type="select",
        options=_LOCATION_OPTIONS,
        required=False,
    )


def _first_name_field() -> DiscoveredField:
    return DiscoveredField(label="First Name", name="name", field_type="text", required=True)


class _FakeLeverAdapter(LeverAdapter):
    """Real `LeverAdapter` identity, so the inherited, unfaked
    `adjust_classified_field` runs for real; every DOM-facing method is
    faked, the same pattern `test_service_lever_stage_diagnostics.py` uses.
    """

    def __init__(self, fields: list[DiscoveredField]) -> None:
        self._fields = fields
        self.fill_calls: list[str | None] = []

    def recognize(self, page: object) -> bool:
        return True

    def detect_challenge(self, page: object) -> str | None:
        return None

    def prepare_page(self, page: object) -> None:
        return None

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        return list(self._fields)

    def fill_field(self, page: object, classified: ClassifiedField) -> bool:
        self.fill_calls.append(classified.field.name or classified.field.label)
        return bool(classified.fill)

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        return "Ada"


@dataclass
class _FakeSession:
    keep_open: bool = False
    closed: bool = False
    page: object = None

    def __post_init__(self) -> None:
        if self.page is None:
            self.page = object()

    def open(self, url: str, *, is_usable: object = None) -> object:
        _ = url, is_usable
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeResolver:
    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = "https://jobs.lever.co/acme/1"
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Acme",
            url=url,
            application_url=url,
        )


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "Berlin, Germany",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def test_opportunity_location_without_override_is_skipped_while_later_field_fills() -> None:
    adapter = _FakeLeverAdapter([_location_field(), _first_name_field()])
    session = _FakeSession()
    ready: list[object] = []
    svc = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=adapter,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
        on_ready=ready.append,
    )

    result = svc.run("target_company:lever:acme", "1", keep_open=True)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert ready == [result]

    # The unsupported location control is never handed to `fill_field` --
    # no native `select_option` attempt, no fill/read-back retry wait.
    assert "opportunityLocationId" not in adapter.fill_calls
    assert adapter.fill_calls == ["name"]

    assert any(item.name == "opportunityLocationId" for item in result.unsupported_fields)
    assert any(item.label == "First Name" for item in result.filled_fields)
