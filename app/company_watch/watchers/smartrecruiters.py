from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
import re
from urllib.parse import urlparse

import httpx

from app.collectors.greenhouse_collector import clean_html_to_text
from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.models import TargetCompany
from app.company_watch.prefilter import passes_role_prefilter

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "target_company:smartrecruiters"
_RESPONSE_SNIPPET_LIMIT = 400
_DEFAULT_PAGE_LIMIT = 100
_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_DETAIL_FETCHES = 100

_SAFE_BOARD_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_POSTING_IDENTITY_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


@dataclass(frozen=True)
class SmartRecruitersCompanyError:
    company_name: str
    message: str
    slug: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    response_snippet: str | None = None
    error_type: str | None = None
    offset: int | None = None


@dataclass(frozen=True)
class SmartRecruitersJobError:
    company_name: str
    posting_id: str
    message: str
    endpoint: str | None = None
    status_code: int | None = None
    response_snippet: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class SmartRecruitersWatchResult:
    vacancies: list[NormalizedVacancy]
    errors: list[SmartRecruitersCompanyError]
    job_errors: list[SmartRecruitersJobError] = field(default_factory=list)
    raw_fetched: int = 0
    unique_count: int = 0
    duplicate_count: int = 0
    title_matched_count: int = 0
    detail_fetched_count: int = 0
    pages_fetched: int = 0
    pages_truncated: bool = False
    detail_truncated: bool = False


class SmartRecruitersCollectionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        slug: str | None = None,
        endpoint: str | None = None,
        status_code: int | None = None,
        response_snippet: str | None = None,
        error_type: str | None = None,
        offset: int | None = None,
        posting_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.slug = slug
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_snippet = response_snippet
        self.error_type = error_type
        self.offset = offset
        self.posting_id = posting_id


@dataclass(frozen=True)
class SmartRecruitersPageFetchResult:
    rows: list[dict[str, object]]
    raw_fetched: int
    pages_fetched: int
    total_found: int | None
    truncated: bool
    error: SmartRecruitersCollectionError | None


@dataclass
class _CompanyOutcome:
    vacancies: list[NormalizedVacancy]
    raw_fetched: int
    unique_count: int
    duplicate_count: int
    title_matched_count: int
    detail_fetched_count: int
    pages_fetched: int
    pages_truncated: bool
    detail_truncated: bool
    company_error: SmartRecruitersCompanyError | None
    job_errors: list[SmartRecruitersJobError]


class SmartRecruitersTargetWatcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "job-vacancy-analyzer/0.1",
        page_limit: int = _DEFAULT_PAGE_LIMIT,
        max_pages: int = _DEFAULT_MAX_PAGES,
        max_detail_fetches: int = _DEFAULT_MAX_DETAIL_FETCHES,
    ) -> None:
        if isinstance(page_limit, bool) or not isinstance(page_limit, int) or not (1 <= page_limit <= 100):
            raise ValueError(f"page_limit must be an integer between 1 and 100, got {page_limit!r}")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError(f"max_pages must be a positive integer, got {max_pages!r}")
        if (
            isinstance(max_detail_fetches, bool)
            or not isinstance(max_detail_fetches, int)
            or not (0 <= max_detail_fetches <= 100)
        ):
            raise ValueError(
                f"max_detail_fetches must be an integer between 0 and 100, got {max_detail_fetches!r}"
            )
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._max_detail_fetches = max_detail_fetches

    def watch(
        self,
        companies: TargetCompany | Sequence[TargetCompany],
    ) -> SmartRecruitersWatchResult:
        vacancies: list[NormalizedVacancy] = []
        errors: list[SmartRecruitersCompanyError] = []
        job_errors: list[SmartRecruitersJobError] = []
        raw_fetched = 0
        unique_count = 0
        duplicate_count = 0
        title_matched_count = 0
        detail_fetched_count = 0
        pages_fetched = 0
        pages_truncated = False
        detail_truncated = False
        seen_keys: set[tuple[str, str]] = set()
        fetched_outcomes: dict[str, _CompanyOutcome] = {}

        with _build_client(
            timeout_seconds=self._timeout_seconds,
            user_agent=self._user_agent,
        ) as client:
            for company in _as_company_list(companies):
                if not is_smartrecruiters_target(company):
                    continue
                try:
                    slug = resolve_smartrecruiters_slug(company)
                except ValueError as exc:
                    message = str(exc).strip() or f"SmartRecruiters watch failed for '{company.name}'."
                    logger.warning("SmartRecruiters watch failed for '%s': %s", company.name, message)
                    errors.append(
                        SmartRecruitersCompanyError(
                            company_name=company.name,
                            message=message,
                            error_type=type(exc).__name__,
                        )
                    )
                    continue

                slug_key = slug.lower()
                outcome = fetched_outcomes.get(slug_key)
                if outcome is None:
                    outcome = self._watch_company(company, slug=slug, client=client)
                    fetched_outcomes[slug_key] = outcome

                    raw_fetched += outcome.raw_fetched
                    unique_count += outcome.unique_count
                    duplicate_count += outcome.duplicate_count
                    title_matched_count += outcome.title_matched_count
                    detail_fetched_count += outcome.detail_fetched_count
                    pages_fetched += outcome.pages_fetched
                    pages_truncated = pages_truncated or outcome.pages_truncated
                    detail_truncated = detail_truncated or outcome.detail_truncated
                    job_errors.extend(outcome.job_errors)
                    if outcome.company_error is not None:
                        logger.warning(
                            "SmartRecruiters watch failed for '%s': %s",
                            company.name,
                            outcome.company_error.message,
                        )
                        errors.append(outcome.company_error)

                for vacancy in outcome.vacancies:
                    key = (vacancy.source, vacancy.external_id)
                    if key in seen_keys:
                        duplicate_count += 1
                        continue
                    seen_keys.add(key)
                    vacancies.append(vacancy)

        return SmartRecruitersWatchResult(
            vacancies=vacancies,
            errors=errors,
            job_errors=job_errors,
            raw_fetched=raw_fetched,
            unique_count=unique_count,
            duplicate_count=duplicate_count,
            title_matched_count=title_matched_count,
            detail_fetched_count=detail_fetched_count,
            pages_fetched=pages_fetched,
            pages_truncated=pages_truncated,
            detail_truncated=detail_truncated,
        )

    def _watch_company(
        self,
        company: TargetCompany,
        *,
        slug: str,
        client: httpx.Client,
    ) -> _CompanyOutcome:
        source = f"{_SOURCE_PREFIX}:{slug.lower()}"

        page_result = fetch_smartrecruiters_postings(
            slug,
            client=client,
            limit=self._page_limit,
            max_pages=self._max_pages,
        )
        raw_rows = page_result.rows
        raw_fetched = page_result.raw_fetched

        identified = 0
        unique_rows: dict[str, dict[str, object]] = {}
        for row in raw_rows:
            identity = smartrecruiters_posting_identity(row, slug=slug)
            if identity is None:
                continue
            identified += 1
            unique_rows.setdefault(identity, row)
        duplicate_count = identified - len(unique_rows)

        matches: list[tuple[str, dict[str, object]]] = []
        for identity, row in unique_rows.items():
            title = str(row.get("name") or row.get("title") or "").strip()
            if not title or not _title_passes_prefilter(title, company):
                continue
            matches.append((identity, row))

        detail_truncated = False
        candidates = matches
        if len(matches) > self._max_detail_fetches:
            candidates = matches[: self._max_detail_fetches]
            detail_truncated = True

        collected: list[NormalizedVacancy] = []
        job_errors: list[SmartRecruitersJobError] = []
        detail_fetched_count = 0
        for identity, row in candidates:
            try:
                detail = fetch_smartrecruiters_posting_detail(slug, identity, client=client)
            except SmartRecruitersCollectionError as exc:
                job_errors.append(
                    SmartRecruitersJobError(
                        company_name=company.name,
                        posting_id=identity,
                        message=str(exc).strip()
                        or f"SmartRecruiters detail fetch failed for posting '{identity}'.",
                        endpoint=exc.endpoint,
                        status_code=exc.status_code,
                        response_snippet=exc.response_snippet,
                        error_type=exc.error_type,
                    )
                )
                continue
            detail_fetched_count += 1
            vacancy = smartrecruiters_detail_to_normalized(
                detail,
                fallback_row=row,
                identity=identity,
                slug=slug,
                source=source,
                company=company.name,
            )
            if vacancy is None:
                job_errors.append(
                    SmartRecruitersJobError(
                        company_name=company.name,
                        posting_id=identity,
                        message=(
                            f"SmartRecruiters posting '{identity}' detail was inactive, "
                            "malformed, or did not match the requested identity."
                        ),
                        endpoint=smartrecruiters_posting_endpoint(slug, identity),
                        error_type="InvalidPostingDetail",
                    )
                )
                continue
            if not passes_role_prefilter(vacancy, company):
                continue
            collected.append(vacancy)

        company_error: SmartRecruitersCompanyError | None = None
        if page_result.error is not None:
            exc = page_result.error
            message = str(exc).strip() or f"SmartRecruiters watch failed for '{company.name}'."
            company_error = SmartRecruitersCompanyError(
                company_name=company.name,
                message=message,
                slug=exc.slug,
                endpoint=exc.endpoint,
                status_code=exc.status_code,
                response_snippet=exc.response_snippet,
                error_type=exc.error_type,
                offset=exc.offset,
            )

        return _CompanyOutcome(
            vacancies=collected,
            raw_fetched=raw_fetched,
            unique_count=len(unique_rows),
            duplicate_count=duplicate_count,
            title_matched_count=len(matches),
            detail_fetched_count=detail_fetched_count,
            pages_fetched=page_result.pages_fetched,
            pages_truncated=page_result.truncated,
            detail_truncated=detail_truncated,
            company_error=company_error,
            job_errors=job_errors,
        )


def is_smartrecruiters_target(company: TargetCompany) -> bool:
    watcher_type = company.watcher_type.strip().lower()
    ats = (company.ats or "").strip().lower()
    return watcher_type == "smartrecruiters" or ats == "smartrecruiters"


def smartrecruiters_postings_endpoint(slug: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"


def smartrecruiters_posting_endpoint(slug: str, posting_id: str) -> str:
    return f"{smartrecruiters_postings_endpoint(slug)}/{posting_id}"


def is_safe_posting_identity(value: str) -> bool:
    return bool(_SAFE_POSTING_IDENTITY_RE.match(value))


_CAREERS_HOSTS = {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"}
_API_HOST = "api.smartrecruiters.com"


def _slug_from_careers_path(parts: list[str]) -> str | None:
    if parts and _SAFE_BOARD_IDENTIFIER_RE.match(parts[0]):
        return parts[0]
    return None


def _slug_from_api_path(parts: list[str]) -> str | None:
    if len(parts) < 4 or parts[0] != "v1" or parts[1] != "companies" or parts[3] != "postings":
        return None
    slug = parts[2]
    return slug if _SAFE_BOARD_IDENTIFIER_RE.match(slug) else None


def resolve_smartrecruiters_slug(company: TargetCompany) -> str:
    for raw in (company.job_board_url, company.career_url):
        if not raw:
            continue
        stripped = raw.strip()
        if "://" in stripped:
            try:
                parsed = urlparse(stripped)
            except ValueError:
                continue
            if parsed.scheme.lower() not in ("http", "https"):
                continue
            if "@" in parsed.netloc:
                continue
            host = (parsed.hostname or "").lower()
            parts = [part for part in parsed.path.split("/") if part]
            if host in _CAREERS_HOSTS:
                slug = _slug_from_careers_path(parts)
            elif host == _API_HOST:
                slug = _slug_from_api_path(parts)
            else:
                slug = None
            if slug:
                return slug
            continue
        candidate = stripped.strip("/")
        if _SAFE_BOARD_IDENTIFIER_RE.match(candidate):
            return candidate
    raise ValueError(f"Cannot resolve SmartRecruiters company slug for: {company.name}")


def _page_signature(rows: list[object]) -> tuple[str, ...]:
    signature: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            signature.append(str(row.get("id") or row.get("uuid") or ""))
        else:
            signature.append("")
    return tuple(signature)


def fetch_smartrecruiters_postings(
    slug: str,
    *,
    client: httpx.Client,
    limit: int = _DEFAULT_PAGE_LIMIT,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> SmartRecruitersPageFetchResult:
    page_limit = max(1, min(int(limit), _DEFAULT_PAGE_LIMIT))
    rows: list[dict[str, object]] = []
    raw_fetched = 0
    pages_fetched = 0
    total_found: int | None = None
    error: SmartRecruitersCollectionError | None = None
    truncated = False
    offset = 0
    seen_signatures: set[tuple[str, ...]] = set()

    endpoint = smartrecruiters_postings_endpoint(slug)
    while pages_fetched < max_pages:
        response: httpx.Response | None = None
        try:
            response = client.get(endpoint, params={"limit": page_limit, "offset": offset})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            error = _request_error(slug, endpoint, exc, response=exc.response, offset=offset)
            break
        except httpx.HTTPError as exc:
            error = _request_error(slug, endpoint, exc, response=response, offset=offset)
            break
        except ValueError as exc:
            error = _request_error(slug, endpoint, exc, response=response, offset=offset)
            break

        if not isinstance(payload, dict):
            error = SmartRecruitersCollectionError(
                f"SmartRecruiters company '{slug}' returned a malformed list payload at offset {offset}.",
                slug=slug,
                endpoint=endpoint,
                error_type="MalformedPayload",
                offset=offset,
            )
            break

        page_rows = payload.get("content")
        if not isinstance(page_rows, list):
            error = SmartRecruitersCollectionError(
                f"SmartRecruiters company '{slug}' returned a malformed page payload at offset {offset}.",
                slug=slug,
                endpoint=endpoint,
                error_type="MalformedPayload",
                offset=offset,
            )
            break

        pages_fetched += 1
        raw_fetched += len(page_rows)
        raw_total = payload.get("totalFound")
        if isinstance(raw_total, int):
            total_found = raw_total

        if not page_rows:
            break

        signature = _page_signature(page_rows)
        if signature in seen_signatures:
            error = SmartRecruitersCollectionError(
                f"SmartRecruiters company '{slug}' returned a repeated page at offset {offset}; stopping pagination.",
                slug=slug,
                endpoint=endpoint,
                error_type="RepeatedPage",
                offset=offset,
            )
            break
        seen_signatures.add(signature)

        rows.extend(item for item in page_rows if isinstance(item, dict))
        page_size = len(page_rows)
        offset += page_size

        if total_found is not None:
            if offset >= total_found:
                break
        else:
            reported_limit = payload.get("limit")
            # Only a server-reported `limit` is trustworthy here. Falling
            # back to the *requested* page_limit would treat any page
            # shorter than what we asked for as proof of completion -- but a
            # server that silently caps page size below our request (without
            # reporting totalFound or its own limit) can return a short page
            # with more data still to come. With no trustworthy signal, keep
            # paging; only an empty page (handled above), a repeated page, or
            # exhausting max_pages can honestly end this.
            if isinstance(reported_limit, int) and reported_limit > 0 and page_size < reported_limit:
                break
    else:
        # The loop exhausted max_pages without a natural completion break
        # above, so we cannot claim the full result set was collected.
        truncated = True

    return SmartRecruitersPageFetchResult(
        rows=rows,
        raw_fetched=raw_fetched,
        pages_fetched=pages_fetched,
        total_found=total_found,
        truncated=truncated,
        error=error,
    )


def _parse_ref_posting_id(ref: str, *, slug: str) -> str | None:
    try:
        parsed = urlparse(ref.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if parsed.netloc.lower() != "api.smartrecruiters.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 5:
        return None
    if parts[0] != "v1" or parts[1] != "companies" or parts[3] != "postings":
        return None
    if parts[2].lower() != slug.lower():
        return None
    candidate = parts[4]
    return candidate if is_safe_posting_identity(candidate) else None


def smartrecruiters_posting_identity(row: object, *, slug: str) -> str | None:
    if not isinstance(row, dict):
        return None
    posting_id = str(row.get("id") or "").strip()
    if posting_id and is_safe_posting_identity(posting_id):
        return posting_id
    uuid_value = str(row.get("uuid") or "").strip()
    if uuid_value and is_safe_posting_identity(uuid_value):
        return uuid_value
    ref = str(row.get("ref") or "").strip()
    if ref:
        parsed_id = _parse_ref_posting_id(ref, slug=slug)
        if parsed_id:
            return parsed_id
    return None


def fetch_smartrecruiters_posting_detail(
    slug: str,
    posting_id: str,
    *,
    client: httpx.Client,
) -> dict[str, object]:
    endpoint = smartrecruiters_posting_endpoint(slug, posting_id)
    response: httpx.Response | None = None
    try:
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise _request_error(slug, endpoint, exc, response=exc.response, posting_id=posting_id) from exc
    except httpx.HTTPError as exc:
        raise _request_error(slug, endpoint, exc, response=response, posting_id=posting_id) from exc
    except ValueError as exc:
        raise _request_error(slug, endpoint, exc, response=response, posting_id=posting_id) from exc

    if not isinstance(payload, dict):
        raise SmartRecruitersCollectionError(
            f"SmartRecruiters posting '{posting_id}' returned a malformed detail payload.",
            slug=slug,
            endpoint=endpoint,
            error_type="MalformedPayload",
            posting_id=posting_id,
        )
    return payload


def _title_passes_prefilter(title: str, company: TargetCompany) -> bool:
    placeholder = NormalizedVacancy(
        source="",
        external_id="",
        title=title,
        company=None,
        location=None,
        employment=None,
        description="",
        url="https://jobs.smartrecruiters.com/placeholder",
        published_at=None,
    )
    return passes_role_prefilter(placeholder, company)


_PUBLIC_POSTING_HOST = "jobs.smartrecruiters.com"


def _canonical_smartrecruiters_url(posting_url: object, *, slug: str, posting_id: str) -> str:
    fallback = f"https://{_PUBLIC_POSTING_HOST}/{slug}/{posting_id}"
    candidate = str(posting_url or "").strip()
    if not candidate:
        return fallback
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return fallback
    if parsed.scheme.lower() not in ("http", "https"):
        return fallback
    if (parsed.hostname or "").lower() != _PUBLIC_POSTING_HOST:
        return fallback
    parts = [part for part in parsed.path.split("/") if part]
    # Only a bare "/{board}/{posting-id}[-slug]" public posting path is
    # trusted -- this rejects api postings URLs, /apply and referral paths,
    # and strips any tracking query string/fragment from the output.
    if len(parts) != 2:
        return fallback
    board_part, posting_part = parts
    if board_part.lower() != slug.lower():
        return fallback
    if posting_part != posting_id and not posting_part.startswith(f"{posting_id}-"):
        return fallback
    return f"https://{_PUBLIC_POSTING_HOST}/{board_part}/{posting_part}"


def smartrecruiters_detail_to_normalized(
    detail: object,
    *,
    fallback_row: dict[str, object],
    identity: str,
    slug: str,
    source: str,
    company: str | None = None,
) -> NormalizedVacancy | None:
    if not isinstance(detail, dict):
        return None
    if detail.get("active") is False:
        return None

    detail_id = str(detail.get("id") or "").strip()
    detail_uuid = str(detail.get("uuid") or "").strip()
    if not detail_id and not detail_uuid:
        return None
    if identity not in (detail_id, detail_uuid):
        return None

    board = detail.get("company")
    if isinstance(board, dict):
        board_identifier = str(board.get("identifier") or "").strip()
        if board_identifier and board_identifier.lower() != slug.lower():
            return None

    title = str(detail.get("name") or detail.get("title") or "").strip()
    if not title:
        title = str(fallback_row.get("name") or fallback_row.get("title") or "").strip()
    if not title:
        return None

    sections = _job_ad_sections(detail.get("jobAd"))
    if not _has_role_content(sections):
        return None
    description = "\n".join(sections.values())

    # Prefer the detail's own safe numeric id as external_id so output stays
    # stable even when the list row only exposed a UUID alias for `identity`.
    external_id = detail_id if detail_id and is_safe_posting_identity(detail_id) else identity

    url = _canonical_smartrecruiters_url(detail.get("postingUrl"), slug=slug, posting_id=external_id)

    location = _location_name(detail.get("location")) or _location_name(fallback_row.get("location"))
    employment = None
    type_of_employment = detail.get("typeOfEmployment")
    if isinstance(type_of_employment, dict):
        employment = str(type_of_employment.get("label") or "").strip() or None
    published = str(detail.get("releasedDate") or fallback_row.get("releasedDate") or "") or None

    return NormalizedVacancy(
        source=source,
        external_id=external_id,
        title=title,
        company=company.strip() if isinstance(company, str) and company.strip() else None,
        location=location,
        employment=employment,
        description=description,
        url=url,
        published_at=published,
        content_completeness="FULL",
    )


def _location_name(location: object) -> str | None:
    if not isinstance(location, dict):
        return None
    full = str(location.get("fullLocation") or "").strip()
    base = full or ", ".join(
        part
        for part in (
            str(location.get("city") or "").strip(),
            str(location.get("region") or "").strip(),
            str(location.get("country") or "").strip(),
        )
        if part
    )
    if location.get("remote") is True:
        tag = "Remote"
    elif location.get("hybrid") is True:
        tag = "Hybrid"
    else:
        tag = None
    if tag is None:
        return base or None
    if not base:
        return tag
    if tag.lower() not in base.lower():
        return f"{base} ({tag})"
    return base


_ROLE_CONTENT_SECTION_KEYS = ("jobDescription", "qualifications")


def _job_ad_sections(job_ad: object) -> dict[str, str]:
    if not isinstance(job_ad, dict):
        return {}
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return {}
    texts: dict[str, str] = {}
    for key, section in sections.items():
        if not isinstance(section, dict):
            continue
        text = clean_html_to_text(str(section.get("text") or "")).strip()
        if text:
            texts[str(key)] = text
    return texts


def _has_role_content(sections: dict[str, str]) -> bool:
    # A generic companyDescription section alone is not role content; require
    # an actual jobDescription or qualifications section to be present.
    return any(sections.get(key) for key in _ROLE_CONTENT_SECTION_KEYS)


def _build_client(*, timeout_seconds: float, user_agent: str) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=timeout_seconds, write=10.0, pool=5.0),
        headers={"User-Agent": user_agent},
    )


def _request_error(
    slug: str,
    endpoint: str,
    exc: Exception,
    *,
    response: httpx.Response | None,
    offset: int | None = None,
    posting_id: str | None = None,
) -> SmartRecruitersCollectionError:
    status_code = getattr(response, "status_code", None)
    error_type = type(exc).__name__
    status_code = status_code if isinstance(status_code, int) else None
    if posting_id:
        if status_code is not None:
            message = f"SmartRecruiters posting '{posting_id}' request failed ({status_code})."
        else:
            message = f"SmartRecruiters posting '{posting_id}' request failed ({error_type})."
    elif status_code is not None:
        message = f"SmartRecruiters company '{slug}' request failed ({status_code})."
    else:
        message = f"SmartRecruiters company '{slug}' request failed ({error_type})."
    snippet = None
    if response is not None:
        raw_text = getattr(response, "text", "") or ""
        snippet = " ".join(raw_text.split())
        if snippet and len(snippet) > _RESPONSE_SNIPPET_LIMIT:
            snippet = snippet[:_RESPONSE_SNIPPET_LIMIT] + "..."
        if not snippet:
            snippet = None
    return SmartRecruitersCollectionError(
        message,
        slug=slug,
        endpoint=endpoint,
        status_code=status_code,
        response_snippet=snippet,
        error_type=error_type,
        offset=offset,
        posting_id=posting_id,
    )


def _as_company_list(
    companies: TargetCompany | Sequence[TargetCompany],
) -> list[TargetCompany]:
    if isinstance(companies, TargetCompany):
        return [companies]
    return list(companies)
