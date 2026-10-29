#!/usr/bin/env python3
"""Supervised, pinned launcher for headless Claude Code runs (stdlib only).

Usage: job-claude [--model ID] [--effort LEVEL] [--resume UUID] [--] "<prompt>"
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
CLAUDE_BIN = Path.home() / ".local" / "bin" / "claude"

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL_RE = re.compile(r"claude-[a-z0-9]+(?:-[a-z0-9.]+)*")

TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "Bash")
ALLOWED = (
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(git branch --show-current)",
    "Bash(.venv/bin/pytest tests/*)",
    "Bash(.venv/bin/ruff check *)",
)
DENIED = (
    "Agent",
    "Task",
    "Bash(claude *)",
    "Bash(*/claude *)",
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(uv *)",
    "Bash(sh *)",
    "Bash(bash *)",
    "Bash(zsh *)",
    "Bash(git * --output*)",
    "Bash(.venv/bin/pytest *..*)",
    "Bash(.venv/bin/pytest * -p *)",
    "Bash(.venv/bin/pytest * -c *)",
    "Bash(.venv/bin/pytest * --rootdir*)",
    "Bash(.venv/bin/ruff check *..*)",
    "Bash(.venv/bin/ruff check * --fix*)",
    "Bash(.venv/bin/ruff check * --unsafe-fixes*)",
    "Bash(.venv/bin/ruff check * --add-noqa*)",
    "Bash(.venv/bin/ruff check * --output-file*)",
    "Bash(.venv/bin/ruff check * --config*)",
)
FORBIDDEN_TOOLS = {"Agent", "Task"}

EXIT_USAGE, EXIT_FAIL, EXIT_BLOCKED, EXIT_MISMATCH = 2, 1, 3, 4

CLAUDE_DENIAL_RE = re.compile(
    r"permission to use .{0,80} has been denied|requested permissions to use"
    r"|haven't granted it yet|running in don't ask mode",
    re.I | re.S,
)
OS_PERMISSION_RE = re.compile(r"EACCES|EPERM|permission denied|operation not permitted|PermissionError", re.I)
TEST_FAILURE_RE = re.compile(r"AssertionError|= FAILURES =|^FAILED |^E\s+assert ", re.M)
ASSERTION_LINE_RE = re.compile(r"AssertionError|^E?\s*assert\b", re.I)
OS_STRONG_RE = re.compile(r"PermissionError|\bEACCES\b|\bEPERM\b|\[Errno (?:1|13)\]")
TOKEN_KEYS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_creation": "cache_creation_input_tokens",
}
SAFE_STR_RE = re.compile(r"[A-Za-z0-9._:\[\]-]{1,100}")


class UsageError(Exception):
    pass


class ProtocolError(Exception):
    pass


def parse_args(argv):
    opts = {"--model": DEFAULT_MODEL, "--effort": DEFAULT_EFFORT, "--resume": None}
    seen = set()
    prompt = None
    literal = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if literal or not arg.startswith("-") or arg == "-":
            if prompt is not None:
                raise UsageError("exactly one prompt argument is allowed")
            prompt = arg
        elif arg == "--":
            literal = True
        else:
            name, eq, value = arg.partition("=")
            if name not in opts:
                raise UsageError(f"unsupported option: {name}")
            if not eq:
                if i >= len(argv):
                    raise UsageError(f"{name} requires a value")
                value = argv[i]
                i += 1
            if name in seen:
                raise UsageError(f"duplicate option: {name}")
            seen.add(name)
            opts[name] = value
    if prompt is None or not prompt.strip():
        raise UsageError("a non-empty prompt is required")
    model, effort, resume = opts["--model"], opts["--effort"], opts["--resume"]
    if len(model) > 64 or not MODEL_RE.fullmatch(model):
        raise UsageError("invalid --model (expected explicit claude-* id)")
    if effort not in EFFORTS:
        raise UsageError(f"invalid --effort (one of {', '.join(EFFORTS)})")
    if resume is not None and not valid_uuid(resume):
        raise UsageError("--resume requires a canonical lowercase UUID")
    return prompt, model, effort, resume


def valid_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def read_records(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def can_resume(records, session_id, workspace):
    return any(
        r.get("status") == "success"
        and r.get("session_id") == session_id
        and r.get("workspace") == str(workspace)
        for r in records
    )


def build_command(claude_bin, prompt, model, effort, resume):
    cmd = [
        str(claude_bin),
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--effort",
        effort,
        "--permission-mode",
        "dontAsk",
        f"--tools={','.join(TOOLS)}",
        f"--allowedTools={','.join(ALLOWED)}",
        f"--disallowedTools={','.join(DENIED)}",
    ]
    if resume:
        cmd += ["--resume", resume]
    return cmd + ["--", prompt]


def build_env(base, model, effort):
    env = dict(base)
    env["ANTHROPIC_MODEL"] = model
    env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
    return env


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)
        )
    return ""


def permission_kind(text):
    if CLAUDE_DENIAL_RE.search(text):
        return "claude_denial"
    if TEST_FAILURE_RE.search(text):
        # Pytest output: ignore lines that merely assert on text; real OS errors still count.
        kept = "\n".join(ln for ln in text.splitlines() if not ASSERTION_LINE_RE.search(ln))
        return "os_permission" if OS_STRONG_RE.search(kept) else None
    if OS_PERMISSION_RE.search(text):
        return "os_permission"
    return None


def model_ok(observed, requested):
    return observed == requested or re.fullmatch(re.escape(requested) + r"-\d{8}", observed) is not None


class Run:
    """Accumulates sanitized facts from the stream-json events."""

    def __init__(self, model, effort, resume=None):
        self.model, self.effort, self.resume = model, effort, resume
        self.session_id = None
        self.init_model = None
        self.observed_effort = None
        self.models = set()
        self.messages = {}
        self.permissions = 0
        self.result = None

    def handle(self, ev):
        """Process one event; return a stop reason ('blocked'/'mismatch') or None.

        Raises ProtocolError on unexpected event shapes.
        """
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            if valid_uuid(ev.get("session_id")):
                self.session_id = ev["session_id"]
            if self.resume and ev.get("session_id") != self.resume:
                return "mismatch"
            tools = ev.get("tools")
            if tools is not None and not isinstance(tools, list):
                raise ProtocolError
            if isinstance(ev.get("model"), str):
                self.init_model = ev["model"]
                self.models.add(ev["model"])
            for key in ("effort", "effortLevel"):
                if isinstance(ev.get(key), str):
                    self.observed_effort = ev[key]
            if FORBIDDEN_TOOLS & {t for t in tools or [] if isinstance(t, str)}:
                return "mismatch"
        elif kind == "assistant":
            msg = ev.get("message")
            if not isinstance(msg, dict):
                raise ProtocolError
            content = msg.get("content")
            if content is not None and not isinstance(content, (list, str)):
                raise ProtocolError
            if msg.get("usage") is not None and not isinstance(msg["usage"], dict):
                raise ProtocolError
            if isinstance(msg.get("model"), str) and msg["model"] != "<synthetic>":
                self.models.add(msg["model"])
            if isinstance(msg.get("id"), str) and isinstance(msg.get("usage"), dict):
                self.messages[msg["id"]] = msg["usage"]
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    raise ProtocolError
                name = block.get("name")
                if block.get("type") == "tool_use" and not isinstance(name, str):
                    raise ProtocolError
                if block.get("type") == "tool_use" and isinstance(name, str) and name in FORBIDDEN_TOOLS:
                    return "mismatch"
        elif kind == "user":
            msg = ev.get("message")
            if not isinstance(msg, dict):
                raise ProtocolError
            content = msg.get("content")
            if content is not None and not isinstance(content, (list, str)):
                raise ProtocolError
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    raise ProtocolError
                if block.get("type") == "tool_result" and block.get("is_error"):
                    if permission_kind(text_of(block.get("content"))):
                        self.permissions += 1
                        return "blocked"
        elif kind == "result":
            self.result = ev
            denials = ev.get("permission_denials")
            if denials is not None and not isinstance(denials, list):
                raise ProtocolError
            if denials:
                self.permissions = max(self.permissions, len(denials))
                return "blocked"
        return self.mismatch()

    def mismatch(self):
        if any(not model_ok(m, self.model) for m in self.models):
            return "mismatch"
        if self.observed_effort is not None and self.observed_effort != self.effort:
            return "mismatch"
        return None

    def tokens(self):
        if not self.messages:
            return None
        return {
            name: sum(u.get(key, 0) for u in self.messages.values() if isinstance(u.get(key, 0), int))
            for name, key in TOKEN_KEYS.items()
        }

    def result_tokens(self):
        usage = (self.result or {}).get("usage")
        if not isinstance(usage, dict):
            return None
        return {name: usage[key] for name, key in TOKEN_KEYS.items() if isinstance(usage.get(key), int)}


def safe_str(value):
    return value if isinstance(value, str) and SAFE_STR_RE.fullmatch(value) else None


def append_record(path, record):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def stop_process(proc):
    if proc.poll() is None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
    proc.wait()


def run(argv, *, claude_bin=CLAUDE_BIN, workspace=WORKSPACE, state_dir=None, env=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    workspace = Path(workspace).resolve()
    log_path = Path(state_dir or workspace / "data") / "claude-invocations.jsonl"
    try:
        prompt, model, effort, resume = parse_args(argv)
        if resume and not can_resume(read_records(log_path), resume, workspace):
            raise UsageError("--resume session has no successful prior record for this workspace")
    except UsageError as exc:
        print(f"job-claude: {exc}", file=err)
        print('usage: job-claude [--model ID] [--effort LEVEL] [--resume UUID] [--] "<prompt>"', file=err)
        return EXIT_USAGE

    state = Run(model, effort, resume)
    started = time.monotonic()
    status, code, claude_rc, malformed = "aborted", EXIT_FAIL, None, False
    proc = None
    try:
        proc = subprocess.Popen(
            build_command(claude_bin, prompt, model, effort, resume),
            cwd=workspace,
            env=build_env(os.environ if env is None else env, model, effort),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        print(f"job-claude: cannot start claude: {exc.__class__.__name__}", file=err)

    stop = None
    try:
        for line in proc.stdout if proc else ():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = None
            if not isinstance(ev, dict):
                malformed = True
                break
            try:
                stop = state.handle(ev)
            except ProtocolError:
                malformed = True
                break
            if stop:
                break
    except KeyboardInterrupt:
        stop = "interrupted"
    finally:
        if proc is None:
            pass
        elif stop or malformed:
            stop_process(proc)
        else:
            proc.stdout.close()
            claude_rc = proc.wait()
        if proc is not None and claude_rc is None:
            claude_rc = proc.returncode

    result = state.result
    if proc is None:
        status = "start_failed"
    elif stop == "blocked":
        status, code = "permission_blocked", EXIT_BLOCKED
    elif stop == "mismatch":
        status, code = "mismatch", EXIT_MISMATCH
    elif stop == "interrupted":
        status, code = "aborted", 130
    elif malformed:
        status, code = "malformed", EXIT_FAIL
    elif result is None or not state.session_id:
        status = "no_result"
    elif claude_rc != 0 or result.get("is_error") or result.get("subtype") != "success":
        status = "error"
    elif not isinstance(result.get("result"), str):
        status = "malformed"
    else:
        status, code = "success", 0

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "workspace": str(workspace),
        "status": status,
        "exit_code": code,
        "claude_exit": claude_rc,
        "requested_model": model,
        "requested_effort": effort,
        "observed_model": safe_str(state.init_model),
        "observed_models": sorted(m for m in map(safe_str, state.models) if m),
        "observed_effort": safe_str(state.observed_effort),
        "tokens": state.tokens(),
        "result_tokens": state.result_tokens(),
        "permission_count": state.permissions,
        "session_id": state.session_id,
        "resume_requested": resume is not None,
        "resumed": resume is not None and state.session_id == resume,
        "duration_s": round(time.monotonic() - started, 1),
    }
    try:
        append_record(log_path, record)
    except OSError:
        print("job-claude: could not write diagnostics; failing the run.", file=err)
        if code == 0:
            status, code = "diagnostics_failed", EXIT_FAIL

    tok = record["tokens"] or {}
    print(
        f"job-claude: status={status} model={record['observed_model'] or 'unknown'}"
        f"(req {model}) effort={record['observed_effort'] or 'unknown'}(req {effort})"
        f" tokens_in={tok.get('input', 'n/a')} tokens_out={tok.get('output', 'n/a')}"
        f" permissions={state.permissions} session={state.session_id or 'none'}",
        file=err,
    )
    if status == "permission_blocked":
        print(
            "job-claude: BLOCKED: a permission failure stopped the run; the operation was not retried."
            " Grant or adjust access outside the run.",
            file=err,
        )
    elif status == "mismatch":
        print("job-claude: observed model/effort or tool exposure differs from the request; failing closed.", file=err)
    if code == 0:
        out.write(result["result"])
        if not result["result"].endswith("\n"):
            out.write("\n")
    return code


def main():
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
