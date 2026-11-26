from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.cli as cli
from app.collectors.habr_details import HabrDetail
from app.collectors.habr_discovery import HabrDiscovery, HabrDiscoveryConfig, habr_policy
from app.collectors.habr_rss import FeedDiagnostic, HabrRecord, HabrRSSResult
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.domain_policy import DomainPolicy
from app.config import Settings
from app.models import VacancyEvaluation
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import build_action_buttons, build_archived_buttons, map_code_to_source, map_source_to_code, parse_callback_data
from app.telegram.formatter import format_telegram_card_html

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc).timestamp()


def constraints():
    return CandidateConstraints(known_languages=["english", "russian"], requires_visa_sponsorship=True,
        open_to_relocation=True, open_to_remote_worldwide=True, target_seniority=["MID", "SENIOR"],
        excluded_seniority=["JUNIOR", "INTERN", "LEAD_MANAGER"])


def vacancy(job_id="1000000001", **updates):
    v = NormalizedVacancy("habr", job_id, "Senior Java Backend Developer", "Synthetic Example", "Алматы (Казахстан)",
        "full time", "Можно удалённо. Требуемые навыки: #senior, #Java, #Spring.",
        f"https://career.habr.com/vacancies/{job_id}", "2026-11-25T00:00:00+00:00", content_completeness="PARTIAL")
    return replace(v, **updates)


def evaluation(**updates):
    return VacancyEvaluation(decision="STRONG_MATCH", summary="synthetic technical match", recommended_resume="java",
                             recommended_cover_template="generic", **updates)


class Analyzer:
    def __init__(self):
        self.calls = []
    def analyze(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return evaluation()


class FakeDetails:
    def __init__(self, *args, **kwargs):
        self.requests = self.cache_hits = 0
    def fetch(self, url, *, allow_network=True):
        if not allow_network:
            return HabrDetail(status="budget_limit")
        self.requests += 1
        return HabrDetail(text="Разработка Java backend. Spring, Kafka, PostgreSQL. Можно удалённо. " * 4,
                          completeness="FULL", status="text", checked_at=NOW)


def service(tmp_path, *, items=None, was_sent=lambda v: False, clock=lambda: NOW, detail_factory=FakeDetails, **config):
    items = items if items is not None else [vacancy()]
    calls = []
    def rss_fetch(_):
        calls.append(True)
        return HabrRSSResult([HabrRecord(v, None, "Можно удалённо", ("senior",)) for v in items],
                             [FeedDiagnostic(0, pages=1, raw=len(items), stop="empty", status=200)])
    adapter = HabrDiscovery(HabrDiscoveryConfig(state_dir=tmp_path, **config), was_sent=was_sent, clock=clock,
        rss_fetch=rss_fetch, detail_factory=detail_factory, constraints=constraints())
    adapter.rss_calls = calls
    return adapter


class Seen:
    def is_seen(self, *args):
        return True  # stale seen cannot lose an unsent Habr card
    def mark_seen(self, *args):
        pytest.fail("Habr uses durable technical cache and delivery history, not premature seen marks")


class Delivery:
    def __init__(self):
        self.sent = set()
    def was_sent(self, source, external_id, chat_id="test"):
        return external_id in self.sent
    def save_sent(self, **kwargs):
        self.sent.add(kwargs["external_id"])
    def mark_history_status(self, **kwargs):
        pass


def pipeline(adapter):
    return cli._collect_pipeline_items(collectors=[cli.RuntimeCollector("habr", adapter.collect, habr_discovery=adapter)],
                                     safe_error_formatter=lambda source, exc: cli._format_collector_error(
                                         source=source, exc=exc, imap_host="unused", secrets=[]))


def analyze(adapter, analyzer):
    result = pipeline(adapter)
    cli._analyze_pipeline_items(analyzer=analyzer, seen_jobs=Seen(), pipeline=result, limit=20,
                               skip_seen=True, mark_seen=True, domain_policy=DomainPolicy())
    result.validate_accounting()
    return result


def test_complete_unknown_remote_flows_to_manual_card_and_delivery(monkeypatch, tmp_path):
    storage, analyzer = Delivery(), Analyzer()
    adapter = service(tmp_path, was_sent=lambda v: storage.was_sent(v.source, v.external_id))
    result = analyze(adapter, analyzer)
    assert len(analyzer.calls) == 1
    assert analyzer.calls[0][1] == {"content_completeness": "FULL"}
    assert "Разработка Java backend" in analyzer.calls[0][0]
    assert result.items[0].habr_policy.recommendation.label == "CHECK_MANUALLY"
    assert result.eligible() == 1
    cards = []
    monkeypatch.setattr(cli, "_upsert_history_item", lambda *a, **k: None)
    client = SimpleNamespace(send_vacancy_card=lambda card: (cards.append(card) or SimpleNamespace(message_id=1)))
    cli._deliver_pipeline_items(pipeline=result, deliveries=storage, telegram_client=client, chat_id="test")
    assert cards[0].employment_route == "CHECK_MANUALLY"
    assert "страны" in format_telegram_card_html(cards[0])
    assert "юридическое лицо" in format_telegram_card_html(cards[0])
    assert not adapter.collect()  # shared delivery dedup, no resend


def test_rss_fallback_remains_partial_and_notices_are_visible(tmp_path):
    class Unavailable(FakeDetails):
        def fetch(self, *args, **kwargs):
            return HabrDetail(status="http_403")
    adapter = service(tmp_path, detail_factory=Unavailable,
                      items=[vacancy(description="Можно удалённо. #senior #Java. Для клиента entertainment.")])
    analyzer = Analyzer()
    result = analyze(adapter, analyzer)
    assert analyzer.calls[0][1]["content_completeness"] == "PARTIAL"
    info = result.items[0].analysis_result.info_items
    assert any("неполное" in s for s in info)
    assert any("клиента" in s for s in info)
    assert result.items[0].telegram_eligible


def test_explicit_russian_contract_is_not_equated_to_residence_or_work_rights():
    policy = habr_policy(vacancy(description="Можно удалённо. Оформление по ТК РФ."), evaluation(), constraints())
    assert policy.recommendation.label == "CHECK_MANUALLY"
    assert policy.feasibility.work_authorization_requirement == "unknown"
    assert "по ТК РФ" in policy.notices[0]


def test_product_users_and_client_countries_are_not_candidate_restrictions():
    policy = habr_policy(vacancy(description="Наши пользователи — граждане РФ. Клиенты вне РФ. Можно удалённо."), evaluation(), constraints())
    assert policy.feasibility.location_restrictions == []
    assert policy.feasibility.work_authorization_requirement == "unknown"
    assert policy.recommendation.label == "CHECK_MANUALLY"


def test_send_failure_and_restart_reuse_analysis_and_same_backlog_batch(monkeypatch, tmp_path):
    items = [vacancy(str(1000000001 + i)) for i in range(4)]
    delivered, analyzer, now = Delivery(), Analyzer(), [NOW]
    kwargs = dict(items=items, was_sent=lambda v: delivered.was_sent("habr", v.external_id),
                  clock=lambda: now[0], initial_backlog_limit=1)
    adapter = service(tmp_path, **kwargs)
    result = analyze(adapter, analyzer)
    assert len(result.items) == 1
    monkeypatch.setattr(cli, "_upsert_history_item", lambda *a, **k: None)
    def fail(card):
        raise ValueError("synthetic delivery failure")
    cli._deliver_pipeline_items(pipeline=result, deliveries=delivered,
                               telegram_client=SimpleNamespace(send_vacancy_card=fail), chat_id="test")
    assert not delivered.sent
    restarted = service(tmp_path, **kwargs)
    retried = analyze(restarted, analyzer)
    assert len(analyzer.calls) == 1 and not restarted.rss_calls
    assert retried.items[0].vacancy.external_id == result.items[0].vacancy.external_id
    delivered.sent.add(result.items[0].vacancy.external_id)
    assert not restarted.collect()  # no replacement within the initial window
    now[0] += 3601
    next_batch = restarted.collect()
    assert len(next_batch) == 1 and next_batch[0].external_id not in delivered.sent


def test_cached_ignore_does_not_starve_rest_of_initial_cohort(tmp_path):
    items, now = [vacancy("1000000001"), vacancy("1000000002")], [NOW]
    adapter = service(tmp_path, items=items, clock=lambda: now[0], initial_backlog_limit=1)
    first = adapter.collect()[0]
    analyzer = SimpleNamespace(analyze=lambda *a, **k: VacancyEvaluation(
        decision="IGNORE", summary="synthetic mismatch", recommended_resume="java", recommended_cover_template="generic"))
    adapter.analyze(first, analyzer)
    assert not adapter.collect()
    now[0] += 3601
    assert adapter.collect()[0].external_id == "1000000002"


def test_below_delivery_threshold_does_not_starve_initial_cohort(tmp_path):
    now = [NOW]
    adapter = service(tmp_path, items=[vacancy("1000000001"), vacancy("1000000002")],
                      clock=lambda: now[0], initial_backlog_limit=1)
    first = adapter.collect()[0]
    adapter.analyze(first, SimpleNamespace(analyze=lambda *a, **k: VacancyEvaluation(
        decision="POTENTIAL_MATCH", summary="synthetic partial fit", recommended_resume="java", recommended_cover_template="generic")))
    assert not adapter.collect()
    now[0] += 3601
    assert adapter.collect()[0].external_id == "1000000002"


def test_existing_minimum_match_change_reconsiders_cached_potential_without_llm(tmp_path):
    adapter = service(tmp_path)
    v = adapter.collect()[0]
    adapter.analyze(v, SimpleNamespace(analyze=lambda *a, **k: VacancyEvaluation(
        decision="POTENTIAL_MATCH", summary="synthetic partial fit", recommended_resume="java", recommended_cover_template="generic")))
    assert not adapter.collect()
    adapter.eligible_decision = lambda decision: cli._meets_telegram_min_match(decision, "POTENTIAL")
    assert adapter.collect()[0].external_id == v.external_id
    adapter.analyze(v, SimpleNamespace(analyze=lambda *a, **k: pytest.fail("must reuse cached evaluation")))


def test_detail_budget_and_interval_do_not_fetch_all_candidates(tmp_path):
    adapter = service(tmp_path, items=[vacancy(str(1000000001 + i)) for i in range(6)], max_detail_requests=1)
    first = adapter.collect()
    assert len(first) == 3
    assert sum(v.content_completeness == "FULL" for v in first) == 1
    adapter.collect()
    assert len(adapter.rss_calls) == 1


def test_technical_cache_key_ignores_date_bumps_but_not_description_upgrades(tmp_path):
    adapter, analyzer = service(tmp_path), Analyzer()
    v = vacancy()
    adapter.analyze(v, analyzer)
    adapter.analyze(replace(v, published_at="2026-11-26T00:00:00+00:00"), analyzer)
    assert len(analyzer.calls) == 1
    adapter.analyze(replace(v, description="New published duties", content_completeness="FULL"), analyzer)
    assert len(analyzer.calls) == 2


@pytest.mark.parametrize("description,remote,expected", [
    ("Можно удалённо.", "worldwide", "CHECK_MANUALLY"),
    ("Remote worldwide. Можно удалённо только из РФ.", "worldwide", "CHECK_MANUALLY"),
    ("Remote worldwide. No visa sponsorship.", "worldwide", "CHECK_MANUALLY"),
    ("Relocation assistance. No visa sponsorship. Must have the right to work in the UK.", "onsite", "SKIP"),
    ("Relocation assistance. No remote work. No visa sponsorship. Must have the right to work in the UK.", "onsite", "SKIP"),
])
def test_shared_eligibility_conflicts_and_no_worldwide_inference(description, remote, expected):
    policy = habr_policy(vacancy(description=description), evaluation(remote_type=remote), constraints())
    assert policy.recommendation.label == expected
    if description == "Можно удалённо.":
        assert policy.feasibility.remote_type != "worldwide"


def test_domain_exclusion_prevents_llm_even_with_positive_cached_technical_result(monkeypatch, tmp_path):
    adapter = service(tmp_path)
    v = replace(adapter.collect()[0], description="We are an online casino. Java backend.")
    adapter.analyze(v, Analyzer())
    assert adapter.analysis_cache.get(adapter._key(v)).evaluation.decision.value == "STRONG_MATCH"
    adapter.collect = lambda: [v]
    result = pipeline(adapter)
    cli._analyze_pipeline_items(analyzer=SimpleNamespace(analyze=lambda *a, **k: pytest.fail("LLM")),
        seen_jobs=Seen(), pipeline=result, limit=20, skip_seen=True, mark_seen=True,
        domain_policy=DomainPolicy(excluded_domains=["gambling"]))
    assert result.items[0].title_filtered and result.eligible() == 0


@pytest.mark.parametrize("variant", ["java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz"])
def test_all_four_existing_resume_variants_survive_cache_and_manual_delivery(monkeypatch, tmp_path, variant):
    adapter = service(tmp_path)
    expected = evaluation(resume_variant=variant)
    analyzer = SimpleNamespace(analyze=lambda *a, **k: expected)
    result = analyze(adapter, analyzer)
    cached = adapter.analysis_cache.get(adapter._key(result.items[0].vacancy))
    assert cached.evaluation.resume_variant == variant
    cards = []
    monkeypatch.setattr(cli, "_upsert_history_item", lambda *a, **k: None)
    cli._deliver_pipeline_items(pipeline=result, deliveries=Delivery(), chat_id="test",
        telegram_client=SimpleNamespace(send_vacancy_card=lambda card: (cards.append(card) or SimpleNamespace(message_id=1))))
    assert cards[0].recommended_resume == variant


def test_habr_source_mapping_and_manual_buttons_only():
    assert map_source_to_code("habr") == "hb" and map_code_to_source("hb") == "habr"
    rows = build_action_buttons("habr", "1000000001", vacancy().url)
    callbacks = [b.callback_data for row in rows for b in row if b.callback_data]
    assert callbacks == ["applied:hb:1000000001", "skip:hb:1000000001"]
    undo = build_archived_buttons(vacancy().url, source="habr", external_id="1000000001", action_id="token")
    assert parse_callback_data(undo[0][0].callback_data) == ("undo", "habr", "1000000001", "token")


@pytest.mark.parametrize("action,status", [("applied", "APPLIED"), ("skip", "SKIPPED")])
def test_real_synthetic_history_callbacks_and_undo_keep_manual_notices(tmp_path, action, status):
    storage = TelegramDeliveryStorage(tmp_path / "synthetic.db")
    v = vacancy()
    storage.save_sent(source="habr", external_id=v.external_id, chat_id="test", message_id=1)
    storage.upsert_application_history(source="habr", external_id=v.external_id, title=v.title, company=v.company,
        location=v.location, url=v.url, decision="STRONG_MATCH", decision_reason="synthetic", recommended_resume="java")
    e = evaluation(info_items=list(habr_policy(v, evaluation(), constraints()).notices))
    storage.save_prepare_cache(source="habr", external_id=v.external_id, evaluation_json=e.model_dump_json(),
        analysis_text=v.to_analysis_text(), title=v.title, company=v.company, location=v.location,
        url=v.url, content_completeness="PARTIAL")
    edits = []
    client = SimpleNamespace(answer_callback_query=lambda *a, **k: None,
        edit_message_text=lambda **k: edits.append(k), delete_message=lambda **k: None)
    def callback(data):
        cli._process_callback_update(update={"callback_query": {"id": "cb", "data": data,
            "message": {"message_id": 1, "chat": {"id": "test"}}}}, client=client, storage=storage,
            configured_chat_id="test", undo_window_seconds=600)
    callback(action + ":hb:" + v.external_id)
    changed = storage.get_delivery("habr", v.external_id)
    assert changed.status == status
    callback(f"undo:hb:{v.external_id}:{changed.last_action_id}")
    assert storage.get_delivery("habr", v.external_id).status == "SENT"
    assert "CHECK_MANUALLY" in edits[-1]["text"] and "юридическое лицо" in edits[-1]["text"]
    assert not any("prepare" in (b.callback_data or "") or "prepapp" in (b.callback_data or "")
                   for row in edits[-1]["buttons"] for b in row)


@pytest.mark.parametrize("action", ["prepare", "prepapp", "resume", "copy"])
def test_forged_prepare_and_artifact_callbacks_do_not_enter_runtime(action):
    answers = []
    cli._process_callback_update(update={"callback_query": {"id": "cb", "data": action + ":hb:1000000001",
        "message": {"message_id": 1, "chat": {"id": "test"}}}},
        client=SimpleNamespace(answer_callback_query=lambda *a, **k: answers.append(k)),
        storage=SimpleNamespace(), configured_chat_id="test")
    assert answers == [{"text": "Для Habr доступен только ручной отклик"}]


def test_runtime_registration_is_default_off_and_invalid_config_does_not_stop_other_sources(monkeypatch, tmp_path):
    monkeypatch.delenv("HABR_DISCOVERY_ENABLED", raising=False)
    settings = Settings(_env_file=None, llm_api_url="https://unused.test", llm_api_key="synthetic", llm_model="synthetic",
                        linkedin_email_enabled=False, greenhouse_boards=["synthetic"])
    kwargs = dict(settings=settings, analyzer=Analyzer(), seen_jobs=Seen(), email_client=None)
    assert not settings.habr_discovery_enabled
    assert [c.name for c in cli._build_runtime_collectors(**kwargs)] == ["greenhouse"]
    settings.habr_discovery_enabled = True
    settings.habr_discovery_config_path = tmp_path / "missing.yaml"
    assert [c.name for c in cli._build_runtime_collectors(**kwargs, deliveries=Delivery())] == ["greenhouse"]
    adapter = service(tmp_path / "state")
    monkeypatch.setattr(cli, "load_habr_config", lambda path: adapter.config)
    monkeypatch.setattr(cli, "HabrDiscovery", lambda *a, **k: adapter)
    assert [c.name for c in cli._build_runtime_collectors(**kwargs, deliveries=Delivery())] == ["greenhouse", "habr"]


def test_habr_collection_failure_is_sanitized_and_other_collector_continues():
    def fail():
        raise ValueError("PRIVATE_TOKEN")
    result = cli._collect_pipeline_items(collectors=[cli.RuntimeCollector("habr", fail),
        cli.RuntimeCollector("greenhouse", lambda: [replace(vacancy(), source="greenhouse")])],
        safe_error_formatter=lambda source, exc: cli._format_collector_error(source=source, exc=exc, imap_host="", secrets=[]))
    assert result.has_collect_error("habr") and result.unique("greenhouse") == 1
    assert "PRIVATE" not in result.collect_error_message("habr")


def test_corrupt_source_state_fails_visibly_without_reset(tmp_path):
    state = tmp_path / "discovery.json"
    state.write_text("not json")
    with pytest.raises(ValueError, match="invalid Habr discovery state"):
        service(tmp_path)
    assert state.read_text() == "not json"


def test_llm_failure_is_backed_off_across_restart_without_logging_payload(tmp_path):
    adapter = service(tmp_path)
    v = adapter.collect()[0]
    def fail(*args, **kwargs):
        raise ValueError("private upstream payload")
    with pytest.raises(ValueError):
        adapter.analyze(v, SimpleNamespace(analyze=fail))
    assert not service(tmp_path).collect()
    assert "private upstream payload" not in (tmp_path / "discovery.json").read_text()
