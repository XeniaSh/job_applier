from __future__ import annotations


def test_playwright_importable() -> None:
    import playwright
    from playwright.sync_api import sync_playwright

    assert playwright.__name__ == "playwright"
    assert callable(sync_playwright)
