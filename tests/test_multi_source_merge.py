from app.collectors.vacancy_collector import NormalizedVacancy
import app.cli as cli_module


def _vacancy(*, source: str, external_id: str, title: str, company: str | None, location: str | None, url: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        employment=None,
        description=title,
        url=url,
        published_at=None,
    )


def test_merge_deduplicates_by_canonical_url() -> None:
    class _LinkedIn:
        def collect(self):
            return [_vacancy(source="linkedin-email", external_id="1", title="Backend", company="A", location="Remote", url="https://jobs.example.com/role/1?utm=1")]

    class _Greenhouse:
        def collect(self):
            return [_vacancy(source="greenhouse", external_id="2", title="Backend", company="A", location="Remote", url="https://jobs.example.com/role/1")]

    merged, errors = cli_module._collect_from_collectors([_LinkedIn(), _Greenhouse()])
    assert errors == 0
    assert len(merged) == 1


def test_merge_deduplicates_by_company_title_location_when_url_missing() -> None:
    class _A:
        def collect(self):
            return [_vacancy(source="linkedin-email", external_id="1", title="Backend Engineer", company="ACME", location="Remote", url="")]

    class _B:
        def collect(self):
            return [_vacancy(source="greenhouse", external_id="2", title="Backend Engineer", company="ACME", location="Remote", url="")]

    merged, errors = cli_module._collect_from_collectors([_A(), _B()])
    assert errors == 0
    assert len(merged) == 1


def test_one_collector_failure_does_not_stop_others() -> None:
    class _Broken:
        def collect(self):
            raise RuntimeError("boom")

    class _Healthy:
        def collect(self):
            return [_vacancy(source="greenhouse", external_id="10", title="Role", company="X", location=None, url="https://job-boards.greenhouse.io/x/jobs/10")]

    merged, errors = cli_module._collect_from_collectors([_Broken(), _Healthy()])
    assert errors == 1
    assert len(merged) == 1
    assert merged[0].source == "greenhouse"
