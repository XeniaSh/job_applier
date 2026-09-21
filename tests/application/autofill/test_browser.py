from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill.browser import (
    BrowserNavigationError,
    BrowserSession,
    BrowserSetupError,
    chromium_executable_available,
    complete_browser_handoff,
)

FIXTURE = Path("tests/fixtures/autofill/hello.html")

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(),
    reason="Playwright Chromium is not installed",
)


def test_open_local_html_and_close() -> None:
    session = BrowserSession(headed=False, keep_open=False)
    try:
        page = session.open_html_file(FIXTURE)
        assert "Fixture page" in page.inner_text("h1")
    finally:
        session.close()
    with pytest.raises(BrowserSetupError, match="not open"):
        _ = session.page


def test_keep_open_handoff_closes_after_wait() -> None:
    session = BrowserSession(headed=False, keep_open=True)
    waited: list[bool] = []

    def _wait() -> None:
        waited.append(True)

    try:
        session.open_html_file(FIXTURE)
        complete_browser_handoff(session, wait=_wait)
    except Exception:
        session.close()
        raise

    assert waited == [True]
    with pytest.raises(BrowserSetupError, match="not open"):
        _ = session.page


class _TimeoutPage:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.goto_calls: list[str] = []

    def goto(self, url: str, wait_until: str) -> None:
        _ = wait_until
        self.goto_calls.append(url)
        raise self._exc


def test_open_timeout_recovers_when_page_is_usable() -> None:
    session = BrowserSession(headed=False)
    fake_page = _TimeoutPage(PlaywrightTimeoutError("Timeout 30000ms exceeded."))
    session._page = fake_page

    seen: list[object] = []
    page = session.open("https://example.test/apply", is_usable=lambda p: (seen.append(p), True)[1])

    assert page is fake_page
    assert fake_page.goto_calls == ["https://example.test/apply"]
    assert seen == [fake_page]


def test_open_timeout_fails_when_page_is_not_usable() -> None:
    session = BrowserSession(headed=False)
    fake_page = _TimeoutPage(PlaywrightTimeoutError("Timeout 30000ms exceeded."))
    session._page = fake_page

    with pytest.raises(BrowserNavigationError, match="timed out"):
        session.open("https://example.test/apply", is_usable=lambda p: False)


def test_open_timeout_fails_closed_without_usability_check() -> None:
    session = BrowserSession(headed=False)
    session._page = _TimeoutPage(PlaywrightTimeoutError("Timeout 30000ms exceeded."))

    with pytest.raises(BrowserNavigationError, match="timed out"):
        session.open("https://example.test/apply")


def test_open_non_timeout_navigation_error_remains_fatal() -> None:
    session = BrowserSession(headed=False)
    session._page = _TimeoutPage(PlaywrightError("net::ERR_NAME_NOT_RESOLVED"))

    with pytest.raises(PlaywrightError, match="ERR_NAME_NOT_RESOLVED"):
        session.open("https://example.test/apply", is_usable=lambda p: True)


def test_missing_binaries_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.sync_api import Error as PlaywrightError

    from app.application.autofill import browser as browser_module

    class _FakeChromium:
        def launch(self, headless: bool) -> object:
            _ = headless
            raise PlaywrightError("Executable doesn't exist at /missing/chromium")

    class _FakePlaywright:
        chromium = _FakeChromium()

        def stop(self) -> None:
            return None

    class _FakeSync:
        def start(self) -> _FakePlaywright:
            return _FakePlaywright()

    monkeypatch.setattr(browser_module, "sync_playwright", lambda: _FakeSync())
    session = BrowserSession(headed=False)
    with pytest.raises(BrowserSetupError, match="playwright install chromium"):
        session.start()
