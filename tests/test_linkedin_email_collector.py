from dataclasses import dataclass
from datetime import datetime, timezone

from app.collectors.email_imap_client import RawEmailMessage
from app.collectors.linkedin_email_collector import LinkedInEmailCollector
from app.collectors.linkedin_models import ContentCompleteness, LinkedInEmailVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)


@dataclass
class _FakeEmailClient:
    messages: list[RawEmailMessage]

    def fetch_linkedin_messages(self):
        return self.messages


class _FakeSeenJobs:
    def __init__(self, seen_ids: set[str] | None = None) -> None:
        self._seen = set(seen_ids or set())
        self.marked: set[str] = set()

    def is_seen(self, source: str, external_id: str) -> bool:
        _ = source
        return external_id in self._seen

    def mark_seen(self, source: str, external_id: str) -> None:
        _ = source
        self._seen.add(external_id)
        self.marked.add(external_id)


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def analyze(self, vacancy: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        self.calls.append((vacancy, content_completeness))
        return VacancyEvaluation(
            decision=Decision.POTENTIAL_MATCH,
            summary="summary",
            matched_points=["java"],
            gaps=[],
            nuances=[],
            match_percentage=None,
            matched_score=0.0,
            total_possible_score=0.0,
            recommended_resume=RecommendedResume.JAVA_BACKEND,
            recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        )


def _raw_message(message_id: str = "<m1>") -> RawEmailMessage:
    from email.message import EmailMessage

    email_message = EmailMessage()
    email_message["From"] = "jobs-noreply@linkedin.com"
    email_message["Subject"] = "Job alert"
    email_message.set_content("https://www.linkedin.com/jobs/view/123/")
    return RawEmailMessage(
        uid="1",
        message_id=message_id,
        from_address="jobs-noreply@linkedin.com",
        subject="Job alert",
        received_at=datetime.now(timezone.utc),
        email_message=email_message,
    )


def test_duplicate_job_ids_within_run_and_seen_skipped(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    def fake_parser(raw_message):
        _ = raw_message
        return [
            LinkedInEmailVacancy("1", "Java Backend", "Acme", "Remote", "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL),
            LinkedInEmailVacancy("1", "Java Backend", "Acme", "Remote", "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL),
            LinkedInEmailVacancy("2", "Java Backend", "Acme", "Remote", "https://www.linkedin.com/jobs/view/2/", None, "m", None, ContentCompleteness.MINIMAL),
        ]

    monkeypatch.setattr(module, "parse_linkedin_email", fake_parser)
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        analyzer=_FakeAnalyzer(),
        seen_jobs=_FakeSeenJobs(seen_ids={"2"}),
    )

    report = collector.collect_and_analyze(limit=20, dry_run=False)

    assert report.new_vacancies == 1
    assert report.analyzed == 1


def test_prefilter_avoids_llm_call(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda raw_message: [
            LinkedInEmailVacancy("1", "Frontend React Engineer", None, None, "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL)
        ],
    )
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": False,
                "reason": "Frontend role",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": ["frontend"],
                "decision": "REJECT",
            },
        )(),
    )
    analyzer = _FakeAnalyzer()
    seen_jobs = _FakeSeenJobs()
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        analyzer=analyzer,
        seen_jobs=seen_jobs,
    )

    report = collector.collect_and_analyze(limit=20, dry_run=False)

    assert report.prefiltered == 1
    assert analyzer.calls == []
    assert "1" in seen_jobs.marked


def test_malformed_email_does_not_stop_run(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    def fake_parser(raw_message):
        if raw_message.message_id == "<bad>":
            raise ValueError("bad message")
        return [
            LinkedInEmailVacancy("10", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/10/", None, "m", None, ContentCompleteness.MINIMAL)
        ]

    monkeypatch.setattr(module, "parse_linkedin_email", fake_parser)
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )

    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message("<bad>"), _raw_message("<good>")]),
        analyzer=_FakeAnalyzer(),
        seen_jobs=_FakeSeenJobs(),
    )
    report = collector.collect_and_analyze(limit=20, dry_run=False)

    assert report.errors == 1
    assert report.analyzed == 1


def test_dry_run_no_llm_and_no_mark_seen(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda raw_message: [
            LinkedInEmailVacancy("1", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL)
        ],
    )
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    analyzer = _FakeAnalyzer()
    seen_jobs = _FakeSeenJobs()
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        analyzer=analyzer,
        seen_jobs=seen_jobs,
    )

    report = collector.collect_and_analyze(limit=20, dry_run=True)

    assert report.new_vacancies == 1
    assert report.analyzed == 0
    assert analyzer.calls == []
    assert seen_jobs.marked == set()


def test_limit_respected(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda raw_message: [
            LinkedInEmailVacancy("1", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL),
            LinkedInEmailVacancy("2", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/2/", None, "m", None, ContentCompleteness.MINIMAL),
        ],
    )
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        analyzer=_FakeAnalyzer(),
        seen_jobs=_FakeSeenJobs(),
    )

    report = collector.collect_and_analyze(limit=1, dry_run=False)
    assert report.new_vacancies == 1


def test_skip_seen_false_keeps_seen_vacancies_for_analysis(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    monkeypatch.setattr(
        module,
        "parse_linkedin_email",
        lambda raw_message: [
            LinkedInEmailVacancy("1", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL)
        ],
    )
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    analyzer = _FakeAnalyzer()
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message()]),
        analyzer=analyzer,
        seen_jobs=_FakeSeenJobs(seen_ids={"1"}),
    )

    report = collector.collect_and_analyze(limit=20, dry_run=False, skip_seen=False, mark_seen=False)
    assert report.already_seen == 1
    assert report.analyzed == 1
    assert len(analyzer.calls) == 1


def test_limit_applies_after_dedup_across_messages(monkeypatch) -> None:
    from app.collectors import linkedin_email_collector as module

    def fake_parser(raw_message):
        if raw_message.message_id == "<m1>":
            return [
                LinkedInEmailVacancy("1", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/1/", None, "m", None, ContentCompleteness.MINIMAL),
                LinkedInEmailVacancy("2", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/2/", None, "m", None, ContentCompleteness.MINIMAL),
            ]
        return [
            LinkedInEmailVacancy("2", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/2/", None, "m", None, ContentCompleteness.MINIMAL),
            LinkedInEmailVacancy("3", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/3/", None, "m", None, ContentCompleteness.MINIMAL),
            LinkedInEmailVacancy("4", "Java Backend", None, None, "https://www.linkedin.com/jobs/view/4/", None, "m", None, ContentCompleteness.MINIMAL),
        ]

    monkeypatch.setattr(module, "parse_linkedin_email", fake_parser)
    monkeypatch.setattr(
        module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "allowed",
                "normalized_title": title.lower(),
                "positive_rules": [],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    analyzer = _FakeAnalyzer()
    collector = LinkedInEmailCollector(
        email_client=_FakeEmailClient(messages=[_raw_message("<m1>"), _raw_message("<m2>")]),
        analyzer=analyzer,
        seen_jobs=_FakeSeenJobs(),
    )

    report = collector.collect_and_analyze(limit=3, dry_run=False)
    assert report.vacancies_extracted == 5
    assert report.unique_vacancies == 3
    assert report.new_vacancies == 3
    assert report.analyzed == 3
