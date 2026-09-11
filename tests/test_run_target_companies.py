from pathlib import Path

from typer.testing import CliRunner

import app.cli as cli_module
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.application_recommendation import ApplicationRecommendation
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.company_watch.watchers.greenhouse import GreenhouseWatchResult
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


def _write_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "target_companies.yaml"
    config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
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
    config_file = _write_config(tmp_path)
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
