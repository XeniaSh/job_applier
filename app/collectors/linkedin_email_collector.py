from __future__ import annotations

import logging
import hashlib
import time
from dataclasses import dataclass, field

from app.collectors.email_imap_client import EmailIMAPClient, ImapSyncResult
from app.collectors.linkedin_email_parser import parse_linkedin_email
from app.collectors.linkedin_models import LinkedInEmailVacancy
from app.collectors.title_filter import should_accept_title
from app.collectors.vacancy_collector import NormalizedVacancy, VacancyCollector
from app.models import Decision, VacancyEvaluation
from app.storage.imap_checkpoint import ImapCheckpointStorage
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


@dataclass(frozen=True)
class LinkedInSyncDiagnostics:
    sync_mode: str
    checkpoint_before: int | None
    checkpoint_after: int | None
    highest_uid_seen: int | None
    messages_matched: int
    messages_fetched: int
    messages_parsed: int
    vacancies_extracted: int
    search_criteria: str
    checkpoint_advanced: bool
    uidvalidity_changed: bool
    timings_ms: dict[str, int]


class LinkedInEmailCollector(VacancyCollector):
    SOURCE = "linkedin-email"

    def __init__(
        self,
        email_client: EmailIMAPClient,
        analyzer: VacancyAnalyzer,
        seen_jobs: SeenJobsStorage,
        checkpoint_storage: ImapCheckpointStorage | None = None,
        incremental_enabled: bool = True,
        bootstrap_message_limit: int = 500,
        bootstrap_lookback_days: int = 7,
        batch_size: int = 200,
    ) -> None:
        self._email_client = email_client
        self._analyzer = analyzer
        self._seen_jobs = seen_jobs
        self._checkpoint_storage = checkpoint_storage
        self._incremental_enabled = incremental_enabled
        self._bootstrap_message_limit = max(1, int(bootstrap_message_limit))
        self._bootstrap_lookback_days = max(1, int(bootstrap_lookback_days))
        self._batch_size = max(1, int(batch_size))
        self._last_sync_diagnostics = LinkedInSyncDiagnostics(
            sync_mode="bootstrap",
            checkpoint_before=None,
            checkpoint_after=None,
            highest_uid_seen=None,
            messages_matched=0,
            messages_fetched=0,
            messages_parsed=0,
            vacancies_extracted=0,
            search_criteria="",
            checkpoint_advanced=False,
            uidvalidity_changed=False,
            timings_ms={},
        )

    def collect_and_analyze(
        self,
        *,
        limit: int = 20,
        dry_run: bool = False,
        skip_seen: bool = True,
        analyze_in_dry_run: bool = False,
        mark_seen: bool = True,
        rescan: bool = False,
    ) -> LinkedInEmailCollectReport:
        report = LinkedInEmailCollectReport()
        unique_vacancies, emails_found, parse_errors, extracted = self._collect_unique_vacancies(rescan=rescan)
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

    def reset_checkpoint(self) -> bool:
        if self._checkpoint_storage is None:
            return False
        return self._checkpoint_storage.reset(
            source=self.SOURCE,
            account_key=self._account_key(),
            folder=self._email_client.folder,
        )

    def last_sync_diagnostics(self) -> LinkedInSyncDiagnostics:
        return self._last_sync_diagnostics

    def _collect_unique_vacancies(self, *, rescan: bool = False) -> tuple[list[LinkedInEmailVacancy], int, int, int]:
        checkpoint_before = None
        checkpoint_uidvalidity = None
        checkpoint = None
        folder = self._email_client.folder if self._checkpoint_storage is not None else ""
        if self._checkpoint_storage is not None:
            checkpoint = self._checkpoint_storage.get(
                source=self.SOURCE,
                account_key=self._account_key(),
                folder=folder,
            )
            if checkpoint is not None:
                checkpoint_before = checkpoint.last_uid
                checkpoint_uidvalidity = checkpoint.uidvalidity

        sync_result: ImapSyncResult
        if self._checkpoint_storage is None:
            raw_messages = self._email_client.fetch_linkedin_messages()
            sync_result = ImapSyncResult(
                mode="bootstrap",
                checkpoint_before=checkpoint_before,
                checkpoint_after=checkpoint_before,
                highest_uid_seen=checkpoint_before,
                uidvalidity=checkpoint_uidvalidity,
                uidvalidity_changed=False,
                messages_matched=len(raw_messages),
                messages_fetched=len(raw_messages),
                search_criteria=f'SINCE "{self._bootstrap_lookback_days}d-legacy"',
                timings_ms={},
                messages=raw_messages,
            )
        else:
            sync_result = self._email_client.fetch_linkedin_messages_sync(
                checkpoint_uid=checkpoint_before,
                checkpoint_uidvalidity=checkpoint_uidvalidity,
                incremental_enabled=self._incremental_enabled,
                bootstrap_lookback_days=self._bootstrap_lookback_days,
                bootstrap_message_limit=self._bootstrap_message_limit,
                batch_size=self._batch_size,
                rescan=rescan,
            )
            raw_messages = sync_result.messages
            if sync_result.uidvalidity_changed:
                logger.warning("LinkedIn IMAP UIDVALIDITY changed for folder %s; running bootstrap sync.", folder)

        unique_vacancies: dict[str, LinkedInEmailVacancy] = {}
        parse_errors = 0
        extracted = 0
        parse_start = time.monotonic()
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
        parse_ms = max(0, int((time.monotonic() - parse_start) * 1000))

        checkpoint_advanced = False
        checkpoint_start = time.monotonic()
        if (
            self._checkpoint_storage is not None
            and not rescan
            and sync_result.checkpoint_after is not None
            and (
                checkpoint_before is None
                or int(sync_result.checkpoint_after) > int(checkpoint_before)
                or sync_result.uidvalidity_changed
            )
        ):
            self._checkpoint_storage.save(
                source=self.SOURCE,
                account_key=self._account_key(),
                folder=folder,
                last_uid=int(sync_result.checkpoint_after),
                uidvalidity=sync_result.uidvalidity,
            )
            checkpoint_advanced = True
        checkpoint_ms = max(0, int((time.monotonic() - checkpoint_start) * 1000))
        timings = dict(sync_result.timings_ms)
        timings["parse"] = parse_ms
        timings["checkpoint"] = checkpoint_ms

        self._last_sync_diagnostics = LinkedInSyncDiagnostics(
            sync_mode=sync_result.mode,
            checkpoint_before=sync_result.checkpoint_before,
            checkpoint_after=sync_result.checkpoint_after,
            highest_uid_seen=sync_result.highest_uid_seen,
            messages_matched=sync_result.messages_matched,
            messages_fetched=sync_result.messages_fetched,
            messages_parsed=len(raw_messages),
            vacancies_extracted=extracted,
            search_criteria=sync_result.search_criteria,
            checkpoint_advanced=checkpoint_advanced,
            uidvalidity_changed=sync_result.uidvalidity_changed,
            timings_ms=timings,
        )
        return list(unique_vacancies.values()), len(raw_messages), parse_errors, extracted

    def _account_key(self) -> str:
        username = self._email_client.username.strip().lower()
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
