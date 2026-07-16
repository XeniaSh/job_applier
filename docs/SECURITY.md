# Security and Privacy

## Secrets Handling

- Never commit `.env`.
- If any secret was committed, rotate it immediately.
- Treat these as secrets:
  - `LLM_API_KEY`
  - `LINKEDIN_EMAIL_PASSWORD` (Gmail App Password)
  - `TELEGRAM_BOT_TOKEN`

## Local Data

- `data/jobs.db` stores operational metadata (job IDs, statuses, Telegram message references).
- Full email bodies and full cover letters are not stored in SQLite as durable records.
- Debug HTML snapshots (when generated) may contain personal/job data.

## Git Hygiene

- Resume PDFs should stay local and be ignored by git.
- Real email/debug artifacts should be ignored by git.
- Verify `.gitignore` before committing.

## Telegram Safety

- Callback actions are accepted only from configured `TELEGRAM_CHAT_ID`.
- Keep bot token private and rotate if exposed.

## LLM Data Exposure

- Vacancy text and candidate profile context are sent to configured LLM endpoint.
- Use trusted providers and review retention policies.
- Avoid placing unnecessary personal identifiers in profile/context files.
