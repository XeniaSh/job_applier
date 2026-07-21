import pytest

import app.cli as cli_module
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation


def _vacancy(*, source: str, external_id: str, title: str, url: str) -> cli_module.NormalizedVacancy:
    return cli_module.NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company="ACME",
        location="Remote",
        employment=None,
        description=title,
        url=url,
        published_at=None,
    )


def _evaluation(decision: Decision = Decision.POTENTIAL_MATCH) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        matched_points=["python"],
        gaps=[],
        nuances=[],
        match_percentage=80.0,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=1,
        evidence_sufficient=True,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def test_pipeline_merges_multiple_collectors_with_dedupe() -> None:
    collectors = [
        cli_module.RuntimeCollector(
            name="linkedin-email",
            collect_fn=lambda: [_vacancy(source="linkedin-email", external_id="1", title="Role", url="https://jobs.example.com/1?utm=123")],
        ),
        cli_module.RuntimeCollector(
            name="greenhouse",
            collect_fn=lambda: [_vacancy(source="greenhouse", external_id="2", title="Role", url="https://jobs.example.com/1")],
        ),
    ]
    result = cli_module._collect_pipeline_items(collectors=collectors, safe_error_formatter=lambda source, exc: f"{source}:{exc}")
    assert result.extracted("linkedin-email") == 1
    assert result.extracted("greenhouse") == 1
    assert result.merged_unique() == 2
    assert sum(1 for item in result.items if item.duplicate) == 0


def test_pipeline_reporting_is_derived_from_items() -> None:
    pipeline = cli_module.PipelineResult(
        items=[
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="1", title="Seen", url="https://jobs.example.com/1"),
                preanalysis_outcome="already_seen",
                already_seen=True,
            ),
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="2", title="Strong", url="https://jobs.example.com/2"),
                preanalysis_outcome="new",
                analysis_result=_evaluation(Decision.STRONG_MATCH),
                telegram_eligible=True,
                telegram_delivered=True,
            ),
        ]
    )
    assert pipeline.unique("linkedin-email") == 2
    assert pipeline.already_seen("linkedin-email") == 1
    assert pipeline.analyzed("linkedin-email") == 1
    assert pipeline.strong_total() == 1
    assert pipeline.eligible() == 1
    assert pipeline.sent() == 1


def test_pipeline_accounting_validation_balances() -> None:
    pipeline = cli_module.PipelineResult(
        items=[
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="1", title="Seen", url="https://jobs.example.com/1"),
                preanalysis_outcome="already_seen",
                already_seen=True,
            ),
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="2", title="Filtered", url="https://jobs.example.com/2"),
                preanalysis_outcome="prefiltered",
                title_filtered=True,
            ),
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="3", title="Analyzed", url="https://jobs.example.com/3"),
                preanalysis_outcome="new",
                analysis_result=_evaluation(Decision.POTENTIAL_MATCH),
            ),
        ]
    )
    pipeline.validate_accounting()

    broken = cli_module.PipelineResult(
        items=[
            cli_module.PipelineItem(
                source="linkedin-email",
                vacancy=_vacancy(source="linkedin-email", external_id="4", title="Broken", url="https://jobs.example.com/4"),
            )
        ]
    )
    with pytest.raises(AssertionError):
        broken.validate_accounting()


def test_pipeline_accepts_new_collector_without_code_changes(monkeypatch) -> None:
    pipeline = cli_module._collect_pipeline_items(
        collectors=[
            cli_module.RuntimeCollector(
                name="rss-feed",
                collect_fn=lambda: [_vacancy(source="rss-feed", external_id="1", title="RSS Role", url="https://rss.example.com/1")],
            )
        ],
        safe_error_formatter=lambda source, exc: f"{source}:{exc}",
    )

    monkeypatch.setattr(cli_module, "evaluate_title", lambda title: type("Gate", (), {"accepted": True, "reason": "allowed"})())
    analyzer = type("A", (), {"analyze": lambda self, *args, **kwargs: _evaluation(Decision.POTENTIAL_MATCH)})()
    seen_jobs = type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})()

    cli_module._analyze_pipeline_items(
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        pipeline=pipeline,
        limit=20,
        skip_seen=True,
        mark_seen=True,
    )
    assert pipeline.sources() == ["rss-feed"]
    assert pipeline.analyzed("rss-feed") == 1


def test_identical_external_ids_across_sources_do_not_collide() -> None:
    items = cli_module._collect_pipeline_items(
        collectors=[
            cli_module.RuntimeCollector(
                name="linkedin-email",
                collect_fn=lambda: [_vacancy(source="linkedin-email", external_id="123", title="A", url="https://a.example.com")],
            ),
            cli_module.RuntimeCollector(
                name="greenhouse",
                collect_fn=lambda: [_vacancy(source="greenhouse", external_id="123", title="B", url="https://b.example.com")],
            ),
        ],
        safe_error_formatter=lambda source, exc: f"{source}:{exc}",
    )
    assert items.merged_unique() == 2
    identities = [item.identity for item in items.items if item.vacancy is not None]
    assert "linkedin-email:123" in identities
    assert "greenhouse:123" in identities


def test_url_identity_used_when_external_id_missing() -> None:
    vacancy = _vacancy(source="linkedin-email", external_id="", title="Role", url="https://jobs.example.com/123?utm=1")
    identity = cli_module.vacancy_identity(vacancy)
    assert identity == "url:https://jobs.example.com/123"
    storage_key = cli_module._identity_storage_key(identity=identity, vacancy=vacancy)
    assert storage_key == ("url", "https://jobs.example.com/123")


def test_malformed_vacancies_are_visible_not_silently_dropped(monkeypatch) -> None:
    malformed = cli_module.NormalizedVacancy(
        source="linkedin-email",
        external_id="",
        title="",
        company=None,
        location=None,
        employment=None,
        description="",
        url="not-a-url",
        published_at=None,
    )
    pipeline = cli_module._collect_pipeline_items(
        collectors=[cli_module.RuntimeCollector(name="linkedin-email", collect_fn=lambda: [malformed])],
        safe_error_formatter=lambda source, exc: f"{source}:{exc}",
    )
    monkeypatch.setattr(cli_module, "evaluate_title", lambda title: type("Gate", (), {"accepted": True, "reason": "allowed"})())
    analyzer = type("A", (), {"analyze": lambda self, *args, **kwargs: _evaluation()})()
    seen_jobs = type("S", (), {"is_seen": lambda self, source, external_id: False, "mark_seen": lambda self, source, external_id: None})()
    cli_module._analyze_pipeline_items(
        analyzer=analyzer,
        seen_jobs=seen_jobs,
        pipeline=pipeline,
        limit=20,
        skip_seen=True,
        mark_seen=True,
    )
    assert pipeline.unique("linkedin-email") == 1
    assert pipeline.invalid_identity("linkedin-email") == 1
    assert pipeline.new("linkedin-email") == 0


def test_identity_drives_dedupe_and_seen_lookup(monkeypatch) -> None:
    vacancy = _vacancy(source="linkedin-email", external_id="", title="Role", url="https://jobs.example.com/777")
    pipeline = cli_module._collect_pipeline_items(
        collectors=[cli_module.RuntimeCollector(name="linkedin-email", collect_fn=lambda: [vacancy])],
        safe_error_formatter=lambda source, exc: f"{source}:{exc}",
    )
    monkeypatch.setattr(cli_module, "evaluate_title", lambda title: type("Gate", (), {"accepted": True, "reason": "allowed"})())
    calls: list[tuple[str, str]] = []

    class Seen:
        def is_seen(self, source, external_id):
            calls.append((source, external_id))
            return True

        def mark_seen(self, source, external_id):
            calls.append((source, external_id))

    analyzer = type("A", (), {"analyze": lambda self, *args, **kwargs: _evaluation()})()
    cli_module._analyze_pipeline_items(
        analyzer=analyzer,
        seen_jobs=Seen(),
        pipeline=pipeline,
        limit=20,
        skip_seen=True,
        mark_seen=True,
    )
    assert calls == [("url", "https://jobs.example.com/777")]
    assert pipeline.already_seen("linkedin-email") == 1
