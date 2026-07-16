# Job Applier Cheat Sheet

Most of the time you only need one command:

```bash
uv run python -m app run
```

This command monitors LinkedIn Job Alert emails, analyzes vacancies, sends relevant ones to Telegram, prepares an application package after pressing "Prepare", and stops with `Ctrl+C`.

## Initial setup (run once)

### Verify IMAP folders

```bash
uv run python -m app list-imap-folders
```

Shows available Gmail folders.

### Preview LinkedIn emails

```bash
uv run python -m app preview-linkedin-email
```

Checks that LinkedIn alerts are parsed correctly.

### Get Telegram chat id

```bash
uv run python -m app telegram-chat-id
```

Shows your Telegram chat ID.

### Cache resume PDFs

```bash
uv run python -m app telegram-cache-resumes
```

Uploads each resume PDF once to Telegram and stores reusable file IDs.

## Daily workflow

### Start background service

```bash
uv run python -m app run
```

Starts continuous monitoring.

## Diagnostics

### Inspect Telegram delivery state

```bash
uv run python -m app telegram-debug
```

Shows statuses such as `SENT`, `PREPARE_REQUESTED`, `PREPARED` and `APPLIED`.

### Inspect application history

```bash
uv run python -m app application-history
```

Shows recent lifecycle events across vacancies.

### Show application statistics

```bash
uv run python -m app application-stats --days 30
```

Shows found/sent/prepared/applied/skipped counts and conversion rates.

### Reset one vacancy

```bash
uv run python -m app telegram-reset <vacancy_id>
```

Moves a vacancy back to `PREPARE_REQUESTED`.

### Inspect resume cache

```bash
uv run python -m app telegram-resume-cache
```

Shows cached Telegram file IDs (preview only).

### Rebuild resume cache

```bash
uv run python -m app telegram-cache-resumes --force
```

Uploads PDFs again.

## Developer tools

### Analyze LinkedIn emails

```bash
uv run python -m app collect-linkedin-email
```

Runs analysis only.

### Preview Telegram cards

```bash
uv run python -m app send-linkedin-telegram --dry-run
```

Shows cards without sending.

### Preview generated applications

```bash
uv run python -m app prepare-telegram-applications --dry-run
```

Shows generated cover letters locally.

## Status lifecycle

```text
NEW
→ SENT
→ PREPARE_REQUESTED
→ PREPARED
→ APPLIED
```

or

```text
SENT
→ SKIPPED
```

## Typical troubleshooting

"No Telegram messages"

→ `preview-linkedin-email`  
→ `telegram-debug`

"Resume not attached"

→ `telegram-cache-resumes`

"Need to regenerate one vacancy"

→ `telegram-reset <id>`
