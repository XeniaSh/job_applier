"""Isolated end-to-end check of the Wise SmartRecruiters Target Companies path.

Real `SmartRecruitersTargetWatcher` + real `TelegramClient` run against an
in-memory `httpx.MockTransport` (no network, no private profile, no production
config/state). Only the LLM analyzer and the candidate-constraints loader are
stubbed. Any request to an unexpected host/path is recorded and fails the test.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import app.cli as cli_module
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.candidate_constraints import CandidateConstraints
from app.company_watch.config_loader import load_target_companies_config
from app.company_watch.watchers.ashby import AshbyWatchResult
from app.company_watch.watchers.greenhouse import GreenhouseWatchResult
from app.company_watch.watchers.lever import LeverWatchResult
from app.company_watch.watchers.smartrecruiters import (
    SmartRecruitersTargetWatcher,
    is_smartrecruiters_target,
    resolve_smartrecruiters_slug,
)
from app.company_watch.wise_eligibility import (
    WISE_MANUAL_EMPLOYER_SITE_INFO,
    WISE_SMARTRECRUITERS_SOURCE,
    WISE_UNKNOWN_SPONSORSHIP_WARNING,
)
from app.models import Decision, RecommendedCoverTemplate, RecommendedResume, VacancyEvaluation
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import (
    TelegramClient,
    build_action_buttons,
    source_supports_application_prepare,
    source_supports_prepare,
)

BOT_TOKEN = "test-token"
CHAT_ID = "222"
WISE_SOURCE = WISE_SMARTRECRUITERS_SOURCE

WISE_CONFIG = """
companies:
  - name: Wise
    priority: A
    language: english
    relocation_status: mixed_check_per_role
    known_hiring_locations: [Tallinn]
    watcher_type: smartrecruiters
    ats: smartrecruiters
    job_board_url: https://careers.smartrecruiters.com/Wise
    role_keywords: [backend, java, kotlin]
    role_title_keywords: [java, kotlin, backend, back-end, software engineer, platform engineer]
  - name: Canva
    priority: A
    language: english
    relocation_status: confirmed_role_based
    watcher_type: smartrecruiters
    ats: smartrecruiters
    job_board_url: https://careers.smartrecruiters.com/canva
    role_keywords: [backend, java]
"""

CONDITIONAL_TEXT = (
    "We are currently only considering local candidates as relocation support is not provided. "
    "For local candidates we are able to support transfer of visa sponsorship."
)
RESTRICTION_TEXT = "Candidates must have legal right to work in the UK."


@dataclass(frozen=True)
class _Job:
    key: str
    posting_id: str
    title: str
    description: str
    posting_url: str | None = None
    expected_url: str | None = None

    @property
    def url(self) -> str:
        return self.expected_url or f"https://jobs.smartrecruiters.com/Wise/{self.posting_id}"


_OFFERED = _Job(
    "offered",
    "744000100001",
    "Senior Java Backend Engineer",
    "Hybrid role in Tallinn with relocation and visa sponsorship support, if needed.",
    posting_url="https://jobs.smartrecruiters.com/Wise/744000100001-senior-java-backend-engineer",
    expected_url="https://jobs.smartrecruiters.com/Wise/744000100001-senior-java-backend-engineer",
)
_UNKNOWN = _Job(
    "unknown",
    "744000100002",
    "Backend Engineer",
    "Build and operate payment backend services.",
)
_IGNORE = _Job(
    "ignore",
    "744000100003",
    "Software Engineer, Data Tools",
    "Maintain internal data tooling written in Ruby.",
)
_MANAGER = _Job(
    "manager",
    "744000100004",
    "Backend Team Lead",
    "Lead a team of backend engineers.",
)
_REFUSAL = _Job(
    "refusal",
    "744000100005",
    "Java Backend Engineer, Cards",
    "Please note we do not offer relocation support or visa sponsorship for this role.",
)
_CONDITIONAL = _Job(
    "conditional",
    "744000100006",
    "Kotlin Backend Engineer",
    CONDITIONAL_TEXT,
)
_WORK_RIGHTS = _Job(
    "work_rights",
    "744000100007",
    "Java Platform Engineer",
    f"{CONDITIONAL_TEXT} {RESTRICTION_TEXT}",
    posting_url="https://jobs.smartrecruiters.com/Wise/744000100007-java-platform-engineer?trid=zzz",
    expected_url="https://jobs.smartrecruiters.com/Wise/744000100007-java-platform-engineer",
)
_MATCHING_JOBS = (_OFFERED, _UNKNOWN, _IGNORE, _MANAGER, _REFUSAL, _CONDITIONAL, _WORK_RIGHTS)
_JOBS_BY_KEY = {job.key: job for job in _MATCHING_JOBS}
# Listed (so they are paged and counted as raw) but dropped by the title prefilter.
_NON_MATCHING = (
    ("744000100008", "Sales Account Executive"),
    ("744000100009", "Frontend Engineer"),
    ("744000100010", "Data Scientist"),
)
# page_limit=3 -> pages [0:3], [3:6], [6:9], [9:10]
_LIST_ORDER = [
    ("744000100001", "Senior Java Backend Engineer"),
    ("744000100002", "Backend Engineer"),
    ("744000100003", "Software Engineer, Data Tools"),
    ("744000100004", "Backend Team Lead"),
    ("744000100005", "Java Backend Engineer, Cards"),
    ("744000100006", "Kotlin Backend Engineer"),
    ("744000100007", "Java Platform Engineer"),
    *_NON_MATCHING[:2],
    _NON_MATCHING[2],
]

EXPECTED_RECOMMENDATIONS = {
    "offered": "APPLY_NOW",
    "unknown": "CHECK_MANUALLY",
    "ignore": "SKIP",
    "manager": "SKIP",
    "refusal": "SKIP",
    "conditional": "CHECK_MANUALLY",
    "work_rights": "SKIP",
}
EXPECTED_VISA = {
    "offered": "yes",
    "unknown": "unknown",
    "refusal": "no",
    "conditional": "unknown",
    "work_rights": "unknown",
}
SENT_KEYS = {"offered", "unknown", "conditional"}


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


def _evaluation(
    decision: Decision,
    *,
    visa_sponsorship: str = "unknown",
    work_authorization_requirement: str = "unknown",
    location_restrictions: list[str] | None = None,
) -> VacancyEvaluation:
    # Crowded on purpose: more warnings/infos than the Telegram formatter keeps.
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
        visa_sponsorship=visa_sponsorship,
        work_authorization_requirement=work_authorization_requirement,
        location_restrictions=location_restrictions or [],
        warning_signals=[{"code": f"w{i}", "evidence": f"preexisting warning {i}"} for i in range(4)],
        info_items=[f"preexisting info {i}" for i in range(6)],
    )


class _StubAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze(self, vacancy_text: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        _ = content_completeness
        self.calls.append(vacancy_text)
        if "internal data tooling" in vacancy_text:
            return _evaluation(Decision.IGNORE)
        if "Lead a team" in vacancy_text:
            return _evaluation(Decision.STRONG_MATCH)
        if "with relocation and visa sponsorship support" in vacancy_text:
            return _evaluation(Decision.STRONG_MATCH, visa_sponsorship="yes")
        if RESTRICTION_TEXT in vacancy_text:
            return _evaluation(
                Decision.STRONG_MATCH,
                work_authorization_requirement="unknown",
                location_restrictions=[f"{CONDITIONAL_TEXT} {RESTRICTION_TEXT}"],
            )
        if "do not offer relocation" in vacancy_text or "only considering local" in vacancy_text:
            return _evaluation(Decision.STRONG_MATCH)
        return _evaluation(Decision.POTENTIAL_MATCH)


@dataclass
class _SentMessage:
    payload: dict
    ok: bool

    @property
    def title(self) -> str:
        return str(self.payload["text"]).split("\n", 1)[0]


@dataclass
class _World:
    """Fake SmartRecruiters + Telegram HTTP surface behind one MockTransport."""

    smart_requests: list[httpx.Request] = field(default_factory=list)
    telegram_sends: list[_SentMessage] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    fail_next_sends: int = 0
    _next_message_id: int = 1

    @property
    def successful_titles(self) -> list[str]:
        return [item.title for item in self.telegram_sends if item.ok]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.smartrecruiters.com":
            return self._smartrecruiters(request)
        if host == "api.telegram.org":
            return self._telegram(request)
        self.unexpected.append(str(request.url))
        raise AssertionError(f"unexpected HTTP host: {request.url}")

    def _smartrecruiters(self, request: httpx.Request) -> httpx.Response:
        self.smart_requests.append(request)
        prefix = "/v1/companies/Wise/postings"
        path = request.url.path
        if request.method != "GET" or not path.startswith(prefix):
            self.unexpected.append(str(request.url))
            raise AssertionError(f"unexpected SmartRecruiters request: {request.url}")
        suffix = path[len(prefix) :].strip("/")
        if not suffix:
            params = parse_qs(request.url.query.decode())
            limit = int(params["limit"][0])
            offset = int(params["offset"][0])
            rows = [
                {"id": posting_id, "name": title, "releasedDate": "2026-10-20T10:00:00.000Z"}
                for posting_id, title in _LIST_ORDER[offset : offset + limit]
            ]
            return httpx.Response(
                200,
                json={"offset": offset, "limit": limit, "totalFound": len(_LIST_ORDER), "content": rows},
            )
        job = next((item for item in _MATCHING_JOBS if item.posting_id == suffix), None)
        if job is None:
            self.unexpected.append(str(request.url))
            raise AssertionError(f"unexpected SmartRecruiters detail request: {request.url}")
        detail: dict = {
            "id": job.posting_id,
            "name": job.title,
            "active": True,
            "company": {"identifier": "Wise"},
            "location": {"city": "Tallinn", "country": "ee", "fullLocation": "Tallinn, Estonia"},
            "typeOfEmployment": {"label": "Full-time"},
            "releasedDate": "2026-10-20T10:00:00.000Z",
            "jobAd": {
                "sections": {
                    "jobDescription": {"title": "Job Description", "text": f"<p>{job.description}</p>"},
                }
            },
        }
        if job.posting_url:
            detail["postingUrl"] = job.posting_url
        return httpx.Response(200, json=detail)

    def _telegram(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != f"/bot{BOT_TOKEN}/sendMessage":
            self.unexpected.append(str(request.url.path))
            raise AssertionError(f"unexpected Telegram request: {request.url.path}")
        payload = json.loads(request.content)
        if self.fail_next_sends > 0:
            self.fail_next_sends -= 1
            self.telegram_sends.append(_SentMessage(payload=payload, ok=False))
            return httpx.Response(500, json={"ok": False, "error_code": 500, "description": "simulated failure"})
        self.telegram_sends.append(_SentMessage(payload=payload, ok=True))
        message_id = self._next_message_id
        self._next_message_id += 1
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": message_id, "chat": {"id": int(CHAT_ID)}}},
        )


@pytest.fixture
def world(monkeypatch, tmp_path: Path) -> _World:
    state = _World()
    original_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return original_client(*args, transport=httpx.MockTransport(state.handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_USERNAME", "mail@example.com")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_PASSWORD", "mail-password")
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM__LINKEDIN_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("PIPELINE_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("TELEGRAM_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: _constraints())
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "target_companies.yaml").write_text(WISE_CONFIG, encoding="utf-8")
    cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()
    yield state
    cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()
    assert state.unexpected == []


class _EmptyGreenhouse:
    def watch(self, companies: object) -> GreenhouseWatchResult:
        _ = companies
        return GreenhouseWatchResult(vacancies=[], errors=[], raw_fetched=0)


class _EmptyLever:
    def watch(self, companies: object) -> LeverWatchResult:
        _ = companies
        return LeverWatchResult(vacancies=[], errors=[], raw_fetched=0)


class _EmptyAshby:
    def watch(self, companies: object) -> AshbyWatchResult:
        _ = companies
        return AshbyWatchResult(vacancies=[], errors=[], raw_fetched=0)


def _run_cycle(
    tmp_path: Path,
    analyzer: _StubAnalyzer,
    deliveries: TelegramDeliveryStorage,
) -> cli_module._TargetCompaniesCycleResult:
    telegram = TelegramClient(BOT_TOKEN, CHAT_ID)
    try:
        return cli_module._run_target_companies_cycle(
            settings=cli_module.Settings(),
            analyzer=analyzer,
            deliveries=deliveries,
            config_path=tmp_path / "config" / "target_companies.yaml",
            cache_path=tmp_path / "analysis_cache.json",
            analyze_limit=30,
            analyze_limit_per_company=10,
            watcher=_EmptyGreenhouse(),
            lever_watcher=_EmptyLever(),
            ashby_watcher=_EmptyAshby(),
            smartrecruiters_watcher=SmartRecruitersTargetWatcher(
                page_limit=3, max_pages=10, max_detail_fetches=100
            ),
            telegram_client=telegram,
        )
    finally:
        telegram.close()


def _open_deliveries(tmp_path: Path) -> TelegramDeliveryStorage:
    return TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")


def _simulate_restart() -> None:
    cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()


def _load_cache(tmp_path: Path) -> TargetCompanyAnalysisCache:
    cache = TargetCompanyAnalysisCache(tmp_path / "analysis_cache.json")
    cache.load()
    return cache


def _sent_titles() -> set[str]:
    return {_JOBS_BY_KEY[key].title for key in SENT_KEYS}


def _card_text(world: _World, title: str) -> str:
    matches = [item for item in world.telegram_sends if item.ok and item.title == html.escape(title, quote=True)]
    assert len(matches) == 1
    return str(matches[0].payload["text"])


def _wise_company(tmp_path: Path):
    loaded = load_target_companies_config(tmp_path / "config" / "target_companies.yaml")
    return next(item for item in loaded.companies if item.name == "Wise")


def test_real_watcher_pages_and_normalizes_stable_wise_postings(world: _World, tmp_path: Path) -> None:
    result = SmartRecruitersTargetWatcher(page_limit=3, max_pages=10, max_detail_fetches=100).watch(
        [_wise_company(tmp_path)]
    )

    assert result.errors == []
    assert result.job_errors == []
    assert (result.raw_fetched, result.unique_count, result.duplicate_count) == (10, 10, 0)
    assert result.title_matched_count == 7
    assert result.detail_fetched_count == 7
    assert result.pages_fetched == 4
    assert not result.pages_truncated
    assert not result.detail_truncated
    assert [(v.source, v.external_id, v.url) for v in result.vacancies] == [
        (WISE_SOURCE, job.posting_id, job.url) for job in _MATCHING_JOBS
    ]
    offsets = [
        int(parse_qs(request.url.query.decode())["offset"][0])
        for request in world.smart_requests
        if request.url.path == "/v1/companies/Wise/postings"
    ]
    assert offsets == [0, 3, 6, 9]
    detail_ids = [
        request.url.path.rsplit("/", 1)[-1]
        for request in world.smart_requests
        if request.url.path != "/v1/companies/Wise/postings"
    ]
    assert detail_ids == [job.posting_id for job in _MATCHING_JOBS]


def test_first_cycle_counts_labels_and_wise_only_allowlist(world: _World, tmp_path: Path) -> None:
    analyzer = _StubAnalyzer()
    result = _run_cycle(tmp_path, analyzer, _open_deliveries(tmp_path))

    assert result.enabled
    assert (result.watched, result.dropped_delivered, result.dropped_cached_skip) == (7, 0, 0)
    assert result.dropped_unsupported_form == 0
    assert (result.selected, result.analyzed, result.cache_hits, result.cache_misses) == (7, 7, 0, 7)
    assert (result.sent, result.send_errors) == (3, 0)
    funnel = result.provider_funnels["smartrecruiters"]
    assert funnel.configured_companies == 1  # Canva is configured but rejected at runtime
    assert (funnel.raw_fetched, funnel.title_prefilter_pass, funnel.post_gate_candidates) == (10, 7, 7)
    assert (funnel.selected, funnel.analyzed, funnel.sent, funnel.watcher_errors) == (7, 7, 3, 0)
    for other in ("greenhouse", "lever", "ashby"):
        assert result.provider_funnels[other].raw_fetched == 0
    counts = result.outcome_counts
    assert (counts.eligible, counts.sent, counts.llm_ignore, counts.excluded_seniority) == (3, 3, 1, 1)
    assert counts.work_auth + counts.other_skip == 2
    assert counts.analysis_error == 0

    assert len(analyzer.calls) == 7
    assert all(request.url.path.startswith("/v1/companies/Wise/postings") for request in world.smart_requests)
    assert not any("canva" in str(request.url).lower() for request in world.smart_requests)
    assert len(world.smart_requests) == 4 + 7

    cache = _load_cache(tmp_path)
    for job in _MATCHING_JOBS:
        cached = cache.get_by_identity(WISE_SOURCE, job.posting_id)
        assert cached is not None, job.key
        assert cached.recommendation.label == EXPECTED_RECOMMENDATIONS[job.key], job.key
        if job.key in EXPECTED_VISA:
            assert cached.feasibility.visa_sponsorship == EXPECTED_VISA[job.key], job.key
    work_rights = cache.get_by_identity(WISE_SOURCE, _WORK_RIGHTS.posting_id)
    assert work_rights is not None
    assert work_rights.feasibility.work_authorization_requirement == "required"
    assert work_rights.feasibility.location_restrictions == [f"{CONDITIONAL_TEXT} {RESTRICTION_TEXT}"]

    # Only the three eligible titles were delivered; only canonical URLs and
    # no Prepare button were offered for the Wise cards.
    assert set(world.successful_titles) == {html.escape(title, quote=True) for title in _sent_titles()}
    assert len(world.successful_titles) == 3
    for key in SENT_KEYS:
        job = _JOBS_BY_KEY[key]
        sent = next(item for item in world.telegram_sends if item.title == html.escape(job.title, quote=True))
        rows = sent.payload["reply_markup"]["inline_keyboard"]
        buttons = [button for row in rows for button in row]
        assert [button.get("url") for button in buttons if "url" in button] == [job.url]
        callbacks = [button["callback_data"] for button in buttons if "callback_data" in button]
        assert callbacks == [f"applied:tcs.wise:{job.posting_id}", f"skip:tcs.wise:{job.posting_id}"]
        assert not any("prep" in data.lower() for data in callbacks)
        assert not any("prepare" in button["text"].lower() for button in buttons)


def test_repeat_polls_and_restart_never_reanalyze_or_resend(world: _World, tmp_path: Path) -> None:
    analyzer = _StubAnalyzer()
    deliveries = _open_deliveries(tmp_path)
    first = _run_cycle(tmp_path, analyzer, deliveries)
    assert first.sent == 3

    second = _run_cycle(tmp_path, analyzer, deliveries)
    assert (second.watched, second.dropped_delivered, second.dropped_cached_skip) == (7, 3, 4)
    assert (second.selected, second.analyzed, second.sent, second.send_errors) == (0, 0, 0, 0)
    assert len(analyzer.calls) == 7

    # Restart: in-process memory is gone; only the SQLite + JSON cache remain.
    _simulate_restart()
    restarted_analyzer = _StubAnalyzer()
    third = _run_cycle(tmp_path, restarted_analyzer, _open_deliveries(tmp_path))
    assert (third.watched, third.dropped_delivered, third.dropped_cached_skip) == (7, 3, 4)
    assert (third.selected, third.analyzed, third.sent, third.send_errors) == (0, 0, 0, 0)
    assert restarted_analyzer.calls == []

    assert len(world.successful_titles) == 3
    assert len(set(world.successful_titles)) == 3
    assert len(world.telegram_sends) == 3


def test_failed_send_is_retried_once_from_cache_without_reanalysis(world: _World, tmp_path: Path) -> None:
    analyzer = _StubAnalyzer()
    deliveries = _open_deliveries(tmp_path)
    world.fail_next_sends = 1

    first = _run_cycle(tmp_path, analyzer, deliveries)
    assert (first.sent, first.send_errors) == (2, 1)
    failed = [item for item in world.telegram_sends if not item.ok]
    assert len(failed) == 1
    failed_title = failed[0].title
    assert failed_title not in world.successful_titles
    assert len(analyzer.calls) == 7

    second = _run_cycle(tmp_path, analyzer, deliveries)
    assert (second.dropped_delivered, second.dropped_cached_skip) == (2, 4)
    assert (second.selected, second.cache_hits, second.cache_misses) == (1, 1, 0)
    assert (second.sent, second.send_errors) == (1, 0)
    assert len(analyzer.calls) == 7  # served from cache
    assert sorted(world.successful_titles) == sorted(html.escape(t, quote=True) for t in _sent_titles())
    assert world.successful_titles.count(failed_title) == 1

    third = _run_cycle(tmp_path, analyzer, deliveries)
    assert (third.selected, third.sent, third.send_errors) == (0, 0, 0)

    _simulate_restart()
    restarted_analyzer = _StubAnalyzer()
    fourth = _run_cycle(tmp_path, restarted_analyzer, _open_deliveries(tmp_path))
    assert (fourth.dropped_delivered, fourth.selected, fourth.sent, fourth.send_errors) == (3, 0, 0, 0)
    assert restarted_analyzer.calls == []
    assert len(world.successful_titles) == 3
    assert len(world.telegram_sends) == 4  # 1 failure + 3 successes, no repeats


def test_rendered_payload_keeps_priority_wise_text_within_formatter_limits(world: _World, tmp_path: Path) -> None:
    _run_cycle(tmp_path, _StubAnalyzer(), _open_deliveries(tmp_path))

    unknown_text = _card_text(world, _UNKNOWN.title)
    conditional_text = _card_text(world, _CONDITIONAL.title)
    offered_text = _card_text(world, _OFFERED.title)
    manual_info = html.escape(WISE_MANUAL_EMPLOYER_SITE_INFO, quote=True)

    for text in (unknown_text, conditional_text, offered_text):
        # Manual-site info leads Info; four preexisting infos fit, so the last
        # two are cut by the formatter's limit (priority text survived).
        info_block = text.split("<b>Info:</b>\n", 1)[1].split("\n\n", 1)[0].split("\n")
        assert info_block[0] == manual_info
        assert info_block[1:] == [f"preexisting info {i}" for i in range(4)]

    def warnings_of(text: str) -> list[str]:
        block = text.split("<b>Warnings:</b>\n", 1)[1].split("\n\n", 1)[0]
        return [line.removeprefix("⚠️ ") for line in block.split("\n")]

    # Unknown sponsorship: truthful notice first, ahead of >=3 preexisting ones.
    assert warnings_of(unknown_text) == [
        html.escape(WISE_UNKNOWN_SPONSORSHIP_WARNING, quote=True),
        "preexisting warning 0",
        "preexisting warning 1",
    ]
    assert "preexisting warning 2" not in unknown_text

    conditional_warnings = warnings_of(conditional_text)
    assert conditional_warnings[0].startswith("Wise: sponsorship is conditional")
    assert "not confirmed" in conditional_warnings[0]
    assert "not offered" not in conditional_text
    assert conditional_warnings[1:] == ["preexisting warning 0", "preexisting warning 1"]
    assert html.escape(WISE_UNKNOWN_SPONSORSHIP_WARNING, quote=True) not in conditional_text

    # Positive visa evidence must not receive an unknown-sponsorship warning.
    assert "Wise:" not in offered_text
    assert warnings_of(offered_text) == [f"preexisting warning {i}" for i in range(3)]


def test_cached_retry_card_keeps_the_same_wise_warning(world: _World, tmp_path: Path) -> None:
    world.fail_next_sends = 3
    analyzer = _StubAnalyzer()
    deliveries = _open_deliveries(tmp_path)
    first = _run_cycle(tmp_path, analyzer, deliveries)
    assert (first.sent, first.send_errors) == (0, 3)

    second = _run_cycle(tmp_path, analyzer, deliveries)
    assert (second.sent, second.cache_hits) == (3, 3)
    assert len(analyzer.calls) == 7
    unknown_text = _card_text(world, _UNKNOWN.title)
    assert html.escape(WISE_UNKNOWN_SPONSORSHIP_WARNING, quote=True) in unknown_text


def test_smartrecruiters_has_no_prepare_capability_and_forged_prepapp_is_inert(monkeypatch) -> None:
    for source in (WISE_SOURCE, "tcs.wise", "target_company:smartrecruiters:canva"):
        assert source_supports_prepare(source) is False
        assert source_supports_application_prepare(source) is False
    url = "https://jobs.smartrecruiters.com/Wise/744000100001"
    callbacks = [
        button.callback_data
        for row in build_action_buttons(WISE_SOURCE, "744000100001", url)
        for button in row
        if button.callback_data
    ]
    assert not any(data.startswith(("prepapp", "prepare")) for data in callbacks)

    def forbidden(*args, **kwargs):
        raise AssertionError("browser/autofill path must not be reached")

    class _ForbiddenService:
        def __getattr__(self, name: str):
            return forbidden

    monkeypatch.setattr(cli_module, "default_review_registry", forbidden)
    answers: list[str | None] = []
    cli_module._dispatch_target_company_application_prepare(
        source=WISE_SOURCE,
        external_id="744000100001",
        client=object(),
        storage=object(),
        chat_id=CHAT_ID,
        message_id=1,
        answer_once=answers.append,
        prepare_service=_ForbiddenService(),
        analysis_cache=None,
        sync=True,
    )
    assert answers == ["Preparation is not available for this vacancy."]


def test_tracked_yaml_wise_override_and_runtime_rejects_canva() -> None:
    tracked = Path(__file__).resolve().parents[1] / "config" / "target_companies.yaml"
    companies = load_target_companies_config(tracked).companies
    smart = [item for item in companies if is_smartrecruiters_target(item)]
    by_name = {item.name: item for item in smart}

    wise = by_name["Wise"]
    assert resolve_smartrecruiters_slug(wise) == "Wise"
    assert wise.role_title_keywords == [
        "java",
        "kotlin",
        "backend",
        "back-end",
        "software engineer",
        "platform engineer",
    ]
    assert cli_module._is_approved_smartrecruiters_company(wise) is True

    canva = by_name["Canva"]
    assert resolve_smartrecruiters_slug(canva) == "canva"
    assert cli_module._is_approved_smartrecruiters_company(canva) is False
    assert [item.name for item in smart if cli_module._is_approved_smartrecruiters_company(item)] == ["Wise"]
