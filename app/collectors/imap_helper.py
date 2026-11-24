"""Supervised short-lived subprocess boundary for blocking IMAP operations.

The parent (``run_supervised``) starts exactly one helper process per IMAP
operation and bounds the whole exchange (spawn, DNS, TLS, auth, reads, cleanup
and IPC) with a single deadline. Credentials, config and results travel only
over the helper's private stdin/stdout pipes as JSON. The helper imports only
the IMAP client types: no CLI, storage, profile or ``.env`` loading.
"""

from __future__ import annotations

import atexit
import base64
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import fields
from datetime import datetime
from email import message_from_bytes
from pathlib import Path
from typing import Any

from app.collectors.email_imap_client import (
    EmailAuthenticationError,
    EmailConnectionError,
    EmailIMAPClient,
    EmailMessageError,
    ImapSyncResult,
    RawEmailMessage,
)

REAP_GRACE_SECONDS = 2.0
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HELPER_MODULE = "app.collectors.imap_helper"

DEADLINE_EXIT_CODE = 124
_exit_now = os._exit  # indirection so tests can observe the child self-deadline

_unreaped: list[subprocess.Popen[bytes]] = []
_unreaped_lock = threading.Lock()
# Helpers currently owned by a ``run_supervised`` call -> "cancelled" flag.
_active: dict[subprocess.Popen[bytes], threading.Event] = {}
_active_lock = threading.Lock()

# Whitelisted error kinds the helper may report; the parent owns all message text.
_KIND_CONNECTION = "connection"
_KIND_TIMEOUT = "timeout"
_KIND_SELECT = "select"
_KIND_LIST = "list"
_KIND_AUTH = "auth"
_KIND_MESSAGE = "message"
_KIND_INTERNAL = "internal"
_KINDS = {
    _KIND_CONNECTION,
    _KIND_TIMEOUT,
    _KIND_SELECT,
    _KIND_LIST,
    _KIND_AUTH,
    _KIND_MESSAGE,
    _KIND_INTERNAL,
}


def validate_positive_finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


# ---------------------------------------------------------------- wire format


def encode_sync_result(result: ImapSyncResult) -> dict[str, Any]:
    data: dict[str, Any] = {
        field.name: getattr(result, field.name) for field in fields(result) if field.name != "messages"
    }
    data["messages"] = [
        {
            "uid": message.uid,
            "message_id": message.message_id,
            "from_address": message.from_address,
            "subject": message.subject,
            "received_at": message.received_at.isoformat() if message.received_at else None,
            "email_b64": base64.b64encode(message.email_message.as_bytes()).decode("ascii"),
        }
        for message in result.messages
    ]
    return data


def decode_sync_result(data: dict[str, Any]) -> ImapSyncResult:
    values = {key: value for key, value in data.items() if key != "messages"}
    messages = [
        RawEmailMessage(
            uid=str(item["uid"]),
            message_id=str(item["message_id"]),
            from_address=str(item["from_address"]),
            subject=str(item["subject"]),
            received_at=datetime.fromisoformat(item["received_at"]) if item["received_at"] else None,
            email_message=message_from_bytes(base64.b64decode(item["email_b64"], validate=True)),
        )
        for item in data["messages"]
    ]
    return ImapSyncResult(messages=messages, **values)


# --------------------------------------------------------------------- parent


def _error_from_kind(kind: str) -> Exception:
    if kind == _KIND_AUTH:
        return EmailAuthenticationError("IMAP authentication failed.")
    if kind == _KIND_TIMEOUT:
        return EmailConnectionError("IMAP connection timeout.")
    if kind == _KIND_SELECT:
        return EmailConnectionError("Cannot select mailbox.")
    if kind == _KIND_LIST:
        return EmailConnectionError("Cannot list IMAP mailboxes.")
    if kind == _KIND_MESSAGE:
        return EmailMessageError("IMAP message processing failed.")
    if kind == _KIND_CONNECTION:
        return EmailConnectionError("IMAP connection failed.")
    return EmailConnectionError("IMAP helper failed.")


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _stop_process(proc: subprocess.Popen[bytes], grace_seconds: float) -> bool:
    """terminate -> kill -> wait, bounded by ``grace_seconds`` in total."""
    half = max(0.05, grace_seconds / 2)
    for stop in (proc.terminate, proc.kill):
        if proc.poll() is not None:
            break
        try:
            stop()
        except OSError:
            pass
        try:
            proc.wait(timeout=half)
        except subprocess.TimeoutExpired:
            continue
    return proc.poll() is not None


def _reap(proc: subprocess.Popen[bytes], grace_seconds: float) -> bool:
    stopped = _stop_process(proc, grace_seconds)
    _close_pipes(proc)
    return stopped


def cancel_active_helpers(grace_seconds: float = REAP_GRACE_SECONDS) -> int:
    """Stop every helper owned by an in-flight attempt (and any unreaped one).

    Safe from any thread and at interpreter exit. Pipes stay open so the owning
    thread unblocks from ``communicate`` and reports a sanitized cancellation;
    the owner still performs the final reap. Not a persistent stop flag: the
    next attempt starts normally.
    """
    with _active_lock:
        owned = list(_active.items())
    with _unreaped_lock:
        leftovers = list(_unreaped)
    for proc, cancelled in owned:
        cancelled.set()
        _stop_process(proc, grace_seconds)
    for proc in leftovers:
        _stop_process(proc, grace_seconds)
    return len(owned)


atexit.register(cancel_active_helpers)


def _refuse_if_previous_helper_alive() -> None:
    with _unreaped_lock:
        _unreaped[:] = [proc for proc in _unreaped if proc.poll() is None]
        if _unreaped:
            raise EmailConnectionError("IMAP helper from a previous attempt is still running.")


def _start_helper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no user data in argv
        [sys.executable, "-m", _HELPER_MODULE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(_PROJECT_ROOT),
        close_fds=True,
    )


def run_supervised(
    *,
    operation: str,
    config: dict[str, Any],
    args: dict[str, Any],
    timeout_seconds: float,
    reap_grace_seconds: float = REAP_GRACE_SECONDS,
) -> Any:
    """Run one IMAP operation in a helper process under a hard total deadline.

    No retries. On any failure nothing partial is returned. The helper is always
    reaped before returning/raising, including on ``KeyboardInterrupt``.
    """
    timeout_seconds = validate_positive_finite("collection timeout", timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    _refuse_if_previous_helper_alive()

    def _expired() -> bool:
        return time.monotonic() >= deadline

    try:
        proc = _start_helper()
    except OSError:
        raise EmailConnectionError("IMAP helper failed to start.") from None

    cancelled = threading.Event()
    with _active_lock:
        _active[proc] = cancelled

    try:
        try:
            remaining = max(0.001, deadline - time.monotonic())
            # The child arms its own hard deadline from the same remaining budget.
            payload = json.dumps(
                {"operation": operation, "config": config, "args": args, "deadline_seconds": remaining}
            ).encode("utf-8")
            stdout, _ = proc.communicate(input=payload, timeout=remaining)
        except subprocess.TimeoutExpired:
            raise EmailConnectionError("IMAP collection timeout.") from None
        except (OSError, ValueError):
            if cancelled.is_set():
                raise EmailConnectionError("IMAP collection cancelled.") from None
            raise EmailConnectionError("IMAP helper communication failed.") from None
    finally:
        with _active_lock:
            _active.pop(proc, None)
        if proc.poll() is None:
            if not _reap(proc, reap_grace_seconds):
                with _unreaped_lock:
                    _unreaped.append(proc)
        else:
            _close_pipes(proc)

    if cancelled.is_set():
        raise EmailConnectionError("IMAP collection cancelled.")
    if proc.returncode == DEADLINE_EXIT_CODE:
        raise EmailConnectionError("IMAP collection timeout.")
    if proc.returncode != 0:
        raise EmailConnectionError("IMAP helper failed.")
    if not stdout or len(stdout) > MAX_RESPONSE_BYTES:
        raise EmailConnectionError("IMAP helper returned an invalid response.")
    if _expired():
        raise EmailConnectionError("IMAP collection timeout.")
    try:
        response = json.loads(stdout)
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise ValueError("bad envelope")
        if not response["ok"]:
            kind = response.get("kind")
            raise _error_from_kind(kind if kind in _KINDS else _KIND_INTERNAL)
        data = response["result"]
        if operation == "sync":
            result: Any = decode_sync_result(data)
        elif operation == "list":
            if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
                raise ValueError("bad list")
            result = data
        else:
            raise ValueError("bad operation")
    except (EmailConnectionError, EmailAuthenticationError, EmailMessageError):
        raise
    except Exception:  # noqa: BLE001 - malformed helper output, never echo it
        raise EmailConnectionError("IMAP helper returned an invalid response.") from None
    if _expired():  # never report success (or let a checkpoint advance) past the budget
        raise EmailConnectionError("IMAP collection timeout.")
    return result


# ---------------------------------------------------------------------- child


def _kind_for_exception(exc: BaseException) -> str:
    if isinstance(exc, EmailAuthenticationError):
        return _KIND_AUTH
    if isinstance(exc, EmailMessageError):
        return _KIND_MESSAGE
    if isinstance(exc, EmailConnectionError):
        text = str(exc).lower()
        if "select mailbox" in text:
            return _KIND_SELECT
        if "list imap" in text:
            return _KIND_LIST
        if "timeout" in text:
            return _KIND_TIMEOUT
        return _KIND_CONNECTION
    return _KIND_INTERNAL


def _handle_request(request: dict[str, Any]) -> Any:
    config = request["config"]
    client = EmailIMAPClient(
        host=str(config["host"]),
        port=int(config["port"]),
        username=str(config["username"]),
        password=str(config["password"]),
        folder=str(config["folder"]),
        search_days=int(config["search_days"]),
        mark_as_read=bool(config["mark_as_read"]),
        socket_timeout_seconds=float(config["socket_timeout_seconds"]),
    )
    if request["operation"] == "sync":
        args = request["args"]
        return encode_sync_result(client._fetch_sync_direct(**args))
    if request["operation"] == "list":
        return client._list_mailboxes_direct()
    raise ValueError("unknown operation")


def _arm_deadline(seconds: object) -> threading.Timer | None:
    """Daemon timer that ends this whole helper process when the budget is spent."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    timer = threading.Timer(float(seconds), lambda: _exit_now(DEADLINE_EXIT_CODE))
    timer.daemon = True
    timer.start()
    return timer


def main() -> int:
    timer: threading.Timer | None = None
    try:
        request = json.loads(sys.stdin.buffer.read())
        timer = _arm_deadline(request.get("deadline_seconds"))
        response: dict[str, Any] = {"ok": True, "result": _handle_request(request)}
    except Exception as exc:  # noqa: BLE001 - only a whitelisted kind leaves the process
        response = {"ok": False, "kind": _kind_for_exception(exc)}
    try:
        # The timer also covers the response IPC; it is cancelled only once flushed.
        sys.stdout.buffer.write(json.dumps(response).encode("utf-8"))
        sys.stdout.buffer.flush()
    finally:
        if timer is not None:
            timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
