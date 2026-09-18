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
