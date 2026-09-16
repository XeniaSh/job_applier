"""Cheap, browser-free signal for Greenhouse application-form support.

Discovery provider is not application-form provider: a vacancy discovered
through the Greenhouse Jobs API (`target_company:greenhouse:<board>`) is not
guaranteed to have an application page the current Greenhouse adapter
(`app.application.autofill.greenhouse`) recognizes. Some companies host their
job board on Greenhouse's own domain (`https://job-boards.greenhouse.io/<board>/jobs/<id>`,
including regional variants like `job-boards.eu.greenhouse.io`) and some
embed Greenhouse behind a custom domain (for example
`https://jobs.elastic.co/jobs?gh_jid=<id>`), which the live DOM-based adapter
may or may not recognize once rendered.

`is_canonical_greenhouse_hosted_url` only answers a narrower question:
"is this URL Greenhouse's own hosted job-board shape?". True is a strong,
verified-in-practice positive signal (every Target Company vacancy this
project has successfully autofilled uses this shape). False means
*unconfirmed*, not *unsupported* — a custom-domain embed could still work;
we simply cannot tell without loading the page in a browser. Callers must
treat False as "do not enter the Stage 1 autonomous workflow yet", never as
a rejection of the vacancy or a recommendation.

This module intentionally has no Playwright import so it can be used from
`app.cli`'s Target Companies pipeline (which must stay Playwright-free).
"""

from __future__ import annotations

from urllib.parse import urlsplit

_GREENHOUSE_HOST_SUFFIX = ".greenhouse.io"
_GREENHOUSE_HOST_EXACT = "greenhouse.io"
_JOBS_PATH_MARKER = "/jobs/"


def is_canonical_greenhouse_hosted_url(url: str) -> bool:
    """True only for Greenhouse's own hosted job-board URL shape."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host != _GREENHOUSE_HOST_EXACT and not host.endswith(_GREENHOUSE_HOST_SUFFIX):
        return False
    return _JOBS_PATH_MARKER in parsed.path
