from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

_INSTALL_HINT = (
    "Playwright browser binaries are missing. Install Chromium with: "
    "uv run playwright install chromium"
)


class BrowserSetupError(Exception):
    """Raised when the headed/headless browser cannot be started."""


class BrowserSession:
    """Playwright Chromium session. Close is explicit; there is no submit helper."""

    def __init__(self, *, headed: bool = True, keep_open: bool = False) -> None:
        self.headed = headed
        self.keep_open = keep_open
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> None:
        if self._page is not None:
            return
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=not self.headed)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        except PlaywrightError as exc:
            self.close()
            raise BrowserSetupError(_setup_message(exc)) from exc

    def open(self, url: str) -> Page:
        self.start()
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page

    def open_html_file(self, path: str | Path) -> Page:
        file_url = Path(path).resolve().as_uri()
        return self.open(file_url)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserSetupError("Browser session is not open.")
        return self._page

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None


def wait_for_manual_review(prompt: str = "Press Enter when you have finished reviewing the form...") -> None:
    input(prompt)


def complete_browser_handoff(
    session: BrowserSession,
    *,
    wait: Callable[[], None] = wait_for_manual_review,
) -> None:
    """Keep the browser open until wait() returns, then close it."""
    try:
        if session.keep_open:
            wait()
    finally:
        session.close()


def chromium_executable_available() -> bool:
    try:
        playwright = sync_playwright().start()
        try:
            return Path(playwright.chromium.executable_path).exists()
        finally:
            playwright.stop()
    except Exception:
        return False


def _setup_message(exc: PlaywrightError) -> str:
    text = str(exc)
    lowered = text.lower()
    if "executable doesn't exist" in lowered or "playwright install" in lowered:
        return _INSTALL_HINT
    return f"Failed to start Playwright Chromium: {exc}"
