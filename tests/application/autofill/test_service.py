from __future__ import annotations

from dataclasses import dataclass

from app.application.autofill.browser import BrowserSetupError
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import AutofillFailureReason, AutofillStatus
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
