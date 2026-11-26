from dataclasses import replace

import pytest

from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.delivery_priority import employment_priority
from app.company_watch.models import TargetCompany


def company(**kwargs):
    return TargetCompany(name="Acme", watcher_type="greenhouse", language="english", priority="A",
                         relocation_status="unknown", **kwargs)


def vacancy(text="Java backend", location="Remote"):
    return NormalizedVacancy(source="target_company:greenhouse:acme", external_id="1", title="Senior Java Engineer",
                             company="Acme", location=location, description=text, url="https://example.invalid/job/1",
                             employment=None, published_at=None)


@pytest.mark.parametrize("metadata", [{"russian_speaking_signal": True}, {"cis_employer": True}])
def test_language_or_cis_only_boosts_supported_cross_border_remote(metadata):
    c = company(**metadata)
    assert employment_priority(vacancy("Remote worldwide."), company=c)[0] == 0
    assert employment_priority(vacancy("Remote within the US only."), company=c)[0] == 3
    assert employment_priority(vacancy(), company=c)[0] == 3


def test_nz_sponsorship_is_protected_without_inference_from_candidate_location():
    assert employment_priority(vacancy("Visa sponsorship available.", "Auckland, New Zealand"))[0] == 0
    assert employment_priority(vacancy("No visa sponsorship.", "Auckland, New Zealand"))[0] == 3
    assert employment_priority(vacancy("Visa sponsorship available.", "London"))[0] == 2
    assert employment_priority(vacancy("Visa sponsorship available.", "London / New Zealand"))[0] != 0


def test_worldwide_and_explicit_sponsored_relocation_beat_unknown_global_fintech():
    unknown = vacancy()
    assert employment_priority(vacancy("Remote worldwide.")) < employment_priority(unknown)
    assert employment_priority(vacancy("Visa sponsorship and relocation package available.")) < employment_priority(unknown)
    assert employment_priority(vacancy("Relocation support. No visa sponsorship."))[0] == 3


def test_company_priority_is_secondary_and_does_not_prove_eligibility():
    c = company()
    low = c.model_copy(update={"priority": "C"})
    assert employment_priority(vacancy("Remote worldwide."), company=low) < employment_priority(vacancy(), company=c)
    assert employment_priority(vacancy(), company=c) < employment_priority(vacancy(), company=low)


def test_wise_transfer_boilerplate_cannot_gain_raw_text_priority():
    v = replace(vacancy("Remote worldwide. We support transfer of visa sponsorship for local candidates."),
                source="target_company:smartrecruiters:wise")
    assert employment_priority(v)[0] == 3
