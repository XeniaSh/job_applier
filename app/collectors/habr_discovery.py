"""Small Habr source adapter using existing technical cache and policy mechanisms."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.collectors.habr_details import HabrDetailClient
from app.collectors.habr_rss import HabrRSSConfig, canonical_habr_url, collect_rss, evaluate_record
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.analysis_cache import TargetCompanyAnalysisCache
from app.company_watch.application_recommendation import ApplicationRecommendation, recommend_application
from app.company_watch.candidate_constraints import load_candidate_constraints
from app.company_watch.delivery_priority import employment_priority
from app.company_watch.domain_policy import excluded_domain_reason, load_domain_policy
from app.company_watch.feasibility import ApplicationFeasibility, assess_application_feasibility
from app.company_watch.seniority import SeniorityClassification, classify_seniority


class HabrDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rss: HabrRSSConfig = Field(default_factory=HabrRSSConfig)
    interval_seconds: int = Field(default=3600, ge=300, le=86400)
    detail_ttl_seconds: int = Field(default=86400, ge=3600, le=604800)
    detail_request_interval_seconds: float = Field(default=1, ge=1, le=10, allow_inf_nan=False)
    max_detail_requests: int = Field(default=5, ge=0, le=20)
    initial_backlog_limit: int = Field(default=3, ge=1, le=20)
    batch_limit: int = Field(default=5, ge=1, le=20)
    state_dir: Path = Path("data/habr")


def load_habr_config(path: Path) -> HabrDiscoveryConfig:
    return HabrDiscoveryConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class HabrPolicy:
    feasibility: ApplicationFeasibility
    recommendation: ApplicationRecommendation
    seniority: SeniorityClassification
    notices: tuple[str, ...]


def habr_policy(vacancy, evaluation, constraints) -> HabrPolicy:
    text = vacancy.description
    limits = list(evaluation.location_restrictions)
    # Extract only explicit source wording; a city, citizenship or phone is not a constraint.
    patterns = (
        r"(?:строго|только|проживание|нахождение|работа|работать|удал[её]нно,?)\s+вне (?:РФ|РБ|России|Беларуси)\b",
        r"(?:только|исключительно) (?:из|в|на территории) (?:РФ|РБ|России|Беларуси|Казахстана|Узбекистана)\b",
        r"(?:требуется|необходимо|обязательно|только) (?:гражданство|граждане|резидентство|резиденты) (?:РФ|РБ|России|Беларуси)\b",
    )
    for pattern in patterns:
        limits.extend(re.findall(pattern, text, re.I))
    worldwide = bool(re.search(r"из любой (?:страны|точки мира)|по всему миру", text, re.I))
    if re.search(r"\b(?:не|нельзя|невозможно)\b", text, re.I):
        worldwide = False
    no_visa = bool(re.search(r"(?:не (?:предоставляем|оказываем|предусмотрена)|без) визов\w* поддерж|визов\w* поддерж\w* не предостав", text, re.I))
    remote_route = bool(re.search(r"удал[её]н|\bremote\b", text, re.I))
    if re.search(r"no remote|not (?:a )?remote|(?:do not|cannot) (?:offer|support) remote|"
                 r"удал[её]н\w* (?:работа )?не (?:предусмотр|поддерж|предостав)|без удал[её]н", text, re.I):
        remote_route = False
    assessment = assess_application_feasibility(
        vacancy_text=text, location=vacancy.location,
        remote_type="country_limited" if limits else ("worldwide" if worldwide else (
            evaluation.remote_type if evaluation.remote_type in {"country_limited", "hybrid", "onsite"} else "unknown")),
        visa_sponsorship="no" if no_visa else evaluation.visa_sponsorship,
        relocation_support=evaluation.relocation_support,
        work_authorization_requirement=evaluation.work_authorization_requirement,
        language_requirements=evaluation.language_requirements, location_restrictions=limits,
        alternative_remote_route=remote_route,
    )
    seniority = classify_seniority(vacancy.title)
    if seniority.label == "UNKNOWN":
        # RSS grade is an explicit vacancy fact, not an inferred experience claim.
        grade = re.search(r"#(junior|intern|lead|senior|middle)\b", text, re.I)
        if grade:
            prefix = {"middle": "mid-level", "lead": "team lead"}.get(grade[1].lower(), grade[1])
            seniority = classify_seniority(prefix + " " + vacancy.title)
    recommendation = recommend_application(decision=evaluation.decision, feasibility=assessment,
                                            constraints=constraints, location=vacancy.location, seniority=seniority)
    if recommendation.label != "SKIP":
        recommendation = ApplicationRecommendation("CHECK_MANUALLY", ["confirm country, employment entity and personal eligibility"])
    notices = ["Страна договора и юридическое лицо: проверить условия оформления."]
    if re.search(r"оформлен\w* по ТК РФ|трудов\w* договор.{0,30}ТК РФ", text, re.I):
        notices[0] = "Оформление по ТК РФ указано; возможность работы из-за рубежа и юрлицо уточнить."
    if assessment.remote_type != "worldwide":
        notices.append("Remote: разрешённые страны неизвестны или ограничены — CHECK_MANUALLY.")
    else:
        notices.append("Worldwide указан в тексте; личные права и оформление требуют проверки — CHECK_MANUALLY.")
    if limits:
        notices.append("Явные ограничения стран/статуса указаны в вакансии; проверить совместимость.")
    if vacancy.content_completeness != "FULL":
        notices.append("Описание неполное: доступен RSS/частичный текст, требования нужно проверить вручную.")
    if re.search(r"конечн\w* клиент|наш\w* клиент|для клиента|заказчик|разных работодателей|аутстафф|outstaff|entertainment|индустри\w* развлечений", text, re.I):
        notices.append("Домен работодателя/конечного клиента требует уточнения.")
    return HabrPolicy(assessment, recommendation, seniority, tuple(notices))


class HabrDiscovery:
    def __init__(self, config: HabrDiscoveryConfig, *, was_sent, log=lambda _: None, clock=time.time,
                 rss_fetch=collect_rss, detail_factory=HabrDetailClient, constraints=None, eligible_decision=None):
        self.config, self.was_sent, self.log, self.clock = config, was_sent, log, clock
        self.rss_fetch, self.detail_factory = rss_fetch, detail_factory
        self.eligible_decision = eligible_decision or (lambda decision: decision == "STRONG_MATCH")
        self.constraints = constraints if constraints is not None else load_candidate_constraints()
        self.state_path = config.state_dir / "discovery.json"
        self.state = {}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
                if not isinstance(self.state, dict) or self.state.get("version") != 1:
                    raise ValueError("invalid state")
            except (OSError, ValueError) as exc:
                raise ValueError("invalid Habr discovery state") from exc
        self.analysis_cache = TargetCompanyAnalysisCache(config.state_dir / "analysis.json")
        self.analysis_cache.load()

    def _save(self):
        self.state["version"] = 1
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False))
        temp.replace(self.state_path)

    @staticmethod
    def _key(vacancy):
        # Keep pubDate bumps out of the technical key; include relevant metadata
        # and completeness so RSS->full upgrades are analyzed at most once each.
        return replace(vacancy, description="\n".join([vacancy.title, vacancy.company or "", vacancy.location or "",
                                                     vacancy.content_completeness or "PARTIAL", vacancy.description]))

    def analyze(self, vacancy, analyzer):
        cached = self.analysis_cache.get(self._key(vacancy))
        if cached:
            evaluation = cached.evaluation
            self.log("Habr analysis: cache_hit=1")
        else:
            try:
                evaluation = analyzer.analyze(vacancy.to_analysis_text(), content_completeness=vacancy.content_completeness or "PARTIAL")
            except Exception:
                self.state.setdefault("analysis_retry_after", {})[vacancy.external_id] = self.clock() + self.config.interval_seconds
                self._save()
                raise
        policy = habr_policy(vacancy, evaluation, self.constraints)
        if not cached:
            self.analysis_cache.put(self._key(vacancy), evaluation=evaluation, feasibility=policy.feasibility,
                                    recommendation=policy.recommendation, seniority=policy.seniority)
            self.analysis_cache.save()
        return evaluation, policy

    def _resolved(self, vacancy):
        if self.was_sent(vacancy):
            return True
        cached = self.analysis_cache.get(self._key(vacancy))
        return bool(cached and (not self.eligible_decision(cached.evaluation.decision.value)
                    or habr_policy(vacancy, cached.evaluation, self.constraints).recommendation.label == "SKIP"))

    def collect(self) -> list[NormalizedVacancy]:
        now = self.clock()
        policy = load_domain_policy()
        due = "last_attempt" not in self.state or now - self.state["last_attempt"] >= self.config.interval_seconds
        if due:
            result = self.rss_fetch(self.config.rss)
            self.state["last_attempt"] = now
            # Failures keep the old pool for retry/delivery without new LLM work.
            if any(feed.pages for feed in result.feeds):
                candidates = []
                for record in result.records:
                    row = evaluate_record(record, policy=policy, now=datetime.fromtimestamp(now, timezone.utc),
                                          max_age_days=self.config.rss.max_age_days)
                    if row["fresh"] and row["jvm_candidate"] and not self.was_sent(record.vacancy):
                        candidates.append(record.vacancy)
                candidates.sort(key=lambda v: (employment_priority(v), -(datetime.fromisoformat(v.published_at).timestamp())))
                details = self.detail_factory(self.config.state_dir / "details.json", timeout=self.config.rss.timeout_seconds,
                    ttl_seconds=self.config.detail_ttl_seconds, min_interval_seconds=self.config.detail_request_interval_seconds)
                pool = []
                complete = partial = unavailable = expired = 0
                for vacancy in candidates[:100]:
                    detail = details.fetch(vacancy.url, allow_network=details.requests < self.config.max_detail_requests)
                    if detail.expired:
                        expired += 1
                        continue
                    complete += detail.completeness == "FULL"
                    partial += detail.completeness == "PARTIAL"
                    unavailable += detail.completeness == "UNAVAILABLE"
                    description = "RSS summary:\n" + vacancy.description
                    if detail.text:
                        description += "\nPublished vacancy description:\n" + detail.text
                    enriched = replace(vacancy, description=description,
                                       content_completeness="FULL" if detail.completeness == "FULL" else "PARTIAL")
                    if not excluded_domain_reason(company=enriched.company or "", source="habr", text=description, policy=policy):
                        pool.append(enriched)
                self.state["pool"] = [asdict(v) for v in pool]
                if "bootstrap_ids" not in self.state:
                    self.state["bootstrap_ids"] = [v.external_id for v in pool]
                pending = [v for v in pool if not self._resolved(v)]
                limit = min(self.config.batch_limit, self.config.initial_backlog_limit) if any(
                    v.external_id in self.state["bootstrap_ids"] for v in pending) else self.config.batch_limit
                self.state["batch_ids"] = [v.external_id for v in pending[:limit]]
                errors = sum(f.stop not in {"empty", "page_limit", "repeated_page"} for f in result.feeds)
                self.log(f"Habr collect: raw={sum(f.raw for f in result.feeds)} unique={len(result.records)} "
                         f"candidate={len(candidates)} full={complete} partial={partial} unavailable={unavailable} "
                         f"expired={expired} detail_requests={details.requests} cache_hits={details.cache_hits} feed_errors={errors}")
            else:
                codes = ",".join(f.stop + ":" + str(f.status) for f in result.feeds)
                self.log("Habr collect: rss_unavailable=" + codes)
            self._save()
        selected = []
        for data in self.state.get("pool", []):
            vacancy = NormalizedVacancy(**data)
            identity = canonical_habr_url(vacancy.url)
            if (not identity or identity[0] != vacancy.external_id or vacancy.source != "habr"
                    or vacancy.external_id not in self.state.get("batch_ids", []) or self.was_sent(vacancy)):
                continue
            if self.state.get("analysis_retry_after", {}).get(vacancy.external_id, 0) > now:
                continue
            published = datetime.fromisoformat(vacancy.published_at) if vacancy.published_at else None
            if not published or not 0 <= now - published.timestamp() <= self.config.rss.max_age_days * 86400:
                continue
            if self._resolved(vacancy):
                continue
            selected.append(vacancy)
        self.log(f"Habr batch: selected={len(selected)} poll_due={int(due)} initial_limit={self.config.initial_backlog_limit}")
        return selected
