"""Browser-free coverage for the Lever-only diagnostic stage markers added to
`app.application.autofill.service`: classification completion, cover-letter
generation start/end, per-field enrichment start/end, and fill-vs-readback
start/completion.

A real stalled run was observed to end its log right after the second
successful `stage=form_recognition` warning with nothing after it -- these
markers (plus `discover_fields`'s own, covered separately in
`test_lever_discovery_diagnostics.py`) close that gap for every later stage.
They are gated on `isinstance(adapter, LeverAdapter)`, so Greenhouse/Ashby
runs through the exact same shared `AutofillService` code are proven
unaffected here too.

No real browser or Playwright Locator is touched: `LeverAdapter` is
subclassed with every DOM-facing method faked, the same pattern
`test_lever_final_write_guard.py`'s `_SpyLeverAdapter` already uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillStatus
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

_LOGGER_NAME = "app.application.autofill.service"


class _FakeLeverAdapter(LeverAdapter):
    """Real `LeverAdapter` identity (so `isinstance(adapter, LeverAdapter)`
    gates the diagnostics under test on), every DOM-facing method faked.
    """

    def __init__(
        self,
        fields: list[DiscoveredField],
        *,
        read_back_value: str | None = None,
        upload_resume_results: list[bool] | None = None,
    ) -> None:
        self._fields = fields
        self._read_back_value = read_back_value
        self.fill_calls: list[str] = []
        self._upload_resume_results = list(upload_resume_results or [])

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
        return self._read_back_value

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        if self._upload_resume_results:
            return self._upload_resume_results.pop(0)
        return False


class _StallingFillLeverAdapter(LeverAdapter):
    """`fill_field` never returns in a real stall; a unit test can only
    express that as an immediate raise. `read_back` asserts it is never
    reached, proving the stall is caught at the fill step itself.
    """

    def recognize(self, page: object) -> bool:
        return True

    def detect_challenge(self, page: object) -> str | None:
        return None

    def prepare_page(self, page: object) -> None:
        return None

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        return [_first_name_field()]

    def fill_field(self, page: object, classified: ClassifiedField) -> bool:
        raise TimeoutError("simulated stalled fill")

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        raise AssertionError("read_back must never be reached after a stalled fill_field")

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        return False


class _FakeNonLeverAdapter:
    """Not a `LeverAdapter` -- proves the new diagnostics never fire for a
    non-Lever adapter even though it is driven through the identical shared
    `AutofillService` code path (Greenhouse/Ashby behavior must be unchanged).
    """

    def __init__(self, fields: list[DiscoveredField]) -> None:
        self._fields = fields

    def recognize(self, page: object) -> bool:
        return True

    def detect_challenge(self, page: object) -> str | None:
        return None

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        return list(self._fields)

    def fill_field(self, page: object, classified: ClassifiedField) -> bool:
        return bool(classified.fill)

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        return "Ada"

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        return False


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


class _FakeCoverLetterProvider:
    def __init__(self, text: str | None) -> None:
        self._text = text
        self.last_error: str | None = None

    def generate(self, vacancy: ResolvedVacancy, profile: CandidateProfile) -> str | None:
        return self._text


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


def _first_name_field() -> DiscoveredField:
    return DiscoveredField(label="First Name", name="name", field_type="text", required=True)


def _cover_letter_field() -> DiscoveredField:
    return DiscoveredField(label="Cover Letter", name="coverLetter", field_type="textarea", required=False)


def _resume_field() -> DiscoveredField:
    return DiscoveredField(label="Resume", name="resume", field_type="file", required=True)


def _make_service(adapter: object, *, cover_letter_provider: object = None) -> tuple[AutofillService, _FakeSession]:
    session = _FakeSession()
    svc = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=adapter,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
        cover_letter_provider=cover_letter_provider,
    )
    return svc, session


def test_lever_run_logs_classification_and_enrichment_stage_markers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _FakeLeverAdapter([_first_name_field()])
    svc, _ = _make_service(adapter)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = svc.run("target_company:lever:acme", "1", keep_open=False)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    messages = [r.getMessage() for r in caplog.records]

    classify_log = next(m for m in messages if "stage=classification_complete" in m)
    assert "field_count=1" in classify_log

    enrich_start = next(m for m in messages if "stage=field_enrich_start" in m)
    enrich_end = next(m for m in messages if "stage=field_enrich_end" in m)
    assert "field_index=0" in enrich_start
    assert "kind=first_name" in enrich_start
    assert "control_type=text" in enrich_start
    assert "required=True" in enrich_start
    assert "field_index=0" in enrich_end
    assert "kind=first_name" in enrich_end
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    # Never the candidate's own resolved value.
    assert "Ada" not in " ".join(messages)


def test_lever_run_logs_distinct_field_index_per_discovered_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two fields of the same `kind`/`control_type` (here, two plain text
    fields) are otherwise indistinguishable in the enrichment markers alone
    -- `field_index` is the only thing that ties a marker back to a specific
    discovered field when a live posting has several ambiguous select/text
    controls in a row.
    """
    second_field = DiscoveredField(label="Middle Name", name="middleName", field_type="text", required=False)
    adapter = _FakeLeverAdapter([_first_name_field(), second_field])
    svc, _ = _make_service(adapter)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        svc.run("target_company:lever:acme", "1", keep_open=False)

    messages = [r.getMessage() for r in caplog.records]
    enrich_starts = [m for m in messages if "stage=field_enrich_start" in m]
    assert any("field_index=0" in m for m in enrich_starts)
    assert any("field_index=1" in m for m in enrich_starts)


def test_lever_run_logs_cover_letter_start_and_end_when_field_discovered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _FakeLeverAdapter([_cover_letter_field()])
    provider = _FakeCoverLetterProvider("Dear hiring team, I am thrilled to apply.")
    svc, _ = _make_service(adapter, cover_letter_provider=provider)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        svc.run("target_company:lever:acme", "1", keep_open=False)

    messages = [r.getMessage() for r in caplog.records]
    assert any(m == "lever_prepare stage=cover_letter_start" for m in messages)
    end_log = next(m for m in messages if "stage=cover_letter_end" in m)
    assert "outcome=generated" in end_log
    # Never the generated prose itself.
    assert not any("Dear hiring team" in m for m in messages)


def test_lever_run_logs_cover_letter_end_outcome_empty_without_provider_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _FakeLeverAdapter([_cover_letter_field()])
    provider = _FakeCoverLetterProvider(None)
    svc, _ = _make_service(adapter, cover_letter_provider=provider)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        svc.run("target_company:lever:acme", "1", keep_open=False)

    messages = [r.getMessage() for r in caplog.records]
    end_log = next(m for m in messages if "stage=cover_letter_end" in m)
    assert "outcome=empty" in end_log


def test_lever_run_logs_fill_and_readback_stage_markers(caplog: pytest.LogCaptureFixture) -> None:
    adapter = _FakeLeverAdapter([_first_name_field()], read_back_value="Ada")
    svc, _ = _make_service(adapter)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = svc.run("target_company:lever:acme", "1", keep_open=False)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == "First Name" for item in result.filled_fields)
    messages = [r.getMessage() for r in caplog.records]

    fill_start = next(m for m in messages if "stage=fill_start" in m)
    readback_start = next(m for m in messages if "stage=readback_start" in m)
    complete = next(m for m in messages if "stage=fill_readback_complete" in m)
    assert "attempt=1" in fill_start and "field_index=0" in fill_start and "kind=first_name" in fill_start
    assert "field_index=0" in readback_start and "fill_ok=True" in readback_start
    assert "field_index=0" in complete
    assert "fill_ok=True" in complete and "readback_present=True" in complete
    # Never the candidate's filled/read-back value.
    assert "Ada" not in " ".join(messages)


def test_lever_fill_start_marker_survives_a_stalled_fill_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `fill_field` call that never returns (simulated here as an
    immediate raise, since a real hang cannot be expressed in a unit test)
    must still have logged its `stage=fill_start` marker beforehand -- that
    marker is exactly the last diagnostic line a genuinely stuck live run
    would leave behind. `stage=readback_start`/`fill_readback_complete` must
    never appear, since `read_back` is never reached.
    """
    adapter = _StallingFillLeverAdapter()
    svc, _ = _make_service(adapter)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = svc.run("target_company:lever:acme", "1", keep_open=False)

    assert result.status is AutofillStatus.FAILED
    messages = [r.getMessage() for r in caplog.records]
    assert any("stage=fill_start" in m and "field_index=0" in m for m in messages)
    assert not any("stage=readback_start" in m for m in messages)
    assert not any("stage=fill_readback_complete" in m for m in messages)


def test_lever_run_logs_resume_upload_attempt_markers_on_retry(caplog: pytest.LogCaptureFixture) -> None:
    """A real run hit an isolated `TargetClosed` somewhere in the resume
    upload retry loop with no later log line to show which attempt it
    happened on. These markers bracket each `adapter.upload_resume` call so a
    live recurrence leaves behind which attempt it was on and how long it
    took, without changing the existing retry-once-on-failure behavior.
    """
    adapter = _FakeLeverAdapter([_resume_field()], upload_resume_results=[False, True])
    svc, _ = _make_service(adapter)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = svc.run("target_company:lever:acme", "1", keep_open=False)

    assert result.resume_uploaded is True
    messages = [r.getMessage() for r in caplog.records]
    start_logs = [m for m in messages if "stage=resume_upload_start" in m]
    end_logs = [m for m in messages if "stage=resume_upload_end" in m]
    assert any("attempt=1" in m for m in start_logs)
    assert any("attempt=2" in m for m in start_logs)
    attempt1_end = next(m for m in end_logs if "attempt=1" in m)
    attempt2_end = next(m for m in end_logs if "attempt=2" in m)
    assert "result=False" in attempt1_end and "elapsed_ms=" in attempt1_end
    assert "result=True" in attempt2_end and "elapsed_ms=" in attempt2_end


def test_non_lever_adapter_never_logs_lever_stage_markers(caplog: pytest.LogCaptureFixture) -> None:
    adapter = _FakeNonLeverAdapter([_first_name_field(), _cover_letter_field()])
    provider = _FakeCoverLetterProvider("Some generated cover letter text.")
    svc, _ = _make_service(adapter, cover_letter_provider=provider)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = svc.run("target_company:greenhouse:acme", "1", keep_open=False)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    messages = [r.getMessage() for r in caplog.records]
    assert not any(m.startswith("lever_prepare") for m in messages)
