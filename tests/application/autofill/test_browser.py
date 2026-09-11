from __future__ import annotations

from pathlib import Path

import pytest
from app.application.autofill.browser import (
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
