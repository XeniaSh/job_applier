from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from app.collectors.email_imap_client import EmailIMAPClient, RawEmailMessage
from app.collectors.linkedin_email_collector import LinkedInEmailCollector
from app.collectors.linkedin_models import ContentCompleteness, LinkedInEmailVacancy
from app.models import (
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)
from app.storage.imap_checkpoint import ImapCheckpointStorage


def _message_bytes(message_id: str) -> bytes:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "Job alert"
    message["Message-ID"] = message_id
    message.set_content("https://www.linkedin.com/jobs/view/123/")
    return message.as_bytes()


class _FakeIMAP:
    def __init__(self, *, uids: list[int], uidvalidity: str = "1") -> None:
        self._uids = sorted(uids)
        self._uidvalidity = uidvalidity
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []

    def login(self, username: str, password: str):
        _ = (username, password)
        return ("OK", [b"logged"])

    def select(self, mailbox: str):
        _ = mailbox
        return ("OK", [b"1"])

    def uid(self, command: str, *args: str):
        lower = command.lower()
        if lower == "search":
            criteria = " ".join(str(part) for part in args if part is not None)
            self.search_calls.append(criteria)
            if len(args) >= 2 and str(args[0]).upper() == "UID":
                start = int(str(args[1]).split(":")[0].strip())
                matched = [uid for uid in self._uids if uid >= start]
            else:
                matched = list(self._uids)
            payload = " ".join(str(uid) for uid in matched).encode("utf-8")
            return ("OK", [payload])
        if lower == "fetch":
            uid = str(args[0])
            self.fetch_calls.append(uid)
            return ("OK", [(b"RFC822", _message_bytes(f"<{uid}>"))])
        if lower == "store":
            return ("OK", [b"stored"])
        return ("NO", [])

    def response(self, code: str):
        if code == "UIDVALIDITY":
            return ("UIDVALIDITY", [self._uidvalidity.encode("utf-8")])
        return (code, [])

    def close(self):
        return ("OK", [])

    def logout(self):
        return ("BYE", [])


class _SeenJobs:
    def is_seen(self, source: str, external_id: str) -> bool:
        _ = (source, external_id)
        return False

    def mark_seen(self, source: str, external_id: str) -> None:
        _ = (source, external_id)


class _Analyzer:
    def analyze(self, vacancy: str, content_completeness: str = "FULL") -> VacancyEvaluation:
        _ = (vacancy, content_completeness)
        return VacancyEvaluation(
            decision=Decision.POTENTIAL_MATCH,
            summary="ok",
            matched_points=[],
            gaps=[],
            nuances=[],
            match_percentage=None,
            matched_score=0.0,
            total_possible_score=0.0,
            recommended_resume=RecommendedResume.JAVA_BACKEND,
            recommended_cover_template=RecommendedCoverTemplate.GENERIC,
        )


def _collector(tmp_path: Path, adapter: _FakeIMAP, *, uidvalidity: str = "1") -> LinkedInEmailCollector:
    _ = uidvalidity
    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="person@example.com",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=adapter,
    )
    storage = ImapCheckpointStorage(db_path=tmp_path / "jobs.db")
    return LinkedInEmailCollector(
        email_client=client,
        analyzer=_Analyzer(),
        seen_jobs=_SeenJobs(),
        checkpoint_storage=storage,
        incremental_enabled=True,
        bootstrap_message_limit=2,
        bootstrap_lookback_days=7,
        batch_size=50,
    )


def _patch_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def _parser(raw_message: RawEmailMessage) -> list[LinkedInEmailVacancy]:
        external_id = raw_message.uid
        return [
            LinkedInEmailVacancy(
                external_id=external_id,
                title=f"Role {external_id}",
                company="Acme",
                location="Remote",
                url=f"https://www.linkedin.com/jobs/view/{external_id}/",
                snippet=None,
                email_message_id=raw_message.message_id,
                received_at=datetime.now(timezone.utc),
                content_completeness=ContentCompleteness.MINIMAL,
            )
        ]

    monkeypatch.setattr("app.collectors.linkedin_email_collector.parse_linkedin_email", _parser)


def test_bootstrap_first_run_is_bounded_and_saves_highest_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    adapter = _FakeIMAP(uids=[100, 101, 102, 103])
    collector = _collector(tmp_path, adapter)

    report = collector.collect_and_analyze(limit=20, dry_run=True)

    assert report.emails_found == 2
    assert report.vacancies_extracted == 2
    assert any("SINCE" in call for call in adapter.search_calls)
    checkpoint = collector._checkpoint_storage.get(  # noqa: SLF001
        source=collector.SOURCE,
        account_key=collector._account_key(),  # noqa: SLF001
        folder="INBOX",
    )
    assert checkpoint is not None
    assert checkpoint.last_uid == 103


def test_next_run_is_incremental_only_newer_uids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    adapter = _FakeIMAP(uids=[10, 11, 12])
    collector = _collector(tmp_path, adapter)
    collector.collect_and_analyze(limit=20, dry_run=True)

    adapter2 = _FakeIMAP(uids=[10, 11, 12, 13, 14])
    collector2 = _collector(tmp_path, adapter2)
    report = collector2.collect_and_analyze(limit=20, dry_run=True)

    assert any("UID 13:*" in call for call in adapter2.search_calls)
    assert report.emails_found == 2
    assert adapter2.fetch_calls == ["13", "14"]


def test_empty_incremental_cycle_fetches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    first = _collector(tmp_path, _FakeIMAP(uids=[20, 21]))
    first.collect_and_analyze(limit=20, dry_run=True)

    second_adapter = _FakeIMAP(uids=[20, 21])
    second = _collector(tmp_path, second_adapter)
    report = second.collect_and_analyze(limit=20, dry_run=True)

    assert report.emails_found == 0
    assert second_adapter.fetch_calls == []
    diagnostics = second.last_sync_diagnostics()
    assert diagnostics.sync_mode == "incremental"
    assert diagnostics.messages_fetched == 0


def test_checkpoint_not_advanced_on_batch_failure_and_retried_next_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    collector = _collector(tmp_path, _FakeIMAP(uids=[30, 31]))
    collector.collect_and_analyze(limit=20, dry_run=True)

    def _explode(_raw_message: RawEmailMessage):
        raise KeyboardInterrupt("fatal")

    monkeypatch.setattr("app.collectors.linkedin_email_collector.parse_linkedin_email", _explode)
    failing_adapter = _FakeIMAP(uids=[30, 31, 32])
    failing_collector = _collector(tmp_path, failing_adapter)
    with pytest.raises(KeyboardInterrupt):
        failing_collector.collect_and_analyze(limit=20, dry_run=True)

    checkpoint = failing_collector._checkpoint_storage.get(  # noqa: SLF001
        source=failing_collector.SOURCE,
        account_key=failing_collector._account_key(),  # noqa: SLF001
        folder="INBOX",
    )
    assert checkpoint is not None
    assert checkpoint.last_uid == 31

    _patch_parser(monkeypatch)
    retry_adapter = _FakeIMAP(uids=[30, 31, 32])
    retry_collector = _collector(tmp_path, retry_adapter)
    retry_collector.collect_and_analyze(limit=20, dry_run=True)
    assert any("UID 32:*" in call for call in retry_adapter.search_calls)


def test_uidvalidity_change_forces_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    first = _collector(tmp_path, _FakeIMAP(uids=[40, 41], uidvalidity="1"))
    first.collect_and_analyze(limit=20, dry_run=True)

    changed_adapter = _FakeIMAP(uids=[40, 41, 42], uidvalidity="2")
    changed = _collector(tmp_path, changed_adapter)
    changed.collect_and_analyze(limit=20, dry_run=True)
    diagnostics = changed.last_sync_diagnostics()

    assert diagnostics.uidvalidity_changed is True
    assert diagnostics.sync_mode == "bootstrap"
    assert any("SINCE" in call for call in changed_adapter.search_calls)


def test_rescan_does_not_overwrite_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    first = _collector(tmp_path, _FakeIMAP(uids=[50, 51]))
    first.collect_and_analyze(limit=20, dry_run=True)

    rescan = _collector(tmp_path, _FakeIMAP(uids=[50, 51, 52, 53]))
    rescan.collect_and_analyze(limit=20, dry_run=True, rescan=True)
    checkpoint = rescan._checkpoint_storage.get(  # noqa: SLF001
        source=rescan.SOURCE,
        account_key=rescan._account_key(),  # noqa: SLF001
        folder="INBOX",
    )
    assert checkpoint is not None
    assert checkpoint.last_uid == 51


def test_reset_checkpoint_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)
    collector = _collector(tmp_path, _FakeIMAP(uids=[60]))
    collector.collect_and_analyze(limit=20, dry_run=True)
    assert collector.reset_checkpoint() is True
    assert collector.reset_checkpoint() is False


def test_rescan_diagnostics_explain_zero_parsed_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parser(monkeypatch)

    class _NonLinkedInIMAP(_FakeIMAP):
        def uid(self, command: str, *args: str):
            lower = command.lower()
            if lower == "search":
                return ("OK", [b" ".join(str(i).encode("ascii") for i in range(1, 26))])
            if lower == "fetch":
                uid = str(args[0])
                message = EmailMessage()
                message["From"] = "digest@example.com"
                message["Subject"] = "Daily digest"
                message["Message-ID"] = f"<{uid}>"
                message.set_content("body")
                return ("OK", [(f"{uid} (RFC822 {{123}}".encode("ascii"), message.as_bytes()), b")"])
            return super().uid(command, *args)

    client = EmailIMAPClient(
        host="imap.gmail.com",
        port=993,
        username="person@example.com",
        password="app-password",
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        adapter=_NonLinkedInIMAP(uids=list(range(1, 26))),
    )
    collector = LinkedInEmailCollector(
        email_client=client,
        analyzer=_Analyzer(),
        seen_jobs=_SeenJobs(),
        checkpoint_storage=ImapCheckpointStorage(db_path=tmp_path / "jobs.db"),
        incremental_enabled=True,
        bootstrap_message_limit=50,
        bootstrap_lookback_days=7,
        batch_size=50,
    )
    report = collector.collect_and_analyze(limit=20, dry_run=True, rescan=True)
    diagnostics = collector.last_sync_diagnostics()

    assert report.emails_found == 0
    assert diagnostics.sync_mode == "rescan"
    assert diagnostics.searched_uids == 25
    assert diagnostics.fetch_succeeded == 25
    assert diagnostics.decode_succeeded == 25
    assert diagnostics.rejected_sender == 25
    assert diagnostics.rejected_subject == 0
    assert diagnostics.parse_errors == 0
    assert diagnostics.messages_parsed == 0
    assert diagnostics.vacancies_extracted == 0

