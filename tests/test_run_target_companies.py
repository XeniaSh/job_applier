from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.greenhouse_collector import greenhouse_job_to_normalized
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.application_recommendation import ApplicationRecommendation
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.company_watch.watchers.greenhouse import GreenhouseWatchResult
from app.company_watch.watchers.lever import LeverWatchResult
from app.company_watch.watchers.ashby import AshbyCompanyError, AshbyWatchResult
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import TelegramRequestError
from app.telegram.destinations import TelegramDestination
from app.telegram.models import TelegramMessageRef


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
  - name: JetBrains
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: greenhouse
    ats: greenhouse
    job_board_url: https://job-boards.greenhouse.io/jetbrains
    role_keywords: [java, backend]
"""

LEVER_COMPANY_CONFIG = """  - name: Loom
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: lever
    ats: lever
    job_board_url: https://jobs.lever.co/loom
    role_keywords: [java, backend]
"""

LEVER_ONLY_CONFIG = "companies:\n" + LEVER_COMPANY_CONFIG

ASHBY_COMPANY_CONFIG = """  - name: TravelPerk
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: ashby
    ats: ashby
    job_board_url: https://jobs.ashbyhq.com/Perk
    role_keywords: [java, backend]
"""

ASHBY_ONLY_CONFIG = "companies:\n" + ASHBY_COMPANY_CONFIG


def _constraints() -> CandidateConstraints:
    return CandidateConstraints(
        known_languages=["english", "russian"],
        requires_visa_sponsorship=True,
        open_to_relocation=True,
        open_to_remote_worldwide=True,
        target_seniority=["MID", "SENIOR"],
        stretch_seniority=["STAFF_PLUS"],
        excluded_seniority=["INTERN", "JUNIOR", "LEAD_MANAGER"],
    )


def _evaluation(decision: Decision = Decision.POTENTIAL_MATCH) -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=decision,
        summary="summary",
        decision_reason="Role is partially aligned with Java backend profile.",
        matched_points=["java"],
        gaps=[],
        nuances=[],
        match_percentage=80.0,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=True,
        recommended_resume=RecommendedResume.JAVA,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


def _vacancy(
    *,
    company: str = "Agoda",
    board: str | None = None,
    external_id: str = "101",
    title: str = "Java Backend Engineer",
    description: str = "Java backend services",
) -> NormalizedVacancy:
    slug = board or company.lower()
    return NormalizedVacancy(
        source=f"target_company:greenhouse:{slug}",
        external_id=external_id,
        title=title,
        company=company,
        location="Bangkok",
        employment="Full-time",
        description=description,
        url=f"https://job-boards.greenhouse.io/{slug}/jobs/{external_id}",
        published_at="2026-09-05T10:00:00Z",
    )


def _lever_vacancy(
    *,
    company: str = "Loom",
    slug: str | None = None,
    external_id: str = "501",
    title: str = "Java Backend Engineer",
    description: str = "Java backend services",
) -> NormalizedVacancy:
    site = slug or company.lower()
    return NormalizedVacancy(
        source=f"target_company:lever:{site}",
        external_id=external_id,
        title=title,
        company=company,
        location="Bangkok",
        employment="Full-time",
        description=description,
        url=f"https://jobs.lever.co/{site}/{external_id}",
        published_at="2026-09-05T10:00:00Z",
    )


def _ashby_vacancy(
    *,
    company: str = "TravelPerk",
    slug: str | None = None,
    external_id: str = "701",
    title: str = "Java Backend Engineer",
    description: str = "Java backend services",
    url: str | None = None,
) -> NormalizedVacancy:
    site = slug or "Perk"
    return NormalizedVacancy(
        source=f"target_company:ashby:{site.lower()}",
        external_id=external_id,
        title=title,
        company=company,
        location="Bangkok",
        employment="Full-time",
        description=description,
        url=url or f"https://jobs.ashbyhq.com/{site}/{external_id}",
        published_at="2026-09-05T10:00:00Z",
    )


def _feasibility() -> ApplicationFeasibility:
    return ApplicationFeasibility(
        label="UNCLEAR",
        visa_sponsorship="unknown",
        relocation_support="unknown",
        remote_type="unknown",
        work_authorization_requirement="unknown",
        language_requirements=[],
        location_restrictions=[],
        warnings=[],
    )


def _seniority() -> SeniorityClassification:
    return SeniorityClassification(label="SENIOR", reasons=["title has senior"])


def _write_config(
    tmp_path: Path,
    *,
    include_lever: bool = False,
    include_ashby: bool = False,
) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "target_companies.yaml"
    content = (
        MINIMAL_CONFIG
        + (LEVER_COMPANY_CONFIG if include_lever else "")
        + (ASHBY_COMPANY_CONFIG if include_ashby else "")
    )
    config_file.write_text(content, encoding="utf-8")
    return config_file


def _set_base_env(monkeypatch, tmp_path: Path, *, target_chat_id: str | None) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM__LINKEDIN_CHAT_ID", "111")
    monkeypatch.setenv("PIPELINE_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("TELEGRAM_POLL_INTERVAL_SECONDS", "1")
    if target_chat_id is None:
        monkeypatch.setenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", "")
    else:
        monkeypatch.setenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", target_chat_id)


def _fake_watcher(vacancies: list[NormalizedVacancy]):
    class FakeWatcher:
        def watch(self, companies: object) -> GreenhouseWatchResult:
            _ = companies
            return GreenhouseWatchResult(vacancies=list(vacancies), errors=[], raw_fetched=len(vacancies))

    return FakeWatcher()


def _fake_lever_watcher(vacancies: list[NormalizedVacancy]):
    class FakeLeverWatcher:
        def watch(self, companies: object) -> LeverWatchResult:
            _ = companies
            return LeverWatchResult(vacancies=list(vacancies), errors=[], raw_fetched=len(vacancies))

    return FakeLeverWatcher()


def _fake_ashby_watcher(vacancies: list[NormalizedVacancy], *, errors: list[object] | None = None):
    class FakeAshbyWatcher:
        def watch(self, companies: object) -> AshbyWatchResult:
            _ = companies
            return AshbyWatchResult(
                vacancies=list(vacancies),
                errors=list(errors or []),
                raw_fetched=len(vacancies),
            )

    return FakeAshbyWatcher()


class _CountingAnalyzer:
    def __init__(self, evaluation: VacancyEvaluation | None = None) -> None:
        self.calls: list[str] = []
        self.evaluation = evaluation or _evaluation()

    def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        _ = content_completeness
        self.calls.append(vacancy_text)
        return self.evaluation


class _FakeTelegram:
    def __init__(self, chat_id: str = "222", *, fail: bool = False) -> None:
        self.chat_id = chat_id
        self.fail = fail
        self.cards: list[object] = []

    def send_vacancy_card(self, card: object) -> TelegramMessageRef:
        if self.fail:
            raise TelegramRequestError("send failed")
        self.cards.append(card)
        return TelegramMessageRef(chat_id=self.chat_id, message_id=len(self.cards))


def _run_cycle(
    monkeypatch,
    tmp_path: Path,
    *,
    vacancies: list[NormalizedVacancy],
    lever_vacancies: list[NormalizedVacancy] | None = None,
    include_lever_company: bool = False,
    ashby_vacancies: list[NormalizedVacancy] | None = None,
    ashby_errors: list[object] | None = None,
    include_ashby_company: bool = False,
    analyzer: _CountingAnalyzer | None = None,
    telegram: _FakeTelegram | None = None,
    deliveries: TelegramDeliveryStorage | None = None,
    analyze_limit: int = 30,
    analyze_limit_per_company: int | None = 10,
    target_chat_id: str = "222",
    reset_sent_memory: bool = True,
) -> tuple[cli_module._TargetCompaniesCycleResult, _CountingAnalyzer, _FakeTelegram, TelegramDeliveryStorage]:
    if reset_sent_memory:
        cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()
    _set_base_env(monkeypatch, tmp_path, target_chat_id=target_chat_id)
    config_file = _write_config(
        tmp_path,
        include_lever=include_lever_company,
        include_ashby=include_ashby_company,
    )
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    analyzer = analyzer or _CountingAnalyzer()
    telegram = telegram or _FakeTelegram(chat_id=target_chat_id)
    deliveries = deliveries or TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=analyzer,
        deliveries=deliveries,
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        analyze_limit=analyze_limit,
        analyze_limit_per_company=analyze_limit_per_company,
        watcher=_fake_watcher(vacancies),
        lever_watcher=_fake_lever_watcher(lever_vacancies or []),
        ashby_watcher=_fake_ashby_watcher(ashby_vacancies or [], errors=ashby_errors),
        telegram_client=telegram,
    )
    return result, analyzer, telegram, deliveries


def test_linkedin_only_run_skips_target_companies_without_chat_id(monkeypatch, tmp_path: Path) -> None:
    _set_base_env(monkeypatch, tmp_path, target_chat_id=None)
    _write_config(tmp_path)
    watcher_calls: list[object] = []

    class ForbiddenWatcher:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("GreenhouseTargetWatcher should not run for LinkedIn-only")

        def watch(self, companies: object) -> GreenhouseWatchResult:
            watcher_calls.append(companies)
            raise AssertionError("watch should not run")

    def _poll_once(**kwargs):
        if int(kwargs.get("timeout", 0) or 0) == 0:
            return kwargs.get("offset"), 0
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_module, "GreenhouseTargetWatcher", ForbiddenWatcher)
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: _CountingAnalyzer())
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_prepare_requested_applications",
        lambda **kwargs: cli_module.PreparationRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", _poll_once)
    monkeypatch.setattr(
        cli_module,
        "evaluate_title",
        lambda title: type(
            "Gate",
            (),
            {
                "accepted": True,
                "reason": "ok",
                "normalized_title": title.lower(),
                "positive_rules": ["java"],
                "negative_rules": [],
                "decision": "PASS",
            },
        )(),
    )
    monkeypatch.setattr(
        cli_module,
        "LinkedInEmailCollector",
        lambda **kwargs: type(
            "L",
            (),
            {
                "SOURCE": "linkedin-email",
                "collect": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(
        cli_module,
        "GreenhouseCollector",
        lambda **kwargs: type("G", (), {"SOURCE": "greenhouse", "collect": lambda self: []})(),
    )
    monkeypatch.setattr(
        cli_module,
        "TelegramClient",
        lambda *args, **kwargs: type("T", (), {"send_vacancy_card": lambda self, card: None})(),
    )

    result = CliRunner().invoke(cli_module.app, ["run"])
    assert result.exit_code == 0
    assert watcher_calls == []
    assert "Target companies:" not in result.output


def test_enabled_cycle_uses_target_companies_chat_not_linkedin(monkeypatch, tmp_path: Path) -> None:
    created_chat_ids: list[str] = []

    class RecordingTelegram:
        def __init__(self, token: str, chat_id: str) -> None:
            created_chat_ids.append(str(chat_id))
            self.chat_id = str(chat_id)
            self.cards: list[object] = []

        def send_vacancy_card(self, card: object) -> TelegramMessageRef:
            self.cards.append(card)
            return TelegramMessageRef(chat_id=self.chat_id, message_id=len(self.cards))

        def close(self) -> None:
            return None

    _set_base_env(monkeypatch, tmp_path, target_chat_id="222")
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    monkeypatch.setattr(cli_module, "TelegramClient", RecordingTelegram)
    analyzer = _CountingAnalyzer()
    deliveries = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    vacancy = _vacancy()
    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=analyzer,
        deliveries=deliveries,
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        watcher=_fake_watcher([vacancy]),
    )

    assert result.enabled is True
    assert result.chat_id == "222"
    assert created_chat_ids == ["222"]
    assert cli_module._telegram_destination_chat_id(
        cli_module.Settings(),
        TelegramDestination.LINKEDIN,
    ) == "111"
    assert deliveries.get_message_ref(
        source=vacancy.source,
        external_id=vacancy.external_id,
        chat_id="222",
    ) is not None
    assert deliveries.get_message_ref(
        source=vacancy.source,
        external_id=vacancy.external_id,
        chat_id="111",
    ) is None


def test_delivery_dedup_happens_before_ranking(monkeypatch, tmp_path: Path) -> None:
    delivered = _vacancy(external_id="1", title="Java Backend Engineer")
    remaining = _vacancy(external_id="2", title="Office Coordinator")
    deliveries = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    deliveries.save_sent(
        source=delivered.source,
        external_id=delivered.external_id,
        chat_id="222",
        message_id=9,
    )
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[delivered, remaining],
        analyzer=analyzer,
        deliveries=deliveries,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_delivered == 1
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert "Office Coordinator" in analyzer.calls[0]
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "2"


def test_cached_skip_is_excluded_before_ranking(monkeypatch, tmp_path: Path) -> None:
    skip_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    remaining = _vacancy(external_id="2", title="Office Coordinator")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        skip_vacancy,
        evaluation=_evaluation(Decision.IGNORE),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="SKIP", reasons=["technical decision is IGNORE"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[skip_vacancy, remaining],
        analyzer=analyzer,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_cached_skip == 1
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert "Office Coordinator" in analyzer.calls[0]
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "2"


def test_unconfirmed_application_form_is_excluded_before_ranking(monkeypatch, tmp_path: Path) -> None:
    # Discovered via Greenhouse (source=target_company:greenhouse:elastic), but
    # the apply URL is a custom-domain embed, not Greenhouse's own hosted
    # job-board shape -- see app.application.autofill.greenhouse_url.
    unconfirmed = NormalizedVacancy(
        source="target_company:greenhouse:elastic",
        external_id="1",
        title="Java Backend Engineer",
        company="Elastic",
        location="Remote",
        employment="Full-time",
        description="Java backend services",
        url="https://jobs.elastic.co/jobs?gh_jid=1",
        published_at="2026-09-05T10:00:00Z",
    )
    remaining = _vacancy(external_id="2", title="Office Coordinator")
    analyzer = _CountingAnalyzer()
    cache_path = tmp_path / "analysis_cache.json"
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[unconfirmed, remaining],
        analyzer=analyzer,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 1
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert "Office Coordinator" in analyzer.calls[0]
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "2"

    # Never recorded as SKIP, delivered, or any other recommendation state.
    reloaded_cache = TargetCompanyAnalysisCache(cache_path)
    reloaded_cache.load()
    assert reloaded_cache.get(unconfirmed) is None
    assert reloaded_cache.get_by_identity("target_company:greenhouse:elastic", "1") is None


def test_greenhouse_custom_domain_drop_emits_grouped_diagnostic(monkeypatch, tmp_path: Path, capsys) -> None:
    unconfirmed = NormalizedVacancy(
        source="target_company:greenhouse:elastic",
        external_id="1",
        title="Java Backend Engineer",
        company="Elastic",
        location="Remote",
        employment="Full-time",
        description="Java backend services -- must never appear in logs",
        url="https://jobs.elastic.co/jobs?gh_jid=1",
        published_at="2026-09-05T10:00:00Z",
    )
    result, _, _, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[unconfirmed],
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 1
    out = capsys.readouterr().out
    log_lines = [line for line in out.splitlines() if "Target companies: unsupported_form " in line]
    assert len(log_lines) == 1
    line = log_lines[0]
    assert "provider=greenhouse" in line
    assert "source_company=elastic" in line
    assert "source=target_company:greenhouse:elastic" in line
    assert "external_id=1" in line
    assert "reason=greenhouse_custom_domain" in line
    assert "url_host=jobs.elastic.co" in line
    assert "url_path=/jobs" in line
    assert "Java backend services" not in out


def test_lever_non_canonical_url_drop_emits_grouped_diagnostic(monkeypatch, tmp_path: Path, capsys) -> None:
    unconfirmed = NormalizedVacancy(
        source="target_company:lever:loom",
        external_id="1",
        title="Java Backend Engineer",
        company="Loom",
        location="Remote",
        employment="Full-time",
        description="Java backend services -- must never appear in logs",
        url="https://jobs.loom.com/apply?posting=1",
        published_at="2026-09-05T10:00:00Z",
    )
    result, _, _, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        lever_vacancies=[unconfirmed],
        include_lever_company=True,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 1
    out = capsys.readouterr().out
    log_lines = [line for line in out.splitlines() if "Target companies: unsupported_form " in line]
    assert len(log_lines) == 1
    line = log_lines[0]
    assert "provider=lever" in line
    assert "source_company=loom" in line
    assert "source=target_company:lever:loom" in line
    assert "external_id=1" in line
    assert "reason=lever_custom_domain" in line
    assert "url_host=jobs.loom.com" in line
    assert "url_path=/apply" in line
    assert "Java backend services" not in out


def test_supported_canonical_urls_do_not_emit_unsupported_form_diagnostic(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    gh_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    lever_vacancy = _lever_vacancy(external_id="501", title="Kotlin Backend Engineer")
    result, _, _, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[gh_vacancy],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
    )

    assert result.dropped_unsupported_form == 0
    out = capsys.readouterr().out
    assert "Target companies: unsupported_form " not in out


def test_custom_domain_greenhouse_vacancy_from_verified_board_passes_supported_form_gate(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    # Regression: a Target Company Greenhouse vacancy whose API absolute_url
    # points to the company's own custom careers domain (Stripe/Databricks/
    # Roblox-style) must not be dropped -- the watcher/collector rewrite it to
    # the canonical job-boards.greenhouse.io URL before it reaches this gate.
    cases = [
        ("stripe", 555001, "https://stripe.com/jobs/listing/staff-backend-engineer/555001"),
        ("databricks", 555002, "https://www.databricks.com/company/careers/open-positions?gh_jid=555002"),
        ("roblox", 555003, "https://careers.roblox.com/jobs/555003-senior-software-engineer"),
    ]
    vacancies = [
        greenhouse_job_to_normalized(
            {
                "id": job_id,
                "title": "Senior Backend Engineer",
                "absolute_url": custom_url,
                "content": "<p>Java backend services</p>",
                "updated_at": "2026-09-05T10:00:00Z",
            },
            source=f"target_company:greenhouse:{board}",
            company=board.title(),
        )
        for board, job_id, custom_url in cases
    ]
    assert all(vacancy is not None for vacancy in vacancies)

    result, _, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=vacancies,
        analyze_limit=len(cases),
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 0
    assert result.selected == len(cases)
    assert {getattr(card, "source") for card in telegram.cards} == {vacancy.source for vacancy in vacancies}
    out = capsys.readouterr().out
    assert "Target companies: unsupported_form " not in out


def test_greenhouse_and_lever_watchers_are_combined(monkeypatch, tmp_path: Path) -> None:
    gh_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    lever_vacancy = _lever_vacancy(external_id="501", title="Kotlin Backend Engineer")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[gh_vacancy],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
        analyzer=analyzer,
    )

    assert result.watched == 2
    assert result.sent == 2
    assert {getattr(card, "source") for card in telegram.cards} == {gh_vacancy.source, lever_vacancy.source}
    assert deliveries.get_message_ref(
        source=lever_vacancy.source,
        external_id=lever_vacancy.external_id,
        chat_id="222",
    ) is not None
    assert deliveries.get_message_ref(
        source=gh_vacancy.source,
        external_id=gh_vacancy.external_id,
        chat_id="222",
    ) is not None


def test_lever_vacancy_with_canonical_hosted_url_passes_supported_form_gate(
    monkeypatch, tmp_path: Path
) -> None:
    lever_vacancy = _lever_vacancy(external_id="501", title="Java Backend Engineer")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
        analyzer=analyzer,
    )

    assert result.dropped_unsupported_form == 0
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "501"


def test_lever_unsupported_form_url_is_excluded_before_ranking(monkeypatch, tmp_path: Path) -> None:
    # Discovered via Lever (source=target_company:lever:loom), but the URL is
    # not Lever's own hosted job-board shape -- see
    # app.application.autofill.lever_url.is_canonical_lever_hosted_url.
    unconfirmed = NormalizedVacancy(
        source="target_company:lever:loom",
        external_id="1",
        title="Java Backend Engineer",
        company="Loom",
        location="Remote",
        employment="Full-time",
        description="Java backend services",
        url="https://jobs.loom.com/apply?posting=1",
        published_at="2026-09-05T10:00:00Z",
    )
    remaining = _lever_vacancy(external_id="2", title="Office Coordinator")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        lever_vacancies=[unconfirmed, remaining],
        include_lever_company=True,
        analyzer=analyzer,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 1
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert "Office Coordinator" in analyzer.calls[0]
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "2"


def test_greenhouse_unsupported_form_still_dropped_with_lever_configured(
    monkeypatch, tmp_path: Path
) -> None:
    unconfirmed = NormalizedVacancy(
        source="target_company:greenhouse:elastic",
        external_id="1",
        title="Java Backend Engineer",
        company="Elastic",
        location="Remote",
        employment="Full-time",
        description="Java backend services",
        url="https://jobs.elastic.co/jobs?gh_jid=1",
        published_at="2026-09-05T10:00:00Z",
    )
    lever_vacancy = _lever_vacancy(external_id="2", title="Office Coordinator")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[unconfirmed],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
        analyzer=analyzer,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 1
    assert result.selected == 1
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "2"


def test_cycle_enabled_with_only_lever_companies(monkeypatch, tmp_path: Path) -> None:
    _set_base_env(monkeypatch, tmp_path, target_chat_id="222")
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "target_companies.yaml"
    config_file.write_text(LEVER_ONLY_CONFIG, encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    analyzer = _CountingAnalyzer()
    telegram = _FakeTelegram(chat_id="222")
    deliveries = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    lever_vacancy = _lever_vacancy(external_id="1")

    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=analyzer,
        deliveries=deliveries,
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        watcher=_fake_watcher([]),
        lever_watcher=_fake_lever_watcher([lever_vacancy]),
        telegram_client=telegram,
    )

    assert result.enabled is True
    assert result.skip_reason is None
    assert result.sent == 1


def test_ashby_watcher_is_combined_with_greenhouse_and_lever(monkeypatch, tmp_path: Path) -> None:
    gh_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    lever_vacancy = _lever_vacancy(external_id="501", title="Kotlin Backend Engineer")
    ashby_vacancy = _ashby_vacancy(external_id="701", title="Go Backend Engineer")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[gh_vacancy],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
        ashby_vacancies=[ashby_vacancy],
        include_ashby_company=True,
        analyzer=analyzer,
    )

    assert result.watched == 3
    assert result.sent == 3
    assert {getattr(card, "source") for card in telegram.cards} == {
        gh_vacancy.source,
        lever_vacancy.source,
        ashby_vacancy.source,
    }
    assert deliveries.get_message_ref(
        source=ashby_vacancy.source,
        external_id=ashby_vacancy.external_id,
        chat_id="222",
    ) is not None


def test_ashby_vacancy_with_canonical_hosted_url_passes_supported_form_gate(
    monkeypatch, tmp_path: Path
) -> None:
    # Discovery through the Ashby watcher confirms only the discovery/delivery
    # identity of this URL shape, not autofill support -- see
    # app.application.autofill.ashby_url.is_canonical_ashby_hosted_url.
    ashby_vacancy = _ashby_vacancy(external_id="701", title="Java Backend Engineer")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        ashby_vacancies=[ashby_vacancy],
        include_ashby_company=True,
        analyzer=analyzer,
    )

    assert result.dropped_unsupported_form == 0
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "701"


def test_ashby_unsupported_form_url_is_excluded_before_ranking(monkeypatch, tmp_path: Path) -> None:
    # Discovered via Ashby (source=target_company:ashby:perk), but the URL is
    # not Ashby's own public job-board shape -- see
    # app.application.autofill.ashby_url.is_canonical_ashby_hosted_url. Must
    # stay gated/dropped, never treated as a SKIP recommendation.
    custom_domain = NormalizedVacancy(
        source="target_company:ashby:perk",
        external_id="1",
        title="Java Backend Engineer",
        company="TravelPerk",
        location="Remote",
        employment="Full-time",
        description="Java backend services",
        url="https://careers.travelperk.com/apply?job=1",
        published_at="2026-09-05T10:00:00Z",
    )
    non_canonical_path = NormalizedVacancy(
        source="target_company:ashby:perk",
        external_id="2",
        title="Kotlin Backend Engineer",
        company="TravelPerk",
        location="Remote",
        employment="Full-time",
        description="Kotlin backend services",
        url="https://jobs.ashbyhq.com/Perk",
        published_at="2026-09-05T10:00:00Z",
    )
    remaining = _ashby_vacancy(external_id="3", title="Office Coordinator")
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        ashby_vacancies=[custom_domain, non_canonical_path, remaining],
        include_ashby_company=True,
        analyzer=analyzer,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    assert result.dropped_unsupported_form == 2
    assert result.selected == 1
    assert len(analyzer.calls) == 1
    assert "Office Coordinator" in analyzer.calls[0]
    assert telegram.cards
    assert getattr(telegram.cards[0], "external_id") == "3"


def test_cycle_enabled_with_only_ashby_companies(monkeypatch, tmp_path: Path) -> None:
    _set_base_env(monkeypatch, tmp_path, target_chat_id="222")
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "target_companies.yaml"
    config_file.write_text(ASHBY_ONLY_CONFIG, encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    analyzer = _CountingAnalyzer()
    telegram = _FakeTelegram(chat_id="222")
    deliveries = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    ashby_vacancy = _ashby_vacancy(external_id="1")

    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=analyzer,
        deliveries=deliveries,
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        watcher=_fake_watcher([]),
        lever_watcher=_fake_lever_watcher([]),
        ashby_watcher=_fake_ashby_watcher([ashby_vacancy]),
        telegram_client=telegram,
    )

    assert result.enabled is True
    assert result.skip_reason is None
    assert result.sent == 1


def test_cycle_skips_when_no_supported_target_companies(monkeypatch, tmp_path: Path) -> None:
    _set_base_env(monkeypatch, tmp_path, target_chat_id="222")
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "target_companies.yaml"
    config_file.write_text("companies: []\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())

    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=_CountingAnalyzer(),
        deliveries=TelegramDeliveryStorage(db_path=tmp_path / "jobs.db"),
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        watcher=_fake_watcher([]),
        lever_watcher=_fake_lever_watcher([]),
    )

    assert result.enabled is False
    assert result.skip_reason == "no_supported_target_companies"


def test_lead_manager_skip_is_not_sent_while_senior_ic_is(monkeypatch, tmp_path: Path) -> None:
    lead = _vacancy(
        external_id="7044713",
        title="Lead Software Engineer - Back End (FinTech) (Bangkok based - Relocation provided)",
    )
    senior = _vacancy(
        company="JetBrains",
        board="jetbrains",
        external_id="2",
        title="Senior Java Backend Engineer",
    )
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[lead, senior],
        analyzer=analyzer,
    )

    assert result.sent == 1
    assert {getattr(card, "external_id") for card in telegram.cards} == {"2"}
    assert "7044713" not in {getattr(card, "external_id") for card in telegram.cards}


def test_cached_apply_now_and_check_manually_do_not_recall_llm(monkeypatch, tmp_path: Path) -> None:
    apply_now = _vacancy(external_id="1", title="Java Backend Engineer")
    check = _vacancy(company="JetBrains", board="jetbrains", external_id="2", title="Kotlin Backend Engineer")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        apply_now,
        evaluation=_evaluation(Decision.STRONG_MATCH),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="APPLY_NOW", reasons=["visa sponsorship is available"]),
        seniority=_seniority(),
    )
    cache.put(
        check,
        evaluation=_evaluation(Decision.POTENTIAL_MATCH),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="CHECK_MANUALLY", reasons=["unclear"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[apply_now, check],
        analyzer=analyzer,
    )

    assert result.cache_hits == 2
    assert result.cache_misses == 0
    assert analyzer.calls == []
    assert result.sent == 2
    assert {getattr(card, "external_id") for card in telegram.cards} == {"1", "2"}


def test_prefers_uncached_candidates_over_cached_when_capped(monkeypatch, tmp_path: Path) -> None:
    cached_first = _vacancy(external_id="1", title="Java Backend Engineer")
    cached_second = _vacancy(external_id="2", title="Java Backend Engineer")
    new_vacancy = _vacancy(external_id="3", title="Java Backend Engineer")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        cached_first,
        evaluation=_evaluation(Decision.STRONG_MATCH),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="APPLY_NOW", reasons=["visa sponsorship is available"]),
        seniority=_seniority(),
    )
    cache.put(
        cached_second,
        evaluation=_evaluation(Decision.POTENTIAL_MATCH),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="CHECK_MANUALLY", reasons=["unclear"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[cached_first, cached_second, new_vacancy],
        analyzer=analyzer,
        analyze_limit=2,
        analyze_limit_per_company=10,
    )

    # Cap only fits 2 of the 3 candidates. The never-analyzed vacancy must
    # win a slot over both cached ones so later cycles drain new vacancies
    # instead of reselecting the same cached subset every time.
    assert result.selected == 2
    assert result.cache_hits == 1
    assert result.cache_misses == 1
    assert len(analyzer.calls) == 1

    # A cached non-SKIP result stays eligible for its own slot (and thus for
    # Telegram delivery retry) once a slot remains -- it is deprioritized,
    # not excluded.
    sent_ids = {getattr(card, "external_id") for card in telegram.cards}
    assert sent_ids == {"1", "3"}


def test_cached_skip_still_hard_excluded_alongside_uncached_preference(monkeypatch, tmp_path: Path) -> None:
    skip_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    apply_now_vacancy = _vacancy(external_id="2", title="Java Backend Engineer")
    new_vacancy = _vacancy(external_id="3", title="Java Backend Engineer")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        skip_vacancy,
        evaluation=_evaluation(Decision.IGNORE),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="SKIP", reasons=["technical decision is IGNORE"]),
        seniority=_seniority(),
    )
    cache.put(
        apply_now_vacancy,
        evaluation=_evaluation(Decision.STRONG_MATCH),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="APPLY_NOW", reasons=["visa sponsorship is available"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer()
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[skip_vacancy, apply_now_vacancy, new_vacancy],
        analyzer=analyzer,
        analyze_limit=2,
        analyze_limit_per_company=10,
    )

    # SKIP is still dropped entirely before selection, unlike a cached
    # non-SKIP result which is merely deprioritized behind uncached
    # candidates -- both slots go to the two remaining candidates.
    assert result.dropped_cached_skip == 1
    assert result.selected == 2
    assert result.cache_hits == 1
    assert result.cache_misses == 1
    assert len(analyzer.calls) == 1
    sent_ids = {getattr(card, "external_id") for card in telegram.cards}
    assert sent_ids == {"2", "3"}


def test_failed_telegram_send_does_not_save_delivery_and_retries(monkeypatch, tmp_path: Path) -> None:
    vacancy = _vacancy()
    analyzer = _CountingAnalyzer()
    failing = _FakeTelegram(fail=True)
    result, analyzer, _, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[vacancy],
        analyzer=analyzer,
        telegram=failing,
    )

    assert result.sent == 0
    assert result.send_errors == 1
    assert result.cache_misses == 1
    assert deliveries.get_message_ref(
        source=vacancy.source,
        external_id=vacancy.external_id,
        chat_id="222",
    ) is None

    retry_telegram = _FakeTelegram()
    retry, analyzer, retry_telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[vacancy],
        analyzer=analyzer,
        telegram=retry_telegram,
        deliveries=deliveries,
    )
    assert retry.cache_hits == 1
    assert len(analyzer.calls) == 1
    assert retry.sent == 1
    assert retry.send_errors == 0
    assert deliveries.get_message_ref(
        source=vacancy.source,
        external_id=vacancy.external_id,
        chat_id="222",
    ) is not None


def test_changed_description_hash_reanalyzes_previous_skip(monkeypatch, tmp_path: Path) -> None:
    original = _vacancy(description="Java backend services")
    updated = _vacancy(description="Java backend services and Kafka")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        original,
        evaluation=_evaluation(Decision.IGNORE),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="SKIP", reasons=["technical decision is IGNORE"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer(_evaluation(Decision.POTENTIAL_MATCH))
    result, analyzer, telegram, _ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[updated],
        analyzer=analyzer,
    )

    assert result.dropped_cached_skip == 0
    assert result.cache_misses == 1
    assert len(analyzer.calls) == 1
    assert result.sent == 1
    assert telegram.cards


def test_same_external_id_on_different_boards_do_not_conflict(monkeypatch, tmp_path: Path) -> None:
    agoda = _vacancy(company="Agoda", board="agoda", external_id="12")
    jetbrains = _vacancy(company="JetBrains", board="jetbrains", external_id="12")
    result, _, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[agoda, jetbrains],
    )

    assert result.sent == 2
    assert deliveries.get_message_ref(source=agoda.source, external_id="12", chat_id="222") is not None
    assert deliveries.get_message_ref(source=jetbrains.source, external_id="12", chat_id="222") is not None
    sources = {getattr(card, "source") for card in telegram.cards}
    assert sources == {agoda.source, jetbrains.source}


def test_skip_recommendation_is_not_sent(monkeypatch, tmp_path: Path) -> None:
    vacancy = _vacancy(title="Junior Java Intern")
    analyzer = _CountingAnalyzer(_evaluation(Decision.IGNORE))
    result, _, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[vacancy],
        analyzer=analyzer,
    )
    assert result.sent == 0
    assert telegram.cards == []
    assert deliveries.get_message_ref(
        source=vacancy.source,
        external_id=vacancy.external_id,
        chat_id="222",
    ) is None


class _SaveSentFailsOnceStorage:
    def __init__(self, inner: TelegramDeliveryStorage, *, fail_external_id: str) -> None:
        self._inner = inner
        self.fail_external_id = fail_external_id
        self.save_sent_calls: list[str] = []

    def save_sent(self, **kwargs: object) -> None:
        external_id = str(kwargs.get("external_id") or "")
        self.save_sent_calls.append(external_id)
        if external_id == self.fail_external_id:
            raise OSError("disk full")
        self._inner.save_sent(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def test_successful_send_with_save_sent_failure_does_not_block_or_resend(monkeypatch, tmp_path: Path) -> None:
    cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()
    first = _vacancy(external_id="persist-1", title="Java Backend Engineer")
    second = _vacancy(
        company="JetBrains",
        board="jetbrains",
        external_id="persist-2",
        title="Kotlin Backend Engineer",
    )
    inner = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    deliveries = _SaveSentFailsOnceStorage(inner, fail_external_id="persist-1")
    telegram = _FakeTelegram()

    first_cycle, _, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[first, second],
        telegram=telegram,
        deliveries=deliveries,
    )

    assert first_cycle.sent == 2
    assert first_cycle.send_errors == 0
    assert {getattr(card, "external_id") for card in telegram.cards} == {"persist-1", "persist-2"}
    assert "persist-1" in deliveries.save_sent_calls
    assert "persist-2" in deliveries.save_sent_calls
    assert inner.get_message_ref(source=first.source, external_id="persist-1", chat_id="222") is None
    assert inner.get_message_ref(source=second.source, external_id="persist-2", chat_id="222") is not None

    second_cycle, _, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[first, second],
        telegram=telegram,
        deliveries=deliveries,
        reset_sent_memory=False,
    )

    assert second_cycle.dropped_delivered == 2
    assert second_cycle.sent == 0
    assert second_cycle.send_errors == 0
    assert len(telegram.cards) == 2
    assert inner.get_message_ref(source=first.source, external_id="persist-1", chat_id="222") is None


# --- TASK-089: provider-level funnel diagnostics -----------------------------


def test_provider_funnel_counts_greenhouse_and_lever_combined(monkeypatch, tmp_path: Path, capsys) -> None:
    gh_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    lever_vacancy = _lever_vacancy(external_id="501", title="Kotlin Backend Engineer")
    result, analyzer, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[gh_vacancy],
        lever_vacancies=[lever_vacancy],
        include_lever_company=True,
    )

    gh = result.provider_funnels["greenhouse"]
    lv = result.provider_funnels["lever"]
    assert gh.configured_companies == 2  # Agoda + JetBrains from MINIMAL_CONFIG
    assert gh.raw_fetched == 1
    assert gh.title_prefilter_pass == 1
    assert gh.watcher_errors == 0
    assert gh.post_gate_candidates == 1
    assert gh.selected == 1
    assert gh.analyzed == 1
    assert gh.sent == 1

    assert lv.configured_companies == 1  # Loom
    assert lv.raw_fetched == 1
    assert lv.title_prefilter_pass == 1
    assert lv.watcher_errors == 0
    assert lv.post_gate_candidates == 1
    assert lv.selected == 1
    assert lv.analyzed == 1
    assert lv.sent == 1

    out = capsys.readouterr().out
    gh_lines = [line for line in out.splitlines() if "Target companies funnel[greenhouse]:" in line]
    lv_lines = [line for line in out.splitlines() if "Target companies funnel[lever]:" in line]
    assert len(gh_lines) == 1
    assert len(lv_lines) == 1
    assert "configured=2" in gh_lines[0]
    assert "sent=1" in gh_lines[0]
    assert "configured=1" in lv_lines[0]
    assert "sent=1" in lv_lines[0]
    # Existing aggregate log line remains unchanged and present.
    assert any("Target companies: watched=" in line for line in out.splitlines())


def test_provider_funnel_counts_ashby(monkeypatch, tmp_path: Path, capsys) -> None:
    ashby_vacancy = _ashby_vacancy(external_id="701", title="Go Backend Engineer")
    result, analyzer, telegram, deliveries = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        ashby_vacancies=[ashby_vacancy],
        include_ashby_company=True,
    )

    ab = result.provider_funnels["ashby"]
    assert ab.configured_companies == 1  # TravelPerk
    assert ab.raw_fetched == 1
    assert ab.title_prefilter_pass == 1
    assert ab.watcher_errors == 0
    assert ab.post_gate_candidates == 1
    assert ab.selected == 1
    assert ab.analyzed == 1
    assert ab.sent == 1

    out = capsys.readouterr().out
    ab_lines = [line for line in out.splitlines() if "Target companies funnel[ashby]:" in line]
    assert len(ab_lines) == 1
    assert "configured=1" in ab_lines[0]
    assert "sent=1" in ab_lines[0]


def test_provider_funnel_counts_ashby_watcher_errors_without_leaking_details(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    result, *_ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[],
        ashby_vacancies=[],
        ashby_errors=[
            AshbyCompanyError(
                company_name="TravelPerk",
                message="super secret internal failure detail",
                response_snippet="<html>leaked-response-body</html>",
                status_code=500,
            )
        ],
        include_ashby_company=True,
    )

    ab = result.provider_funnels["ashby"]
    assert ab.configured_companies == 1
    assert ab.raw_fetched == 0
    assert ab.title_prefilter_pass == 0
    assert ab.watcher_errors == 1
    assert ab.post_gate_candidates == 0
    assert ab.selected == 0
    assert ab.analyzed == 0
    assert ab.sent == 0

    out = capsys.readouterr().out
    ab_lines = [line for line in out.splitlines() if "Target companies funnel[ashby]:" in line]
    assert len(ab_lines) == 1
    for line in ab_lines:
        assert "secret" not in line
        assert "leaked" not in line


def test_provider_funnel_zero_valued_when_provider_not_configured(monkeypatch, tmp_path: Path, capsys) -> None:
    gh_vacancy = _vacancy(external_id="1")
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[gh_vacancy])

    lv = result.provider_funnels["lever"]
    assert lv.configured_companies == 0
    assert lv.raw_fetched == 0
    assert lv.title_prefilter_pass == 0
    assert lv.watcher_errors == 0
    assert lv.post_gate_candidates == 0
    assert lv.selected == 0
    assert lv.analyzed == 0
    assert lv.sent == 0

    out = capsys.readouterr().out
    lv_lines = [line for line in out.splitlines() if "Target companies funnel[lever]:" in line]
    assert len(lv_lines) == 1
    assert "configured=0" in lv_lines[0]
    assert "sent=0" in lv_lines[0]


def test_provider_funnel_counts_watcher_errors_without_leaking_details(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from app.company_watch.watchers.greenhouse import GreenhouseCompanyError

    class ErrorWatcher:
        def watch(self, companies: object) -> GreenhouseWatchResult:
            _ = companies
            return GreenhouseWatchResult(
                vacancies=[],
                errors=[
                    GreenhouseCompanyError(
                        company_name="Agoda",
                        message="super secret internal failure detail",
                        response_snippet="<html>leaked-response-body</html>",
                    )
                ],
                raw_fetched=0,
            )

    _set_base_env(monkeypatch, tmp_path, target_chat_id="222")
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    result = cli_module._run_target_companies_cycle(
        settings=cli_module.Settings(),
        analyzer=_CountingAnalyzer(),
        deliveries=TelegramDeliveryStorage(db_path=tmp_path / "jobs.db"),
        config_path=config_file,
        cache_path=tmp_path / "analysis_cache.json",
        watcher=ErrorWatcher(),
        lever_watcher=_fake_lever_watcher([]),
    )

    gh = result.provider_funnels["greenhouse"]
    assert gh.configured_companies == 2
    assert gh.raw_fetched == 0
    assert gh.title_prefilter_pass == 0
    assert gh.watcher_errors == 1
    assert gh.post_gate_candidates == 0
    assert gh.selected == 0
    assert gh.analyzed == 0
    assert gh.sent == 0

    out = capsys.readouterr().out
    funnel_lines = [line for line in out.splitlines() if "Target companies funnel[" in line]
    assert len(funnel_lines) == 3  # one each for greenhouse, lever, ashby
    for line in funnel_lines:
        assert "secret" not in line
        assert "leaked" not in line
        assert "Agoda" not in line
        assert "html" not in line
        assert "gh_jid" not in line


def test_provider_funnel_post_gate_candidates_reflects_drops(monkeypatch, tmp_path: Path) -> None:
    delivered = _vacancy(external_id="1", title="Java Backend Engineer")
    remaining = _vacancy(external_id="2", title="Office Coordinator")
    deliveries = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    deliveries.save_sent(
        source=delivered.source,
        external_id=delivered.external_id,
        chat_id="222",
        message_id=9,
    )
    result, *_ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[delivered, remaining],
        deliveries=deliveries,
        analyze_limit=1,
        analyze_limit_per_company=1,
    )

    gh = result.provider_funnels["greenhouse"]
    assert gh.title_prefilter_pass == 2
    assert gh.post_gate_candidates == 1  # one dropped as already delivered
    assert gh.selected == 1
    assert gh.analyzed == 1
    assert gh.sent == 1


def test_provider_funnel_analyzed_excludes_analysis_errors(monkeypatch, tmp_path: Path) -> None:
    class _FailingAnalyzer:
        def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
            _ = vacancy_text, content_completeness
            raise RuntimeError("sensitive-analyzer-failure-detail")

    vacancy = _vacancy(external_id="1")
    result, *_ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[vacancy],
        analyzer=_FailingAnalyzer(),
    )

    assert result.analyzed == 0
    gh = result.provider_funnels["greenhouse"]
    assert gh.selected == 1
    assert gh.analyzed == 0
    assert gh.sent == 0


def test_provider_funnel_sent_only_counts_successful_delivery(monkeypatch, tmp_path: Path) -> None:
    vacancy = _vacancy(external_id="1")
    failing = _FakeTelegram(fail=True)
    result, *_ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[vacancy],
        telegram=failing,
    )

    gh = result.provider_funnels["greenhouse"]
    assert result.sent == 0
    assert result.send_errors == 1
    assert gh.analyzed == 1
    assert gh.sent == 0


# --- outcome diagnostics: aggregate, provider-independent counters ----------


def _outcome_log_line(out: str) -> str:
    lines = [line for line in out.splitlines() if "Target companies outcomes: " in line]
    assert len(lines) == 1
    return lines[0]


def _assert_no_private_values_leaked(line: str, *private_values: str) -> None:
    for value in private_values:
        assert value not in line


def test_outcome_counts_llm_ignore(monkeypatch, tmp_path: Path, capsys) -> None:
    vacancy = _vacancy(title="Very Secret Confidential Title", description="secret-description-xyz")
    analyzer = _CountingAnalyzer(_evaluation(Decision.IGNORE))
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[vacancy], analyzer=analyzer)

    assert result.outcome_counts.llm_ignore == 1
    assert result.outcome_counts.eligible == 0
    assert result.sent == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "llm_ignore=1" in line
    assert "eligible=0" in line
    _assert_no_private_values_leaked(line, "Very Secret Confidential Title", "secret-description-xyz", vacancy.url)


def test_outcome_counts_excluded_seniority(monkeypatch, tmp_path: Path, capsys) -> None:
    lead = _vacancy(title="Engineering Manager - Backend")
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[lead], analyzer=analyzer)

    assert result.outcome_counts.excluded_seniority == 1
    assert result.outcome_counts.eligible == 0
    assert result.sent == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "excluded_seniority=1" in line
    _assert_no_private_values_leaked(line, "Engineering Manager - Backend", lead.url)


def test_outcome_counts_missing_language(monkeypatch, tmp_path: Path, capsys) -> None:
    vacancy = _vacancy()

    def _fake_assess(**kwargs: object):
        _ = kwargs
        return ApplicationFeasibility(
            label="UNCLEAR",
            visa_sponsorship="unknown",
            relocation_support="unknown",
            remote_type="unknown",
            work_authorization_requirement="unknown",
            language_requirements=["german"],
            location_restrictions=[],
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "assess_application_feasibility", _fake_assess)
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[vacancy], analyzer=analyzer)

    assert result.outcome_counts.missing_languages == 1
    assert result.outcome_counts.eligible == 0
    assert result.sent == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "missing_languages=1" in line


def test_outcome_counts_work_authorization_blocker(monkeypatch, tmp_path: Path, capsys) -> None:
    vacancy = _vacancy()

    def _fake_assess(**kwargs: object):
        _ = kwargs
        return ApplicationFeasibility(
            label="UNCLEAR",
            visa_sponsorship="no",
            relocation_support="no",
            remote_type="unknown",
            work_authorization_requirement="required",
            language_requirements=[],
            location_restrictions=[],
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "assess_application_feasibility", _fake_assess)
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[vacancy], analyzer=analyzer)

    assert result.outcome_counts.work_auth == 1
    assert result.outcome_counts.eligible == 0
    assert result.sent == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "work_auth=1" in line


def test_outcome_counts_eligible_and_sent(monkeypatch, tmp_path: Path, capsys) -> None:
    vacancy = _vacancy(title="Senior Java Backend Engineer")
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[vacancy], analyzer=analyzer)

    assert result.sent == 1
    assert result.outcome_counts.eligible == 1
    assert result.outcome_counts.sent == 1
    assert result.outcome_counts.llm_ignore == 0
    assert result.outcome_counts.other_skip == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "eligible=1" in line
    assert "sent=1" in line


def test_outcome_counts_analysis_error(monkeypatch, tmp_path: Path, capsys) -> None:
    class _FailingAnalyzer:
        def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
            _ = vacancy_text, content_completeness
            raise RuntimeError("sensitive-analyzer-failure-detail")

    vacancy = _vacancy()
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[vacancy], analyzer=_FailingAnalyzer())

    assert result.outcome_counts.analysis_error == 1
    assert result.outcome_counts.eligible == 0
    assert result.sent == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "analysis_error=1" in line
    _assert_no_private_values_leaked(line, "sensitive-analyzer-failure-detail")


def test_outcome_counts_cached_skip_drop(monkeypatch, tmp_path: Path, capsys) -> None:
    skip_vacancy = _vacancy(external_id="1", title="Java Backend Engineer")
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.put(
        skip_vacancy,
        evaluation=_evaluation(Decision.IGNORE),
        feasibility=_feasibility(),
        recommendation=ApplicationRecommendation(label="SKIP", reasons=["technical decision is IGNORE"]),
        seniority=_seniority(),
    )
    cache.save()
    analyzer = _CountingAnalyzer()
    result, *_ = _run_cycle(monkeypatch, tmp_path, vacancies=[skip_vacancy], analyzer=analyzer)

    assert result.dropped_cached_skip == 1
    assert result.outcome_counts.cached_skip == 1
    # Dropped before analysis, so it must not double-count into any
    # analyzed-item bucket.
    assert result.outcome_counts.llm_ignore == 0
    assert result.outcome_counts.eligible == 0

    line = _outcome_log_line(capsys.readouterr().out)
    assert "cached_skip=1" in line


def test_outcome_counts_line_has_no_private_values(monkeypatch, tmp_path: Path, capsys) -> None:
    sent_vacancy = _vacancy(
        external_id="1",
        title="Senior Java Backend Engineer",
        description="Very secret job description body",
    )
    ignored_vacancy = _vacancy(
        company="JetBrains",
        board="jetbrains",
        external_id="2",
        title="Confidential Kotlin Role",
        description="another secret description",
    )
    analyzer = _CountingAnalyzer(_evaluation(Decision.STRONG_MATCH))

    call_count = {"n": 0}
    original_analyze = analyzer.analyze

    def _analyze(vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        call_count["n"] += 1
        if call_count["n"] == 2:
            return _evaluation(Decision.IGNORE)
        return original_analyze(vacancy_text, content_completeness)

    analyzer.analyze = _analyze  # type: ignore[method-assign]

    result, *_ = _run_cycle(
        monkeypatch,
        tmp_path,
        vacancies=[sent_vacancy, ignored_vacancy],
        analyzer=analyzer,
    )

    assert result.outcome_counts.eligible == 1
    assert result.outcome_counts.llm_ignore == 1

    line = _outcome_log_line(capsys.readouterr().out)
    _assert_no_private_values_leaked(
        line,
        "Senior Java Backend Engineer",
        "Confidential Kotlin Role",
        "Very secret job description body",
        "another secret description",
        sent_vacancy.url,
        ignored_vacancy.url,
    )
