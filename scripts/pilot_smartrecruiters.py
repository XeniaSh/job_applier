#!/usr/bin/env python3
"""Read-only SmartRecruiters discovery pilot for a single employer.

Standalone diagnostic script for TASK-092. Imports only the SmartRecruiters
watcher and the `TargetCompany` model -- never `app.config`/Settings, any
storage/cache/DB module, the Telegram client, or the LLM/vacancy analyzer --
so it cannot touch private profiles, production caches/DBs, chat delivery,
or LLM calls. The `TargetCompany` passed to the watcher is constructed
in-memory here and is never read from or written to the repo's real
`target_companies*` config.

Network access is limited to read-only GET requests the watcher itself
issues against the public SmartRecruiters postings API (list + per-posting
detail); this script never calls an ATS write/apply endpoint.

Output is a single JSON file written to an isolated, gitignored path under
`data/smartrecruiters-pilot/` (never overwriting an existing file). It
contains only public posting fields already present on `NormalizedVacancy`
(source, external_id, title, company, location, url, description, ...) plus
the watcher's own counters/errors/bounds; creator/contact fields from the
SmartRecruiters API are never read by the watcher and so can never leak
into this output.

A title-prefilter match is NOT a verified backend-skills match or an
immigration/relocation eligibility determination -- both the per-run
output and the printed summary say so explicitly; a human (Codex) reviews
the full descriptions separately before drawing either conclusion.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap this checkout's repo root onto sys.path *before* importing
# `app.*`, so running this script by path can never resolve `app` from a
# different editable install (e.g. another worktree/checkout) that happens
# to be importable from the current environment.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.company_watch.models import TargetCompany  # noqa: E402
from app.company_watch.watchers.smartrecruiters import SmartRecruitersTargetWatcher  # noqa: E402

DEFAULT_EMPLOYER_NAME = "Wise"
DEFAULT_BOARD_URL = "https://careers.smartrecruiters.com/Wise"
# Tied to this checkout's actual repo root (not a relative "data/..." path)
# so `_validate_output_path` can refuse any directory that merely happens to
# be *named* "smartrecruiters-pilot" outside of it.
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data" / "smartrecruiters-pilot"
PILOT_TITLE_INCLUDE_KEYWORDS = [
    "java",
    "kotlin",
    "backend",
    "back-end",
    "software engineer",
    "platform engineer",
]
MAX_ALLOWED_DETAIL_FETCHES = 100

LIMITATIONS = [
    "A title-prefilter match only means the posting's title contains one of "
    "the configured keywords (java, kotlin, backend, back-end, software "
    "engineer, platform engineer); it is not a verified backend-skills "
    "match and not an immigration/relocation eligibility determination.",
    "relocation_status on the in-memory pilot TargetCompany is 'unknown' by "
    "design -- this script does not classify or guess visa/relocation "
    "support; read the full description text for any job-specific "
    "relocation/visa evidence.",
    "`pages_truncated`/`detail_truncated` being true means the configured "
    "bound (max_pages / max_detail_fetches) was hit before the watcher "
    "could prove it had seen every posting; results are a partial, "
    "honestly-labeled sample, not a claim of completeness.",
    "Only the public fields already present on NormalizedVacancy are "
    "written here; creator/contact fields from the SmartRecruiters API are "
    "never read by the watcher and so never appear in this output.",
]


def build_pilot_company(*, employer_name: str, board_url: str) -> TargetCompany:
    return TargetCompany.model_validate(
        {
            "name": employer_name,
            "priority": "PILOT",
            "language": "english",
            "relocation_status": "unknown",
            "watcher_type": "smartrecruiters",
            "ats": "smartrecruiters",
            "job_board_url": board_url,
            "role_title_keywords": list(PILOT_TITLE_INCLUDE_KEYWORDS),
        }
    )


def _vacancy_to_dict(vacancy) -> dict[str, object]:
    return {
        "source": vacancy.source,
        "external_id": vacancy.external_id,
        "title": vacancy.title,
        "company": vacancy.company,
        "location": vacancy.location,
        "employment": vacancy.employment,
        "url": vacancy.url,
        "published_at": vacancy.published_at,
        "content_completeness": vacancy.content_completeness,
        "description": vacancy.description,
    }


# Explicit public whitelists: `response_snippet` is raw ATS response text and
# is deliberately excluded here, since an error body can carry creator/contact
# payload that must never reach this pilot's output.
_COMPANY_ERROR_PUBLIC_FIELDS = (
    "company_name",
    "message",
    "slug",
    "endpoint",
    "status_code",
    "error_type",
    "offset",
)
_JOB_ERROR_PUBLIC_FIELDS = (
    "company_name",
    "posting_id",
    "message",
    "endpoint",
    "status_code",
    "error_type",
)


def _public_error_dict(error, fields: tuple[str, ...]) -> dict[str, object]:
    data = asdict(error)
    return {field_name: data[field_name] for field_name in fields}


def build_report(
    *,
    employer_name: str,
    board_url: str,
    page_limit: int,
    max_pages: int,
    max_detail_fetches: int,
    result,
) -> dict[str, object]:
    is_complete = (
        not result.pages_truncated
        and not result.detail_truncated
        and not result.errors
        and not result.job_errors
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pilot": "TASK-092 SmartRecruiters discovery pilot",
        "employer": employer_name,
        "board_url": board_url,
        "complete": is_complete,
        "bounds": {
            "page_limit": page_limit,
            "max_pages": max_pages,
            "max_detail_fetches": max_detail_fetches,
            "title_include_keywords": list(PILOT_TITLE_INCLUDE_KEYWORDS),
        },
        "counters": {
            "raw_fetched": result.raw_fetched,
            "unique_count": result.unique_count,
            "duplicate_count": result.duplicate_count,
            "title_matched_count": result.title_matched_count,
            "detail_fetched_count": result.detail_fetched_count,
            "pages_fetched": result.pages_fetched,
            "pages_truncated": result.pages_truncated,
            "detail_truncated": result.detail_truncated,
            "vacancy_count": len(result.vacancies),
        },
        "errors": [_public_error_dict(error, _COMPANY_ERROR_PUBLIC_FIELDS) for error in result.errors],
        "job_errors": [_public_error_dict(error, _JOB_ERROR_PUBLIC_FIELDS) for error in result.job_errors],
        "vacancies": [_vacancy_to_dict(vacancy) for vacancy in result.vacancies],
        "limitations": LIMITATIONS,
    }


def _validate_bounds(*, page_limit: int, max_pages: int, max_detail_fetches: int) -> str | None:
    if not (1 <= page_limit <= 100):
        return f"--page-limit must be between 1 and 100, got {page_limit}"
    if max_pages <= 0:
        return f"--max-pages must be a positive integer, got {max_pages}"
    if not (0 <= max_detail_fetches <= MAX_ALLOWED_DETAIL_FETCHES):
        return f"--max-detail must be between 0 and {MAX_ALLOWED_DETAIL_FETCHES}, got {max_detail_fetches}"
    return None


def _validate_output_path(output_path: Path) -> str | None:
    # Compare against the actual allowed directory, not merely its name --
    # a directory elsewhere on disk that happens to be named
    # "smartrecruiters-pilot" must not pass this check.
    allowed_dir = DEFAULT_OUTPUT_DIR.resolve()
    if output_path.parent != allowed_dir:
        return f"refusing to write output: parent directory must be {allowed_dir}"
    if output_path.exists():
        return f"refusing to overwrite existing output: {output_path}"
    return None


def _default_output_path(employer_name: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = "".join(c if c.isalnum() else "-" for c in employer_name.strip().lower()).strip("-") or "employer"
    return DEFAULT_OUTPUT_DIR / f"{safe_name}-{timestamp}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--employer-name", default=DEFAULT_EMPLOYER_NAME, help="Single employer to watch (default: Wise)")
    parser.add_argument("--board-url", default=DEFAULT_BOARD_URL, help="Public SmartRecruiters careers/board URL for the employer")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (must not exist; parent must be the allowed smartrecruiters-pilot output directory)",
    )
    parser.add_argument("--page-limit", type=int, default=100, help="Requested page size for the postings list (<=100)")
    parser.add_argument("--max-pages", type=int, default=10, help="Bound on list pages fetched for this employer")
    parser.add_argument(
        "--max-detail",
        type=int,
        default=100,
        help=f"Bound on detail fetches for this employer (<= {MAX_ALLOWED_DETAIL_FETCHES})",
    )
    args = parser.parse_args(argv)

    max_detail_fetches = args.max_detail
    bounds_problem = _validate_bounds(
        page_limit=args.page_limit, max_pages=args.max_pages, max_detail_fetches=max_detail_fetches
    )
    if bounds_problem:
        print(f"error: {bounds_problem}", file=sys.stderr)
        return 2

    output_path = Path(args.output).resolve() if args.output else _default_output_path(args.employer_name).resolve()

    problem = _validate_output_path(output_path)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    company = build_pilot_company(employer_name=args.employer_name, board_url=args.board_url)
    watcher = SmartRecruitersTargetWatcher(
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        max_detail_fetches=max_detail_fetches,
    )
    result = watcher.watch(company)

    report = build_report(
        employer_name=args.employer_name,
        board_url=args.board_url,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        max_detail_fetches=max_detail_fetches,
        result=result,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(output_path, "x", encoding="utf-8") as output_fh:
            json.dump(report, output_fh, indent=2, ensure_ascii=False, sort_keys=False)
            output_fh.write("\n")
    except FileExistsError:
        print(f"error: refusing to overwrite existing output: {output_path}", file=sys.stderr)
        return 2

    print(f"wrote {len(result.vacancies)} title-matched SmartRecruiters vacancies for '{args.employer_name}' to {output_path}")
    if result.errors:
        print(f"note: {len(result.errors)} company-level error(s) recorded in output", file=sys.stderr)
    if result.job_errors:
        print(f"note: {len(result.job_errors)} per-posting detail error(s) recorded in output", file=sys.stderr)
    if result.pages_truncated or result.detail_truncated:
        print("note: result is bounded/partial (pages_truncated or detail_truncated set) -- see output", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
