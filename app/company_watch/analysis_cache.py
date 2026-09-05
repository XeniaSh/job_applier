from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

from pydantic import ValidationError

from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.application_recommendation import ApplicationRecommendation
from app.company_watch.feasibility import ApplicationFeasibility
from app.company_watch.seniority import SeniorityClassification
from app.models import VacancyEvaluation

DEFAULT_TARGET_COMPANY_ANALYSIS_CACHE_PATH = Path("data/target_company_analysis_cache.json")
_CACHE_VERSION = 1


@dataclass(frozen=True)
class CachedTargetCompanyAnalysis:
    evaluation: VacancyEvaluation
    feasibility: ApplicationFeasibility
    recommendation: ApplicationRecommendation
    seniority: SeniorityClassification


class TargetCompanyAnalysisCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, object]] = {}

    def load(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            self._entries = {}
            return
        if not raw.strip():
            self._entries = {}
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._entries = {}
            return
        entries = _extract_entries(payload)
        loaded: dict[str, dict[str, object]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = _key_from_entry(item)
            if key is None:
                continue
            loaded[key] = item
        self._entries = loaded

    def get(self, vacancy: NormalizedVacancy) -> CachedTargetCompanyAnalysis | None:
        entry = self._entries.get(_vacancy_key(vacancy))
        if entry is None:
            return None
        return _record_from_entry(entry)

    def put(
        self,
        vacancy: NormalizedVacancy,
        *,
        evaluation: VacancyEvaluation,
        feasibility: ApplicationFeasibility,
        recommendation: ApplicationRecommendation,
        seniority: SeniorityClassification,
    ) -> None:
        desc_hash = vacancy_description_hash(vacancy)
        entry: dict[str, object] = {
            "source": vacancy.source,
            "external_id": vacancy.external_id,
            "description_hash": desc_hash,
            "company": vacancy.company,
            "title": vacancy.title,
            "location": vacancy.location,
            "url": vacancy.url,
            "score": evaluation.match_percentage,
            "decision": evaluation.decision.value,
            "decision_reason": evaluation.decision_reason,
            "matched_points": list(evaluation.matched_points),
            "reasons": {
                "matched": list(evaluation.matched_points),
                "decision_reason": evaluation.decision_reason or None,
            },
            "application_feasibility": feasibility.label,
            "visa_sponsorship": feasibility.visa_sponsorship,
            "relocation_support": feasibility.relocation_support,
            "remote_type": feasibility.remote_type,
            "work_authorization_requirement": feasibility.work_authorization_requirement,
            "language_requirements": list(feasibility.language_requirements),
            "location_restrictions": list(feasibility.location_restrictions),
            "feasibility_warnings": list(feasibility.warnings),
            "application_recommendation": recommendation.label,
            "recommendation_reasons": list(recommendation.reasons),
            "seniority": seniority.label,
            "seniority_reasons": list(seniority.reasons),
            "evaluation": evaluation.model_dump(mode="json"),
        }
        self._entries[_entry_key(vacancy.source, vacancy.external_id, desc_hash)] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": _CACHE_VERSION,
            "entries": list(self._entries.values()),
        }
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.path)


def vacancy_description_hash(vacancy: NormalizedVacancy) -> str:
    description = (vacancy.description or "").strip()
    if description:
        payload = description
    else:
        payload = "\n".join(
            (
                (vacancy.title or "").strip(),
                (vacancy.location or "").strip(),
                (vacancy.url or "").strip(),
            )
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _vacancy_key(vacancy: NormalizedVacancy) -> str:
    return _entry_key(vacancy.source, vacancy.external_id, vacancy_description_hash(vacancy))


def _entry_key(source: str, external_id: str, description_hash: str) -> str:
    return f"{source}\x1f{external_id}\x1f{description_hash}"


def _extract_entries(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    entries = payload.get("entries")
    if isinstance(entries, list):
        return entries
    if isinstance(entries, dict):
        return list(entries.values())
    return []


def _key_from_entry(entry: dict[str, object]) -> str | None:
    source = entry.get("source")
    external_id = entry.get("external_id")
    description_hash = entry.get("description_hash")
    if not isinstance(source, str) or not isinstance(external_id, str) or not isinstance(description_hash, str):
        return None
    if not source or not external_id or not description_hash:
        return None
    return _entry_key(source, external_id, description_hash)


def _record_from_entry(entry: dict[str, object]) -> CachedTargetCompanyAnalysis | None:
    raw_evaluation = entry.get("evaluation")
    if not isinstance(raw_evaluation, dict):
        return None
    try:
        evaluation = VacancyEvaluation.model_validate(raw_evaluation)
        feasibility = ApplicationFeasibility(
            label=str(entry.get("application_feasibility") or "UNCLEAR"),
            visa_sponsorship=str(entry.get("visa_sponsorship") or "unknown"),
            relocation_support=str(entry.get("relocation_support") or "unknown"),
            remote_type=str(entry.get("remote_type") or "unknown"),
            work_authorization_requirement=str(
                entry.get("work_authorization_requirement") or "unknown"
            ),
            language_requirements=_string_list(entry.get("language_requirements")),
            location_restrictions=_string_list(entry.get("location_restrictions")),
            warnings=_string_list(entry.get("feasibility_warnings")),
        )
        recommendation = ApplicationRecommendation(
            label=str(entry.get("application_recommendation") or "CHECK_MANUALLY"),
            reasons=_string_list(entry.get("recommendation_reasons")),
        )
        seniority = SeniorityClassification(
            label=str(entry.get("seniority") or "UNKNOWN"),
            reasons=_string_list(entry.get("seniority_reasons")),
        )
    except (TypeError, ValueError, KeyError, ValidationError):
        return None
    return CachedTargetCompanyAnalysis(
        evaluation=evaluation,
        feasibility=feasibility,
        recommendation=recommendation,
        seniority=seniority,
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
