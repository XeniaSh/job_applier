from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.application.autofill.classifier import classify_field
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.models import AutofillStatus, FieldClassification
from app.application.autofill.questions import QuestionKind
from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.service import AutofillService, _decide_salary, _readback_matches
from app.application.candidate_profile import CandidateProfile
from app.collectors.vacancy_collector import NormalizedVacancy

SALARY_LABEL = "Desired annual gross salary in EUR"
PUBLISHED = "Salary range: EUR 80,000 - 100,000 per year gross."


def _profile(salary: dict[str, object] | None) -> CandidateProfile:
    data: dict[str, object] = {
        "identity": {
            "first_name": "Ada",
            "last_name": "Example",
            "email": "ada.example@example.test",
            "phone": "+15555550100",
        },
        "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
    }
    if salary is not None:
        data["employment"] = {"salary_expectations": salary}
    return CandidateProfile.model_validate(data)


class _Resolver:
    def __init__(self, description: str) -> None:
        self._description = description

    def resolve(self, source: str, external_id: str) -> ResolvedVacancy:
        url = "https://boards.example.test/example/jobs/1"
        return ResolvedVacancy(
            source=source,
            external_id=external_id,
            title="Senior Backend Engineer",
            company="Example",
            url=url,
            application_url=url,
            vacancy=NormalizedVacancy(
                source=source,
                external_id=external_id,
                title="Senior Backend Engineer",
                company="Example",
                location="Berlin, Germany",
                employment=None,
                description=self._description,
                url=url,
                published_at=None,
            ),
        )


@dataclass
class _Session:
    page: object = object()

    def open(self, url: str, *, is_usable: object = None) -> object:
        return self.page

    def close(self) -> None:
        pass


@dataclass
class _Adapter:
    salary_label: str = SALARY_LABEL
    readback_override: str | None = None
    values: dict[str, object] = field(default_factory=dict)
    submit_called: bool = False

    def detect_challenge(self, page: object) -> str | None:
        return None

    def recognize(self, page: object) -> bool:
        return True

    def discover_fields(self, page: object) -> list[DiscoveredField]:
        return [
            DiscoveredField(label="First Name", name="first_name", required=True),
            DiscoveredField(label=self.salary_label, name="salary", required=True),
        ]

    def fill_field(self, page: object, classified: object) -> bool:
        if getattr(classified, "fill", False):
            self.values[classified.field.name] = classified.value  # type: ignore[attr-defined]
            return True
        return False

    def upload_resume(self, page: object, resume_path: object, field: object) -> bool:
        return False

    def read_back(self, page: object, field: DiscoveredField) -> str | None:
        if field.name == "salary" and self.readback_override is not None:
            return self.readback_override
        value = self.values.get(field.name or "")
        return None if value is None else str(value)


def _run(source: str, salary: dict[str, object] | None, description: str = "", adapter: _Adapter | None = None):
    adapter = adapter or _Adapter()
    service = AutofillService(
        resolver=_Resolver(description),
        profile_loader=lambda: _profile(salary),
        adapter=adapter,
        browser_factory=_Session,
        wait_for_review=lambda: None,
    )
    return service.run(source, "1", keep_open=True), adapter


SOURCES = ["target_company:greenhouse:example", "target_company:lever:example", "target_company:ashby:example"]
CANDIDATE = {"fill_salary": True, "amount": 120000, "currency": "EUR", "period": "annual", "basis": "gross"}


@pytest.mark.parametrize("source", SOURCES)
def test_candidate_preference_fills_numeric_amount_and_never_submits(source: str) -> None:
    result, adapter = _run(source, CANDIDATE, PUBLISHED)
    assert result.status is AutofillStatus.READY_FOR_REVIEW
    assert result.submit_performed is False
    assert adapter.values == {"first_name": "Ada", "salary": "120000"}
    note = next(f.note for f in result.filled_fields if f.name == "salary")
    assert note and "source=candidate_preference" in note and "EUR annual gross" in note


@pytest.mark.parametrize("source", SOURCES)
def test_published_range_midpoint_fills_without_candidate_amount(source: str) -> None:
    result, adapter = _run(source, {"fill_salary": True}, PUBLISHED)
    assert adapter.values["salary"] == "90000"
    note = next(f.note for f in result.filled_fields if f.name == "salary")
    assert note and "source=published_range" in note and "evidence=https://boards.example.test/example/jobs/1" in note


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    ("salary", "label"),
    [
        ({**CANDIDATE, "fill_salary": False}, SALARY_LABEL),  # opt-out
        ({"fill_salary": False}, SALARY_LABEL),  # existing object = opt-out even for published ranges
        (CANDIDATE, "Current annual gross salary in EUR"),
        (CANDIDATE, "Desired annual gross salary"),  # missing currency
        (CANDIDATE, "Desired monthly gross salary in EUR"),  # no conversion
        ({k: v for k, v in CANDIDATE.items() if k != "basis"}, SALARY_LABEL),  # incomplete preference
    ],
)
def test_manual_cases_leave_salary_unfilled_but_keep_other_fields(
    source: str, salary: dict[str, object] | None, label: str
) -> None:
    result, adapter = _run(source, salary, PUBLISHED, _Adapter(salary_label=label))
    assert adapter.values == {"first_name": "Ada"}
    assert result.submit_performed is False
    assert any(f.name == "salary" for f in result.unresolved_required_fields)
    assert any(f.name == "first_name" for f in result.filled_fields)


@pytest.mark.parametrize("source", SOURCES)
def test_absent_salary_config_permits_published_fallback_only(source: str) -> None:
    result, adapter = _run(source, None, PUBLISHED)
    assert adapter.values == {"first_name": "Ada", "salary": "90000"}
    assert result.submit_performed is False
    result, adapter = _run(source, None, "We offer great benefits.")
    assert adapter.values == {"first_name": "Ada"}
    assert any(f.name == "salary" for f in result.unresolved_required_fields)


def test_opt_out_discards_a_legacy_adapter_fill() -> None:
    enabled = _profile({**CANDIDATE})
    item = classify_field(DiscoveredField(label=SALARY_LABEL, name="salary", required=True), enabled)
    assert item.fill is True
    resolved = _Resolver(PUBLISHED).resolve("target_company:greenhouse:example", "1")
    decided = _decide_salary(item, profile=_profile({**CANDIDATE, "fill_salary": False}), vacancy=resolved)
    assert decided.fill is False and decided.value is None


class _AshbyHookAdapter(_Adapter):
    """Offline fake DOM, but the real `AshbyAdapter.adjust_classified_field` participates."""

    def adjust_classified_field(self, item: object, *, profile: object, vacancy: object) -> object:
        from app.application.autofill.ashby import AshbyAdapter

        return AshbyAdapter().adjust_classified_field(item, profile=profile, vacancy=vacancy)  # type: ignore[arg-type]


def test_ashby_hook_published_fallback_works_after_provider_guard() -> None:
    source = "target_company:ashby:example"
    result, adapter = _run(source, None, PUBLISHED, _AshbyHookAdapter())
    assert adapter.values == {"first_name": "Ada", "salary": "90000"}
    note = next(f.note for f in result.filled_fields if f.name == "salary")
    assert note and "source=published_range" in note
    result, adapter = _run(source, CANDIDATE, PUBLISHED, _AshbyHookAdapter())
    assert adapter.values["salary"] == "120000"


def test_ashby_hook_ambiguous_local_currency_stays_manual() -> None:
    label = "Desired annual gross salary in local currency"
    for salary in (None, CANDIDATE):
        result, adapter = _run(
            "target_company:ashby:example", salary, PUBLISHED, _AshbyHookAdapter(salary_label=label)
        )
        assert adapter.values == {"first_name": "Ada"}
        assert any(f.name == "salary" for f in result.unresolved_required_fields)


def test_unconfirmed_salary_readback_is_not_reported_filled() -> None:
    result, _ = _run(SOURCES[0], {**CANDIDATE, "amount": 80000}, adapter=_Adapter(readback_override="180000"))
    assert not any(f.name == "salary" for f in result.filled_fields)


def test_decision_overrides_a_legacy_prefilled_salary_item() -> None:
    legacy = {"fill_salary": True, "amount": 80000, "currency": "EUR", "period": "annual"}
    profile = _profile(legacy)
    field_ = DiscoveredField(label=SALARY_LABEL, name="salary", required=True)
    item = classify_field(field_, profile)
    assert item.kind is QuestionKind.SALARY and item.fill is True  # direct mapper compatibility
    resolved = _Resolver("").resolve("target_company:greenhouse:example", "1")
    decided = _decide_salary(item, profile=profile, vacancy=resolved)
    assert decided.fill is False and decided.value is None
    assert decided.classification is FieldClassification.UNKNOWN_REQUIRED


def test_mapper_treats_prior_pay_wording_as_manual_even_with_currency_word() -> None:
    profile = _profile({**CANDIDATE})
    for label in ("Previous salary", "Last drawn salary", "Present salary"):
        item = classify_field(DiscoveredField(label=label), profile)
        assert item.fill is False
    assert classify_field(DiscoveredField(label="Desired salary (currency: EUR)"), profile).kind is QuestionKind.SALARY


@pytest.mark.parametrize(
    ("wanted", "actual", "ok"),
    [("80000", "80000", True), ("80000", "180000", False), ("80000", "abc80000", False), ("80000", "80000 EUR", False)],
)
def test_service_readback_for_salary_is_strict(wanted: str, actual: str, ok: bool) -> None:
    item = classify_field(DiscoveredField(label=SALARY_LABEL), _profile(CANDIDATE))
    from dataclasses import replace

    assert _readback_matches(replace(item, value=wanted), actual) is ok
