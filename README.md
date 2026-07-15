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


## Quick start

```bash
git clone ...

cp .env.example .env

# Fill in your API key

uv sync

uv run python -m app examples/vacancy.txt