"""Trusted canonical Habr JobPosting text; public GET only, no application links."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import time

import httpx

from app.collectors.habr_rss import _plain, canonical_habr_url


@dataclass(frozen=True)
class HabrDetail:
    text: str = ""
    completeness: str = "UNAVAILABLE"
    status: str = "unavailable"
    checked_at: float = 0
    expired: bool = False


class _StructuredData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.capture = dict(attrs).get("type") == "application/ld+json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.blocks.append(data)


def _postings(data, depth=0):
    if depth > 6:
        return
    if isinstance(data, list):
        for entry in data[:100]:
            yield from _postings(entry, depth + 1)
    elif isinstance(data, dict):
        if data.get("@type") == "JobPosting":
            yield data
        if "@graph" in data:
            yield from _postings(data["@graph"], depth + 1)


def parse_detail(html: str, external_id: str, *, now: float) -> HabrDetail:
    parser = _StructuredData()
    parser.feed(html)
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except (ValueError, RecursionError):
            continue
        for job in _postings(data):
            identity = job.get("identifier")
            if not isinstance(identity, dict) or str(identity.get("value")) != external_id:
                continue
            raw = job.get("description")
            if not isinstance(raw, str):
                continue
            text = _plain(raw)
            expired = False
            try:
                until = datetime.fromisoformat(str(job.get("validThrough", "")).replace("Z", "+00:00"))
                # Date-only validThrough expires after that calendar day, not at its start.
                expired = until.date() < datetime.fromtimestamp(now, timezone.utc).date()
            except ValueError:
                pass
            image = "<img" in raw.casefold() or "<picture" in raw.casefold()
            completeness = "FULL" if len(text) >= 160 and not image else ("PARTIAL" if text else "UNAVAILABLE")
            return HabrDetail(text, completeness, "expired" if expired else ("image_content" if image else "text"), now, expired)
    return HabrDetail(status="no_matching_jobposting", checked_at=now)


class HabrDetailClient:
    def __init__(self, cache_path: Path, *, timeout=15.0, ttl_seconds=86400, min_interval_seconds=1.0,
                 max_bytes=1_000_000, client=None, clock=time.time, monotonic=time.monotonic, sleep=time.sleep):
        self.path = cache_path
        self.timeout = timeout
        self.ttl = ttl_seconds
        self.interval = min_interval_seconds
        self.max_bytes = max_bytes
        self.client = client
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.entries = {}
        self.last_request = None
        self.blocked = False
        self.requests = 0
        self.cache_hits = 0
        try:
            payload = json.loads(self.path.read_text())
            for key, value in payload.items():
                if not str(key).isdigit() or not isinstance(value, dict):
                    continue
                detail = HabrDetail(**value)
                if (detail.completeness in {"FULL", "PARTIAL", "UNAVAILABLE"}
                        and isinstance(detail.text, str) and isinstance(detail.status, str)
                        and isinstance(detail.checked_at, (int, float)) and isinstance(detail.expired, bool)):
                    self.entries[key] = detail
        except (OSError, ValueError, TypeError, AttributeError):
            self.entries = {}
        # A restart/shorter RSS interval must not turn a cached source denial
        # into probes of other posting URLs during the same cooldown.
        self.blocked = any(entry.status in {"http_401", "http_403", "http_429"}
                           and 0 <= self.clock() - entry.checked_at < min(self.ttl, 3600)
                           for entry in self.entries.values())

    def fetch(self, url: str, *, allow_network: bool = True) -> HabrDetail:
        identity = canonical_habr_url(url)
        if identity is None:
            return HabrDetail(status="invalid_url", checked_at=self.clock())
        key, canonical = identity
        cached = self.entries.get(key)
        ttl = self.ttl if cached and cached.completeness == "FULL" else min(self.ttl, 3600)
        if cached and 0 <= self.clock() - cached.checked_at < ttl:
            self.cache_hits += 1
            return cached
        if not allow_network:
            return HabrDetail(status="budget_limit", checked_at=self.clock())
        if self.blocked:
            return HabrDetail(status="access_restricted", checked_at=self.clock())
        if self.last_request is not None:
            self.sleep(max(0, self.interval - (self.monotonic() - self.last_request)))
        self.last_request = self.monotonic()
        self.requests += 1
        owned = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout, follow_redirects=False)
        try:
            deadline = self.monotonic() + self.timeout
            with client.stream("GET", canonical, timeout=self.timeout, follow_redirects=False) as response:
                if response.status_code != 200:
                    self.blocked = response.status_code in {401, 403, 429}
                    result = HabrDetail(status=f"http_{response.status_code}", checked_at=self.clock())
                else:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes or self.monotonic() > deadline:
                            raise ValueError("response_bound")
                    result = parse_detail(body.decode("utf-8"), key, now=self.clock())
        except httpx.TimeoutException:
            result = HabrDetail(status="timeout", checked_at=self.clock())
        except (httpx.HTTPError, ValueError, RecursionError):
            result = HabrDetail(status="unavailable", checked_at=self.clock())
        finally:
            if owned:
                client.close()
        self.entries[key] = result
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({k: asdict(v) for k, v in self.entries.items()}, ensure_ascii=False))
        temporary.replace(self.path)
        return result
