"""Synthetic (no-Chromium) tests for Greenhouse `prepare_page` stage
diagnostics on recovered target-company vacancies whose canonical
job-boards.greenhouse.io URL redirected to a custom careers domain.

These use fake Page/Locator doubles instead of a real browser so they run
even where Playwright's Chromium binary is not installed. They assert on
`caplog` records only -- never on selectors, page content, or field values --
matching the diagnostics contract: hosts, booleans, counts, and stable
reason tokens only. Modeled on `test_lever_prepare_diagnostics.py`.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill.greenhouse import _FORM_READY_SELECTOR, GreenhouseAdapter

LOGGER_NAME = "app.application.autofill.greenhouse"


class FakeCountLocator:
    """Minimal double for pure existence/count checks -- the selectors
    `is_greenhouse_application_page` probes, and the form-ready selector.
    """

    def __init__(self, count: int = 0) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class FakeAnchor:
    def __init__(self, href: str, *, visible: bool = True, click_error: Exception | None = None) -> None:
        self._href = href
        self._visible = visible
        self._click_error = click_error
        self.click_calls = 0

    def evaluate(self, script: str) -> str:
        return self._href

    def is_visible(self) -> bool:
        return self._visible

    def click(self, timeout: int | None = None) -> None:
        self.click_calls += 1
        if self._click_error is not None:
            raise self._click_error


class FakeAnchorGroup:
    def __init__(self, anchors: list[FakeAnchor]) -> None:
        self._anchors = anchors

    def count(self) -> int:
        return len(self._anchors)

    def nth(self, index: int) -> FakeAnchor:
        return self._anchors[index]


class FakePage:
    """Minimal double covering only what GreenhouseAdapter.prepare_page touches."""

    def __init__(
        self,
        *,
        url: str,
        locators: dict[str, object] | None = None,
        form_ready_error: Exception | None = None,
        navigate_to: str | None = None,
    ) -> None:
        self.url = url
        self._locators = locators or {}
        self._form_ready_error = form_ready_error
        self._navigate_to = navigate_to
        self.wait_for_selector_calls: list[str] = []

    def locator(self, selector: str) -> object:
        return self._locators.get(selector, FakeCountLocator(0))

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


def _application_form_page(url: str = "file:///tmp/greenhouse_application.html") -> FakePage:
    return FakePage(
        url=url,
        locators={
            "form[data-ats='greenhouse']": FakeCountLocator(1),
            _FORM_READY_SELECTOR: FakeCountLocator(1),
        },
    )


def _custom_domain_detail_page(
    *,
    apply_hrefs: list[str] | None = None,
    click_error: Exception | None = None,
    form_ready_error: Exception | None = None,
    navigate_to: str | None = "https://boards.greenhouse.io/acme/jobs/12345",
    url: str = "https://careers.acme.com/jobs/senior-engineer",
) -> FakePage:
    hrefs = (
        apply_hrefs
        if apply_hrefs is not None
        else ["https://boards.greenhouse.io/acme/jobs/12345/apply"]
    )
    anchors = [FakeAnchor(href, click_error=click_error if index == 0 else None) for index, href in enumerate(hrefs)]
    return FakePage(
        url=url,
        locators={
            "a[href]": FakeAnchorGroup(anchors),
            "form": FakeCountLocator(1),
        },
        form_ready_error=form_ready_error,
        navigate_to=navigate_to,
    )


def test_prepare_page_logs_detail_detection_for_already_recognized_form(caplog) -> None:
    page = _application_form_page()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    detail_log = next(m for m in messages if "stage=detail_detection" in m)
    assert "already_form=True" in detail_log
    assert not any("stage=apply_action" in m for m in messages)
    assert not any("stage=navigation" in m for m in messages)
    recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=True" in recognition_log
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_prepare_page_clicks_unique_apply_link_and_reaches_form(caplog) -> None:
    page = _custom_domain_detail_page()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    detail_log = next(m for m in messages if "stage=detail_detection" in m)
    assert "already_form=False" in detail_log

    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=1" in apply_count_log

    click_log = next(m for m in messages if "click_attempted" in m and "stage=apply_action" in m)
    assert "click_attempted=True" in click_log
    assert "click_raised=None" in click_log

    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "url_changed=True" in nav_log
    assert "form_ready_selector_found=True" in nav_log

    recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=True" in recognition_log
    assert page.url == "https://boards.greenhouse.io/acme/jobs/12345"
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_prepare_page_no_unique_action_fails_closed_when_no_apply_link(caplog) -> None:
    page = _custom_domain_detail_page(apply_hrefs=[], navigate_to=None)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=0" in apply_count_log
    assert not any("stage=apply_action" in m and "click_attempted" in m for m in messages)

    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "click_attempted=False" in nav_log
    assert "click_raised=None" in nav_log
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=False" in nav_log

    recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=False" in recognition_log
    assert page.url == "https://careers.acme.com/jobs/senior-engineer"


def test_prepare_page_no_unique_action_fails_closed_when_ambiguous(caplog) -> None:
    page = _custom_domain_detail_page(
        apply_hrefs=[
            "https://boards.greenhouse.io/acme/jobs/12345/apply",
            "https://boards.greenhouse.io/acme/jobs/99999/apply",
        ],
        navigate_to=None,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=2" in apply_count_log
    assert not any("stage=apply_action" in m and "click_attempted" in m for m in messages)

    recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=False" in recognition_log


def test_prepare_page_logs_click_exception_and_fails_closed_on_overlay(caplog) -> None:
    page = _custom_domain_detail_page(
        click_error=PlaywrightError("element intercepts pointer events"),
        navigate_to=None,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    click_log = next(m for m in messages if "click_attempted" in m and "stage=apply_action" in m)
    assert "click_attempted=True" in click_log
    assert "click_raised=Error" in click_log

    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=False" in nav_log

    recognition_log = next(m for m in messages if "stage=form_recognition" in m)
    assert "result=False" in recognition_log


def test_prepare_page_logs_form_ready_not_found_after_click(caplog) -> None:
    page = _custom_domain_detail_page(
        form_ready_error=PlaywrightTimeoutError("timeout"),
        navigate_to="https://boards.greenhouse.io/acme/jobs/12345/apply",
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    nav_log = next(m for m in messages if "stage=navigation" in m)
    assert "form_ready_selector_found=False" in nav_log
    assert "url_changed=True" in nav_log


def test_ignores_apply_marker_anchor_that_is_not_visible(caplog) -> None:
    hidden = FakeAnchor("https://boards.greenhouse.io/acme/jobs/12345/apply", visible=False)
    page = FakePage(
        url="https://careers.acme.com/jobs/senior-engineer",
        locators={"a[href]": FakeAnchorGroup([hidden]), "form": FakeCountLocator(1)},
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=0" in apply_count_log
    assert hidden.click_calls == 0


def test_ignores_anchor_without_greenhouse_apply_marker(caplog) -> None:
    unrelated = FakeAnchor("https://careers.acme.com/about-us")
    page = FakePage(
        url="https://careers.acme.com/jobs/senior-engineer",
        locators={"a[href]": FakeAnchorGroup([unrelated]), "form": FakeCountLocator(1)},
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=0" in apply_count_log
    assert unrelated.click_calls == 0


def test_ignores_plain_greenhouse_job_link_without_apply_marker(caplog) -> None:
    unrelated = FakeAnchor("https://boards.greenhouse.io/acme/jobs/12345")
    page = FakePage(
        url="https://careers.acme.com/jobs/senior-engineer",
        locators={"a[href]": FakeAnchorGroup([unrelated]), "form": FakeCountLocator(1)},
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    messages = [r.getMessage() for r in caplog.records]
    apply_count_log = next(m for m in messages if "apply_locator_count" in m)
    assert "apply_locator_count=0" in apply_count_log
    assert unrelated.click_calls == 0


def test_navigation_stage_excludes_query_and_fragment_from_resulting_url(caplog) -> None:
    page = _custom_domain_detail_page(
        apply_hrefs=["https://boards.greenhouse.io/acme/jobs/12345/apply?utm_source=x&token=secret#section"],
        navigate_to="https://boards.greenhouse.io/acme/jobs/12345?utm_source=x&token=secret#section",
        url="https://careers.acme.com/jobs/senior-engineer?ref=email&session=abc123#top",
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        GreenhouseAdapter().prepare_page(page)

    nav_log = next(r.getMessage() for r in caplog.records if "stage=navigation" in r.getMessage())
    assert "utm_source" not in nav_log
    assert "token" not in nav_log
    assert "secret" not in nav_log
    assert "section" not in nav_log
    assert "ref=email" not in nav_log
    assert "session" not in nav_log
    assert "boards.greenhouse.io/acme/jobs/12345" in nav_log
    assert "careers.acme.com/jobs/senior-engineer" in nav_log

    all_messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "utm_source" not in all_messages
    assert "secret" not in all_messages
    assert "session=abc123" not in all_messages
