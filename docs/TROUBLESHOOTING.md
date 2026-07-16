# Troubleshooting

## `uv` Is Not Recognized

Use explicit path:

```powershell
& "C:\Users\<USER>\.local\bin\uv.exe" sync
```

or install/add `uv` to `PATH`.

## PowerShell Blocks `Activate.ps1`

You do not need virtualenv activation when using `uv run`. Prefer:

```powershell
uv run pytest
uv run python -m app run
```

## `.env` Is Missing

Create it from template:

```powershell
copy .env.example .env
```

Then fill required variables.

## LLM Returns Empty Content or Invalid Response

- Verify `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`.
- Check network/proxy restrictions.
- Run:

```bash
uv run python -m app review examples/vacancy.txt
```

and inspect error output.

## Gmail Authentication Fails

- Confirm IMAP is enabled.
- Use Google App Password (not account password).
- Verify `LINKEDIN_EMAIL_USERNAME` and `LINKEDIN_EMAIL_PASSWORD`.

## IMAP Folder With Spaces Fails

- Confirm exact mailbox name using:

```bash
uv run python -m app list-imap-folders
```

- Set `LINKEDIN_EMAIL_FOLDER` exactly as listed, for example `LinkedIn Jobs`.

## No LinkedIn Emails Found

- Ensure LinkedIn alerts are enabled and sent to configured email.
- Check Gmail filter/label routing.
- Increase `LINKEDIN_EMAIL_SEARCH_DAYS`.
- Preview parser input:

```bash
uv run python -m app preview-linkedin-email
```

## Parser Extracts Mostly `MINIMAL` Vacancies

- This can happen when alert cards are sparse.
- Open the LinkedIn URL from Telegram for full context.
- Check parser output with `preview-linkedin-email`.

## Telegram Sends No Cards

- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Confirm the bot received at least one message from your account.
- Run dry-run:

```bash
uv run python -m app send-linkedin-telegram --dry-run --limit 5
```

- Check delivery state:

```bash
uv run python -m app telegram-debug
```

## Preparation Queue Is Empty

- No items in `PREPARE_REQUESTED`.
- Check states:

```bash
uv run python -m app telegram-debug --status PREPARE_REQUESTED
```

## Resume PDF Was Updated but Old Version Is Sent

- Cache invalidates automatically by path/size/mtime.
- If needed, force refresh:

```bash
uv run python -m app telegram-cache-resumes --force
```

- Or clear one cache record:

```bash
uv run python -m app telegram-clear-resume-cache java-backend --yes
```

## Resume Cache Diagnostics

Inspect cache metadata safely (without full `file_id` output):

```bash
uv run python -m app telegram-resume-cache
```

## Service Reports Already Running

- Another instance is active, or stale lock file exists:
  - `data/job_applier.lock`
- Stop the active process cleanly or remove stale lock after verifying no running service.

## HH API Returns 403

- Confirm `HH_USER_AGENT` is set.
- Retry later; API may throttle or block based on access conditions.
- Continue using LinkedIn email workflow as primary source.
