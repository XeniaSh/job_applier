"""Pure, browser-free URL checks for SmartRecruiters OneClick application support.

Stage 1 is deliberately Wise-only (`SUPPORTED_COMPANIES`) and unregistered.
The publication UUID of the application form is never derived from the
numeric posting ID: it only ever comes from a validated, visible link on the
public posting page (or a directly supplied, validated base publication URL).
No Playwright import so it stays usable from Playwright-free pipelines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

SMARTRECRUITERS_HOST = "jobs.smartrecruiters.com"
SUPPORTED_COMPANIES = frozenset({"Wise"})

_POSTING_PATH_RE = re.compile(r"^/(?P<company>[A-Za-z0-9_-]+)/(?P<posting_id>\d{6,})(?:-[A-Za-z0-9%._~-]*)?/?$")
_APPLICATION_PATH_RE = re.compile(
    r"^/oneclick-ui/company/(?P<company>[A-Za-z0-9_-]+)/publication/"
    r"(?P<publication>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?P<stage>/screening)?/?$"
)


@dataclass(frozen=True)
class PostingRef:
    company: str
    posting_id: str


@dataclass(frozen=True)
class ApplicationRef:
    company: str
    publication: str
    screening: bool


def _split_trusted(url: str | None):
    """`urlsplit` result for an exact credential-free, port-free https
    jobs.smartrecruiters.com URL without fragment; otherwise None."""
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or port is not None or parsed.username or parsed.password:
        return None
    if parsed.fragment or (parsed.hostname or "").lower() != SMARTRECRUITERS_HOST:
        return None
    if parsed.netloc.lower() != SMARTRECRUITERS_HOST:
        return None
    return parsed


def parse_posting_url(url: str | None) -> PostingRef | None:
    """A supported company's public numeric posting URL (no query), or None."""
    parsed = _split_trusted(url)
    if parsed is None or parsed.query:
        return None
    match = _POSTING_PATH_RE.match(parsed.path)
    if match is None or match["company"] not in SUPPORTED_COMPANIES:
        return None
    return PostingRef(company=match["company"], posting_id=match["posting_id"])


def parse_application_url(url: str | None) -> ApplicationRef | None:
    """A supported company's OneClick publication URL (base or /screening).

    The only query accepted is the observed `dcr_ci=<same company>`; any
    other parameter (referral/tracking) rejects the URL."""
    parsed = _split_trusted(url)
    if parsed is None:
        return None
    match = _APPLICATION_PATH_RE.match(parsed.path)
    if match is None or match["company"] not in SUPPORTED_COMPANIES:
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if query not in ([], [("dcr_ci", match["company"])]):
        return None
    return ApplicationRef(
        company=match["company"],
        publication=match["publication"],
        screening=match["stage"] is not None,
    )


def is_base_application_url(url: str | None) -> bool:
    ref = parse_application_url(url)
    return ref is not None and not ref.screening


def same_company_application_ref(posting: PostingRef, candidate_url: str | None) -> ApplicationRef | None:
    """The base application ref for `candidate_url` iff it belongs to the
    posting's own company; screening or other-company links are rejected."""
    ref = parse_application_url(candidate_url)
    if ref is None or ref.screening or ref.company != posting.company:
        return None
    return ref
