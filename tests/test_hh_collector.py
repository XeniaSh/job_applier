from dataclasses import dataclass

from app.collectors.hh_client import HHRequestError
from app.collectors.hh_collector import HHCollector
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation


@dataclass
class _FakeHHClient:
    previews: list[dict]
    details: dict[str, dict]
    fail_detail_ids: set[str]

    def search_vacancies(self, query: str, page: int = 0, per_page: int = 20) -> list[dict]:
        _ = (query, page, per_page)
        return self.previews

    def get_vacancy(self, vacancy_id: str) -> dict:
        if vacancy_id in self.fail_detail_ids:
            raise HHRequestError("failed details")
        return self.details[vacancy_id]


class _FakeSeenJobs:
    def __init__(self, seen_ids: set[str] | None = None) -> None:
        self.seen_ids = set(seen_ids or set())
        self.marked: set[str] = set()

    def is_seen(self, source: str, external_id: str) -> bool:
        _ = source
        return external_id in self.seen_ids

    def mark_seen(self, source: str, external_id: str) -> None:
        _ = source
        self.seen_ids.add(external_id)
        self.marked.add(external_id)


class _FakeAnalyzer:
    def __init__(self, decisions: dict[str, Decision]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def analyze(self, vacancy: str) -> VacancyEvaluation:
        self.calls.append(vacancy)
        external_id = vacancy.split("ID:", maxsplit=1)[1].split("\n", maxsplit=1)[0].strip()
        decision = self.decisions.get(external_id, Decision.IGNORE)
        return VacancyEvaluation(
            decision=decision,
            summary="summary",
            matched_points=["java"],
            gaps=["redis"],
            nuances=["remote"],
            match_percentage=88.9,
            matched_score=8.0,
            total_possible_score=9.0,
            recommended_resume=RecommendedResume.JAVA_BACKEND,
            recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        )


def _vacancy_payload(vacancy_id: str, title: str = "Java Backend Engineer") -> dict:
    return {
        "id": vacancy_id,
        "name": title,
        "employer": {"name": "Acme"},
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "area": {"name": "Remote"},
        "employment": {"name": "Full-time"},
        "salary": None,
        "description": f"<p>ID:{vacancy_id}</p>",
        "published_at": "2026-07-15",
    }


def test_duplicate_ids_and_seen_ids_are_skipped() -> None:
    previews = [_vacancy_payload("1"), _vacancy_payload("1"), _vacancy_payload("2")]
    details = {"1": _vacancy_payload("1"), "2": _vacancy_payload("2")}
    collector = HHCollector(
        hh_client=_FakeHHClient(previews=previews, details=details, fail_detail_ids=set()),
        analyzer=_FakeAnalyzer({"1": Decision.STRONG_MATCH, "2": Decision.POTENTIAL_MATCH}),
        seen_jobs=_FakeSeenJobs(seen_ids={"2"}),
    )

    report = collector.collect_and_analyze(queries=["Java Backend"], limit=20)

    assert report.new_found == 1
    assert report.analyzed == 1
    assert report.strong_matches == 1


def test_one_vacancy_failure_does_not_stop_run() -> None:
    previews = [_vacancy_payload("1"), _vacancy_payload("2")]
    details = {"1": _vacancy_payload("1"), "2": _vacancy_payload("2")}
    collector = HHCollector(
        hh_client=_FakeHHClient(previews=previews, details=details, fail_detail_ids={"1"}),
        analyzer=_FakeAnalyzer({"2": Decision.POTENTIAL_MATCH}),
        seen_jobs=_FakeSeenJobs(),
    )

    report = collector.collect_and_analyze(queries=["Java Backend"], limit=20)

    assert report.errors == 1
    assert report.analyzed == 1
    assert report.potential_matches == 1


def test_limit_is_respected() -> None:
    previews = [_vacancy_payload("1"), _vacancy_payload("2"), _vacancy_payload("3")]
    details = {"1": _vacancy_payload("1"), "2": _vacancy_payload("2"), "3": _vacancy_payload("3")}
    collector = HHCollector(
        hh_client=_FakeHHClient(previews=previews, details=details, fail_detail_ids=set()),
        analyzer=_FakeAnalyzer({"1": Decision.STRONG_MATCH, "2": Decision.STRONG_MATCH, "3": Decision.STRONG_MATCH}),
        seen_jobs=_FakeSeenJobs(),
    )

    report = collector.collect_and_analyze(queries=["Java Backend"], limit=2)

    assert report.analyzed == 2
