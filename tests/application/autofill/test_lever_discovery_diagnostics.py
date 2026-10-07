"""Browser-free coverage for `LeverAdapter.discover_fields`'s own stage
diagnostics (`stage=discovery_start` / `discover_field` / `discovery_complete`).

A real stalled run was observed to end its log right after the second
successful `stage=form_recognition` warning (the one `discover_fields` itself
emits via `self.recognize(page)`) with nothing after it, leaving no way to
tell whether the hang was in discovery, classification, enrichment, or a
fill/readback call. These markers close the discovery half of that gap: a
`discover_field` line is logged for a control's index *before* the much
heavier per-control Playwright work in `_discover_control` runs, so a
genuinely stuck live run leaves behind the exact index it stalled on.

Uses a minimal fake Page/Locator double (not a real browser) and
monkeypatches the per-control `_discover_control` helper itself, since
exercising its own real DOM logic is already covered by the Chromium-backed
tests in `test_lever_adapter.py`.
"""

from __future__ import annotations

import logging

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.application.autofill import lever
from app.application.autofill.fields import DiscoveredField
from app.application.autofill.lever import LeverAdapter

LEVER_LOGGER = "app.application.autofill.lever"


class _FakeControlsLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count

    def nth(self, index: int) -> int:
        return index


class _FakeFormLocator:
    def __init__(self, control_count: int) -> None:
        self._control_count = control_count

    def count(self) -> int:
        return 1

    @property
    def first(self) -> "_FakeFormLocator":
        return self

    def locator(self, selector: str) -> _FakeControlsLocator:
        assert selector == "input, textarea, select"
        return _FakeControlsLocator(self._control_count)


class _FakeDiscoveryPage:
    """Covers only what `recognize`/`_application_form`/`discover_fields`
    touch: a form-selector locator that is always "found" (count=1) and,
    from it, a fixed-size `input, textarea, select` control list.
    """

    def __init__(self, control_count: int) -> None:
        self.url = "file:///tmp/lever_application.html"
        self._control_count = control_count

    def locator(self, selector: str) -> _FakeFormLocator:
        _ = selector
        return _FakeFormLocator(self._control_count)


def test_discover_fields_logs_start_and_completion_stage_markers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_discover_control(page: object, locator: int, seen_radio_names: set[str]) -> DiscoveredField:
        _ = page, seen_radio_names
        return DiscoveredField(label=f"Field {locator}", field_type="text", required=False)

    monkeypatch.setattr(lever, "_discover_control", fake_discover_control)
    page = _FakeDiscoveryPage(control_count=3)

    with caplog.at_level(logging.WARNING, logger=LEVER_LOGGER):
        fields = LeverAdapter().discover_fields(page)

    assert len(fields) == 3
    messages = [r.getMessage() for r in caplog.records]
    assert any(m == "lever_prepare stage=discovery_start" for m in messages)
    field_logs = [m for m in messages if "stage=discover_field" in m]
    assert len(field_logs) == 3
    assert any("index=0 control_count=3" in m for m in field_logs)
    assert any("index=1 control_count=3" in m for m in field_logs)
    assert any("index=2 control_count=3" in m for m in field_logs)
    complete_log = next(m for m in messages if "stage=discovery_complete" in m)
    assert "field_count=3" in complete_log
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_discover_fields_stage_markers_never_include_field_identity(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The per-control/start/completion markers are purely structural
    (index, counts, timing) -- never the field's own label, name, or any
    other discovered identity, unlike the final-write diagnostic
    (`lever_final_write`) that does log a normalized label token. A stalled
    discovery loop never finished classifying the field it stalled on, so
    there is nothing safe to say about it beyond its position.
    """

    def fake_discover_control(page: object, locator: int, seen_radio_names: set[str]) -> DiscoveredField:
        _ = page, seen_radio_names
        return DiscoveredField(label="Candidate Secret Question Answer", name="secret_field", field_type="text")

    monkeypatch.setattr(lever, "_discover_control", fake_discover_control)
    page = _FakeDiscoveryPage(control_count=2)

    with caplog.at_level(logging.WARNING, logger=LEVER_LOGGER):
        LeverAdapter().discover_fields(page)

    messages = [r.getMessage() for r in caplog.records if "stage=discover" in r.getMessage()]
    assert messages
    assert not any("Candidate Secret Question Answer" in m or "secret_field" in m for m in messages)


def test_discover_fields_logs_start_marker_before_a_stalled_form_locator_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`_application_form(page)` and `form.locator(...)` are both further
    Playwright DOM calls made right after the second successful `recognize`
    check inside `discover_fields`. `stage=discovery_start` must already be
    logged before either of them, so a live recurrence that stalls on one of
    those two calls (as previously happened right after the second
    `stage=form_recognition` marker) still leaves `discovery_start` behind.
    """

    class _StalledFormLocatorPage:
        def __init__(self) -> None:
            self.url = "file:///tmp/lever_application.html"

        def locator(self, selector: str) -> object:
            if selector == "#application-form, form.application-form":
                return _FakeFormLocator(control_count=0)
            raise PlaywrightTimeoutError("simulated stalled form locator")

    page = _StalledFormLocatorPage()

    with caplog.at_level(logging.WARNING, logger=LEVER_LOGGER):
        with pytest.raises(PlaywrightTimeoutError):
            LeverAdapter().discover_fields(page)

    messages = [r.getMessage() for r in caplog.records]
    assert any(m == "lever_prepare stage=discovery_start" for m in messages)
    assert not any("stage=discovery_complete" in m for m in messages)


def test_discover_fields_stalled_control_leaves_start_marker_for_its_index(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Simulates a per-control call that never returns (here, an immediate
    raise stands in for a real hang, since a unit test cannot express an
    actual infinite stall) at control index 2 of 4. The `discover_field`
    marker for that exact index must already be logged, since it is emitted
    before the per-control call it precedes -- proving a genuinely stuck
    live run leaves behind exactly the index it stalled on. `discovery_complete`
    must never appear, since the loop never finishes, and no later index is
    ever reached.
    """

    def fake_discover_control(page: object, locator: int, seen_radio_names: set[str]) -> DiscoveredField | None:
        _ = page, seen_radio_names
        if locator == 2:
            raise TimeoutError("simulated stalled control")
        return None

    monkeypatch.setattr(lever, "_discover_control", fake_discover_control)
    page = _FakeDiscoveryPage(control_count=4)

    with caplog.at_level(logging.WARNING, logger=LEVER_LOGGER):
        with pytest.raises(TimeoutError):
            LeverAdapter().discover_fields(page)

    messages = [r.getMessage() for r in caplog.records]
    field_logs = [m for m in messages if "stage=discover_field" in m]
    assert any("index=0" in m for m in field_logs)
    assert any("index=1" in m for m in field_logs)
    assert any("index=2" in m for m in field_logs)
    assert not any("index=3" in m for m in field_logs)
    assert not any("stage=discovery_complete" in m for m in messages)
