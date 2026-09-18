"""Cheap, browser-free signal for Lever application-form support.

Mirrors `app.application.autofill.greenhouse_url`: `is_canonical_lever_hosted_url`
only answers "is this URL Lever's own hosted job-board shape?"
(`https://jobs.lever.co/<company>/<posting-id>`). Unlike Greenhouse, the
Lever Postings API (`api.lever.co/v0/postings/<slug>`) always returns
`hostedUrl`/`applyUrl` on Lever's own domain, so this is expected to be True
for every vacancy `LeverTargetWatcher` discovers -- there is no custom-domain
embed case to worry about here.

This module intentionally has no Playwright import so it can be used from
`app.cli`'s Target Companies pipeline (which must stay Playwright-free).
"""

from __future__ import annotations

from urllib.parse import urlsplit

_LEVER_HOST_SUFFIX = ".lever.co"
_LEVER_HOST_EXACT = "lever.co"


def is_canonical_lever_hosted_url(url: str) -> bool:
    """True only for Lever's own hosted job-board URL shape."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host != _LEVER_HOST_EXACT and not host.endswith(_LEVER_HOST_SUFFIX):
        return False
    segments = [part for part in parsed.path.split("/") if part]
    return len(segments) >= 2


def canonical_lever_job_apply_path(url: str) -> str | None:
    """The expected `/apply` path for the job-detail page at `url`, or None.

    Returns None when `url` is not itself a canonical Lever job-detail URL
    (wrong host, too few path segments, or already an `/apply` page), so
    callers can use this to compute the *one* apply link that belongs to
    this exact job -- never a link merely shaped like a Lever apply URL.
    """
    if not is_canonical_lever_hosted_url(url):
        return None
    parsed = urlsplit(url)
    segments = [part for part in parsed.path.split("/") if part]
    if segments[-1].lower() == "apply":
        return None
    return "/" + "/".join(segments) + "/apply"


def is_same_job_apply_url(job_detail_url: str, candidate_url: str) -> bool:
    """True only if `candidate_url` is the `/apply` link for `job_detail_url`'s
    own job -- same Lever host, same company/posting-id path, `/apply` suffix.
    Rejects external links, other jobs' apply links, and non-Lever hosts.
    """
    expected_path = canonical_lever_job_apply_path(job_detail_url)
    if expected_path is None or not candidate_url:
        return False
    try:
        parsed = urlsplit(candidate_url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host != _LEVER_HOST_EXACT and not host.endswith(_LEVER_HOST_SUFFIX):
        return False
    return parsed.path == expected_path
