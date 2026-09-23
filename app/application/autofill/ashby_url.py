"""Cheap, browser-free signal for a recognizable canonical public Ashby job URL.

Mirrors `app.application.autofill.greenhouse_url` and `...lever_url`, but for
discovery/delivery gating only: `is_canonical_ashby_hosted_url` only answers
"is this URL Ashby's own public job-board shape?"
(`https://jobs.ashbyhq.com/<job-board-name>/<job-id>`), the shape returned by
`jobUrl` in Ashby's public posting API
(`https://api.ashbyhq.com/posting-api/job-board/<job-board-name>`). True is a
discovery/delivery signal only -- it does not assert that
`app.application.autofill` has an Ashby form adapter or that autofill is
supported for this vacancy. False means unconfirmed, not unsupported; callers
must treat it the same way the Greenhouse and Lever gates do.

This module intentionally has no Playwright import so it can be used from
`app.cli`'s Target Companies pipeline (which must stay Playwright-free).
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ASHBY_HOST_EXACT = "jobs.ashbyhq.com"


def is_canonical_ashby_hosted_url(url: str) -> bool:
    """True only for Ashby's own public job-board URL shape, over HTTPS.

    The public posting API's `jobUrl` is always HTTPS and Telegram delivery
    only validates HTTP(S) links, so a non-HTTPS scheme (or none) is treated
    as not canonical rather than trusted at face value.
    """
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host != _ASHBY_HOST_EXACT:
        return False
    segments = [part for part in parsed.path.split("/") if part]
    return len(segments) >= 2


def canonical_ashby_job_apply_path(url: str) -> str | None:
    """The expected `/application` path for the job-detail page at `url`, or
    None. Returns None when `url` is not itself a canonical Ashby job-detail
    URL (wrong host, non-HTTPS, too few path segments, or already an
    `/application` page), so callers can use this to compute the *one*
    apply link that belongs to this exact job -- never a link merely shaped
    like an Ashby apply URL. Mirrors `lever_url.canonical_lever_job_apply_path`.
    """
    if not is_canonical_ashby_hosted_url(url):
        return None
    parsed = urlsplit(url)
    segments = [part for part in parsed.path.split("/") if part]
    if segments[-1].lower() == "application":
        return None
    return "/" + "/".join(segments) + "/application"


def is_same_job_apply_url(job_detail_url: str, candidate_url: str) -> bool:
    """True only if `candidate_url` is the `/application` link for
    `job_detail_url`'s own job -- same Ashby host over HTTPS, same
    job-board/job-id path, `/application` suffix. Rejects external links,
    other jobs' apply links, and non-Ashby hosts. Mirrors
    `lever_url.is_same_job_apply_url`.
    """
    expected_path = canonical_ashby_job_apply_path(job_detail_url)
    if expected_path is None or not candidate_url:
        return False
    try:
        parsed = urlsplit(candidate_url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host != _ASHBY_HOST_EXACT:
        return False
    return parsed.path == expected_path
