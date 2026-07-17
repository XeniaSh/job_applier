from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.collectors.email_imap_client import EmailIMAPClient
from app.collectors.linkedin_email_parser import parse_linkedin_email
from app.collectors.linkedin_models import LinkedInEmailVacancy
from app.collectors.title_filter import should_accept_title
from app.collectors.vacancy_collector import NormalizedVacancy, VacancyCollector
from app.models import Decision, VacancyEvaluation
from app.storage.seen_jobs import SeenJobsStorage
from app.vacancy_analyzer import VacancyAnalyzer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkedInProcessedVacancy:
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    content_completeness: str
    evaluation: VacancyEvaluation | None
    skipped_by_prefilter: bool = False
    source: str = "linkedin-email"


@dataclass
class LinkedInEmailCollectReport:
    emails_found: int = 0
    vacancies_extracted: int = 0
    unique_vacancies: int = 0
    already_seen: int = 0
    already_delivered: int = 0
    new_vacancies: int = 0
    prepared_cards: int = 0
    sent: int = 0
    send_errors: int = 0
    analyzed: int = 0
    strong_matches: int = 0
    potential_matches: int = 0
    ignored: int = 0
    prefiltered: int = 0
    errors: int = 0
    processed: list[LinkedInProcessedVacancy] = field(default_factory=list)


class LinkedInEmailCollector(VacancyCollector):
    SOURCE = "linkedin-email"

    def __init__(
        self,
        email_client: EmailIMAPClient,
        analyzer: VacancyAnalyzer,
        seen_jobs: SeenJobsStorage,
    ) -> None:
        self._email_client = email_client
        self._analyzer = analyzer
        self._seen_jobs = seen_jobs

    def collect_and_analyze(
        self,
        *,
        limit: int = 20,
        dry_run: bool = False,
        skip_seen: bool = True,
        analyze_in_dry_run: bool = False,
        mark_seen: bool = True,
    ) -> LinkedInEmailCollectReport:
        report = LinkedInEmailCollectReport()
        unique_vacancies, emails_found, parse_errors, extracted = self._collect_unique_vacancies()
        report.emails_found = emails_found
        report.errors += parse_errors
        report.vacancies_extracted = extracted
        limited_vacancies = unique_vacancies[:limit]
        report.unique_vacancies = len(limited_vacancies)
        report.new_vacancies = 0

        for vacancy in limited_vacancies:
            is_seen = self._seen_jobs.is_seen(self.SOURCE, vacancy.external_id)
            if is_seen:
                report.already_seen += 1
                if skip_seen:
                    continue
            report.new_vacancies += 1

            if not should_accept_title(vacancy.title):
                report.prefiltered += 1
                report.processed.append(
                    LinkedInProcessedVacancy(
                        external_id=vacancy.external_id,
                        title=vacancy.title,
                        company=vacancy.company,
                        location=vacancy.location,
                        url=vacancy.url,
                        content_completeness=vacancy.content_completeness.value,
                        evaluation=None,
                        skipped_by_prefilter=True,
                    )
                )
                if mark_seen and not dry_run:
                    self._seen_jobs.mark_seen(self.SOURCE, vacancy.external_id)
                continue

            if dry_run and not analyze_in_dry_run:
                report.processed.append(
                    LinkedInProcessedVacancy(
                        external_id=vacancy.external_id,
                        title=vacancy.title,
                        company=vacancy.company,
                        location=vacancy.location,
                        url=vacancy.url,
                        content_completeness=vacancy.content_completeness.value,
                        evaluation=None,
                    )
                )
                continue

            try:
                evaluation = self._analyzer.analyze(
                    vacancy.to_analysis_text(),
                    content_completeness=vacancy.content_completeness.value,
                )
            except Exception as exc:  # noqa: BLE001
                report.errors += 1
                logger.error("LinkedIn vacancy %s failed: %s", vacancy.external_id, exc)
                continue

            if mark_seen and not dry_run:
                self._seen_jobs.mark_seen(self.SOURCE, vacancy.external_id)
            report.analyzed += 1
            if evaluation.decision == Decision.STRONG_MATCH:
                report.strong_matches += 1
            elif evaluation.decision == Decision.POTENTIAL_MATCH:
                report.potential_matches += 1
            else:
                report.ignored += 1
            report.processed.append(
                LinkedInProcessedVacancy(
                    external_id=vacancy.external_id,
                    title=vacancy.title,
                    company=vacancy.company,
                    location=vacancy.location,
                    url=vacancy.url,
                    content_completeness=vacancy.content_completeness.value,
                    evaluation=evaluation,
                )
            )

        return report

    def collect(self) -> list[NormalizedVacancy]:
        unique_vacancies, _, _, _ = self._collect_unique_vacancies()
        normalized: list[NormalizedVacancy] = []
        for vacancy in unique_vacancies:
            normalized.append(
                NormalizedVacancy(
                    source=self.SOURCE,
                    external_id=vacancy.external_id,
                    title=vacancy.title,
                    company=vacancy.company,
                    location=vacancy.location,
                    employment=None,
                    description=vacancy.to_analysis_text(),
                    url=vacancy.url,
                    published_at=vacancy.received_at.isoformat() if vacancy.received_at else None,
                )
            )
        return normalized

    def _collect_unique_vacancies(self) -> tuple[list[LinkedInEmailVacancy], int, int, int]:
        raw_messages = self._email_client.fetch_linkedin_messages()
        unique_vacancies: dict[str, LinkedInEmailVacancy] = {}
        parse_errors = 0
        extracted = 0
        for raw_message in raw_messages:
            try:
                vacancies = parse_linkedin_email(raw_message)
            except Exception as exc:  # noqa: BLE001
                parse_errors += 1
                logger.error("LinkedIn email parse failed: %s", exc)
                continue

            extracted += len(vacancies)
            for vacancy in vacancies:
                if vacancy.external_id not in unique_vacancies:
                    unique_vacancies[vacancy.external_id] = vacancy

        return list(unique_vacancies.values()), len(raw_messages), parse_errors, extracted
