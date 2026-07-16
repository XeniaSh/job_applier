# Personal Job Vacancy Analyzer

Small CLI tool that compares a vacancy text against a candidate profile using an OpenAI-compatible LLM API.

## Stack

- Python 3.13
- uv
- Pydantic v2 + pydantic-settings
- httpx
- Typer
- pytest + respx

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Create `.env` from `.env.example` and set:
- `LLM_API_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `HH_USER_AGENT`
- `LINKEDIN_EMAIL_IMAP_*` (for LinkedIn Job Alert mailbox collection)
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (for Telegram delivery commands)

## Usage

```bash
python -m app review examples/vacancy.txt
```

Print validated raw JSON:

```bash
python -m app review examples/vacancy.txt --json
```

## Files used by analyzer

- `profiles/candidate_skills.yml`
- `prompts/analyze_vacancy.md`
- `<vacancy path from CLI>`

## Run tests

```bash
uv run pytest
```

## LinkedIn Job Alert Email MVP

1. Create LinkedIn Job Alerts manually.
2. Route LinkedIn alert emails to a dedicated mailbox.
3. Enable IMAP for that mailbox/provider.
4. For Gmail with 2-Step Verification, create an app password.
5. Copy `.env.example` to `.env`.
6. Fill IMAP settings (`LINKEDIN_EMAIL_IMAP_*`).
7. First run in dry-run mode:

```bash
uv run python -m app collect-linkedin-email --dry-run --limit 5
```

8. Then run real analysis:

```bash
uv run python -m app collect-linkedin-email --limit 5
```

Use app-specific passwords where supported instead of normal mailbox passwords.

## Telegram delivery setup (MVP)

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Run `/newbot`.
3. Choose bot name and username.
4. Copy token to `TELEGRAM_BOT_TOKEN`.
5. Send any message to your new bot.
6. Get `TELEGRAM_CHAT_ID` with:

```bash
uv run python -m app telegram-chat-id
```

7. Start automation service:

```bash
uv run python -m app run
```

8. Stop service with:

```text
Ctrl+C
```

### Telegram debug commands

These commands are intended only for local development and troubleshooting.

```bash
uv run python -m app telegram-debug
uv run python -m app telegram-debug --status PREPARE_REQUESTED
uv run python -m app telegram-reset 4439900667
uv run python -m app telegram-reset 4439900667 --status SENT
uv run python -m app preview-linkedin-email --limit-emails 1 --limit-vacancies 5
uv run python -m app poll-telegram-actions --once
uv run python -m app prepare-telegram-applications --limit 5
```


## Quick start

```bash
git clone ...

cp .env.example .env

# Fill in your API key

uv sync

uv run python -m app examples/vacancy.txt