from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill.browser import BrowserSetupError
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.classifier import classify_field
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillFailureReason, AutofillStatus, FieldClassification
from app.application.autofill.resolver import ResolvedVacancy, VacancyResolveError
from app.application.autofill.service import AutofillService, _enrich_unresolved, default_adapter_for_source
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy


def test_anticipated_work_country_checkbox_uses_one_vacancy_country() -> None:
    field = DiscoveredField(
        label="United States",
        context="Please select the country or countries you anticipate working in for the role in which you are applying.",
        field_type="checkbox",
        required=True,
    )
    item = classify_field(field, _profile())
    vacancy = ResolvedVacancy(
        source="target_company:greenhouse:stripe",
        external_id="8035723",
        title="Backend Engineer",
        company="Stripe",
        url="https://job-boards.greenhouse.io/stripe/jobs/8035723",
        application_url="https://job-boards.greenhouse.io/stripe/jobs/8035723",
        vacancy=NormalizedVacancy(
            source="target_company:greenhouse:stripe",
            external_id="8035723",
            title="Backend Engineer",
            company="Stripe",
            location="New York, United States",
            employment=None,
            description="Backend engineering",
            url="https://job-boards.greenhouse.io/stripe/jobs/8035723",
            published_at=None,
        ),
    )
    enriched = _enrich_unresolved(
        item,
        profile=_profile(),
        vacancy=vacancy,
        cover_letter_text=None,
        answer_generator=None,
    )
    assert enriched.kind.value == "anticipated_work_country"
    assert enriched.fill is True
    assert enriched.value is True


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


def _profile_with_kotlin_years() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "employment": {
                "professional_tech_stack": ["Kotlin"],
                "technology_years": [{"technology": "Kotlin", "years": 2}],
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
    assert result.failure_reason is AutofillFailureReason.VACANCY_RESOLVE_FAILED


class _UnsupportedFormAdapter(_FakeAdapter):
    def recognize(self, page: object) -> bool:
        _ = page
        return False


def test_service_unsupported_form_sets_failure_reason() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_UnsupportedFormAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.FAILED
    assert result.failure_reason is AutofillFailureReason.UNSUPPORTED_FORM
    assert "UNSUPPORTED_FORM" in result.warnings
    # An unsupported form is never left open for a nonexistent "review".
    assert session.closed is True


def test_service_unsupported_form_logs_safe_host_diagnostics(caplog) -> None:
    """A recognition failure must log enough to diagnose it against a live
    ATS page -- the application/current page hosts -- without ever logging
    the page body, candidate data, or raw HTML.
    """
    session = _FakeSession(page=SimpleNamespace(url="https://jobs.lever.co/qonto/some-redirect-target"))
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_UnsupportedFormAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    with caplog.at_level("WARNING"):
        service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if "UNSUPPORTED_FORM" in record.getMessage() and "page_host" in record.getMessage()
    ]
    assert len(diagnostics) == 1
    assert "job-boards.greenhouse.io" in diagnostics[0]
    assert "jobs.lever.co" in diagnostics[0]


def _raising_browser_factory():
    raise BrowserSetupError("Playwright browser binaries are missing. Install Chromium with: uv run playwright install chromium")


def test_service_browser_setup_failure_sets_failure_reason() -> None:
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=_raising_browser_factory,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.FAILED
    assert result.failure_reason is AutofillFailureReason.BROWSER_SETUP_FAILED


class _ChallengeAdapter(_FakeAdapter):
    def detect_challenge(self, page: object) -> str | None:
        _ = page
        return "captcha"


def test_service_security_challenge_needs_manual_intervention() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_ChallengeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.NEEDS_MANUAL_INTERVENTION
    assert result.failure_reason is None
    assert any("captcha" in warning for warning in result.warnings)
    # Manual intervention still keeps the browser open for the user.
    assert session.closed is True  # closed only after wait_for_review() returns (fake resolves immediately)


class _MismatchedYearsAdapter(_FakeAdapter):
    """fill_field reports success, but the years field never actually reads back."""

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(
                label="How many years of experience do you have with Kotlin?",
                name="kotlin_years",
                field_type="number",
                required=True,
            ),
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        return bool(getattr(classified, "fill", False))

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        if field.name == "kotlin_years":
            return "0"
        return None


def test_service_leaves_mismatched_years_readback_unresolved() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile_with_kotlin_years,
        adapter=_MismatchedYearsAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == "First Name" for item in result.filled_fields)
    assert any("kotlin" in item.label.lower() for item in result.unresolved_required_fields)
    assert any("read-back failed" in warning.lower() for warning in result.warnings)


class _SubstringMismatchedYearsAdapter(_MismatchedYearsAdapter):
    """Actual value "12" numerically contains expected "2" as a substring but must not match."""

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        if field.name == "kotlin_years":
            return "12"
        return None


def test_service_rejects_substring_matching_years_readback() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile_with_kotlin_years,
        adapter=_SubstringMismatchedYearsAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any("kotlin" in item.label.lower() for item in result.unresolved_required_fields)
    assert any("read-back failed" in warning.lower() for warning in result.warnings)


class _NonNumericSuffixYearsAdapter(_MismatchedYearsAdapter):
    """Actual value "12 years" fails numeric parsing but contains expected "2" as a substring."""

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        if field.name == "kotlin_years":
            return "12 years"
        return None


def test_service_rejects_nonnumeric_years_readback_on_native_input() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile_with_kotlin_years,
        adapter=_NonNumericSuffixYearsAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any("kotlin" in item.label.lower() for item in result.unresolved_required_fields)
    assert any("read-back failed" in warning.lower() for warning in result.warnings)


class _MatchedYearsAdapter(_MismatchedYearsAdapter):
    """Same shape as the mismatch case, but the years field reads back correctly."""

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        if field.name == "kotlin_years":
            return "2"
        return None


def test_service_accepts_matching_years_readback_with_no_warning() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile_with_kotlin_years,
        adapter=_MatchedYearsAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any("kotlin" in item.label.lower() for item in result.filled_fields)
    assert all("kotlin" not in item.label.lower() for item in result.unresolved_required_fields)
    assert not any("read-back failed" in warning.lower() for warning in result.warnings)


def test_default_adapter_for_source_is_source_aware() -> None:
    from app.application.autofill.greenhouse import GreenhouseAdapter
    from app.application.autofill.lever import LeverAdapter
    from app.application.autofill.service import default_adapter_for_source

    assert isinstance(default_adapter_for_source("target_company:greenhouse:agoda"), GreenhouseAdapter)
    assert isinstance(default_adapter_for_source("target_company:lever:qonto"), LeverAdapter)
    # Unknown sources keep the historical Greenhouse default.
    assert isinstance(default_adapter_for_source("linkedin-email"), GreenhouseAdapter)


class _SourceAwareFakeResolver:
    """Echoes back whatever source it is asked to resolve, like the real resolvers do."""

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Example",
            url="https://example.test/apply",
            application_url="https://example.test/apply",
        )


def test_service_picks_adapter_by_resolved_source_when_none_given_explicitly() -> None:
    session = _FakeSession()
    seen: list[str] = []

    def _adapter_for_source(source: str) -> _FakeAdapter:
        seen.append(source)
        return _FakeAdapter()

    service = AutofillService(
        resolver=_SourceAwareFakeResolver(),
        profile_loader=_profile,
        adapter_for_source=_adapter_for_source,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:lever:qonto", "abc123", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert seen == ["target_company:lever:qonto"]


class _FakeSubmitAdapter:
    def __init__(self, *, submit_ok: bool = True, confirmed: bool = True) -> None:
        self.submit_ok = submit_ok
        self.confirmed = confirmed
        self.submit_calls = 0
        self.confirm_calls = 0

    def submit(self, page: object) -> bool:
        _ = page
        self.submit_calls += 1
        return self.submit_ok

    def submit_confirmed(self, page: object) -> bool:
        _ = page
        self.confirm_calls += 1
        return self.confirmed


class _AutoSubmitSafeAdapter(_FakeAdapter):
    """Only discovers a field that fills and reads back cleanly, so the policy is AUTO_SUBMIT_SAFE."""

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [DiscoveredField(label="First Name", name="first_name", required=True)]


def test_service_auto_submit_disabled_by_default_never_calls_submit_adapter() -> None:
    session = _FakeSession()
    submit_adapter = _FakeSubmitAdapter()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_AutoSubmitSafeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
        submit_adapter=submit_adapter,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert submit_adapter.submit_calls == 0


def test_service_auto_submit_enabled_and_policy_safe_performs_mocked_submit() -> None:
    session = _FakeSession()
    submit_adapter = _FakeSubmitAdapter()
    waited: list[bool] = []
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_AutoSubmitSafeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: waited.append(True),
        auto_submit_enabled=True,
        submit_adapter=submit_adapter,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=True)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is True
    assert submit_adapter.submit_calls == 1
    assert submit_adapter.confirm_calls == 1
    # The browser handoff still happens after a successful auto-submit, in
    # case a post-submit challenge (e.g. email verification) needs a human.
    assert waited == [True]
    assert session.closed is True


def test_service_auto_submit_enabled_but_blocked_by_unresolved_required_field() -> None:
    session = _FakeSession()
    submit_adapter = _FakeSubmitAdapter()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
        auto_submit_enabled=True,
        submit_adapter=submit_adapter,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any("favorite IDE" in item.label for item in result.unresolved_required_fields)
    assert result.submit_performed is False
    assert submit_adapter.submit_calls == 0


class _ChallengeBeforeSubmitAdapter(_AutoSubmitSafeAdapter):
    """Stage 1 detection sees no challenge, but one appears by the time submit is attempted."""

    def __init__(self) -> None:
        self.detect_calls = 0

    def detect_challenge(self, page: object) -> str | None:
        _ = page
        self.detect_calls += 1
        return None if self.detect_calls == 1 else "captcha"


def test_service_auto_submit_blocked_by_challenge_appearing_before_submit() -> None:
    session = _FakeSession()
    submit_adapter = _FakeSubmitAdapter()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_ChallengeBeforeSubmitAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
        auto_submit_enabled=True,
        submit_adapter=submit_adapter,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert submit_adapter.submit_calls == 0


def test_service_explicit_adapter_overrides_source_based_selection() -> None:
    session = _FakeSession()
    seen: list[str] = []

    def _adapter_for_source(source: str) -> _FakeAdapter:
        seen.append(source)
        return _FakeAdapter()

    service = AutofillService(
        resolver=_SourceAwareFakeResolver(),
        profile_loader=_profile,
        adapter=_FakeAdapter(),
        adapter_for_source=_adapter_for_source,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:lever:qonto", "abc123", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert seen == []


def test_default_adapter_for_source_selects_lever_and_logs_dispatch(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="app.application.autofill.service")
    adapter = default_adapter_for_source("target_company:lever:qonto")
    assert isinstance(adapter, LeverAdapter)
    dispatch_records = [
        record for record in caplog.records if record.getMessage().startswith("autofill_adapter_dispatch")
    ]
    assert len(dispatch_records) == 1
    assert dispatch_records[0].levelno == logging.WARNING
    message = dispatch_records[0].getMessage()
    assert "provider=lever" in message
    assert "adapter=LeverAdapter" in message
    assert "source=target_company:lever:qonto" in message


def test_default_adapter_for_source_selects_greenhouse_without_dispatch_log(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="app.application.autofill.service")
    adapter = default_adapter_for_source("target_company:greenhouse:agoda")
    assert isinstance(adapter, GreenhouseAdapter)
    assert not any(
        record.getMessage().startswith("autofill_adapter_dispatch") for record in caplog.records
    )


class _LeverFakeLocator:
    def count(self) -> int:
        return 0

    @property
    def first(self) -> "_LeverFakeLocator":
        return self

    def inner_text(self) -> str:
        return ""


class _LeverFakePage:
    """Minimal double for the same prepare_page -> recognize path the real
    Telegram-triggered runtime drives, without Chromium or network access.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    def locator(self, selector: str) -> _LeverFakeLocator:
        _ = selector
        return _LeverFakeLocator()

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        _ = selector, timeout
        raise PlaywrightTimeoutError("no application form present")

    def title(self) -> str:
        return ""


class _LeverJobDetailResolver:
    """Resolves to a Lever-hosted URL carrying a query string and fragment,
    like a real tracked-link application URL would.
    """

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = "https://jobs.lever.co/qonto/1234-secret-slug?utm_source=telegram#top"
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Qonto",
            url=url,
            application_url=url,
        )


def test_service_lever_source_dispatches_and_logs_unsupported_form_diagnostics(caplog) -> None:
    """Exercises the same source-aware dispatch -> LeverAdapter.prepare_page ->
    recognize path the real Telegram Prepare runtime uses, ending in the
    generic UNSUPPORTED_FORM failure. Confirms every diagnostic stage
    appears at warning level and never leaks the query string or fragment.
    """
    session = _FakeSession()
    session.page = _LeverFakePage(url="https://jobs.lever.co/qonto/1234-secret-slug?utm_source=telegram#top")
    service = AutofillService(
        resolver=_LeverJobDetailResolver(),
        profile_loader=_profile,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    with caplog.at_level(logging.WARNING):
        result = service.run("target_company:lever:qonto", "1234", keep_open=False)

    assert result.status is AutofillStatus.FAILED
    assert result.failure_reason is AutofillFailureReason.UNSUPPORTED_FORM

    messages = [r.getMessage() for r in caplog.records]
    dispatch_log = next(m for m in messages if m.startswith("autofill_adapter_dispatch"))
    assert "adapter=LeverAdapter" in dispatch_log
    detail_log = next(m for m in messages if "stage=detail_detection" in m)
    assert "is_job_detail=False" in detail_log
    form_recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=False" in form_recognition_log
    assert "reason=no_match" in form_recognition_log
    assert "jobs.lever.co" in detail_log
    assert "jobs.lever.co" in form_recognition_log
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    assert not any("utm_source" in m or "#top" in m for m in messages)


class _RequiredSensitiveAndUnsupportedAdapter(_FakeAdapter):
    """A required demographic field with no decline option, plus a required
    unsupported (signature) control -- neither is fillable, and both must
    still count toward unresolved_required_fields.
    """

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(label="Ethnicity", name="ethnicity", field_type="select", required=True),
            DiscoveredField(label="Sign here to certify", name="signature", field_type="signature", required=True),
        ]


def test_service_required_sensitive_and_unsupported_fields_count_as_unresolved_required() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_RequiredSensitiveAndUnsupportedAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == "Ethnicity" for item in result.sensitive_fields)
    assert any(item.label == "Ethnicity" for item in result.unresolved_required_fields)
    assert any("Sign here" in item.label for item in result.unsupported_fields)
    assert any("Sign here" in item.label for item in result.unresolved_required_fields)
    # No duplicate accounting: each field appears in unresolved_required once.
    labels = [item.label for item in result.unresolved_required_fields]
    assert labels.count("Ethnicity") == 1
    assert labels.count("Sign here to certify") == 1


class _OptionalSensitiveAndUnsupportedAdapter(_FakeAdapter):
    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(label="Ethnicity", name="ethnicity", field_type="select", required=False),
            DiscoveredField(label="Sign here to certify", name="signature", field_type="signature", required=False),
        ]


def test_service_optional_sensitive_and_unsupported_fields_are_not_unresolved_required() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=_OptionalSensitiveAndUnsupportedAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == "Ethnicity" for item in result.sensitive_fields)
    assert any("Sign here" in item.label for item in result.unsupported_fields)
    assert not any(item.label == "Ethnicity" for item in result.unresolved_required_fields)
    assert not any("Sign here" in item.label for item in result.unresolved_required_fields)


_NATIONALITY_LABEL = "Are you a national of the country where you are applying to work?"


class _NationalityFakeAdapter:
    """Records whatever value AutofillService fills and echoes it back on
    read-back, like a real select control's post-fill visible text would.
    """

    def __init__(self) -> None:
        self.filled: dict[str | None, object] = {}

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
                label=_NATIONALITY_LABEL,
                name="nationality",
                field_type="select",
                options=["Yes", "No"],
                required=True,
            )
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        if not getattr(classified, "fill", False):
            return False
        self.filled[classified.field.name] = classified.value
        return True

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        _ = page, resume_path, field
        return False

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        value = self.filled.get(field.name)
        return None if value is None else str(value)


class _NationalityResolver:
    """Resolves to a vacancy whose structured location is `location`, like
    the real Greenhouse/Lever resolvers' `NormalizedVacancy.location`.
    """

    def __init__(self, location: str | None) -> None:
        self._location = location

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        normalized = NormalizedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Wolt",
            location=self._location,
            employment=None,
            description="",
            url="https://example.test/apply",
            published_at=None,
        )
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Backend Engineer",
            company="Wolt",
            url="https://example.test/apply",
            application_url="https://example.test/apply",
            vacancy=normalized,
        )


def _profile_with_citizenship(citizenship: list[str]) -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "work_eligibility": {"citizenship": citizenship},
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def test_service_nationality_answers_yes_when_citizenship_matches_vacancy_country() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_citizenship(["Germany"]),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)
    filled_item = next(item for item in result.filled_fields if item.label == _NATIONALITY_LABEL)
    # A vacancy-aware deterministic enrichment must report as such, not as
    # the pre-enrichment UNKNOWN_REQUIRED classification it started from.
    assert filled_item.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_service_nationality_answers_no_when_citizenship_does_not_match_vacancy_country() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_citizenship(["Spain"]),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)


def test_service_nationality_stays_unresolved_when_vacancy_country_is_ambiguous() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany or Amsterdam, Netherlands"),
        profile_loader=lambda: _profile_with_citizenship(["Germany"]),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)


def test_service_nationality_stays_unresolved_when_citizenship_is_missing() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_citizenship([]),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)


def test_service_nationality_stays_unresolved_when_vacancy_location_is_none() -> None:
    """A vacancy with no structured location at all (not merely an ambiguous
    one) must fail closed the same way -- `_single_vacancy_work_country`
    returns None before `countries_mentioned` is even consulted.
    """
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver(None),
        profile_loader=lambda: _profile_with_citizenship(["Germany"]),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)


def _profile_with_citizenship_and_matching_residence(
    citizenship: list[str],
    *,
    current_location: str,
    country: str,
    work_authorized_country: str,
) -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": current_location,
                "country": country,
            },
            "work_eligibility": {
                "citizenship": citizenship,
                "work_authorizations": [{"country": work_authorized_country, "authorized": True}],
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def test_service_nationality_stays_unresolved_when_residence_and_work_authorization_match_but_citizenship_is_empty() -> None:
    """Residence and work authorization must never substitute for citizenship.
    Both explicitly point at Germany here, the vacancy's single work
    country, yet with citizenship left empty the nationality question must
    still stay unresolved instead of being answered from either fact.
    """
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_citizenship_and_matching_residence(
            [],
            current_location="Berlin, Germany",
            country="Germany",
            work_authorized_country="Germany",
        ),
        adapter=_NationalityFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _NATIONALITY_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _NATIONALITY_LABEL for item in result.filled_fields)


_WORK_AUTH_GENERIC_LABEL = "Are you legally authorized to work in the country for which you applied?"
_WORK_AUTH_GENERIC_ROLE_LOCATED_LABEL = (
    "Are you legally authorized to work in the country in which this role is located?"
)


class _WorkAuthFakeAdapter:
    """Records whatever value AutofillService fills and echoes it back on
    read-back, like a real select control's post-fill visible text would.
    """

    def __init__(self, label: str = _WORK_AUTH_GENERIC_LABEL) -> None:
        self.label = label
        self.filled: dict[str | None, object] = {}

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
                label=self.label,
                name="work_authorization",
                field_type="select",
                options=["Yes", "No"],
                required=True,
            )
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        if not getattr(classified, "fill", False):
            return False
        self.filled[classified.field.name] = classified.value
        return True

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        _ = page, resume_path, field
        return False

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        value = self.filled.get(field.name)
        return None if value is None else str(value)


def _profile_with_work_authorization(country: str | None, authorized: bool | None) -> CandidateProfile:
    work_eligibility: dict[str, object] = {}
    if country is not None and authorized is not None:
        work_eligibility["work_authorizations"] = [{"country": country, "authorized": authorized}]
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "work_eligibility": work_eligibility,
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


@pytest.mark.parametrize(
    "label",
    [_WORK_AUTH_GENERIC_LABEL, _WORK_AUTH_GENERIC_ROLE_LOCATED_LABEL],
)
def test_service_generic_work_authorization_answers_yes_for_single_vacancy_country(label: str) -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_work_authorization("Germany", True),
        adapter=_WorkAuthFakeAdapter(label),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == label for item in result.filled_fields)
    assert not any(item.label == label for item in result.unresolved_required_fields)
    filled_item = next(item for item in result.filled_fields if item.label == label)
    # Vacancy-aware deterministic enrichment must report as such, not as the
    # pre-enrichment UNKNOWN_REQUIRED classification it started from.
    assert filled_item.classification is FieldClassification.SUPPORTED_DETERMINISTIC


def test_service_generic_work_authorization_answers_no_when_explicit_false() -> None:
    session = _FakeSession()
    adapter = _WorkAuthFakeAdapter()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_work_authorization("Germany", False),
        adapter=adapter,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _WORK_AUTH_GENERIC_LABEL for item in result.filled_fields)
    assert adapter.filled["work_authorization"] == "No"


def test_service_generic_work_authorization_stays_unresolved_when_vacancy_country_is_ambiguous() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany or Amsterdam, Netherlands"),
        profile_loader=lambda: _profile_with_work_authorization("Germany", True),
        adapter=_WorkAuthFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _WORK_AUTH_GENERIC_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _WORK_AUTH_GENERIC_LABEL for item in result.filled_fields)


def test_service_generic_work_authorization_stays_unresolved_when_profile_fact_is_missing() -> None:
    session = _FakeSession()
    service = AutofillService(
        resolver=_NationalityResolver("Berlin, Germany"),
        profile_loader=lambda: _profile_with_work_authorization(None, None),
        adapter=_WorkAuthFakeAdapter(),
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:wolt", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == _WORK_AUTH_GENERIC_LABEL for item in result.unresolved_required_fields)
    assert not any(item.label == _WORK_AUTH_GENERIC_LABEL for item in result.filled_fields)


_ADYEN_VISA_RELOCATION_LABEL = (
    "Do you need visa and/or relocation support for this role? If yes, please, elaborate."
)


class _AdyenVisaRelocationAdapter(_FakeAdapter):
    def discover_fields(self, page: object) -> list[DiscoveredField]:
        _ = page
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(
                label=_ADYEN_VISA_RELOCATION_LABEL,
                name="visa_relocation",
                field_type="textarea",
                required=True,
            ),
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        _ = page
        if getattr(classified.field, "name", None) == "visa_relocation":
            raise AssertionError(
                "fill_field must not be called for the unresolved visa/relocation textarea"
            )
        return bool(getattr(classified, "fill", False))

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        _ = page
        if field.name == "first_name":
            return "Ada"
        return None


def test_service_adyen_combined_visa_relocation_textarea_stays_unresolved_required() -> None:
    """Exact regression for the Adyen-shaped combined visa/relocation
    elaboration textarea: discovered required, never filled, an empty
    read-back, and counted exactly once as unresolved-required -- never in
    filled_fields. Mirrors the adapter-level coverage in
    test_visually_required_elaboration_textarea_stays_unresolved_required_never_true.
    """
    adapter = _AdyenVisaRelocationAdapter()
    field = DiscoveredField(
        label=_ADYEN_VISA_RELOCATION_LABEL,
        name="visa_relocation",
        field_type="textarea",
        required=True,
    )
    assert field.required is True
    assert adapter.read_back(object(), field) is None

    session = _FakeSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile,
        adapter=adapter,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    result = service.run("target_company:greenhouse:agoda", "1", keep_open=False)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    unresolved_labels = [item.label for item in result.unresolved_required_fields]
    assert unresolved_labels.count(_ADYEN_VISA_RELOCATION_LABEL) == 1
    assert not any(item.label == _ADYEN_VISA_RELOCATION_LABEL for item in result.filled_fields)
