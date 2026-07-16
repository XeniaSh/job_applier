# Commands Reference

All commands are available through:

```bash
uv run python -m app <command> [options]
```

## Command Matrix

| Command | Purpose | Example | Calls LLM | Writes SQLite | Sends Telegram | Safe Dry-Run Option |
|---|---|---|---|---|---|---|
| `review` | Analyze one vacancy text file | `uv run python -m app review examples/vacancy.txt` | Yes | No | No | No |
| `collect-hh` | Collect and analyze HH vacancies | `uv run python -m app collect-hh --limit 20` | Yes | Yes (`seen_jobs`) | No | No |
| `collect-linkedin-email` | Collect/analyze LinkedIn email vacancies | `uv run python -m app collect-linkedin-email --limit 20` | Yes | Yes | No | Yes (`--dry-run`) |
| `preview-linkedin-email` | Parse/inspect raw LinkedIn emails | `uv run python -m app preview-linkedin-email --limit-emails 2` | No | No | No | Always dry |
| `list-imap-folders` | Show IMAP-visible mailbox names | `uv run python -m app list-imap-folders` | No | No | No | Always dry |
| `send-linkedin-telegram` | Send relevant analyzed vacancies to Telegram | `uv run python -m app send-linkedin-telegram --limit 10` | Yes (analysis step) | Yes | Yes | Yes (`--dry-run`) |
| `telegram-chat-id` | Discover private Telegram chat IDs | `uv run python -m app telegram-chat-id` | No | No | Reads Telegram | Always read-only |
| `poll-telegram-actions` | Process callback actions from Telegram | `uv run python -m app poll-telegram-actions --once` | No | Yes | Yes (callback answers/markup edits) | N/A |
| `prepare-telegram-applications` | Build and send application packages from queue | `uv run python -m app prepare-telegram-applications --limit 5` | Yes | Yes | Yes | Yes (`--dry-run`) |
| `telegram-debug` | List delivery rows for troubleshooting | `uv run python -m app telegram-debug --status PREPARE_REQUESTED` | No | No | No | Always read-only |
| `telegram-reset` | Reset delivery status for one row | `uv run python -m app telegram-reset 4439900667 --status PREPARE_REQUESTED` | No | Yes | No | No |
| `telegram-delete-delivery` | Delete one delivery row (with confirmation) | `uv run python -m app telegram-delete-delivery 4439900667 --yes` | No | Yes | No | No |
| `run` | Start continuous automation service | `uv run python -m app run` | Yes | Yes | Yes | No |

## Status Lifecycle

Primary path:

`SENT` -> `PREPARE_REQUESTED` -> `PREPARED` -> `APPLIED`

Alternative states:

- `SKIPPED`
- `FAILED`
- `PREPARATION_FAILED`

## Transition Triggers

- `SENT`: vacancy card successfully sent to Telegram.
- `PREPARE_REQUESTED`: user pressed prepare callback action.
- `PREPARED`: package generated and sent.
- `APPLIED`: user pressed applied callback action.
- `SKIPPED`: user pressed skip callback action.
- `FAILED`: delivery/send failure.
- `PREPARATION_FAILED`: preparation pipeline failed for queued item.
