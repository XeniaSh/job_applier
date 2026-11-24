from __future__ import annotations

import imaplib
import json
import math
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.cli as cli_module
from app.collectors import imap_helper
from app.collectors.email_imap_client import (
    EmailAuthenticationError,
    EmailConnectionError,
    EmailIMAPClient,
)
from app.collectors.linkedin_email_collector import LinkedInEmailCollector
from app.collectors.linkedin_models import ContentCompleteness, LinkedInEmailVacancy
from app.config import Settings
from app.storage.imap_checkpoint import ImapCheckpointStorage

from test_linkedin_imap_incremental import _Analyzer, _FakeIMAP, _SeenJobs

SECRET = "super-secret-app-password"


class _SilentPeer:
    """Loopback TCP listener that accepts connections and never sends a byte."""

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._conns: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        self._server.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                continue
            self._conns.append(conn)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for conn in self._conns:
            conn.close()
        self._server.close()


@pytest.fixture
def silent_peer():
    peer = _SilentPeer()
    yield peer
    peer.close()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen]:
    """Record every helper process started by the supervisor."""
    procs: list[subprocess.Popen] = []
    real_start = imap_helper._start_helper

    def _recording_start():
        proc = real_start()
        procs.append(proc)
        return proc

    monkeypatch.setattr(imap_helper, "_start_helper", _recording_start)
    return procs


def _fake_child(monkeypatch: pytest.MonkeyPatch, spawned: list, code: str) -> None:
    def _start():
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(imap_helper, "_start_helper", _start)


def _client(port: int, *, socket_timeout: float, collection_timeout: float) -> EmailIMAPClient:
    return EmailIMAPClient(
        host="127.0.0.1",
        port=port,
        username="person@example.com",
        password=SECRET,
        folder="INBOX",
        search_days=7,
        mark_as_read=False,
        socket_timeout_seconds=socket_timeout,
        collection_timeout_seconds=collection_timeout,
    )


def _sync(client: EmailIMAPClient):
    return client.fetch_linkedin_messages_sync(
        checkpoint_uid=None,
        checkpoint_uidvalidity=None,
        incremental_enabled=True,
        bootstrap_lookback_days=7,
        bootstrap_message_limit=10,
        batch_size=10,
        rescan=False,
    )


def _assert_reaped(procs: list[subprocess.Popen]) -> None:
    assert procs
    assert all(proc.poll() is not None for proc in procs)
    assert imap_helper._unreaped == []


# ------------------------------------------------------- real loopback helper


def test_silent_tls_peer_hits_socket_timeout_in_helper(silent_peer, spawned, monkeypatch) -> None:
    monkeypatch.chdir(Path(__file__).parent)  # helper must not depend on the caller's cwd
    client = _client(silent_peer.port, socket_timeout=0.5, collection_timeout=20)
    start = time.monotonic()
    with pytest.raises(EmailConnectionError) as excinfo:
        _sync(client)
    assert time.monotonic() - start < 15
    assert str(excinfo.value) == "IMAP connection timeout."
    assert excinfo.value.__cause__ is None
    assert SECRET not in str(excinfo.value)
    assert len(spawned) == 1  # no immediate retry
    _assert_reaped(spawned)


def test_hard_collection_deadline_beats_larger_socket_timeout(silent_peer, spawned) -> None:
    client = _client(silent_peer.port, socket_timeout=30, collection_timeout=1.5)
    start = time.monotonic()
    with pytest.raises(EmailConnectionError, match="collection timeout"):
        _sync(client)
    assert time.monotonic() - start < 1.5 + imap_helper.REAP_GRACE_SECONDS + 2
    _assert_reaped(spawned)


def test_list_mailboxes_is_also_supervised(silent_peer, spawned) -> None:
    client = _client(silent_peer.port, socket_timeout=30, collection_timeout=1.5)
    with pytest.raises(EmailConnectionError, match="collection timeout"):
        client.list_mailboxes()
    _assert_reaped(spawned)


def test_repeated_attempts_do_not_accumulate_children_or_threads(silent_peer, spawned) -> None:
    client = _client(silent_peer.port, socket_timeout=30, collection_timeout=1.0)
    threads_before = threading.active_count()
    for _ in range(3):
        with pytest.raises(EmailConnectionError):
            _sync(client)
    assert len(spawned) == 3  # exactly one spawn per attempt, no retries
    _assert_reaped(spawned)
    assert threading.active_count() <= threads_before


# ----------------------------------------------------------- misbehaving child


def test_hanging_child_that_ignores_sigterm_is_killed_and_reaped(monkeypatch, spawned) -> None:
    code = "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)"
    _fake_child(monkeypatch, spawned, code)
    start = time.monotonic()
    with pytest.raises(EmailConnectionError, match="collection timeout"):
        imap_helper.run_supervised(
            operation="list", config={"folder": "INBOX"}, args={}, timeout_seconds=1.0, reap_grace_seconds=1.0
        )
    assert time.monotonic() - start < 5
    _assert_reaped(spawned)


def test_trickling_child_cannot_extend_the_deadline(monkeypatch, spawned) -> None:
    code = (
        "import sys,time\n"
        "while True:\n"
        "    sys.stdout.write('x'); sys.stdout.flush(); time.sleep(0.05)\n"
    )
    _fake_child(monkeypatch, spawned, code)
    start = time.monotonic()
    with pytest.raises(EmailConnectionError, match="collection timeout"):
        imap_helper.run_supervised(
            operation="list", config={"folder": "INBOX"}, args={}, timeout_seconds=1.0, reap_grace_seconds=1.0
        )
    assert time.monotonic() - start < 5
    _assert_reaped(spawned)


@pytest.mark.parametrize(
    "code",
    [
        f"import sys; sys.stdout.write('{SECRET} partial'); sys.exit(3)",
        f"import sys; sys.stdout.write('{SECRET} not json')",
        f"import sys; sys.stdout.write('{{\"ok\": true, \"result\": \"{SECRET}\"}}')",
        f"import sys; sys.stdout.write('{{\"ok\": false, \"kind\": \"{SECRET}\"}}')",
        "pass",
    ],
)
def test_bad_helper_output_is_sanitized(monkeypatch, spawned, code) -> None:
    _fake_child(monkeypatch, spawned, code)
    with pytest.raises(EmailConnectionError) as excinfo:
        imap_helper.run_supervised(
            operation="list", config={"folder": "INBOX"}, args={}, timeout_seconds=10.0
        )
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    _assert_reaped(spawned)


def test_helper_spawn_failure_is_safe(monkeypatch) -> None:
    def _boom():
        raise OSError(f"cannot exec {SECRET}")

    monkeypatch.setattr(imap_helper, "_start_helper", _boom)
    with pytest.raises(EmailConnectionError) as excinfo:
        imap_helper.run_supervised(operation="list", config={}, args={}, timeout_seconds=5)
    assert SECRET not in str(excinfo.value)


def test_helper_reaped_on_keyboard_interrupt(monkeypatch, spawned) -> None:
    _fake_child(monkeypatch, spawned, "import time; time.sleep(60)")
    real_communicate = subprocess.Popen.communicate

    def _interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", _interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            imap_helper.run_supervised(
                operation="list", config={}, args={}, timeout_seconds=10, reap_grace_seconds=1.0
            )
    finally:
        monkeypatch.setattr(subprocess.Popen, "communicate", real_communicate)
    _assert_reaped(spawned)


def test_refuses_new_attempt_while_previous_helper_alive(monkeypatch) -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(imap_helper, "_unreaped", [sleeper])
    started: list[int] = []
    monkeypatch.setattr(imap_helper, "_start_helper", lambda: started.append(1))
    try:
        with pytest.raises(EmailConnectionError, match="still running"):
            imap_helper.run_supervised(operation="list", config={}, args={}, timeout_seconds=5)
        assert started == []
    finally:
        sleeper.kill()
        sleeper.wait()


# ------------------------------------------------------------------ wire format


def test_sync_result_wire_roundtrip_matches_direct_parse() -> None:
    adapter = _FakeIMAP(uids=[1, 2, 3])
    client = EmailIMAPClient(
        host="h", port=993, username="u", password="p", folder="INBOX",
        search_days=7, mark_as_read=False, adapter=adapter,
    )
    direct = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None, checkpoint_uidvalidity=None, incremental_enabled=True,
        bootstrap_lookback_days=7, bootstrap_message_limit=10, batch_size=10, rescan=False,
    )
    wire = json.loads(json.dumps(imap_helper.encode_sync_result(direct)))
    decoded = imap_helper.decode_sync_result(wire)
    assert [m.uid for m in decoded.messages] == [m.uid for m in direct.messages]
    assert [m.message_id for m in decoded.messages] == [m.message_id for m in direct.messages]
    assert decoded.messages[0].email_message.get_payload() == direct.messages[0].email_message.get_payload()
    assert decoded.checkpoint_after == direct.checkpoint_after
    assert decoded.timings_ms == direct.timings_ms


def test_wire_roundtrip_preserves_received_at() -> None:
    adapter = _FakeIMAP(uids=[1])
    client = EmailIMAPClient(
        host="h", port=993, username="u", password="p", folder="INBOX",
        search_days=7, mark_as_read=False, adapter=adapter,
    )
    direct = client.fetch_linkedin_messages_sync(
        checkpoint_uid=None, checkpoint_uidvalidity=None, incremental_enabled=True,
        bootstrap_lookback_days=7, bootstrap_message_limit=10, batch_size=10, rescan=False,
    )
    message = direct.messages[0]
    object.__setattr__(message, "received_at", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    decoded = imap_helper.decode_sync_result(json.loads(json.dumps(imap_helper.encode_sync_result(direct))))
    assert decoded.messages[0].received_at == message.received_at


def test_child_error_kinds_map_to_safe_parent_errors() -> None:
    assert isinstance(imap_helper._error_from_kind("auth"), EmailAuthenticationError)
    assert "timeout" in str(imap_helper._error_from_kind("timeout"))
    assert str(imap_helper._error_from_kind("select")) == "Cannot select mailbox."
    assert str(imap_helper._error_from_kind("anything-else")) == "IMAP helper failed."
    assert imap_helper._kind_for_exception(EmailConnectionError("Cannot select mailbox.")) == "select"
    assert imap_helper._kind_for_exception(EmailAuthenticationError("x")) == "auth"
    assert imap_helper._kind_for_exception(EmailConnectionError("IMAP connection timeout.")) == "timeout"
    assert imap_helper._kind_for_exception(RuntimeError(SECRET)) == "internal"


# ----------------------------------------------- direct client timeouts/cleanup


class _Recorder:
    def __init__(self, *, login_exc: BaseException | None = None) -> None:
        self.login_exc = login_exc
        self.calls: list[str] = []

    def login(self, username, password):
        if self.login_exc is not None:
            raise self.login_exc
        return ("OK", [])

    def close(self):
        self.calls.append("close")

    def logout(self):
        self.calls.append("logout")

    def shutdown(self):
        self.calls.append("shutdown")


def _direct_client(adapter) -> EmailIMAPClient:
    return EmailIMAPClient(
        host="h", port=993, username="u", password=SECRET, folder="INBOX",
        search_days=7, mark_as_read=False, adapter=adapter,
    )


def test_real_imap_constructor_receives_socket_timeout(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake_ssl(host, port, **kwargs):
        seen.update(kwargs)
        return _Recorder()

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _fake_ssl)
    client = EmailIMAPClient(
        host="h", port=993, username="u", password="p", folder="INBOX",
        search_days=7, mark_as_read=False, socket_timeout_seconds=12.5,
    )
    client._open_authenticated_client()  # noqa: SLF001
    assert seen["timeout"] == 12.5


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (socket.timeout(f"timed out {SECRET}"), "IMAP connection timeout."),
        (ConnectionResetError(SECRET), "IMAP connection failed."),
        (imaplib.IMAP4.abort(SECRET), "IMAP connection failed."),
    ],
)
def test_login_network_failures_normalize_and_skip_protocol_cleanup(exc, message) -> None:
    adapter = _Recorder(login_exc=exc)
    with pytest.raises(EmailConnectionError) as excinfo:
        _direct_client(adapter)._open_authenticated_client()  # noqa: SLF001
    assert str(excinfo.value) == message
    assert excinfo.value.__cause__ is None
    assert adapter.calls == ["shutdown"]  # no close/logout on a broken connection


def test_auth_failure_stays_auth_error_without_chain() -> None:
    adapter = _Recorder(login_exc=imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] {SECRET}"))
    with pytest.raises(EmailAuthenticationError) as excinfo:
        _direct_client(adapter)._open_authenticated_client()  # noqa: SLF001
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_read_timeout_midway_is_normalized() -> None:
    class _Slow(_FakeIMAP):
        def uid(self, command, *args):
            if command.lower() == "fetch":
                raise TimeoutError(f"read {SECRET}")
            return super().uid(command, *args)

    with pytest.raises(EmailConnectionError, match="timeout"):
        _sync(_direct_client(_Slow(uids=[1])))


# --------------------------------------------- checkpoint/dedup on late failure


def _collector(tmp_path: Path, adapter, client: EmailIMAPClient | None = None) -> LinkedInEmailCollector:
    return LinkedInEmailCollector(
        email_client=client
        or EmailIMAPClient(
            host="h", port=993, username="u", password="p", folder="INBOX",
            search_days=7, mark_as_read=False, adapter=adapter,
        ),
        analyzer=_Analyzer(),
        seen_jobs=_SeenJobs(),
        checkpoint_storage=ImapCheckpointStorage(db_path=tmp_path / "jobs.db"),
        incremental_enabled=True,
        bootstrap_message_limit=10,
        bootstrap_lookback_days=7,
        batch_size=50,
    )


def _patch_parser(monkeypatch) -> None:
    def _parser(raw_message):
        return [
            LinkedInEmailVacancy(
                external_id=raw_message.uid,
                title=f"Role {raw_message.uid}",
                company="Acme",
                location="Remote",
                url=f"https://www.linkedin.com/jobs/view/{raw_message.uid}/",
                snippet=None,
                email_message_id=raw_message.message_id,
                received_at=datetime.now(timezone.utc),
                content_completeness=ContentCompleteness.MINIMAL,
            )
        ]

    monkeypatch.setattr("app.collectors.linkedin_email_collector.parse_linkedin_email", _parser)


def test_late_failure_after_partial_messages_leaves_checkpoint_untouched(tmp_path, monkeypatch) -> None:
    _patch_parser(monkeypatch)

    class _FailsOnSecondFetch(_FakeIMAP):
        def uid(self, command, *args):
            if command.lower() == "fetch" and self.fetch_calls:
                raise TimeoutError("late")
            return super().uid(command, *args)

    failing = _collector(tmp_path, _FailsOnSecondFetch(uids=[5, 6]))
    with pytest.raises(EmailConnectionError):
        failing.collect()
    storage = failing._checkpoint_storage  # noqa: SLF001
    assert storage.get(source=failing.SOURCE, account_key=failing._account_key(), folder="INBOX") is None  # noqa: SLF001

    recovered = _collector(tmp_path, _FakeIMAP(uids=[5, 6]))
    vacancies = recovered.collect()
    assert sorted(v.external_id for v in vacancies) == ["5", "6"]
    checkpoint = storage.get(source=recovered.SOURCE, account_key=recovered._account_key(), folder="INBOX")  # noqa: SLF001
    assert checkpoint is not None and checkpoint.last_uid == 6

    again = _collector(tmp_path, _FakeIMAP(uids=[5, 6])).collect()
    assert again == []  # normal incremental dedup preserved


# ------------------------------------------------------------- config / wiring


def test_settings_defaults_and_env_names(monkeypatch) -> None:
    for name, value in (("LLM_API_URL", "u"), ("LLM_API_KEY", "k"), ("LLM_MODEL", "m")):
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.linkedin_email_enabled is True
    assert settings.linkedin_email_imap_timeout_seconds == 30.0
    assert settings.linkedin_email_collection_timeout_seconds == 75.0
    monkeypatch.setenv("LINKEDIN_EMAIL_ENABLED", "false")
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LINKEDIN_EMAIL_COLLECTION_TIMEOUT_SECONDS", "20.5")
    settings = Settings(_env_file=None)
    assert settings.linkedin_email_enabled is False
    assert settings.linkedin_email_imap_timeout_seconds == 5.0
    assert settings.linkedin_email_collection_timeout_seconds == 20.5


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf", "-inf"])
@pytest.mark.parametrize(
    "name", ["LINKEDIN_EMAIL_IMAP_TIMEOUT_SECONDS", "LINKEDIN_EMAIL_COLLECTION_TIMEOUT_SECONDS"]
)
def test_settings_reject_non_positive_or_non_finite_timeouts(monkeypatch, name, bad) -> None:
    for key, value in (("LLM_API_URL", "u"), ("LLM_API_KEY", "k"), ("LLM_MODEL", "m")):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad", [0, -1.0, math.nan, math.inf])
def test_client_rejects_invalid_timeouts(bad) -> None:
    with pytest.raises(ValueError):
        EmailIMAPClient(
            host="h", port=1, username="u", password="p", folder="INBOX",
            search_days=1, mark_as_read=False, socket_timeout_seconds=bad,
        )
    with pytest.raises(ValueError):
        EmailIMAPClient(
            host="h", port=1, username="u", password="p", folder="INBOX",
            search_days=1, mark_as_read=False, collection_timeout_seconds=bad,
        )


def test_build_email_client_wires_both_timeouts(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: captured.update(kwargs))
    for key, value in (("LLM_API_URL", "u"), ("LLM_API_KEY", "k"), ("LLM_MODEL", "m")):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LINKEDIN_EMAIL_IMAP_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("LINKEDIN_EMAIL_COLLECTION_TIMEOUT_SECONDS", "33")
    settings = Settings(_env_file=None)
    cli_module._build_email_client(settings)  # noqa: SLF001
    assert captured["socket_timeout_seconds"] == 7.0
    assert captured["collection_timeout_seconds"] == 33.0
    cli_module._build_email_client(settings, mark_as_read=False)  # noqa: SLF001
    assert captured["mark_as_read"] is False


def test_disabled_flag_skips_email_collector_but_keeps_greenhouse(monkeypatch, capsys) -> None:
    for key, value in (("LLM_API_URL", "u"), ("LLM_API_KEY", "k"), ("LLM_MODEL", "m")):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LINKEDIN_EMAIL_ENABLED", "false")
    monkeypatch.setenv("GREENHOUSE_BOARDS", "acme")
    built: list[int] = []
    monkeypatch.setattr(cli_module, "_build_linkedin_email_collector", lambda **kw: built.append(1))
    collectors = cli_module._build_runtime_collectors(  # noqa: SLF001
        settings=Settings(_env_file=None), analyzer=object(), seen_jobs=object(), email_client=object()
    )
    assert built == []
    assert [c.name for c in collectors] == ["greenhouse"]
    assert "disabled" in capsys.readouterr().out


def test_enabled_flag_registers_email_collector_first(monkeypatch) -> None:
    for key, value in (("LLM_API_URL", "u"), ("LLM_API_KEY", "k"), ("LLM_MODEL", "m")):
        monkeypatch.setenv(key, value)
    fake = type("C", (), {"collect": lambda self: []})()
    monkeypatch.setattr(cli_module, "_build_linkedin_email_collector", lambda **kw: fake)
    collectors = cli_module._build_runtime_collectors(  # noqa: SLF001
        settings=Settings(_env_file=None), analyzer=object(), seen_jobs=object(), email_client=object()
    )
    assert [c.name for c in collectors] == ["linkedin-email"]


# --------------------------------------------------- progress logs / ATS keeps going


def test_collect_progress_logs_are_sanitized_and_failure_continues(capsys) -> None:
    class _Failing:
        name = "linkedin-email"

        def collect(self):
            raise EmailConnectionError(f"boom {SECRET}")

    ok = cli_module.RuntimeCollector(name="greenhouse", collect_fn=lambda: [])
    result = cli_module._collect_pipeline_items(  # noqa: SLF001
        collectors=[_Failing(), ok], safe_error_formatter=lambda source, exc: "safe"
    )
    out = capsys.readouterr().out
    assert "Collect start: source=linkedin-email" in out
    assert "Collect failed: source=linkedin-email error_type=EmailConnectionError elapsed_ms=" in out
    assert "Collect done: source=greenhouse vacancies=0 elapsed_ms=" in out
    assert SECRET not in out
    assert result.has_collect_error("linkedin-email")


@pytest.mark.parametrize("mode", ["fail", "timeout", "disabled"])
def test_normal_run_continues_all_four_ats_after_email_failure_or_disable(monkeypatch, tmp_path, mode) -> None:
    """Real ``run`` wiring: pipeline thread cycle, then target-company ATS stages."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    import test_run_target_companies as trc

    trc._set_base_env(monkeypatch, tmp_path, target_chat_id="222")  # noqa: SLF001
    trc._write_config(  # noqa: SLF001
        tmp_path, include_lever=True, include_ashby=True, include_smartrecruiters=True
    )
    monkeypatch.setenv("LINKEDIN_EMAIL_ENABLED", "false" if mode == "disabled" else "true")
    cli_module._TARGET_COMPANY_SENT_IN_PROCESS.clear()  # noqa: SLF001

    watcher_calls: list[str] = []

    def _watcher_class(name: str, fake):
        class _Watcher:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def watch(self, companies: object):
                watcher_calls.append(name)
                return fake.watch(companies)

        return _Watcher

    monkeypatch.setattr(cli_module, "GreenhouseTargetWatcher", _watcher_class("greenhouse", trc._fake_watcher([])))
    monkeypatch.setattr(cli_module, "LeverTargetWatcher", _watcher_class("lever", trc._fake_lever_watcher([])))
    monkeypatch.setattr(cli_module, "AshbyTargetWatcher", _watcher_class("ashby", trc._fake_ashby_watcher([])))
    monkeypatch.setattr(
        cli_module,
        "SmartRecruitersTargetWatcher",
        _watcher_class("smartrecruiters", trc._fake_smartrecruiters_watcher([])),
    )

    email_collect_calls: list[int] = []

    class _EmailCollector:
        SOURCE = "linkedin-email"

        def collect(self):
            email_collect_calls.append(1)
            if mode == "timeout":
                raise EmailConnectionError("IMAP collection timeout.")
            raise EmailConnectionError("IMAP connection failed.")

    built: list[int] = []

    def _build_collector(**kwargs):
        built.append(1)
        return _EmailCollector()

    class _Telegram(trc._FakeTelegram):  # noqa: SLF001
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__()

        def close(self) -> None:
            return None

    cycle_done = threading.Event()
    real_target_cycle = cli_module._run_target_companies_cycle  # noqa: SLF001

    def _target_cycle(**kwargs):
        try:
            return real_target_cycle(**kwargs)
        finally:
            cycle_done.set()

    def _poll_once(**kwargs):
        if int(kwargs.get("timeout", 0) or 0) == 0:
            return kwargs.get("offset"), 0
        # Shut down only AFTER the pipeline thread finished its ATS cycle.
        assert cycle_done.wait(timeout=30)
        raise KeyboardInterrupt()

    def _idle_worker(**kwargs):
        kwargs["stop_event"].wait(timeout=30)

    monkeypatch.setattr(cli_module, "_run_target_companies_cycle", _target_cycle)
    monkeypatch.setattr(cli_module, "load_candidate_constraints", lambda path: trc._constraints())  # noqa: SLF001
    monkeypatch.setattr(cli_module, "build_analyzer", lambda settings: trc._CountingAnalyzer())  # noqa: SLF001
    monkeypatch.setattr(cli_module, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "EmailIMAPClient", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "PreparationService", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "TelegramClient", _Telegram)
    monkeypatch.setattr(cli_module, "_build_linkedin_email_collector", _build_collector)
    monkeypatch.setattr(cli_module, "_poll_telegram_actions_once", _poll_once)
    # Keep the unrelated resume/worker config out of this test (no resume_profiles.yaml in tmp cwd).
    monkeypatch.setattr(cli_module, "_load_configured_resume_profiles", lambda settings: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(cli_module, "ResumeCacheService", lambda **kwargs: object())
    monkeypatch.setattr(cli_module, "_recover_and_requeue_abandoned_preparations", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_prepare_worker_loop", _idle_worker)
    monkeypatch.setattr(cli_module, "_cleanup_worker_loop", _idle_worker)

    result = CliRunner().invoke(cli_module.app, ["run"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert sorted(watcher_calls) == ["ashby", "greenhouse", "lever", "smartrecruiters"]
    for provider in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        assert f"Collect start: source=ats:{provider}" in out
        assert f"Collect done: source=ats:{provider}" in out
    if mode == "disabled":
        assert built == [] and email_collect_calls == []
        assert "linkedin-email: disabled" in out
        assert "Collect start: source=linkedin-email" not in out
    else:
        assert email_collect_calls == [1]
        assert "Collect failed: source=linkedin-email error_type=EmailConnectionError" in out
        assert "linkedin-email: failed" in out


# ------------------------------------------------- privacy of select failures


def test_select_failure_never_echoes_private_folder() -> None:
    folder = "Private-Folder-xyz"

    class _NoSelect(_FakeIMAP):
        def select(self, mailbox):
            return ("NO", [])

    client = EmailIMAPClient(
        host="h", port=993, username="u", password=SECRET, folder=folder,
        search_days=7, mark_as_read=False, adapter=_NoSelect(uids=[1]),
    )
    with pytest.raises(EmailConnectionError) as excinfo:
        _sync(client)
    assert str(excinfo.value) == "Cannot select mailbox."
    assert folder not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_parent_maps_select_kind_without_private_folder(monkeypatch, spawned) -> None:
    folder = "Private-Folder-xyz"
    _fake_child(monkeypatch, spawned, "import sys; sys.stdout.write('{\"ok\": false, \"kind\": \"select\"}')")
    with pytest.raises(EmailConnectionError) as excinfo:
        imap_helper.run_supervised(
            operation="list", config={"folder": folder}, args={}, timeout_seconds=10.0
        )
    assert str(excinfo.value) == "Cannot select mailbox."
    assert folder not in str(excinfo.value)
    _assert_reaped(spawned)


# ------------------------------------------- lifecycle: self-deadline / cancel


def test_child_enforces_its_own_deadline_without_parent_timeout(silent_peer) -> None:
    """Parent never times out here: only the child's own timer can end it."""
    request = {
        "operation": "list",
        "config": {
            "host": "127.0.0.1", "port": silent_peer.port, "username": "u", "password": SECRET,
            "folder": "INBOX", "search_days": 7, "mark_as_read": False, "socket_timeout_seconds": 60.0,
        },
        "args": {},
        "deadline_seconds": 1.0,
    }
    start = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-m", "app.collectors.imap_helper"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        cwd=str(imap_helper._PROJECT_ROOT),  # noqa: SLF001
        timeout=40,
    )
    assert time.monotonic() - start < 20
    assert completed.returncode == imap_helper.DEADLINE_EXIT_CODE
    assert completed.stdout == b""
    assert SECRET.encode() not in completed.stderr


def test_deadline_exit_code_maps_to_whitelisted_timeout(monkeypatch, spawned) -> None:
    _fake_child(monkeypatch, spawned, f"import sys; sys.exit({imap_helper.DEADLINE_EXIT_CODE})")
    with pytest.raises(EmailConnectionError) as excinfo:
        imap_helper.run_supervised(operation="list", config={}, args={}, timeout_seconds=10.0)
    assert str(excinfo.value) == "IMAP collection timeout."
    _assert_reaped(spawned)


def test_child_deadline_timer_fires_and_cancels(monkeypatch) -> None:
    fired: list[int] = []
    monkeypatch.setattr(imap_helper, "_exit_now", fired.append)

    timer = imap_helper._arm_deadline(0.2)  # noqa: SLF001
    assert timer is not None
    timer.cancel()
    timer.join(timeout=2)
    time.sleep(0.4)
    assert fired == [] and not timer.is_alive()  # cancelled: no lingering thread, no exit

    timer = imap_helper._arm_deadline(0.1)  # noqa: SLF001
    assert timer is not None
    timer.join(timeout=2)
    assert fired == [imap_helper.DEADLINE_EXIT_CODE] and not timer.is_alive()

    for bad in (None, 0, -1, math.nan, math.inf, True, "5"):
        assert imap_helper._arm_deadline(bad) is None  # noqa: SLF001


def _fake_stdio(monkeypatch, request: dict, *, write_delay: float = 0.0):
    import io
    from types import SimpleNamespace

    class _Out(io.BytesIO):
        def write(self, data):
            time.sleep(write_delay)
            return super().write(data)

    out = _Out()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=out))
    return out


def test_child_timer_is_cancelled_after_successful_ipc(monkeypatch) -> None:
    fired: list[int] = []
    monkeypatch.setattr(imap_helper, "_exit_now", fired.append)
    monkeypatch.setattr(imap_helper, "_handle_request", lambda request: ["INBOX"])
    out = _fake_stdio(monkeypatch, {"deadline_seconds": 0.3})
    assert imap_helper.main() == 0
    time.sleep(0.6)
    assert fired == []
    assert json.loads(out.getvalue()) == {"ok": True, "result": ["INBOX"]}


def test_child_timer_covers_a_stalled_response_write(monkeypatch) -> None:
    fired: list[int] = []
    monkeypatch.setattr(imap_helper, "_exit_now", fired.append)
    monkeypatch.setattr(imap_helper, "_handle_request", lambda request: ["INBOX"])
    _fake_stdio(monkeypatch, {"deadline_seconds": 0.2}, write_delay=0.8)
    imap_helper.main()
    assert fired == [imap_helper.DEADLINE_EXIT_CODE]


def test_cancel_from_another_thread_reaps_and_next_attempt_is_clean(monkeypatch, spawned) -> None:
    _fake_child(monkeypatch, spawned, "import time; time.sleep(60)")
    outcome: list[BaseException] = []

    def _attempt() -> None:
        try:
            imap_helper.run_supervised(
                operation="list", config={}, args={}, timeout_seconds=30, reap_grace_seconds=1.0
            )
        except BaseException as exc:  # noqa: BLE001
            outcome.append(exc)

    thread = threading.Thread(target=_attempt)
    thread.start()
    deadline = time.monotonic() + 10
    while not imap_helper._active and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.02)
    assert imap_helper.cancel_active_helpers(grace_seconds=1.0) == 1
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], EmailConnectionError)
    assert str(outcome[0]) == "IMAP collection cancelled."
    assert imap_helper._active == {}  # noqa: SLF001
    _assert_reaped(spawned)

    # Cancel is not a persistent stop flag: the next attempt runs normally.
    _fake_child(monkeypatch, spawned, "import sys; sys.stdout.write('{\"ok\": true, \"result\": [\"INBOX\"]}')")
    assert imap_helper.run_supervised(operation="list", config={}, args={}, timeout_seconds=10.0) == ["INBOX"]
    assert imap_helper.cancel_active_helpers() == 0


def test_success_is_never_returned_after_budget_expired(monkeypatch, spawned) -> None:
    payload = "{\"ok\": true, \"result\": {\"messages\": []}}"
    _fake_child(monkeypatch, spawned, f"import sys; sys.stdout.write('{payload}')")

    def _slow_decode(data):
        time.sleep(1.3)  # decoding overran the budget
        return "late-result"

    monkeypatch.setattr(imap_helper, "decode_sync_result", _slow_decode)
    with pytest.raises(EmailConnectionError) as excinfo:
        imap_helper.run_supervised(operation="sync", config={}, args={}, timeout_seconds=1.0)
    assert str(excinfo.value) == "IMAP collection timeout."
    assert excinfo.value.__cause__ is None
    _assert_reaped(spawned)


# ------------------------------------------------------- cleanup-specific cover

_STALLED_CLEANUP_CHILD = """
import time
from app.collectors import imap_helper
from app.collectors.email_imap_client import EmailIMAPClient
class A:
    def login(self, u, p): return ("OK", [])
    def select(self, m): return ("OK", [b"1"])
    def uid(self, c, *a): return ("OK", [b""])
    def response(self, c): return (c, [])
    def close(self): time.sleep(60)
    def logout(self): time.sleep(60)
EmailIMAPClient._open_authenticated_client = lambda self: A()
raise SystemExit(imap_helper.main())
"""

_PARTIAL_IPC_CHILD = (
    "import sys,time\n"
    "sys.stdout.write('{\"ok\": true, \"result\": {\"messages\": [')\n"
    "sys.stdout.flush()\n"
    "time.sleep(60)\n"
)


def _fake_child_in_project(monkeypatch, spawned: list, code: str) -> None:
    def _start():
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(imap_helper._PROJECT_ROOT),  # noqa: SLF001
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(imap_helper, "_start_helper", _start)


@pytest.mark.parametrize("child", [_STALLED_CLEANUP_CHILD, _PARTIAL_IPC_CHILD], ids=["cleanup-stall", "partial-ipc"])
def test_stalled_cleanup_or_partial_ipc_is_bounded_and_preserves_checkpoint(
    tmp_path, monkeypatch, spawned, child
) -> None:
    _patch_parser(monkeypatch)
    _fake_child_in_project(monkeypatch, spawned, child)
    client = EmailIMAPClient(
        host="h", port=993, username="u", password=SECRET, folder="INBOX",
        search_days=7, mark_as_read=False, socket_timeout_seconds=30, collection_timeout_seconds=3.0,
    )
    collector = _collector(tmp_path, None, client)
    start = time.monotonic()
    with pytest.raises(EmailConnectionError) as excinfo:
        collector.collect()
    assert time.monotonic() - start < 3.0 + imap_helper.REAP_GRACE_SECONDS + 3
    assert "timeout" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert len(spawned) == 1  # no retry
    _assert_reaped(spawned)
    storage = collector._checkpoint_storage  # noqa: SLF001
    assert storage.get(source=collector.SOURCE, account_key=collector._account_key(), folder="INBOX") is None  # noqa: SLF001


def test_close_and_logout_timeouts_after_complete_read_are_swallowed_and_sanitized() -> None:
    class _CleanupTimeouts(_FakeIMAP):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleanup: list[str] = []

        def close(self):
            self.cleanup.append("close")
            raise TimeoutError(f"close {SECRET}")

        def logout(self):
            self.cleanup.append("logout")
            raise TimeoutError(f"logout {SECRET}")

    adapter = _CleanupTimeouts(uids=[1])
    result = _sync(_direct_client(adapter))
    assert adapter.cleanup == ["close", "logout"]  # both attempted, neither retried
    assert result.checkpoint_after == 1  # a fully read result is not discarded by cleanup noise


@pytest.mark.parametrize("subject", ["Job alert PRIVATE_SUBJECT_CANARY", "Security alert PRIVATE_SUBJECT_CANARY"])
def test_imap_diagnostics_omit_private_subject_and_body(subject, caplog) -> None:
    from email.message import EmailMessage

    class _PrivateMail(_FakeIMAP):
        def uid(self, command, *args):
            if command.lower() == "fetch":
                message = EmailMessage()
                message["From"] = "jobs-noreply@linkedin.com"
                message["Subject"] = subject
                message.set_content("PRIVATE_BODY_CANARY https://www.linkedin.com/jobs/view/123/")
                return ("OK", [(b"RFC822", message.as_bytes())])
            return super().uid(command, *args)

    caplog.set_level("INFO")
    result = _sync(_direct_client(_PrivateMail(uids=[1])))
    diagnostic_text = repr(result.classification_events) + repr(result.rejection_events) + caplog.text
    assert "private_subject_canary" not in diagnostic_text.lower()
    assert "private_body_canary" not in diagnostic_text.lower()
    if subject.startswith("Job alert"):
        assert result.messages[0].subject == subject
        assert b"PRIVATE_BODY_CANARY" in result.messages[0].email_message.get_payload(decode=True)
    else:
        assert result.messages == []
