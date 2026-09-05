# AGENTS.md

This file contains instructions for AI coding assistants working on this repository.

## Project overview

JobApplier is a local AI-assisted job search automation tool.

The project collects job vacancies, normalizes them into a shared vacancy model, analyzes them with deterministic rules and LLM-assisted extraction, sends relevant jobs to Telegram, and can prepare application materials such as cover letters and resume packages.

The project is intentionally conservative:

- It may assist with job discovery and application preparation.
- It must not submit job applications automatically unless explicitly implemented as a separate, reviewed feature.
- Final application submission should remain under user control.

## Current architecture

The project is a Python CLI application.

Important areas:

- `app/collectors/`
  - Collectors for vacancy sources.
  - Collectors should return normalized vacancies.
  - Existing Greenhouse collector logic is shared and should be reused.

- `app/company_watch/`
  - Target company configuration and company-specific watchers.
  - This is a separate bounded context for monitoring selected companies.
  - It should not contain Telegram, database, or application-submission logic.

- `app/application/`
  - Application preparation logic.
  - Cover letter and resume package preparation belongs here.

- `app/telegram/`
  - Telegram Bot API integration, message rendering, callbacks, and lifecycle handling.

- `app/storage/`
  - SQLite-backed persistence, deduplication, checkpoints, Telegram delivery state, and preparation state.

- `app/config.py`
  - Environment-based settings using `pydantic-settings`.

- `config/`
  - Static project configuration files.
  - `config/target_companies.yaml` contains target companies for company watchers.

- `data/`
  - Runtime state only: SQLite database, lock files, caches, and debug files.
  - Do not put static configuration into `data/`.

## Important design principles

### Keep changes small

Implement one step at a time.

Do not combine unrelated work in one change. For example:

- Do not add a watcher and also wire it into Telegram.
- Do not add CLI integration and also change database schema.
- Do not add autofill while working on vacancy collection.

### Preserve existing pipeline boundaries

New vacancy sources should produce the existing normalized vacancy model.

Do not create a parallel vacancy model unless explicitly requested.

The intended flow is:

```text
collector / watcher
↓
NormalizedVacancy
↓
analysis
↓
Telegram
↓
application preparation
```

### Do not over-automate

Do not implement automatic final application submission.

Autofill may be implemented later, but it must stop before final Submit unless explicitly requested.

### Prefer deterministic logic over LLM decisions

LLMs may extract structured information and generate text.

Deterministic code should own:

- scoring;
- hard filters;
- state transitions;
- deduplication;
- final decision labels.

### Prefer boring, reliable code

Avoid unnecessary frameworks.

Do not add the following unless explicitly requested:

- LangChain;
- agent frameworks;
- Celery/RQ;
- new databases;
- browser automation;
- distributed queues.

## Target companies

Target companies are configured in:

```text
config/target_companies.yaml
```

The field `known_hiring_locations` is metadata only.

It must not be used as a hard filter.

The following fields are also metadata for now and must not filter vacancies unless explicitly requested:

- `language`
- `relocation_status`
- `remote`
- `hiring_modes`
- `russian_speaking_signal`

For now, company watchers should use only source-specific fields such as:

- `watcher_type`
- `ats`
- `career_url`
- `job_board_url`
- `role_keywords`

## Company watchers

Company watchers belong under:

```text
app/company_watch/watchers/
```

A watcher should:

- accept one `TargetCompany` or a list of companies;
- process only companies matching its ATS/source type;
- isolate errors per company;
- return normalized vacancies;
- not write to SQLite;
- not send Telegram messages;
- not modify application state;
- not call the main pipeline unless explicitly requested.

For Target Company vacancies, use a source format similar to:

```text
target_company:<watcher_type>:<company-slug>
```

Example:

```text
target_company:greenhouse:agoda
```

`external_id` should be stable:

- use the ATS job ID when available;
- otherwise use a stable fallback derived from the vacancy URL.

## Greenhouse

Greenhouse logic should be shared with the existing Greenhouse collector.

Do not duplicate HTTP/API mapping code if shared functions already exist.

Existing shared helpers may include:

- `build_greenhouse_http_client`
- `fetch_greenhouse_board_jobs`
- `greenhouse_jobs_endpoint`
- `greenhouse_job_to_normalized`

Use existing timeout and HTTP style where possible.

## Filtering

For Target Company watchers:

- `role_keywords` are a soft OR filter over title + description.
- If `role_keywords` is empty, do not filter by keywords.
- Do not filter by `known_hiring_locations`.
- Do not filter by relocation metadata.
- Do not filter by language metadata.

The goal is to avoid missing potentially good roles from high-priority companies.

## Error handling

A failure in one company must not stop processing of other companies.

Network errors, invalid responses, and unsupported companies should be reported in the result structure or logged according to the existing project style.

Do not introduce complex retry or circuit-breaker logic unless explicitly requested.

## Tests

Add focused tests for every new module.

Prefer small, isolated tests over broad integration tests.

Use existing test libraries and patterns. Do not add dependencies unless necessary.

For company watcher work, test at least:

- unsupported watcher type is skipped;
- mocked API response is parsed;
- vacancies map into `NormalizedVacancy`;
- keyword filtering works;
- empty keyword list does not filter;
- one company failure does not break another company;
- `source` and `external_id` are stable.

## Commands

Use project commands from `pyproject.toml`, existing tests, or README.

Common commands:

```bash
uv run pytest tests/company_watch/test_config_loader.py
uv run pytest tests/company_watch/watchers/test_greenhouse_watcher.py
uv run pytest tests/test_greenhouse_collector.py
uv run ruff check app/company_watch tests/company_watch
```

Do not blindly fix unrelated historical failures.

Known unrelated issues may exist in the wider test suite:

- `tests/test_config_greenhouse.py` may depend on environment variables from `.env`;
- `tests/test_run_cli.py` may hang or be interrupted around lock-related tests;
- `uv run ruff check .` may report older unrelated issues outside the files being changed.

If a failure is unrelated to the current diff, report it clearly instead of modifying unrelated code.

## Scope control

Before making changes, state:

1. what files will be changed;
2. what will not be changed;
3. how the change will be tested.

After making changes, report:

1. changed files;
2. tests added or updated;
3. commands run;
4. remaining known issues;
5. suggested next step.

## Do not do these unless explicitly requested

- Do not wire new watchers into the main `run` loop.
- Do not add Telegram messages.
- Do not write to SQLite.
- Do not change database schema.
- Do not implement auto-apply.
- Do not implement ATS autofill.
- Do not add browser automation.
- Do not add Workday support.
- Do not change resume generation.
- Do not rewrite existing collectors.
- Do not refactor unrelated modules.
- Do not fix unrelated old tests.
