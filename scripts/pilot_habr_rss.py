#!/usr/bin/env python3
"""Public RSS pilot; no runtime Settings, LLM, DB, cache or Telegram imports."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collectors.habr_rss import HabrRSSConfig, collect_rss, evaluate_record  # noqa: E402
from app.company_watch.domain_policy import load_domain_policy  # noqa: E402


def build_report(result, config, *, now=None):
    now = now or datetime.now(timezone.utc)
    policy = load_domain_policy(ROOT / "config/discovery_policy.yaml")
    rows = [evaluate_record(r, policy=policy, now=now, max_age_days=config.max_age_days) for r in result.records]
    # Local research artifact only: public, contact-redacted snippets allow manual
    # review. The console prints counters/diagnostics, never descriptions.
    for row, record in zip(rows, result.records, strict=True):
        row["snippet"] = record.vacancy.description
    raw = sum(f.raw for f in result.feeds)
    invalid = sum(f.invalid for f in result.feeds)
    counts = {key: sum(bool(r[key]) for r in rows) for key in [
        "jvm_candidate", "ai_backend_candidate", "ai_backend_review_candidate", "explicit_international_remote",
        "remote_geography_unknown", "remote_geography_restricted", "abroad_only_restriction",
        "unsuitable", "excluded_by_current_rules", "fresh",
    ]}
    counts.update(raw=raw, unique=len(rows), invalid=invalid,
                  duplicates=max(0, raw - invalid - len(rows)),
                  missing_company=sum(not r["company"] for r in rows), missing_location=sum(not r["location"] for r in rows),
                  missing_salary=sum(not r["salary"] for r in rows), missing_date=sum(not r["published_at"] for r in rows))
    counts["fresh_jvm_candidates"] = sum(r["jvm_candidate"] and r["fresh"] for r in rows)
    counts["feed_errors"] = sum(f.stop not in {"page_limit", "empty", "repeated_page"} for f in result.feeds)
    return {"checked_at": now.isoformat(), "config": config.model_dump(), "counts": counts,
            "duplicate_rate": round(counts["duplicates"] / raw, 4) if raw else 0,
            "feeds": [asdict(f) for f in result.feeds], "vacancies": rows,
            "limits": ["RSS snippets are PARTIAL; title candidates are not verified skills matches or legal eligibility.",
                       "Dates may reflect bumps. No new-to-user/weekly yield, archived-status or full-text verification.",
                       "No LLM, production database, history/cache or Telegram access."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--config", type=Path, default=ROOT / "config/habr_rss_pilot.yaml")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "data/habr-pilot"):
        parser.error("output must be a new file under this checkout's data/habr-pilot")
    if output.exists():
        parser.error("output already exists; use a new report path")
    try:
        payload = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid mapping")
        if args.queries:
            payload["queries"] = args.queries
        if args.max_pages is not None:
            payload["max_pages"] = args.max_pages
        config = HabrRSSConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError):
        parser.error("Invalid pilot configuration; check documented field names and bounds")
    report = build_report(collect_rss(config), config)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"counts": report["counts"], "feeds": report["feeds"], "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
