from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.watchers.greenhouse import GreenhouseCompanyError, GreenhouseWatchResult
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)

MINIMAL_CONFIG = """
companies:
  - name: Agoda
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: greenhouse
    ats: greenhouse
    job_board_url: https://job-boards.greenhouse.io/agoda
    role_keywords: [java, backend]
  - name: Qonto
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: lever
    ats: lever
    job_board_url: https://jobs.lever.co/qonto
    role_keywords: [backend]
  - name: Canonical
    priority: A
    language: english
    relocation_status: remote_global
    watcher_type: greenhouse
    ats: greenhouse
    job_board_url: https://job-boards.greenhouse.io/canonical
    role_keywords: [backend]
"""


def _vacancy(
    *,
    external_id: str = "101",
    title: str = "Java Backend Engineer",
    company: str = "Agoda",
    location: str = "Bangkok",
    description: str = "Java backend services",
) -> NormalizedVacancy:
    return NormalizedVacancy(
        source=f"target_company:greenhouse:{company.lower()}",
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        employment="Full-time",
        description=description,
        url=f"https://job-boards.greenhouse.io/{company.lower()}/jobs/{external_id}",
        published_at="2026-09-05T10:00:00Z",
    )


def _evaluation(
    *,
    decision: Decision = Decision.STRONG_MATCH,
    match_percentage: float | None = 86.0,
    matched_points: list[str] | None = None,
    decision_reason: str = "Java backend match",
    summary: str = "Strong Java backend role",
) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary=summary,
        decision_reason=decision_reason,
        matched_points=matched_points if matched_points is not None else ["java", "backend"],
        gaps=[],
        nuances=[],
        match_percentage=match_percentage,
        matched_score=12.0,
        total_possible_score=14.0,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return config_file


def _fake_watcher(result: GreenhouseWatchResult, captured: dict[str, object] | None = None):
    captured = {} if captured is None else captured

    class FakeWatcher:
        def __init__(self, **kwargs: object) -> None:
            captured["init_kwargs"] = kwargs

        def watch(self, companies: object) -> GreenhouseWatchResult:
            captured["companies"] = companies
            return result

    return FakeWatcher


def _fake_analyzer(evaluation: VacancyEvaluation | list[VacancyEvaluation], captured: dict[str, object]):
    remaining = [evaluation] if isinstance(evaluation, VacancyEvaluation) else list(evaluation)

    class FakeAnalyzer:
        def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
            captured.setdefault("texts", [])
            captured["texts"].append(vacancy_text)
            captured.setdefault("completeness", [])
            captured["completeness"].append(content_completeness)
            if remaining:
                return remaining.pop(0)
            return _evaluation()

    return FakeAnalyzer()


def _patch_runtime(
    monkeypatch,
    *,
    watch_result: GreenhouseWatchResult,
    evaluation: VacancyEvaluation | list[VacancyEvaluation] | None = None,
    captured: dict[str, object] | None = None,
) -> dict[str, object]:
    captured = {} if captured is None else captured
    monkeypatch.setattr(
        cli_module,
        "GreenhouseTargetWatcher",
        _fake_watcher(watch_result, captured),
    )
    if evaluation is not None:
        analyzer = _fake_analyzer(evaluation, captured)
        monkeypatch.setattr(cli_module, "Settings", lambda: object())
        monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: analyzer)
    return captured


def _invoke(config_file: Path, *extra: str):
    return CliRunner().invoke(
        cli_module.app,
        ["analyze-target-companies-greenhouse", "--config", str(config_file), *extra],
    )


def test_command_runs_watcher_and_analyzer(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancy = _vacancy()
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[vacancy], errors=[], raw_fetched=5),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [item.name for item in companies] == ["Agoda", "Qonto", "Canonical"]
    assert captured["texts"]
    assert captured["texts"][0].startswith("Title: Java Backend Engineer")
    assert captured["completeness"] == ["FULL"]
    assert "[STRONG_MATCH] 86 | [FEASIBILITY: UNCLEAR]" in result.output
    assert "Agoda - Java Backend Engineer" in result.output
    assert "Location: Bangkok" in result.output
    assert "Matched: java, backend" in result.output
    assert "Reasons: Java backend match" in result.output
    assert "Feasibility: visa/relocation not mentioned; check manually" in result.output
    assert "URL: https://job-boards.greenhouse.io/agoda/jobs/101" in result.output


def test_limit_restricts_analyzed_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Java Backend Engineer"),
        _vacancy(external_id="202", title="Platform Engineer"),
        _vacancy(external_id="303", title="Payments Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--limit", "2")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 2
    assert "Analyzing 2 vacancies (analyze-limit: 2)" in result.output
    assert "Vacancies analyzed: 2" in result.output
    assert "Payments Engineer" not in result.output


def test_analyze_limit_restricts_analyzed_vacancies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Java Backend Engineer"),
        _vacancy(external_id="202", title="Platform Engineer"),
        _vacancy(external_id="303", title="Payments Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--analyze-limit", "2")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 2
    assert "Analyzing 2 vacancies (analyze-limit: 2)" in result.output
    assert "Payments Engineer" not in result.output


def test_company_filter_selects_named_companies(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[_vacancy()], errors=[], raw_fetched=1),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--company", "agoda")

    assert result.exit_code == 0
    companies = captured["companies"]
    assert isinstance(companies, list)
    assert [item.name for item in companies] == ["Agoda"]
    assert "Selected companies: 1" in result.output
    assert "Greenhouse companies: 1" in result.output


def test_summary_includes_analyzed_and_decision_counts(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", company="Agoda", title="Java Backend Engineer"),
        _vacancy(external_id="202", company="Canonical", title="Platform Engineer"),
    ]
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=10),
        evaluation=[
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=86.0),
            _evaluation(
                decision=Decision.POTENTIAL_MATCH,
                match_percentage=54.0,
                matched_points=["backend"],
            ),
        ],
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Companies in config: 3" in result.output
    assert "Greenhouse companies: 2" in result.output
    assert "Raw vacancies fetched: 10" in result.output
    assert "Vacancies after prefilter: 2" in result.output
    assert "Analyzing 2 vacancies (analyze-limit: 30)" in result.output
    assert "Vacancies analyzed: 2" in result.output
    assert "Results shown: 2" in result.output
    assert "Watcher errors: 0" in result.output
    assert "Decisions after filters:" not in result.output
    assert "STRONG_MATCH: 1" in result.output
    assert "POTENTIAL_MATCH: 1" in result.output
    assert "IGNORE: 0" in result.output
    assert "Feasibility:" in result.output
    assert "UNCLEAR:" in result.output
    assert "Agoda: 1" in result.output
    assert "Canonical: 1" in result.output


def test_output_writes_utf8_json_results(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    output_file = tmp_path / "analysis.json"
    vacancy = _vacancy(title="Senior Java \u6c49 Backend")
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[vacancy],
            errors=[
                GreenhouseCompanyError(
                    company_name="Adyen",
                    message="Greenhouse board 'adyen' request failed (500).",
                    slug="adyen",
                    status_code=500,
                    attempts=3,
                )
            ],
            raw_fetched=1,
        ),
        evaluation=_evaluation(matched_points=["java", "backend"]),
    )

    result = _invoke(config_file, "--output", str(output_file))

    assert result.exit_code == 0
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["vacancies"][0]["company"] == "Agoda"
    assert payload["vacancies"][0]["title"] == "Senior Java \u6c49 Backend"
    assert payload["vacancies"][0]["location"] == "Bangkok"
    assert payload["vacancies"][0]["url"] == vacancy.url
    assert payload["vacancies"][0]["source"] == vacancy.source
    assert payload["vacancies"][0]["external_id"] == "101"
    assert payload["vacancies"][0]["decision"] == "STRONG_MATCH"
    assert payload["vacancies"][0]["score"] == 86.0
    assert payload["vacancies"][0]["application_feasibility"] == "UNCLEAR"
    assert payload["vacancies"][0]["visa_sponsorship"] == "unknown"
    assert payload["vacancies"][0]["relocation_support"] == "unknown"
    assert payload["vacancies"][0]["remote_type"] == "unknown"
    assert payload["vacancies"][0]["work_authorization_requirement"] == "unknown"
    assert "feasibility_warnings" in payload["vacancies"][0]
    assert payload["vacancies"][0]["decision_reason"] == "Java backend match"
    assert payload["vacancies"][0]["reasons"]["matched"] == ["java", "backend"]
    assert payload["vacancies"][0]["reasons"]["decision_reason"] == "Java backend match"
    assert payload["errors"][0]["company_name"] == "Adyen"
    assert payload["errors"][0]["status_code"] == 500
    assert payload["errors"][0]["attempts"] == 3
    raw = output_file.read_bytes()
    assert "\\u6c49" not in raw.decode("utf-8")
    assert "\u6c49" in raw.decode("utf-8")


def test_watcher_errors_are_shown_without_failing(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[_vacancy()],
            errors=[
                GreenhouseCompanyError(
                    company_name="Adyen",
                    message="Greenhouse board 'adyen' request failed (404).",
                )
            ],
            raw_fetched=1,
        ),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies analyzed: 1" in result.output
    assert "Watcher errors: 1" in result.output
    assert "Adyen: Greenhouse board 'adyen' request failed (404)." in result.output


def test_missing_config_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    result = _invoke(missing)
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "missing" in result.output.lower()


def test_unknown_company_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[], errors=[]),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--company", "NoSuchCorp")

    assert result.exit_code != 0
    assert "Unknown company: NoSuchCorp" in result.output


def test_unicode_in_title_and_reasons_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[
                _vacancy(
                    title="Senior Java \u6c49 Backend",
                    location="M\u00fcnchen",
                )
            ],
            errors=[],
            raw_fetched=1,
        ),
        evaluation=_evaluation(
            matched_points=["java", "backend", "M\u00fcnchen"],
            decision_reason="Relocation to M\u00fcnchen",
        ),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies analyzed: 1" in result.output
    assert "Senior Java" in result.output
    assert "Matched:" in result.output
    assert "Feasibility:" in result.output


def test_feasibility_filter_shows_only_selected_values(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(
            external_id="101",
            title="Sponsored Java Engineer",
            description="Java backend. Visa sponsorship is available.",
            location="Amsterdam",
        ),
        _vacancy(
            external_id="202",
            title="Local Only Engineer",
            description="Must be authorized to work in the United States. We cannot sponsor visas.",
            location="Chicago",
        ),
        _vacancy(
            external_id="303",
            title="Unclear Java Engineer",
            description="Java backend services.",
            location="Sao Jose dos Campos",
        ),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=[
            _evaluation(match_percentage=90.0),
            _evaluation(match_percentage=88.0),
            _evaluation(match_percentage=80.0),
        ],
    )

    result = _invoke(config_file, "--feasibility", "LIKELY", "--feasibility", "UNCLEAR")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 3
    assert "Sponsored Java Engineer" in result.output
    assert "Unclear Java Engineer" in result.output
    assert "Local Only Engineer" not in result.output
    assert "Vacancies analyzed: 3" in result.output
    assert "Results shown: 2" in result.output
    assert "Feasibility after filters:" in result.output


def test_output_includes_feasibility_fields(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    output_file = tmp_path / "analysis.json"
    vacancy = _vacancy(
        title="Java Engineer",
        description="Relocation package provided. Visa sponsorship is available.",
        location="Amsterdam",
    )
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=[vacancy], errors=[], raw_fetched=1),
        evaluation=_evaluation(),
    )

    result = _invoke(config_file, "--output", str(output_file))

    assert result.exit_code == 0
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    item = payload["vacancies"][0]
    assert item["application_feasibility"] == "LIKELY"
    assert item["visa_sponsorship"] == "yes"
    assert item["relocation_support"] == "yes"
    assert item["remote_type"] == "unknown"
    assert item["work_authorization_requirement"] == "unknown"
    assert item["language_requirements"] == []
    assert "feasibility_warnings" in item


def test_unicode_in_feasibility_warnings_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[
                _vacancy(
                    title="Senior Java \u6c49 Backend",
                    location="M\u00fcnchen",
                    description="Java backend services.",
                )
            ],
            errors=[],
            raw_fetched=1,
        ),
        evaluation=_evaluation(
            matched_points=["java", "backend", "M\u00fcnchen"],
            decision_reason="Relocation to M\u00fcnchen",
        ),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Vacancies analyzed: 1" in result.output
    assert "Senior Java" in result.output
    assert "Feasibility:" in result.output


def test_sort_by_score_descending(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Low Score Engineer"),
        _vacancy(external_id="202", title="High Score Engineer"),
        _vacancy(external_id="303", title="Mid Score Engineer"),
    ]
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=[
            _evaluation(decision=Decision.POTENTIAL_MATCH, match_percentage=40.0, matched_points=["backend"]),
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=91.0, matched_points=["java"]),
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=70.0, matched_points=["java"]),
        ],
    )

    result = _invoke(config_file, "--sort-by", "score")

    assert result.exit_code == 0
    high = result.output.index("High Score Engineer")
    mid = result.output.index("Mid Score Engineer")
    low = result.output.index("Low Score Engineer")
    assert high < mid < low


def test_decision_filter_shows_only_selected_decisions(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="Strong Java Engineer"),
        _vacancy(external_id="202", title="Ignored Data Engineer"),
        _vacancy(external_id="303", title="Potential Platform Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=[
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=90.0),
            _evaluation(
                decision=Decision.IGNORE,
                match_percentage=82.0,
                matched_points=["java"],
                decision_reason="Primary role is data platform, not Java backend",
            ),
            _evaluation(decision=Decision.POTENTIAL_MATCH, match_percentage=55.0, matched_points=["backend"]),
        ],
    )

    result = _invoke(
        config_file,
        "--decision",
        "STRONG_MATCH",
        "--decision",
        "POTENTIAL_MATCH",
    )

    assert result.exit_code == 0
    assert len(captured["texts"]) == 3
    assert "Strong Java Engineer" in result.output
    assert "Potential Platform Engineer" in result.output
    assert "Ignored Data Engineer" not in result.output
    assert "Vacancies analyzed: 3" in result.output
    assert "Results shown: 2" in result.output
    assert "Decisions after filters:" in result.output


def test_min_score_filters_results(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="High Score Engineer"),
        _vacancy(external_id="202", title="Low Score Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=2),
        evaluation=[
            _evaluation(match_percentage=88.0),
            _evaluation(decision=Decision.POTENTIAL_MATCH, match_percentage=40.0, matched_points=["backend"]),
        ],
    )

    result = _invoke(config_file, "--min-score", "70")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 2
    assert "High Score Engineer" in result.output
    assert "Low Score Engineer" not in result.output
    assert "Results shown: 1" in result.output
    assert "Decisions after filters:" in result.output


def test_output_filters_apply_after_analysis(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    vacancies = [
        _vacancy(external_id="101", title="First Engineer"),
        _vacancy(external_id="202", title="Second Engineer"),
        _vacancy(external_id="303", title="Third Engineer"),
    ]
    captured = _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=3),
        evaluation=[
            _evaluation(decision=Decision.IGNORE, match_percentage=80.0, matched_points=["java"]),
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=92.0),
            _evaluation(decision=Decision.POTENTIAL_MATCH, match_percentage=50.0, matched_points=["backend"]),
        ],
    )

    result = _invoke(config_file, "--analyze-limit", "3", "--decision", "STRONG_MATCH", "--min-score", "90")

    assert result.exit_code == 0
    assert len(captured["texts"]) == 3
    assert "Second Engineer" in result.output
    assert "First Engineer" not in result.output
    assert "Third Engineer" not in result.output
    assert "Vacancies analyzed: 3" in result.output
    assert "Results shown: 1" in result.output


def test_output_writes_filtered_results(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    output_file = tmp_path / "analysis.json"
    vacancies = [
        _vacancy(external_id="101", title="Keep This Role"),
        _vacancy(external_id="202", title="Drop This Role"),
    ]
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(vacancies=vacancies, errors=[], raw_fetched=2),
        evaluation=[
            _evaluation(decision=Decision.STRONG_MATCH, match_percentage=91.0),
            _evaluation(decision=Decision.IGNORE, match_percentage=20.0, matched_points=["java"]),
        ],
    )

    result = _invoke(config_file, "--decision", "STRONG_MATCH", "--output", str(output_file))

    assert result.exit_code == 0
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert [item["title"] for item in payload["vacancies"]] == ["Keep This Role"]
    assert payload["vacancies"][0]["score"] == 91.0
    assert payload["vacancies"][0]["decision"] == "STRONG_MATCH"
    assert "company" in payload["vacancies"][0]
    assert "url" in payload["vacancies"][0]
    assert "source" in payload["vacancies"][0]
    assert "external_id" in payload["vacancies"][0]
    assert "reasons" in payload["vacancies"][0]
    assert "Wrote 1 analyzed vacancies" in result.output


def test_ignore_prints_rejected_because(tmp_path: Path, monkeypatch) -> None:
    config_file = _write_config(tmp_path)
    _patch_runtime(
        monkeypatch,
        watch_result=GreenhouseWatchResult(
            vacancies=[_vacancy(title="Data Platform Engineer")],
            errors=[],
            raw_fetched=1,
        ),
        evaluation=_evaluation(
            decision=Decision.IGNORE,
            match_percentage=82.0,
            matched_points=["java", "postgresql"],
            decision_reason="Primary role is data platform, not Java backend",
        ),
    )

    result = _invoke(config_file)

    assert result.exit_code == 0
    assert "Matched: java, postgresql" in result.output
    assert "Rejected because: Primary role is data platform, not Java backend" in result.output
    assert "[IGNORE] 82 | [FEASIBILITY: UNCLEAR]" in result.output
