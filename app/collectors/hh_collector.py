from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.collectors.hh_client import HHClient, HHRequestError
from app.collectors.models import HHVacancyDetails, HHVacancyPreview
from app.collectors.title_filter import should_accept_title
from app.models import Decision, VacancyEvaluation
from app.storage.seen_jobs import SeenJobsStorage
from app.vacancy_analyzer import VacancyAnalyzer

DEFAULT_HH_QUERIES = [
    "Java Backend",
    "Java Spring Boot",
    "Kotlin Backend",
    "JVM Developer",
    "Java Kafka",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessedVacancy:
    external_id: str
    title: str
    company: str
    url: str
    evaluation: VacancyEvaluation


@dataclass
class HHCollectReport:
    new_found: int = 0
    analyzed: int = 0
    strong_matches: int = 0
    potential_matches: int = 0
    ignored: int = 0
    errors: int = 0
    processed: list[ProcessedVacancy] = field(default_factory=list)
    search_errors: int = 0
    successful_searches: int = 0


class HHCollector:
    SOURCE = "hh"

    def __init__(
        self,
        hh_client: HHClient,
        analyzer: VacancyAnalyzer,
        seen_jobs: SeenJobsStorage,
    ) -> None:
        self._hh_client = hh_client
        self._analyzer = analyzer
        self._seen_jobs = seen_jobs

    def collect_and_analyze(
        self,
        *,
        queries: list[str] | None = None,
        limit: int = 20,
    ) -> HHCollectReport:
        report = HHCollectReport()
        selected_queries = queries or list(DEFAULT_HH_QUERIES)
        seen_ids_in_run: set[str] = set()

        for query in selected_queries:
            if report.analyzed >= limit:
                break
            try:
                raw_previews = self._hh_client.search_vacancies(query=query, page=0, per_page=20)
                report.successful_searches += 1
            except HHRequestError as exc:
                report.search_errors += 1
                logger.error("HH search failed for query '%s': %s", query, exc)
                continue

            for raw_preview in raw_previews:
                if report.analyzed >= limit:
                    break
                preview = HHVacancyPreview.from_hh_payload(raw_preview)
                if not preview.external_id:
                    continue
                if preview.external_id in seen_ids_in_run:
                    continue
                seen_ids_in_run.add(preview.external_id)

                if self._seen_jobs.is_seen(self.SOURCE, preview.external_id):
                    continue
                report.new_found += 1

                try:
                    raw_details = self._hh_client.get_vacancy(preview.external_id)
                    details = HHVacancyDetails.from_hh_payload(raw_details)

                    if not should_accept_title(details.title):
                        self._seen_jobs.mark_seen(self.SOURCE, details.external_id)
                        continue

                    evaluation = self._analyzer.analyze(details.to_analysis_text())
                    self._seen_jobs.mark_seen(self.SOURCE, details.external_id)

                    report.analyzed += 1
                    if evaluation.decision == Decision.STRONG_MATCH:
                        report.strong_matches += 1
                    elif evaluation.decision == Decision.POTENTIAL_MATCH:
                        report.potential_matches += 1
                    else:
                        report.ignored += 1

                    report.processed.append(
                        ProcessedVacancy(
                            external_id=details.external_id,
                            title=details.title,
                            company=details.company,
                            url=details.url,
                            evaluation=evaluation,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    report.errors += 1
                    logger.error("HH vacancy %s failed: %s", preview.external_id, exc)
                    continue

        return report
