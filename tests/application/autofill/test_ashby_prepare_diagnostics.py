"""Synthetic (no-Chromium) tests for Ashby `prepare_page` stage diagnostics.

Playwright Chromium cannot launch in this sandbox, so these use fake
Page/Locator/anchor doubles instead of a real browser -- mirroring
`test_lever_prepare_diagnostics.py`. Real Ashby `/application` pages are a
loading `#root` shell (`window.__appData`, no form elements) until React
hydrates; a GET of the page therefore never shows the strict application
panel immediately. These doubles model that: `panel_count` starts at 0 and
only flips to 1 once `wait_for_selector` is actually awaited (hydration) or
the one structurally-validated job-detail apply link is clicked. Assertions
are on `caplog` records, `page.url`, and `recognize()` only -- never on
selectors, DOM content, or field values -- matching the diagnostics
contract: hosts, booleans, counts, and stable reason tokens only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill.ashby import (
    _APPLICATION_PANEL_SELECTOR,
    _EMAIL_FIELD_SELECTOR,
    _GENERIC_FORM_CONTAINER_SELECTOR,
    _NAME_FIELD_SELECTOR,
    _SUBMIT_BUTTON_SELECTOR,
    AshbyAdapter,
    is_ashby_job_detail_page,
)

LOGGER_NAME = "app.application.autofill.ashby"

_REAL_JOB_DETAIL_URL = "https://jobs.ashbyhq.com/perk/5f6e7d8c-1234-5678-9abc-def012345678"
_REAL_APPLICATION_URL = f"{_REAL_JOB_DETAIL_URL}/application"


class FakeAnchor:
    def __init__(
        self,
        href: str,
        *,
        click_error: Exception | None = None,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self.href = href
        self._click_error = click_error
        self._on_click = on_click
        self.click_calls = 0

    def evaluate(self, script: str) -> str:
        return self.href

    def click(self, timeout: int | None = None) -> None:
        self.click_calls += 1
        if self._click_error is not None:
            raise self._click_error
        if self._on_click is not None:
            self._on_click()


class FakeAnchorGroup:
    def __init__(self, anchors: list[FakeAnchor]) -> None:
        self._anchors = anchors

    def count(self) -> int:
        return len(self._anchors)

    def nth(self, index: int) -> FakeAnchor:
        return self._anchors[index]


class PanelLocator:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    def count(self) -> int:
        return self._page.panel_count


class EmptyLocator:
    def count(self) -> int:
        return 0


class _FixedCountLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class FakePage:
    """Minimal double covering only what AshbyAdapter.prepare_page touches."""

    def __init__(
        self,
        *,
        url: str,
        panel_count: int = 0,
        anchors: list[FakeAnchor] | None = None,
        form_ready_error: Exception | None = None,
        hydrate_on_wait: bool = False,
        extra_locator_counts: dict[str, int] | None = None,
    ) -> None:
        self.url = url
        self.panel_count = panel_count
        self._anchors = anchors or []
        self._form_ready_error = form_ready_error
        self._hydrate_on_wait = hydrate_on_wait
        self._extra_locator_counts = extra_locator_counts or {}
        self.wait_for_selector_calls: list[str] = []

    def locator(self, selector: str):
        if selector == "a[href]":
            return FakeAnchorGroup(self._anchors)
        if selector == _APPLICATION_PANEL_SELECTOR:
            return PanelLocator(self)
        if selector in self._extra_locator_counts:
            return _FixedCountLocator(self._extra_locator_counts[selector])
        return EmptyLocator()

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        self.wait_for_selector_calls.append(selector)
        if selector != _APPLICATION_PANEL_SELECTOR:
            return
        if self._hydrate_on_wait:
            self.panel_count = 1
        if self.panel_count > 0:
            return
        raise self._form_ready_error or PlaywrightTimeoutError("timeout")

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def title(self) -> str:
        return ""


def _rendered_form_page(url: str = _REAL_APPLICATION_URL) -> FakePage:
    return FakePage(url=url, panel_count=1)


def _loading_shell_page(*, form_ready_error: Exception | None = None, hydrate_on_wait: bool = True) -> FakePage:
    # No anchors at all -- the real pre-hydration `#root` shell has none.
    return FakePage(
        url=_REAL_APPLICATION_URL,
        panel_count=0,
        form_ready_error=form_ready_error,
        hydrate_on_wait=hydrate_on_wait,
    )


def _job_detail_page(
    *,
    anchor_href: str = _REAL_APPLICATION_URL,
    click_error: Exception | None = None,
    hydrate_after_click: bool = True,
) -> FakePage:
    page = FakePage(url=_REAL_JOB_DETAIL_URL, panel_count=0)

    def _on_click() -> None:
        page.url = anchor_href
        if hydrate_after_click:
            page.panel_count = 1

    page._anchors = [FakeAnchor(anchor_href, click_error=click_error, on_click=_on_click)]
    return page


# -- is_ashby_job_detail_page -------------------------------------------------


def test_is_job_detail_true_for_same_posting_apply_link() -> None:
    page = _job_detail_page()
    assert is_ashby_job_detail_page(page) is True  # type: ignore[arg-type]


def test_is_job_detail_false_once_form_already_recognized() -> None:
    page = _rendered_form_page()
    assert is_ashby_job_detail_page(page) is False  # type: ignore[arg-type]


def test_is_job_detail_false_for_loading_shell_with_no_anchors_yet() -> None:
    page = _loading_shell_page()
    assert is_ashby_job_detail_page(page) is False  # type: ignore[arg-type]


def test_is_job_detail_false_for_wrong_job_apply_link() -> None:
    other_job_apply_url = "https://jobs.ashbyhq.com/perk/other-job-id/application"
    page = _job_detail_page(anchor_href=other_job_apply_url)
    assert is_ashby_job_detail_page(page) is False  # type: ignore[arg-type]


def test_is_job_detail_false_for_external_apply_link() -> None:
    page = _job_detail_page(anchor_href="https://evil.example.com/apply")
    assert is_ashby_job_detail_page(page) is False  # type: ignore[arg-type]


# -- prepare_page: already-rendered form -------------------------------------


def test_prepare_page_already_rendered_form_logs_detail_detection_false(caplog) -> None:
    page = _rendered_form_page()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    detail_logs = [r.getMessage() for r in caplog.records if "stage=detail_detection" in r.getMessage()]
    assert len(detail_logs) == 1
    assert "is_job_detail=False" in detail_logs[0]
    assert AshbyAdapter().recognize(page) is True  # type: ignore[arg-type]


# -- prepare_page: delayed loading shell -> form -----------------------------


def test_prepare_page_waits_out_hydration_delay_then_recognizes_form() -> None:
    page = _loading_shell_page()
    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert page.wait_for_selector_calls == [_APPLICATION_PANEL_SELECTOR]
    assert AshbyAdapter().recognize(page) is True  # type: ignore[arg-type]


def test_prepare_page_fails_closed_when_hydration_never_completes() -> None:
    page = _loading_shell_page(form_ready_error=PlaywrightTimeoutError("timeout"), hydrate_on_wait=False)

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]


# -- prepare_page: safe job-detail -> form navigation ------------------------


def test_prepare_page_navigates_from_job_detail_to_application_form(caplog) -> None:
    page = _job_detail_page()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    messages = [r.getMessage() for r in caplog.records]
    detail = next(m for m in messages if "stage=detail_detection" in m)
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    click_log = next(m for m in messages if "click_attempted" in m)
    nav_log = next(m for m in messages if "stage=navigation" in m)

    assert "is_job_detail=True" in detail
    assert "apply_locator_count=1" in apply_count_log
    assert "click_attempted=True" in click_log
    assert "click_raised=None" in click_log
    assert "url_changed=True" in nav_log
    assert "form_ready_selector_found=True" in nav_log
    assert page.url == _REAL_APPLICATION_URL
    assert AshbyAdapter().recognize(page) is True  # type: ignore[arg-type]
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_prepare_page_does_not_redundantly_click_when_already_on_application_form() -> None:
    page = _rendered_form_page()
    page._anchors = [FakeAnchor(_REAL_APPLICATION_URL)]

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert page._anchors[0].click_calls == 0


def test_navigate_logs_click_exception_and_fails_closed(caplog) -> None:
    page = _job_detail_page(click_error=PlaywrightError("boom"))
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter()._navigate_from_job_detail(page)  # type: ignore[arg-type]

    messages = [r.getMessage() for r in caplog.records]
    click_log = next(m for m in messages if "click_attempted" in m and "stage=apply_action" in m)
    assert "click_raised=Error" in click_log
    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "click_attempted=True" in nav_log
    assert "click_raised=Error" in nav_log
    assert "form_ready_selector_found=False" in nav_log


def test_navigate_logs_form_ready_not_found_after_click(caplog) -> None:
    page = _job_detail_page(hydrate_after_click=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter()._navigate_from_job_detail(page)  # type: ignore[arg-type]

    nav_log = next(r.getMessage() for r in caplog.records if "stage=navigation" in r.getMessage())
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=True" in nav_log


# -- prepare_page: wrong-job / external apply link is never clicked ---------


def test_prepare_page_ignores_wrong_job_apply_link_and_fails_closed(caplog) -> None:
    other_job_apply_url = "https://jobs.ashbyhq.com/perk/other-job-id/application"
    page = _job_detail_page(anchor_href=other_job_apply_url)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    detail_logs = [r.getMessage() for r in caplog.records if "stage=detail_detection" in r.getMessage()]
    assert "is_job_detail=False" in detail_logs[0]
    assert page._anchors[0].click_calls == 0
    assert page.url == _REAL_JOB_DETAIL_URL
    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]


def test_prepare_page_ignores_external_apply_link_and_fails_closed() -> None:
    page = _job_detail_page(anchor_href="https://evil.example.com/apply")

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert page._anchors[0].click_calls == 0
    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]


# -- prepare_page: unsupported page -------------------------------------


def test_prepare_page_unsupported_page_fails_closed() -> None:
    page = FakePage(url="https://example.com/unrelated", panel_count=0)

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]


def test_prepare_page_no_apply_locator_still_logs_navigation_stage(caplog) -> None:
    page = FakePage(url=_REAL_JOB_DETAIL_URL, panel_count=0)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter()._navigate_from_job_detail(page)  # type: ignore[arg-type]

    messages = [r.getMessage() for r in caplog.records]
    assert any("apply_locator_count=0" in m for m in messages)
    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "click_attempted=False" in nav_log
    assert "click_raised=None" in nav_log
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=False" in nav_log


def test_navigation_stage_excludes_query_and_fragment_from_resulting_url(caplog) -> None:
    tracked_url = f"{_REAL_APPLICATION_URL}?utm_source=x&token=secret#section"
    page = _job_detail_page(anchor_href=tracked_url, hydrate_after_click=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter()._navigate_from_job_detail(page)  # type: ignore[arg-type]

    nav_log = next(r.getMessage() for r in caplog.records if "stage=navigation" in r.getMessage())
    assert "utm_source" not in nav_log
    assert "secret" not in nav_log
    assert "section" not in nav_log


# -- prepare_page: single bounded wait, not cumulative -----------------------


def test_prepare_page_after_job_detail_click_waits_once_not_cumulatively() -> None:
    """A validated click into the form must be followed by exactly one
    bounded form-readiness wait (owned by `_navigate_from_job_detail`) --
    `prepare_page` must never wait a second time on top of it, which would
    let a single call block for the sum of both timeouts instead of one.
    """
    page = _job_detail_page(hydrate_after_click=False)

    AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    assert page.wait_for_selector_calls == [_APPLICATION_PANEL_SELECTOR]
    assert AshbyAdapter().recognize(page) is False  # type: ignore[arg-type]


# -- prepare_page: compact final page-state diagnostic on timeout -----------


def test_prepare_page_logs_final_page_state_for_pre_hydration_shell(caplog) -> None:
    page = _loading_shell_page(form_ready_error=PlaywrightTimeoutError("timeout"), hydrate_on_wait=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    state_log = next(r.getMessage() for r in caplog.records if "stage=final_page_state" in r.getMessage())
    assert "strict_panel=False" in state_log
    assert "generic_form_container=False" in state_log
    assert "name_field=False" in state_log
    assert "email_field=False" in state_log
    assert "submit_button=False" in state_log
    assert "tab_or_apply_action=False" in state_log
    assert "jobs.ashbyhq.com" in state_log


def test_prepare_page_logs_final_page_state_distinguishes_partial_render(caplog) -> None:
    """A rendered-but-structurally-different page (e.g. some pieces present,
    the strict panel still absent) must be distinguishable in the logs from
    the pre-hydration shell, where nothing is present yet.
    """
    tracked_url = f"{_REAL_APPLICATION_URL}?utm_source=x&token=secret#section"
    page = FakePage(
        url=tracked_url,
        panel_count=0,
        form_ready_error=PlaywrightTimeoutError("timeout"),
        extra_locator_counts={
            _GENERIC_FORM_CONTAINER_SELECTOR: 1,
            _NAME_FIELD_SELECTOR: 1,
            _EMAIL_FIELD_SELECTOR: 0,
            _SUBMIT_BUTTON_SELECTOR: 0,
        },
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        AshbyAdapter().prepare_page(page)  # type: ignore[arg-type]

    state_log = next(r.getMessage() for r in caplog.records if "stage=final_page_state" in r.getMessage())
    assert "strict_panel=False" in state_log
    assert "generic_form_container=True" in state_log
    assert "name_field=True" in state_log
    assert "email_field=False" in state_log
    assert "submit_button=False" in state_log
    assert "jobs.ashbyhq.com" in state_log
    assert "utm_source" not in state_log
    assert "secret" not in state_log
    assert "section" not in state_log
