from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "job_claude", Path(__file__).resolve().parents[1] / "scripts" / "job_claude.py"
)
jc = importlib.util.module_from_spec(SPEC)
sys.modules["job_claude"] = jc
SPEC.loader.exec_module(jc)

SID = "11111111-2222-4333-8444-555555555555"
FAKE = """#!{python}
import json, os, sys, time
sc = json.load(open(os.environ["FAKE_SCENARIO"]))
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(),
           "env": {{k: os.environ.get(k) for k in ("ANTHROPIC_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
                                                 "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_API_KEY")}}}},
          open(os.environ["FAKE_OUT"], "w"))
for i, line in enumerate(sc["lines"]):
    print(line, flush=True)
    if sc.get("pause_after") == i:
        time.sleep(30)
        open(sc["marker"], "w").write("retried")
sys.exit(sc.get("rc", 0))
"""


def init(model="claude-sonnet-5-5", sid=SID, **extra):
    return {"type": "system", "subtype": "init", "session_id": sid, "model": model, **extra}


def assistant(mid, usage=None, model="claude-sonnet-5-5", content=None):
    msg = {"id": mid, "model": model, "content": content or [{"type": "text", "text": "SECRET-TEXT"}]}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "message": msg}


def tool_error(text):
    block = {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": text}
    return {"type": "user", "message": {"content": [block]}}


def result(text="done", **extra):
    ev = {"type": "result", "subtype": "success", "is_error": False, "result": text,
          "usage": {"input_tokens": 7, "output_tokens": 3}, "permission_denials": []}
    ev.update(extra)
    return ev


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.setenv("FAKE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "keep-me")

    class E:
        workspace = work
        log = work / "data" / "claude-invocations.jsonl"

        @staticmethod
        def go(argv, events, rc=0, raw=None, **sc):
            lines = raw if raw is not None else [json.dumps(e) for e in events]
            Path(tmp_path / "scenario.json").write_text(json.dumps({"lines": lines, "rc": rc, **sc}))
            out, err = io.StringIO(), io.StringIO()
            code = jc.run(argv, claude_bin=fake, workspace=work, out=out, err=err)
            return code, out.getvalue(), err.getvalue()

        @staticmethod
        def called():
            return json.loads(Path(tmp_path / "out.json").read_text())

        @staticmethod
        def records():
            return [json.loads(x) for x in E.log.read_text().splitlines()]

    return E


OK = [init(), assistant("m1", {"input_tokens": 5, "output_tokens": 2}), result()]


def flag(argv, name):
    return argv[argv.index(name) + 1]


def test_defaults_and_exact_multiline_prompt(env):
    prompt = "line1\n$(rm -rf x); `y` \"q\" 'z' *\n"
    code, out, err = env.go([prompt], OK)
    assert code == 0 and out == "done\n"
    called = env.called()
    argv = called["argv"]
    assert argv[-2:] == ["--", prompt]
    assert flag(argv, "--model") == "claude-sonnet-5-5" and flag(argv, "--effort") == "medium"
    assert flag(argv, "--permission-mode") == "dontAsk"
    assert "--resume" not in argv and "--continue" not in argv
    assert called["cwd"] == str(env.workspace.resolve())
    assert called["env"] == {"ANTHROPIC_MODEL": "claude-sonnet-5-5", "CLAUDE_CODE_EFFORT_LEVEL": "medium",
                             "CLAUDE_CODE_SUBAGENT_MODEL": None, "ANTHROPIC_API_KEY": "keep-me"}
    assert "status=success" in err


@pytest.mark.parametrize("argv", [
    ["--model", "claude-opus-5-5", "--effort", "high", "p"],
    ["p", "--model=claude-opus-5-5", "--effort=high"],
    ["--model", "claude-opus-5-5", "p", "--effort", "high"],
])
def test_overrides_in_any_position(env, argv):
    code, _, _ = env.go(argv, [init("claude-opus-5-5"), result()])
    assert code == 0
    called = env.called()
    assert flag(called["argv"], "--model") == "claude-opus-5-5"
    assert flag(called["argv"], "--effort") == "high"
    assert called["env"]["ANTHROPIC_MODEL"] == "claude-opus-5-5"
    assert called["argv"][-1] == "p"


@pytest.mark.parametrize("argv", [
    ["p", "--dangerously-skip-permissions"],
    ["p", "--permission-mode", "bypassPermissions"],
    ["p", "--allowedTools", "Bash"],
    ["p", "--add-dir", "/"],
    ["p", "--continue"],
    ["p", "-c"],
    ["p", "--settings", "x.json"],
    ["p", "--model"],
    ["p", "--model", "opus"],
    ["p", "--model", "claude-x;rm"],
    ["p", "--effort", "ultra"],
    ["p", "--effort", "low", "--effort=high"],
    ["p", "second"],
    ["-p"],
    [],
    ["  "],
    ["p", "--resume", "latest"],
])
def test_unsafe_or_unknown_args_rejected(env, argv):
    code, out, err = env.go(argv, OK)
    assert code == jc.EXIT_USAGE and out == ""
    assert not env.log.exists()


def test_dash_prompt_after_double_dash(env):
    assert env.go(["--", "-weird"], OK)[0] == 0
    assert env.called()["argv"][-1] == "-weird"


def test_resume_requires_successful_record_for_same_workspace(env, tmp_path):
    other = "99999999-2222-4333-8444-555555555555"
    assert env.go(["p", "--resume", SID], OK)[0] == jc.EXIT_USAGE  # no log yet
    assert env.go(["p"], OK)[0] == 0  # records SID as success
    recs = env.records()
    assert recs[0]["session_id"] == SID and recs[0]["resumed"] is False

    assert env.go(["p", "--resume", other], OK)[0] == jc.EXIT_USAGE  # unknown id
    env.log.write_text(json.dumps({**recs[0], "workspace": "/elsewhere"}) + "\n")
    assert env.go(["p", "--resume", SID], OK)[0] == jc.EXIT_USAGE  # other workspace
    env.log.write_text(json.dumps({**recs[0], "status": "error"}) + "\n")
    assert env.go(["p", "--resume", SID], OK)[0] == jc.EXIT_USAGE  # not successful

    env.log.write_text(json.dumps(recs[0]) + "\n")
    assert env.go(["p", "--resume", SID], OK)[0] == 0
    argv = env.called()["argv"]
    assert flag(argv, "--resume") == SID
    assert env.records()[-1]["resumed"] is True


def test_tool_policy(env):
    env.go(["p"], OK)
    argv = env.called()["argv"]
    tools = next(a for a in argv if a.startswith("--tools=")).split("=", 1)[1].split(",")
    assert tools == ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    allowed = next(a for a in argv if a.startswith("--allowedTools=")).split("=", 1)[1]
    denied = next(a for a in argv if a.startswith("--disallowedTools=")).split("=", 1)[1]
    assert "Bash(git branch --show-current)" in allowed and "git branch *" not in allowed
    assert "Bash(.venv/bin/pytest tests/*)" in allowed and "Bash(.venv/bin/ruff check *)" in allowed
    for banned in ("uv run", "Bash(python", "Bash(claude", "git commit", "git push", "Agent", "Task"):
        assert banned not in allowed
    assert {"Agent", "Task", "Bash(claude *)"} <= set(denied.split(","))
    assert "bypass" not in " ".join(argv).lower().replace("--permission-mode", "")


@pytest.mark.parametrize("text", [
    "Permission to use Bash with command git push has been denied because Claude Code is running in don't ask mode.",
    "bash: .venv/bin/pytest: Permission denied",
    "OSError: [Errno 13] EACCES: open",
    "EPERM: operation not permitted",
])
def test_first_permission_failure_stops_without_retry(env, tmp_path, text):
    marker = tmp_path / "retried"
    events = [init(), assistant("m1", {"input_tokens": 1}), tool_error(text), tool_error(text), result()]
    started = time.monotonic()
    code, out, err = env.go(["p"], events, pause_after=2, marker=str(marker))
    assert code == jc.EXIT_BLOCKED and out == ""
    assert time.monotonic() - started < 20
    time.sleep(0.2)
    assert not marker.exists()
    assert "BLOCKED" in err and "git push" not in err and "EACCES" not in err
    rec = env.records()[-1]
    assert rec["status"] == "permission_blocked" and rec["permission_count"] == 1


def test_result_permission_denials_block(env):
    code, out, _ = env.go(["p"], [init(), result(permission_denials=[{"tool_name": "Bash"}])])
    assert code == jc.EXIT_BLOCKED and out == ""
    assert env.records()[-1]["permission_count"] == 1


def test_assertion_failure_is_not_a_permission_failure(env):
    text = "FAILED tests/test_x.py::t - AssertionError: expected 'permission denied' text\n= FAILURES ="
    code, out, _ = env.go(["p"], [init(), tool_error(text), result("fixing")])
    assert code == 0 and out == "fixing\n"
    assert env.records()[-1]["permission_count"] == 0


def test_real_permission_error_inside_pytest_output_blocks(env, tmp_path):
    text = "= FAILURES =\nPermissionError: [Errno 13] Permission denied\nFAILED tests/test_x.py::test_x"
    marker = tmp_path / "retried"
    events = [init(), tool_error(text), tool_error(text), result()]
    code, out, err = env.go(["p"], events, pause_after=1, marker=str(marker))
    assert code == jc.EXIT_BLOCKED and out == ""
    time.sleep(0.2)
    assert not marker.exists()
    assert env.records()[-1]["permission_count"] == 1


@pytest.mark.parametrize("payload", [
    {"type": "assistant", "message": ["RAW-SECRET"]},
    {"type": "assistant", "message": {"content": ["RAW-SECRET"]}},
    {"type": "assistant", "message": {"content": 5, "id": "m"}},
    {"type": "assistant", "message": {"id": "m", "usage": "RAW-SECRET"}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": ["RAW-SECRET"]}], "id": "m"}},
    {"type": "user", "message": "RAW-SECRET"},
    {"type": "user", "message": {"content": ["RAW-SECRET"]}},
    {"type": "system", "subtype": "init", "tools": "RAW-SECRET"},
    {"type": "result", "permission_denials": "RAW-SECRET"},
])
def test_malformed_nested_event_terminates_child(env, tmp_path, payload):
    marker = tmp_path / "retried"
    started = time.monotonic()
    code, out, err = env.go(["p"], [init(), payload, result()], pause_after=1, marker=str(marker))
    assert code != 0 and out == ""
    assert time.monotonic() - started < 20
    time.sleep(0.2)
    assert not marker.exists()
    assert env.records()[-1]["status"] == "malformed"
    assert "RAW-SECRET" not in err and "RAW-SECRET" not in env.log.read_text()


def test_resume_session_identity_must_match(env):
    other = "99999999-2222-4333-8444-555555555555"
    assert env.go(["p"], OK)[0] == 0
    code, out, _ = env.go(["p", "--resume", SID], [init(sid=other), result()])
    assert code == jc.EXIT_MISMATCH and out == ""
    assert env.records()[-1]["status"] == "mismatch"
    assert env.records()[-1]["resume_requested"] is True
    assert env.records()[-1]["resumed"] is False
    for bad in ("not-a-uuid", None):
        ev = init(sid=bad)
        if bad is None:
            del ev["session_id"]
        assert env.go(["p", "--resume", SID], [ev, result()])[0] == jc.EXIT_MISMATCH
    assert env.go(["p", "--resume", SID], OK)[0] == 0


def test_diagnostics_write_failure_is_not_success(env, monkeypatch):
    def boom(path, record):
        raise OSError("RAW-SECRET")

    monkeypatch.setattr(jc, "append_record", boom)
    code, out, err = env.go(["p"], OK)
    assert code == jc.EXIT_FAIL and out == ""
    assert "RAW-SECRET" not in err and "diagnostics_failed" in err


@pytest.mark.parametrize("kwargs", [
    {"raw": ["not json"]},
    {"raw": [json.dumps(init()), "{broken", json.dumps(result())]},
    {"events": [init(), assistant("m1")]},  # no result
    {"events": [init(), result()], "rc": 1},
    {"events": [init(), result(is_error=True, subtype="error_during_execution")]},
    {"events": [result()]},  # no init/session
    {"events": []},
])
def test_malformed_or_aborted_output_fails(env, kwargs):
    kwargs = {"events": None, **kwargs}
    code, out, _ = env.go(["p"], **kwargs)
    assert code != 0 and out == ""
    assert env.records()[-1]["status"] != "success"


@pytest.mark.parametrize("events", [
    [init("claude-opus-5-5"), result()],
    [init(), assistant("m1", model="claude-haiku-5-5"), result()],
    [init(effort="high"), result()],
    [init(tools=["Read", "Agent"]), result()],
    [init(), assistant("m1", content=[{"type": "tool_use", "name": "Task", "input": {}}]), result()],
])
def test_mismatch_fails_closed(env, events):
    code, out, _ = env.go(["p"], events)
    assert code == jc.EXIT_MISMATCH and out == ""
    assert env.records()[-1]["status"] == "mismatch"


def test_dated_model_and_matching_effort_accepted(env):
    code, _, _ = env.go(["p"], [init("claude-sonnet-5-5-20260101", effort="medium"), result()])
    assert code == 0
    rec = env.records()[-1]
    assert rec["observed_effort"] == "medium"


def test_diagnostics_sanitized_and_tokens_deduplicated(env):
    events = [
        init(),
        assistant("m1", {"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 10}),
        assistant("m1", {"input_tokens": 5, "output_tokens": 4, "cache_read_input_tokens": 10}),
        assistant("m2", {"input_tokens": 2, "output_tokens": 6, "cache_creation_input_tokens": 3}),
        tool_error("assertion SECRET-TOOL-OUTPUT failed"),
        result("final"),
    ]
    code, out, err = env.go(["PRIVATE-PROMPT"], events)
    assert code == 0 and out == "final\n"
    raw = env.log.read_text()
    for secret in ("PRIVATE-PROMPT", "SECRET-TEXT", "SECRET-TOOL-OUTPUT", "final", "keep-me"):
        assert secret not in raw and secret not in err
    rec = env.records()[-1]
    assert rec["tokens"] == {"input": 7, "output": 10, "cache_read": 10, "cache_creation": 3}
    assert rec["result_tokens"] == {"input": 7, "output": 3}
    assert rec["observed_model"] == "claude-sonnet-5-5" and rec["observed_effort"] is None
    assert rec["requested_effort"] == "medium" and rec["permission_count"] == 0
    assert rec["session_id"] == SID and rec["resumed"] is False and rec["claude_exit"] == 0
    assert stat.S_IMODE(env.log.stat().st_mode) == 0o600
    assert stat.S_IMODE(env.log.parent.stat().st_mode) == 0o700


def test_missing_claude_binary(tmp_path):
    err = io.StringIO()
    code = jc.run(["PRIVATE-PROMPT", "--effort", "high"], claude_bin=tmp_path / "nope", workspace=tmp_path, err=err)
    assert code == jc.EXIT_FAIL
    raw = (tmp_path / "data" / "claude-invocations.jsonl").read_text()
    rec = json.loads(raw)
    assert rec["status"] == "start_failed" and rec["exit_code"] == jc.EXIT_FAIL
    assert rec["requested_effort"] == "high" and rec["requested_model"] == "claude-sonnet-5-5"
    assert rec["resumed"] is False and rec["observed_model"] is None and rec["tokens"] is None
    for secret in ("PRIVATE-PROMPT", "nope", str(tmp_path)[1:]):
        assert secret not in err.getvalue()
    assert "PRIVATE-PROMPT" not in raw and "nope" not in raw
