"""Synthetic (no-Chromium) tests for Lever `prepare_page` stage diagnostics.

These use fake Page/Locator doubles instead of a real browser so they run
even where Playwright's Chromium binary is not installed. They assert on
`caplog` records only -- never on selectors, page content, or field values --
matching the diagnostics contract: hosts, booleans, counts, and stable
reason tokens only.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill.lever import (
    _FORM_READY_SELECTOR,
    _JOB_DETAIL_APPLY_SELECTOR,
    LeverAdapter,
)

LOGGER_NAME = "app.application.autofill.lever"


class FakeLocator:
    def __init__(self, count: int = 0, *, click_error: Exception | None = None) -> None:
        self._count = count
        self._click_error = click_error
        self.click_calls = 0

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "FakeLocator":
        return self

    def click(self, timeout: int | None = None) -> None:
        self.click_calls += 1
        if self._click_error is not None:
            raise self._click_error


class FakePage:
    """Minimal double covering only what LeverAdapter.prepare_page touches."""

    def __init__(
        self,
        *,
        url: str,
        locators: dict[str, FakeLocator] | None = None,
        form_ready_error: Exception | None = None,
        navigate_to: str | None = None,
    ) -> None:
        self.url = url
        self._locators = locators or {}
        self._form_ready_error = form_ready_error
        self._navigate_to = navigate_to
        self.wait_for_selector_calls: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        return self._locators.get(selector, FakeLocator(0))

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        self.wait_for_selector_calls.append(selector)
        if selector == _FORM_READY_SELECTOR and self._navigate_to is not None:
            self.url = self._navigate_to
        if self._form_ready_error is not None:
            raise self._form_ready_error

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def title(self) -> str:
        return ""


def _application_form_page(url: str = "file:///tmp/lever_application.html") -> FakePage:
    return FakePage(
        url=url,
        locators={"#application-form, form.application-form": FakeLocator(1)},
    )


def _job_detail_page(
    *,
    apply_count: int = 1,
    click_error: Exception | None = None,
    form_ready_error: Exception | None = None,
    navigate_to: str | None = "file:///tmp/lever_application.html",
) -> FakePage:
    return FakePage(
        url="file:///tmp/lever_job_detail.html",
        locators={_JOB_DETAIL_APPLY_SELECTOR: FakeLocator(apply_count, click_error=click_error)},
        form_ready_error=form_ready_error,
        navigate_to=navigate_to,
    )


def test_prepare_page_logs_detail_detection_for_application_page(caplog) -> None:
    page = _application_form_page()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        LeverAdapter().prepare_page(page)
    detail_logs = [r.getMessage() for r in caplog.records if "stage=detail_detection" in r.getMessage()]
    assert len(detail_logs) == 1
    assert "is_job_detail=False" in detail_logs[0]


def test_prepare_page_navigates_and_logs_all_stages(caplog) -> None:
    page = _job_detail_page()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        LeverAdapter().prepare_page(page)

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
    assert page.url == "file:///tmp/lever_application.html"


def test_prepare_page_no_apply_locator_skips_click(caplog) -> None:
    page = _job_detail_page(apply_count=0)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        LeverAdapter()._navigate_from_job_detail(page)

    messages = [r.getMessage() for r in caplog.records]
    assert any("apply_locator_count=0" in m for m in messages)
    assert not any("click_attempted" in m for m in messages)
    assert not any("stage=navigation" in m for m in messages)


def test_navigate_logs_click_exception_without_navigation_stage(caplog) -> None:
    page = _job_detail_page(click_error=PlaywrightError("boom"))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        LeverAdapter()._navigate_from_job_detail(page)

    messages = [r.getMessage() for r in caplog.records]
    click_log = next(m for m in messages if "click_attempted" in m)
    assert "click_raised=Error" in click_log
    assert not any("stage=navigation" in m for m in messages)


def test_navigate_logs_form_ready_not_found(caplog) -> None:
    page = _job_detail_page(
        form_ready_error=PlaywrightTimeoutError("timeout"),
        navigate_to="file:///tmp/lever_dead_end.html",
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        LeverAdapter()._navigate_from_job_detail(page)

    messages = [r.getMessage() for r in caplog.records]
    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=True" in nav_log


def test_recognize_logs_reason_for_application_form_selector(caplog) -> None:
    page = _application_form_page()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = LeverAdapter().recognize(page)

    assert result is True
    messages = [r.getMessage() for r in caplog.records]
    form_recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=True" in form_recognition_log
    assert "reason=application_form_selector" in form_recognition_log


def test_recognize_logs_no_match_reason_for_unrelated_page(caplog) -> None:
    page = FakePage(url="file:///tmp/unrelated.html")
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = LeverAdapter().recognize(page)

    assert result is False
    messages = [r.getMessage() for r in caplog.records]
    form_recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=False" in form_recognition_log
    assert "reason=no_match" in form_recognition_log
