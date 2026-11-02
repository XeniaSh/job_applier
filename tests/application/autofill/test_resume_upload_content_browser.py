"""Offline real-browser proof that the real Greenhouse/Lever/Ashby
`upload_resume` methods hand the browser the selected profile's exact PDF
*bytes*, not just a matching filename. All four profiles share one basename
in separate directories, so a filename-only check could not tell them apart.

A capture-phase `change` listener installed on the page reads each chosen
`File` before any widget script can drop the input (the Greenhouse modern
widget removes it on completion). Synthetic PDFs only; no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator

from app.application.autofill.ashby import AshbyAdapter
from app.application.autofill.browser import BrowserSession, chromium_executable_available
from app.application.autofill.greenhouse import GreenhouseAdapter
from app.application.autofill.lever import LeverAdapter
from app.application.autofill.fields import DiscoveredField
from tests.pdf_fixtures import build_labeled_valid_pdf

pytestmark = pytest.mark.skipif(
    not chromium_executable_available(), reason="Playwright Chromium is not installed"
)

VARIANT_IDS = ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")
BASENAME = "Synthetic_Person_Resume.pdf"
FIXTURES = Path("tests/fixtures/autofill")

# (adapter class, fixture, resume input id)
ATS_CASES = {
    "greenhouse": (GreenhouseAdapter, "greenhouse_resume_modern_widget.html", "resume"),
    "lever": (LeverAdapter, "lever_application.html", "resume-upload-input"),
    "ashby": (AshbyAdapter, "ashby_application.html", "_systemfield_resume"),
}

_CAPTURE_SCRIPT = """() => {
    window.__captured = [];
    document.addEventListener('change', (event) => {
        const file = event.target && event.target.files && event.target.files[0];
        if (!file) return;
        file.arrayBuffer().then((buffer) => {
            window.__captured.push({
                name: file.name,
                hex: Array.from(new Uint8Array(buffer))
                    .map((b) => b.toString(16).padStart(2, '0')).join(''),
            });
        });
    }, true);
}"""


def _sources(tmp_path: Path) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for variant in VARIANT_IDS:
        path = tmp_path / "resumes" / variant / BASENAME
        path.parent.mkdir(parents=True)
        path.write_bytes(build_labeled_valid_pdf(variant))
        sources[variant] = path
    assert len({path.read_bytes() for path in sources.values()}) == 4
    return sources


def _field(element_id: str) -> DiscoveredField:
    return DiscoveredField(label="Resume", field_type="file", required=True, element_id=element_id)


def _open(ats: str):
    adapter_cls, fixture, element_id = ATS_CASES[ats]
    session = BrowserSession(headed=False, keep_open=False)
    page = session.open_html_file(FIXTURES / fixture)
    page.evaluate(_CAPTURE_SCRIPT)
    return adapter_cls(), session, page, _field(element_id)


def _captured(page, count: int) -> list[dict]:
    page.wait_for_function("n => window.__captured.length >= n", arg=count, timeout=5_000)
    return page.evaluate("window.__captured")


@pytest.mark.parametrize("ats", sorted(ATS_CASES))
@pytest.mark.parametrize("variant", VARIANT_IDS)
def test_upload_resume_hands_browser_selected_profile_bytes(
    tmp_path: Path, ats: str, variant: str
) -> None:
    source = _sources(tmp_path)[variant]
    adapter, session, page, field = _open(ats)
    try:
        assert adapter.upload_resume(page, source, field) is True
        captured = _captured(page, 1)[-1]
    finally:
        session.close()

    assert captured["name"] == BASENAME
    assert bytes.fromhex(captured["hex"]) == source.read_bytes()


@pytest.mark.parametrize("ats", ["lever", "ashby"])
def test_sequential_same_name_replacements_carry_each_profiles_bytes(
    tmp_path: Path, ats: str
) -> None:
    sources = _sources(tmp_path)
    adapter, session, page, field = _open(ats)
    try:
        for index, variant in enumerate(VARIANT_IDS, start=1):
            assert adapter.upload_resume(page, sources[variant], field) is True
            captured = _captured(page, index)[-1]
            assert captured["name"] == BASENAME
            assert bytes.fromhex(captured["hex"]) == sources[variant].read_bytes()
    finally:
        session.close()


def test_failed_write_after_same_name_upload_is_not_reported_as_new_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lever keeps its input, so another profile's same-basename file stays
    attached. The next profile's (existing, different) source is rejected by
    a Playwright error injected on the second `set_input_files` call only;
    the stale filename must not count as success.
    """
    sources = _sources(tmp_path)
    adapter, session, page, field = _open("lever")
    try:
        assert adapter.upload_resume(page, sources["java_backend"], field) is True
        _captured(page, 1)

        def rejected(self, *args, **kwargs):
            raise PlaywrightError("injected write failure")

        monkeypatch.setattr(Locator, "set_input_files", rejected)
        assert adapter.upload_resume(page, sources["ai_backend"], field) is False
        monkeypatch.undo()

        captured = page.evaluate("window.__captured")
    finally:
        monkeypatch.undo()
        session.close()

    assert len(captured) == 1
    assert bytes.fromhex(captured[0]["hex"]) == sources["java_backend"].read_bytes()


def test_greenhouse_noop_write_after_widget_removed_input_is_not_reported_as_new_profile(
    tmp_path: Path,
) -> None:
    """The modern Greenhouse widget removes its input on completion, so the
    next profile's write is a no-op and must not be confirmed by the
    completed same-basename readout.
    """
    sources = _sources(tmp_path)
    adapter, session, page, field = _open("greenhouse")
    try:
        assert adapter.upload_resume(page, sources["java_backend"], field) is True
        _captured(page, 1)

        assert adapter.upload_resume(page, sources["ai_backend"], field) is False

        captured = page.evaluate("window.__captured")
    finally:
        session.close()

    assert len(captured) == 1
    assert bytes.fromhex(captured[0]["hex"]) == sources["java_backend"].read_bytes()
