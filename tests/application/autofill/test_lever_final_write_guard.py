"""Synthetic (no-Chromium) tests for the Lever final-write guard.

`LeverAdapter.fill_field`'s final-write boundary (`_final_write_normalized_label` /
`_FINAL_WRITE_BLOCKED_NAMES` in `app.application.autofill.lever`) must skip Lever's two
known live-failure optional fields ("Current company" / "Other website") before ever
touching the page -- it recognizes them from the discovered field's own stable identity,
never from `classified.kind`. These use a spy `Page` double instead of a real browser to
prove the skip returns before any Playwright call (locator resolution, check/fill, etc.)
is attempted, and run even where Playwright's Chromium binary is not installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import pytest

from app.application.autofill.classifier import ClassifiedField
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.models import AutofillStatus, FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService
from app.application.candidate_profile import CandidateProfile

LOGGER_NAME = "app.application.autofill.lever"


class _UntouchedPage:
    """Raises on any attribute access, proving `fill_field` never resolves a
    locator or otherwise touches the page for a field the guard should block.
    """

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"fill_field touched page.{name}")


def test_final_write_guard_blocks_optional_current_company_without_touching_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False)
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Acme Corp",
        fill=True,
        kind=QuestionKind.CURRENT_EMPLOYER,
        generated=True,
    )
    adapter = LeverAdapter()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = adapter.fill_field(_UntouchedPage(), classified)
    assert result is False
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert len(logs) == 1
    assert "field=current_company" in logs[0]
    assert "action=skip" in logs[0]
    assert "source=generated" in logs[0]


def test_final_write_guard_blocks_optional_other_website_by_label_when_name_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The `name="urls[Other]"` recognition path is only one route in; a
    posting could plausibly omit/rename the `name` and still carry the exact
    "Other website" label, so the guard must also catch it by normalized
    label alone.
    """
    field = DiscoveredField(label="Other website*", name=None, field_type="url", required=False)
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="https://example.test",
        fill=True,
        kind=QuestionKind.WEBSITE,
    )
    adapter = LeverAdapter()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = adapter.fill_field(_UntouchedPage(), classified)
    assert result is False
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert len(logs) == 1
    assert "field=other_website" in logs[0]
    assert "action=skip" in logs[0]
    assert "source=resolver" in logs[0]


def test_final_write_guard_does_not_block_unrelated_optional_field() -> None:
    """A narrow allowlist: an unrelated optional URL-shaped field (e.g.
    "Portfolio URL") must fall straight through the guard into the real fill
    logic -- proven here by that real logic then needing to touch the page,
    which the spy raises on.
    """
    field = DiscoveredField(label="Portfolio URL", name="urls[Portfolio]", field_type="url", required=False)
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="https://example.test/portfolio",
        fill=True,
        kind=QuestionKind.WEBSITE,
    )
    adapter = LeverAdapter()
    with pytest.raises(AssertionError, match="page.locator"):
        adapter.fill_field(_UntouchedPage(), classified)


def test_final_write_guard_allows_required_current_company(caplog: pytest.LogCaptureFixture) -> None:
    """The guard only blocks the two known fields when they are optional; a
    required field with the same stable identity must log `action=fill` and
    fall through to the real fill logic instead of being skipped.
    """
    field = DiscoveredField(label="Current company", name="org", field_type="text", required=True)
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Acme Corp",
        fill=True,
        kind=QuestionKind.CURRENT_EMPLOYER,
    )
    adapter = LeverAdapter()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(AssertionError, match="page.locator"):
            adapter.fill_field(_UntouchedPage(), classified)
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert len(logs) == 1
    assert "field=current_company" in logs[0]
    assert "action=fill" in logs[0]


def _adjusted_current_company_classified(current_employer: str) -> tuple[LeverAdapter, ClassifiedField]:
    """A `ClassifiedField` that went through the real
    `LeverAdapter.adjust_classified_field` from an explicit
    `employment.current_employer` fact -- the one legitimate way
    `_trusted_current_employer_value` ever gets set -- rather than one built
    by hand with an assumed kind/generated/value combination.
    """
    from app.application.autofill.classifier import classify_field

    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False)
    profile = CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
            "employment": {"current_employer": current_employer},
        }
    )
    vacancy = ResolvedVacancy(
        source="target_company:lever:acme",
        external_id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://jobs.lever.co/acme/1",
        application_url="https://jobs.lever.co/acme/1",
    )
    classified = classify_field(field, profile)
    adapter = LeverAdapter()
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=vacancy)
    return adapter, adjusted


def test_final_write_guard_allows_optional_current_company_from_trusted_resolver_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one trusted exception: an optional "Current company" field that
    `adjust_classified_field` itself resolved from the explicit
    `employment.current_employer` fact must fall through the guard into the
    real fill logic -- proven here by that real logic then needing to touch
    the page, which the spy raises on.
    """
    adapter, adjusted = _adjusted_current_company_classified("Acme Corp")
    assert adjusted.value == "Acme Corp"
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(AssertionError, match="page.locator"):
            adapter.fill_field(_UntouchedPage(), adjusted)
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert len(logs) == 1
    assert "field=current_company" in logs[0]
    assert "action=fill" in logs[0]
    assert "source=resolver" in logs[0]


def test_final_write_guard_blocks_optional_current_company_with_wrong_value_despite_matching_kind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a correctly-classified, non-generated `CURRENT_EMPLOYER` field
    must still be blocked if its value is not the exact one
    `adjust_classified_field` read from the explicit
    `employment.current_employer` fact: kind/generated alone must never be
    trusted as a stand-in for the explicit fact itself.
    """
    adapter, adjusted = _adjusted_current_company_classified("Acme Corp")
    tampered = replace(adjusted, value="Some Other Corp")
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = adapter.fill_field(_UntouchedPage(), tampered)
    assert result is False
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert len(logs) == 1
    assert "field=current_company" in logs[0]
    assert "action=skip" in logs[0]


def test_final_write_guard_blocks_optional_current_company_with_wrong_kind_even_without_generated_flag() -> None:
    """Defense in depth: even when `classified.generated` is left False, a
    misclassified `kind` (anything other than the real `CURRENT_EMPLOYER`)
    must still be blocked -- the guard never trusts `fill=True` alone.
    """
    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False)
    classified = ClassifiedField(
        field=field,
        classification=FieldClassification.SUPPORTED_DETERMINISTIC,
        value="Acme Corp",
        fill=True,
        kind=QuestionKind.WEBSITE,
        generated=False,
    )
    adapter = LeverAdapter()
    result = adapter.fill_field(_UntouchedPage(), classified)
    assert result is False


def test_adjust_current_company_fills_from_explicit_profile_fact() -> None:
    """`LeverAdapter.adjust_classified_field` opts the optional "Current
    company" field back in only from the explicit
    `employment.current_employer` fact -- never a generated/LLM value, and
    the shared `CandidateProfile.current_employer_for_autofill` used by
    `map_question` itself still always returns None (see its docstring).
    """
    from app.application.autofill.classifier import classify_field

    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False)
    profile = CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
            "employment": {"current_employer": "Acme Corp"},
        }
    )
    classified = classify_field(field, profile)
    assert classified.kind is QuestionKind.CURRENT_EMPLOYER
    assert classified.fill is False
    assert classified.value is None

    vacancy = ResolvedVacancy(
        source="target_company:lever:acme",
        external_id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://jobs.lever.co/acme/1",
        application_url="https://jobs.lever.co/acme/1",
    )
    adapter = LeverAdapter()
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=vacancy)
    assert adjusted.fill is True
    assert adjusted.value == "Acme Corp"
    assert adjusted.generated is False
    assert adjusted.kind is QuestionKind.CURRENT_EMPLOYER


def test_adjust_current_company_stays_blank_without_explicit_fact() -> None:
    from app.application.autofill.classifier import classify_field

    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False)
    profile = CandidateProfile.model_validate(
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
    classified = classify_field(field, profile)
    vacancy = ResolvedVacancy(
        source="target_company:lever:acme",
        external_id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://jobs.lever.co/acme/1",
        application_url="https://jobs.lever.co/acme/1",
    )
    adapter = LeverAdapter()
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=vacancy)
    assert adjusted.fill is False
    assert adjusted.value is None


def test_adjust_current_company_never_overrides_a_required_field() -> None:
    """Scoped to the optional field only; a required field with the same
    stable identity (hypothetical posting shape) is left for the ordinary
    classification/fill path, not force-filled here.
    """
    from app.application.autofill.classifier import classify_field

    field = DiscoveredField(label="Current company", name="org", field_type="text", required=True)
    profile = CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
            "employment": {"current_employer": "Acme Corp"},
        }
    )
    classified = classify_field(field, profile)
    vacancy = ResolvedVacancy(
        source="target_company:lever:acme",
        external_id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://jobs.lever.co/acme/1",
        application_url="https://jobs.lever.co/acme/1",
    )
    adapter = LeverAdapter()
    adjusted = adapter.adjust_classified_field(classified, profile=profile, vacancy=vacancy)
    assert adjusted is classified


class _SpyLeverAdapter(LeverAdapter):
    """Real `LeverAdapter` guard/hook logic wired to a spy for everything
    that would otherwise touch a live page. `fill_field`/`read_back` for the
    two guarded fields ("Current company" / "Other website") delegate to the
    real `LeverAdapter` implementation -- proven safe with `_UntouchedPage`
    because the guard in `fill_field` returns before ever resolving a
    locator -- so the guard behavior under test is the real one, not a
    reimplementation of it. Every other field (here, only the required
    LinkedIn field) is simulated the way `_FakeAdapter` in `test_service.py`
    simulates a normal adapter, since there is no real DOM to write to or
    read back from in this non-browser test.
    """

    _GUARDED_NAMES = frozenset({"org", "urls[Other]"})

    def __init__(self) -> None:
        self.write_calls: list[str] = []

    def recognize(self, page: object) -> bool:
        return True

    def detect_challenge(self, page: object) -> str | None:
        return None

    def prepare_page(self, page: object) -> None:
        return None

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        return [
            DiscoveredField(label="Current company", name="org", field_type="text", required=False),
            DiscoveredField(label="Other website", name="urls[Other]", field_type="url", required=False),
            DiscoveredField(label="LinkedIn URL", name="urls[LinkedIn]", field_type="text", required=True),
        ]

    def fill_field(self, page: object, classified: ClassifiedField) -> bool:
        if classified.field.name in self._GUARDED_NAMES:
            return super().fill_field(page, classified)
        self.write_calls.append(classified.field.name or classified.field.label)
        return bool(classified.fill)

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        if field.name in self._GUARDED_NAMES:
            return None
        return "https://www.linkedin.com/in/ada-example-test"

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        return False


@dataclass
class _UntouchedSession:
    keep_open: bool = False
    closed: bool = False
    page: object = None

    def __post_init__(self) -> None:
        if self.page is None:
            self.page = _UntouchedPage()

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
            company="Acme",
            url="https://jobs.lever.co/acme/1",
            application_url="https://jobs.lever.co/acme/1",
        )


def _profile_with_linkedin_and_website() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "professional_links": {
                "linkedin": "https://www.linkedin.com/in/ada-example-test",
                "website": "https://ada-example.test",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


def test_service_fill_loop_logs_both_skips_and_never_writes_to_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drives `AutofillService`'s real fill loop (real `classify_field`,
    real `_enrich_unresolved`, real per-field branching) end to end against
    a `LeverAdapter` spy and an `_UntouchedPage`, covering both diagnostic
    paths for the two live-failure fields: optional "Current company"
    classifies `fill=False`, so the service's own hook call
    (`observe_final_fill_plan`, added at the fill-plan boundary right after
    `_enrich_unresolved`) is the only thing that ever logs its skip, since
    `fill_field` is never invoked for it. Optional "Other website" resolves
    a real value and classifies `fill=True`, so the service does call
    `fill_field`, and the pre-existing guard inside it logs the skip
    instead -- proving the hook does not duplicate that log. The required
    LinkedIn field is unaffected and still fills, and `_UntouchedPage`
    guarantees no code path here actually reached the page.
    """
    adapter = _SpyLeverAdapter()
    session = _UntouchedSession()
    service = AutofillService(
        resolver=_FakeResolver(),
        profile_loader=_profile_with_linkedin_and_website,
        adapter=adapter,
        browser_factory=lambda: session,
        wait_for_review=lambda: None,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = service.run("target_company:lever:acme", "1", keep_open=False)

    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert any(item.label == "LinkedIn URL" for item in result.filled_fields)
    assert adapter.write_calls == ["urls[LinkedIn]"]
    assert any(item.label == "Other website" for item in result.unresolved_optional_fields)
    assert any(item.label == "Current company" for item in result.unresolved_optional_fields)

    skip_logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("lever_final_write")]
    assert any("field=current_company" in m and "action=skip" in m for m in skip_logs)
    assert any("field=other_website" in m and "action=skip" in m for m in skip_logs)
    assert session.closed is True


class _RecognizedEmptyFormPage:
    """A page `recognize()`/`discover_fields` treat as a valid, empty Lever
    application form -- just enough surface for `discover_fields` to run to
    completion without a real DOM, used to prove its own reset side effect
    independent of any particular discovered field.
    """

    url = "https://jobs.lever.co/acme/1/apply"

    def locator(self, selector: str) -> "_RecognizedEmptyFormPage":
        return self

    def count(self) -> int:
        return 1

    @property
    def first(self) -> "_EmptyFormLocator":
        return _EmptyFormLocator()


class _EmptyFormLocator:
    def locator(self, selector: str) -> "_EmptyFormLocator":
        return self

    def count(self) -> int:
        return 0


def test_discover_fields_resets_trusted_current_employer_value_from_a_previous_run() -> None:
    """A reused adapter instance must never carry trust from a previous
    Prepare run into this one: a second run with no explicit
    `employment.current_employer` must not inherit a first run's cached
    trusted value.
    """
    adapter = LeverAdapter()
    adapter._trusted_current_employer_value = "Stale Corp From A Previous Run"
    adapter.discover_fields(_RecognizedEmptyFormPage())
    assert adapter._trusted_current_employer_value is None


class _FakeOrgLocator:
    """A spy standing in for the live "Current company" text input."""

    def __init__(self, *, initial_value: str, clear_succeeds: bool) -> None:
        self.value = initial_value
        self._clear_succeeds = clear_succeeds
        self.fill_calls: list[str] = []

    def fill(self, value: str, timeout: int | None = None) -> None:
        self.fill_calls.append(value)
        if self._clear_succeeds:
            self.value = value

    def input_value(self) -> str:
        return self.value


@dataclass
class _ReconcilePage:
    org_locator: _FakeOrgLocator

    def locator(self, selector: str) -> _FakeOrgLocator:
        assert selector == '[name="org"]'
        return self.org_locator


def _profile_without_explicit_employer() -> CandidateProfile:
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


def _profile_with_explicit_employer(current_employer: str) -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
            "employment": {"current_employer": current_employer},
        }
    )


def test_reconcile_discovered_fields_clears_stale_current_company_without_explicit_employer() -> None:
    """The exact scenario this hook exists for: an optional "Current
    company" field still carrying browser/autofill text from a previous
    employer, with no explicit `employment.current_employer` fact to fill it
    from instead.
    """
    field = DiscoveredField(
        label="Current company", name="org", field_type="text", required=False, current_value="Previous Employer LLC"
    )
    locator = _FakeOrgLocator(initial_value="Previous Employer LLC", clear_succeeds=True)
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _ReconcilePage(locator), profile=_profile_without_explicit_employer(), fields=[field]
    )
    assert warnings == []
    assert locator.fill_calls == [""]
    assert locator.value == ""


def test_reconcile_discovered_fields_warns_when_clearing_fails() -> None:
    field = DiscoveredField(
        label="Current company", name="org", field_type="text", required=False, current_value="Previous Employer LLC"
    )
    locator = _FakeOrgLocator(initial_value="Previous Employer LLC", clear_succeeds=False)
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _ReconcilePage(locator), profile=_profile_without_explicit_employer(), fields=[field]
    )
    assert len(warnings) == 1
    assert "manual" in warnings[0].lower()
    assert locator.value == "Previous Employer LLC"


def test_reconcile_discovered_fields_leaves_explicit_employer_untouched() -> None:
    """When an explicit `employment.current_employer` fact is configured,
    the ordinary `adjust_classified_field`/`fill_field` path fills (and so
    overwrites) this field later in the same run -- this hook has nothing to
    reconcile and must never touch the page.
    """
    field = DiscoveredField(
        label="Current company", name="org", field_type="text", required=False, current_value="Previous Employer LLC"
    )
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _UntouchedPage(), profile=_profile_with_explicit_employer("Acme Corp"), fields=[field]
    )
    assert warnings == []


def test_reconcile_discovered_fields_skips_already_blank_field() -> None:
    field = DiscoveredField(label="Current company", name="org", field_type="text", required=False, current_value=None)
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _UntouchedPage(), profile=_profile_without_explicit_employer(), fields=[field]
    )
    assert warnings == []


def test_reconcile_discovered_fields_skips_required_field() -> None:
    field = DiscoveredField(
        label="Current company", name="org", field_type="text", required=True, current_value="Previous Employer LLC"
    )
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _UntouchedPage(), profile=_profile_without_explicit_employer(), fields=[field]
    )
    assert warnings == []


def test_reconcile_discovered_fields_skips_unrelated_optional_field() -> None:
    field = DiscoveredField(
        label="Portfolio URL", name="urls[Portfolio]", field_type="url", required=False, current_value="stale"
    )
    adapter = LeverAdapter()
    warnings = adapter.reconcile_discovered_fields(
        _UntouchedPage(), profile=_profile_without_explicit_employer(), fields=[field]
    )
    assert warnings == []
