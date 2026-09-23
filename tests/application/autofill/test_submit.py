from __future__ import annotations

from app.application.autofill.models import (
    AutofillFieldResult,
    AutofillResult,
    AutofillStatus,
    FieldClassification,
)
from app.application.autofill.submit import (
    AshbySubmitAdapter,
    GreenhouseSubmitAdapter,
    LeverSubmitAdapter,
    attempt_auto_submit,
    default_submit_adapter_for_source,
)

_BASE = dict(
    source="target_company:greenhouse:agoda",
    external_id="1",
    application_url="https://example.test/jobs/1",
)


def _safe_result(**overrides: object) -> AutofillResult:
    data: dict[str, object] = dict(_BASE, status=AutofillStatus.READY_FOR_REVIEW)
    data.update(overrides)
    return AutofillResult(**data)


class _FakeChallengeDetector:
    def __init__(self, challenge: str | None = None) -> None:
        self.challenge = challenge
        self.calls = 0

    def detect_challenge(self, page: object) -> str | None:
        _ = page
        self.calls += 1
        return self.challenge


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


def test_disabled_by_default_never_submits_even_if_policy_is_safe() -> None:
    result = _safe_result()
    detector = _FakeChallengeDetector()
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=False, challenge_detector=detector, submit_adapter=adapter, page=object()
    )

    assert out is result
    assert out.submit_performed is False
    assert adapter.submit_calls == 0
    assert adapter.confirm_calls == 0
    assert detector.calls == 0


def test_policy_needs_review_blocks_submit() -> None:
    result = _safe_result(
        unresolved_required_fields=[
            AutofillFieldResult(
                label="Phone", classification=FieldClassification.UNKNOWN_REQUIRED, required=True
            )
        ]
    )
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result,
        enabled=True,
        challenge_detector=_FakeChallengeDetector(),
        submit_adapter=adapter,
        page=object(),
    )

    assert out is result
    assert out.submit_performed is False
    assert adapter.submit_calls == 0


def test_challenge_immediately_before_submit_blocks() -> None:
    result = _safe_result()
    adapter = _FakeSubmitAdapter()
    detector = _FakeChallengeDetector(challenge="captcha")

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=detector, submit_adapter=adapter, page=object()
    )

    assert out.submit_performed is False
    assert adapter.submit_calls == 0


def test_failed_click_leaves_result_unchanged() -> None:
    result = _safe_result()
    adapter = _FakeSubmitAdapter(submit_ok=False)

    out = attempt_auto_submit(
        result,
        enabled=True,
        challenge_detector=_FakeChallengeDetector(),
        submit_adapter=adapter,
        page=object(),
    )

    assert out.submit_performed is False
    assert adapter.confirm_calls == 0


def test_missing_confirmation_leaves_result_unchanged() -> None:
    result = _safe_result()
    adapter = _FakeSubmitAdapter(confirmed=False)

    out = attempt_auto_submit(
        result,
        enabled=True,
        challenge_detector=_FakeChallengeDetector(),
        submit_adapter=adapter,
        page=object(),
    )

    assert out.submit_performed is False


def test_successful_mocked_submit_sets_submit_performed_without_mutating_input() -> None:
    result = _safe_result()
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result,
        enabled=True,
        challenge_detector=_FakeChallengeDetector(),
        submit_adapter=adapter,
        page=object(),
    )

    assert out.submit_performed is True
    assert result.submit_performed is False
    assert out is not result
    assert adapter.submit_calls == 1
    assert adapter.confirm_calls == 1


def test_default_submit_adapter_for_source_is_source_aware() -> None:
    assert isinstance(
        default_submit_adapter_for_source("target_company:greenhouse:agoda"), GreenhouseSubmitAdapter
    )
    assert isinstance(default_submit_adapter_for_source("target_company:lever:qonto"), LeverSubmitAdapter)
    # Unknown sources keep the historical Greenhouse default, like default_adapter_for_source.
    assert isinstance(default_submit_adapter_for_source("linkedin-email"), GreenhouseSubmitAdapter)


def test_default_submit_adapter_for_source_ashby_never_falls_back_to_greenhouse() -> None:
    adapter = default_submit_adapter_for_source("target_company:ashby:perk")
    assert isinstance(adapter, AshbySubmitAdapter)
    assert not isinstance(adapter, GreenhouseSubmitAdapter)


def test_ashby_submit_adapter_never_submits_even_when_policy_is_safe() -> None:
    result = _safe_result(source="target_company:ashby:perk")
    detector = _FakeChallengeDetector()
    adapter = AshbySubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=detector, submit_adapter=adapter, page=object()
    )

    assert out is result
    assert out.submit_performed is False
    assert adapter.submit(object()) is False
    assert adapter.submit_confirmed(object()) is False


class _FakeLocator:
    def __init__(self, count: int, texts: list[str] | None = None) -> None:
        self._count = count
        self._texts = texts or []
        self.clicked = False

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "_FakeLocator":
        return self

    def click(self, timeout: int | None = None) -> None:
        _ = timeout
        self.clicked = True

    def inner_text(self, timeout: int | None = None) -> str:
        _ = timeout
        return " ".join(self._texts)


class _FakePage:
    """In-memory stand-in for a Playwright Page: no browser, no network."""

    def __init__(self, submit_button_count: int, body_text: str) -> None:
        self._submit_button_count = submit_button_count
        self._body_text = body_text

    def locator(self, selector: str) -> _FakeLocator:
        if "submit" in selector:
            return _FakeLocator(self._submit_button_count)
        return _FakeLocator(1, [self._body_text])


def test_click_submit_button_adapter_clicks_and_confirms() -> None:
    page = _FakePage(submit_button_count=1, body_text="Thank you for applying!")
    adapter = GreenhouseSubmitAdapter()

    assert adapter.submit(page) is True
    assert adapter.submit_confirmed(page) is True


def test_click_submit_button_adapter_returns_false_when_no_button() -> None:
    page = _FakePage(submit_button_count=0, body_text="")
    adapter = GreenhouseSubmitAdapter()

    assert adapter.submit(page) is False


def test_click_submit_button_adapter_no_confirmation_text() -> None:
    page = _FakePage(submit_button_count=1, body_text="Please fix the errors below.")
    adapter = LeverSubmitAdapter()

    assert adapter.submit(page) is True
    assert adapter.submit_confirmed(page) is False


def test_otp_on_page_blocks_submit() -> None:
    result = _safe_result()
    page = _FakePage(submit_button_count=1, body_text="Enter the one-time password sent to your phone.")
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=_FakeChallengeDetector(), submit_adapter=adapter, page=page
    )

    assert out.submit_performed is False
    assert adapter.submit_calls == 0


def test_email_verification_on_page_blocks_submit() -> None:
    result = _safe_result()
    page = _FakePage(submit_button_count=1, body_text="Please verify your email address to continue.")
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=_FakeChallengeDetector(), submit_adapter=adapter, page=page
    )

    assert out.submit_performed is False
    assert adapter.submit_calls == 0


def test_consent_ambiguity_on_page_blocks_submit() -> None:
    result = _safe_result()
    page = _FakePage(
        submit_button_count=1,
        body_text="Please confirm your consent to proceed with this application.",
    )
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=_FakeChallengeDetector(), submit_adapter=adapter, page=page
    )

    assert out.submit_performed is False
    assert adapter.submit_calls == 0


def test_clean_safe_page_still_permits_mocked_submit() -> None:
    result = _safe_result()
    page = _FakePage(submit_button_count=1, body_text="Review your application details before submitting.")
    adapter = _FakeSubmitAdapter()

    out = attempt_auto_submit(
        result, enabled=True, challenge_detector=_FakeChallengeDetector(), submit_adapter=adapter, page=page
    )

    assert out.submit_performed is True
    assert adapter.submit_calls == 1
    assert adapter.confirm_calls == 1
