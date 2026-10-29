# Claude invocation (`job-claude`)

`scripts/job_claude.py` (stdlib only) supervises headless Claude Code runs.
`scripts/job-claude` is a thin zsh entry that runs it with `.venv/bin/python`
(fallback `/usr/bin/python3`); it needs `chmod +x`, and should be installed as
a symlink (not a copy) so the repository root resolves correctly.

```
job-claude [--model claude-<id>] [--effort low|medium|high|xhigh|max] [--resume UUID] [--] "<prompt>"
```

- Exactly one prompt, passed as a single argv element after `--` (no shell, no eval).
- Defaults are explicit: `claude-sonnet-5-5` / `medium`. No fallback, no automatic Opus/high.
  `ANTHROPIC_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` are overridden in the child environment,
  `CLAUDE_CODE_SUBAGENT_MODEL` is removed; auth variables are untouched.
- Any other flag (settings, permission mode, `--add-dir`, `--continue`, ...) exits 2.
- `--resume` accepts only a UUID recorded with `status: success` for the same workspace in
  `data/claude-invocations.jsonl`. Nothing is ever resumed implicitly.

Examples (options work before or after the prompt):

```sh
job-claude "Implement the bounded task described here."
job-claude "Fix the reviewed edge case." --effort high
job-claude --model claude-opus-5-5 --effort medium "Investigate this difficult bounded defect."
job-claude --resume <exact-session-UUID> "Continue the related fix."
```

## Tool policy

`--tools Read,Glob,Grep,Edit,Write,Bash`, `--permission-mode dontAsk`. Bash is limited to
`git status|diff|log *`, `git branch --show-current`, `.venv/bin/pytest tests/*` and
`.venv/bin/ruff check *` (no `uv`, so no global cache or egg-info rebuild). `Agent`/`Task`,
nested `claude`, general interpreters/shells and risky pytest/ruff/git options are denied.

## Supervision

The launcher reads `stream-json` events and kills the Claude process group at the first
permission failure: a Claude denial message, non-empty `permission_denials`, or a tool error
with `EACCES`/`EPERM`/`permission denied`/`operation not permitted`. It prints a sanitized
blocker to stderr and exits 3. In pytest-style output (`AssertionError`, `FAILED`, `= FAILURES =`)
lines that merely assert on text are ignored, but a real `PermissionError`, `EACCES`, `EPERM` or
`[Errno 1|13]` elsewhere in the output is still a permission failure.

Unexpected event shapes (non-object messages, content, tools, usage or denials) are a protocol
error: the process group is terminated, status `malformed`, exit 1, no final answer. With
`--resume`, the init `session_id` must equal the requested UUID (missing/invalid/different
fails closed with exit 4). A failed child start is recorded as `start_failed` (exit 1). If the
diagnostics record cannot be written, the run is reported as `diagnostics_failed` (exit 1) and
no final answer is printed.

Exit codes: 0 success, 1 failed/malformed/aborted/no result, 2 usage, 3 permission blocker,
4 observed model/effort/tool-exposure mismatch (fail closed). The final answer goes to stdout
only on success; a one-line diagnostic goes to stderr.

## Diagnostics

One JSON line per invocation in `data/claude-invocations.jsonl` (git-ignored, mode 0600):
requested vs observed model/effort (`null` when the CLI does not emit them), tokens
(deduplicated by assistant message id) and result-event tokens, permission count, session
UUID, resume-requested and observed-resumed flags, statuses. No prompt, commands,
file contents or transcripts. A requested resume counts as observed only when
the CLI initializes with that exact session UUID.

## Limitations

- Effort is usually not emitted by the CLI, so it is recorded as requested-only (observed `null`).
- Permission rule wildcards are matched by Claude Code; the deny list narrows but is not a sandbox.
- The child receives matching explicit `--effort` and `CLAUDE_CODE_EFFORT_LEVEL` values;
  an inherited parent value cannot silently raise the requested level.
- Permission detection relies on message text and is heuristic.
- Native Claude transcript persistence must be available for real resumption.
  A successful launcher record alone does not create a native transcript. Missing
  conversations fail without automatically starting a new session.

## Verification — 2026-10-29

- Native installation upgraded with `claude update`; installed version: 2.1.312.
- 57 isolated launcher tests, scoped Ruff and zsh syntax check pass.
- Tiny live no-tools check observed Sonnet 5.5. A separate live check ran one
  focused launcher pytest case and scoped Ruff through the installed entry point,
  both successfully with zero permission denials. No application task was run.
- Live exact-UUID resume did not complete: Claude reported no saved conversation,
  and no corresponding native transcript was present in the project log directory.
  Resume validation/identity checks pass in isolated tests; native persistence and
  live resume acceptance remain pending. No config/auth relocation or broader
  filesystem permissions were introduced to work around this.
