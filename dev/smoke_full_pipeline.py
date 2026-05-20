#!/usr/bin/env python3
"""End-to-end integration smoke — Command bus + EventBus contract.

Verifies the unified-UI principle in code: every UI on top of next/
(CLI, API, GUI, daemon clients via AppProxy, future VR) ultimately
builds one of the registered Command classes and hands it to
``App.dispatch``.  The Commands publish Events back on
``App.events`` for any subscriber.

This script:
  1. Boots one App (in-process, no daemon — same as a CLI invocation)
  2. Subscribes to every event type the bus exposes
  3. Dispatches one representative Command per family
  4. Asserts the expected Event arrived

If this passes, the bus carries the traffic and every UI inherits
the same behavior.

Run via::

    PYTHONPATH=src python dev/smoke_full_pipeline.py

Exit code 0 on full green, 1 on any divergence.  Suitable as a pre-tag
sanity check or a tight feedback loop during refactors.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# =========================================================================
# Output formatting
# =========================================================================


_OK = "✓"
_FAIL = "✗"
_LINE = "═" * 60


@dataclass(slots=True)
class _Step:
    """One smoke step — command name + the event class to wait for."""
    label: str
    passed: bool = False
    detail: str = ""


# =========================================================================
# Fake platform — reused from the test harness so behavior matches
# =========================================================================


def _platform() -> Any:
    """Build the FakePlatform used by tests/next/conftest.py."""
    import tempfile

    sys.path.insert(0, str(_REPO_ROOT / "tests"))
    # ``tests/next/conftest.py`` exports FakePlatform; import path
    # works once the tests/ dir is on sys.path.  Static analyzers
    # won't resolve it from the dev script — runtime-only.
    from next.conftest import FakePlatform  # type: ignore[import-not-found]

    return FakePlatform(Path(tempfile.mkdtemp(prefix="trcc-smoke-")))


def _smoke_renderer() -> Any:
    """Minimal Renderer impl — keeps DisplayService constructible."""

    class _Surface:
        def __init__(self, w: int = 100, h: int = 100) -> None:
            self.w, self.h = w, h

    class _R:
        def create_surface(self, w: int, h: int, color: Any = None) -> Any:
            return _Surface(w, h)
        def open_image(self, path: Any) -> Any:
            return _Surface()
        def surface_size(self, surface: Any) -> tuple[int, int]:
            return (surface.w, surface.h)
        def composite(self, b: Any, o: Any, p: Any, m: Any = None) -> Any:
            return b
        def resize(self, s: Any, w: int, h: int) -> Any:
            return _Surface(w, h)
        def rotate(self, s: Any, d: int) -> Any:
            return s
        def apply_brightness(self, s: Any, p: int) -> Any:
            return s
        def draw_text(self, *a: Any, **kw: Any) -> None:
            pass
        def encode_rgb565(self, s: Any) -> bytes:
            return b"\x00\x00" * (s.w * s.h)
        def encode_jpeg(self, *a: Any, **kw: Any) -> bytes:
            return b""
        def from_raw_rgb24(self, f: Any) -> Any:
            return _Surface()

    return _R()


# =========================================================================
# The smoke matrix — one Command per family + the event we expect
# =========================================================================


def _build_app() -> Any:
    from trcc.next.app import App

    return App(platform=_platform(), renderer=_smoke_renderer())


def _run_steps() -> list[_Step]:
    """Drive one Command per family + capture which events fire.

    Each step builds a Command, dispatches it, asserts:
      (1) Result.ok is the expected value (most setters return ok=True)
      (2) the expected Event class shows up in the captured stream

    Failures are isolated per step — a broken Command doesn't mask
    the rest.
    """
    from trcc.next.core.commands import (
        DiscoverDevices,
        EnableLedTestMode,
        SetBrightness,
        SetGpuDevice,
        SetLanguage,
        SetLedBrightness,
        SetLedColor,
        SetLedMode,
        SetOrientation,
        SetRefreshInterval,
        SetTempUnit,
    )
    from trcc.next.core.events import (
        BrightnessChanged,
        Event,
        GpuDeviceChanged,
        LanguageChanged,
        LedColorsChanged,
        OrientationChanged,
        RefreshIntervalChanged,
        TempUnitChanged,
    )
    from trcc.next.core.led_models import LEDMode

    app = _build_app()

    # Subscribe once to every Event type — captures the full stream so
    # we can ask "did this Command publish the expected event?"
    captured: list[Event] = []
    for event_type in (
        OrientationChanged, BrightnessChanged, LedColorsChanged,
        TempUnitChanged, LanguageChanged, GpuDeviceChanged,
        RefreshIntervalChanged,
    ):
        app.events.subscribe(event_type, captured.append)

    steps: list[_Step] = []

    def _step(label: str, command: Any, expected_event: type | None) -> None:
        step = _Step(label=label)
        try:
            before = len(captured)
            result = app.dispatch(command)
            if expected_event is None:
                step.passed = result.ok
                step.detail = result.message
            else:
                new_events = captured[before:]
                got_expected = any(
                    isinstance(e, expected_event) for e in new_events
                )
                step.passed = result.ok and got_expected
                step.detail = (
                    f"{result.message} | events: "
                    f"{[type(e).__name__ for e in new_events]}"
                )
        except Exception as e:
            step.passed = False
            step.detail = f"{type(e).__name__}: {e}"
        steps.append(step)

    # ── Discovery (no event; just exercises the dispatch path) ─────
    _step("DiscoverDevices", DiscoverDevices(), expected_event=None)

    # ── Display setters ────────────────────────────────────────────
    _step(
        "SetOrientation",
        SetOrientation(key="0402:3922", degrees=90),
        OrientationChanged,
    )
    _step(
        "SetBrightness",
        SetBrightness(key="0402:3922", percent=75),
        BrightnessChanged,
    )

    # ── LED setters ───────────────────────────────────────────────
    _step(
        "SetLedMode",
        SetLedMode(key="0416:8001", mode=LEDMode.RAINBOW),
        LedColorsChanged,
    )
    _step(
        "SetLedColor",
        SetLedColor(key="0416:8001", color=(50, 100, 200)),
        LedColorsChanged,
    )
    _step(
        "SetLedBrightness",
        SetLedBrightness(key="0416:8001", percent=65),
        LedColorsChanged,
    )
    _step(
        "EnableLedTestMode",
        EnableLedTestMode(key="0416:8001", enabled=True),
        LedColorsChanged,
    )

    # ── Control center ────────────────────────────────────────────
    _step("SetTempUnit", SetTempUnit(unit="F"), TempUnitChanged)
    _step("SetLanguage", SetLanguage(language="de"), LanguageChanged)
    _step("SetGpuDevice", SetGpuDevice(gpu_key="nvidia:0"), GpuDeviceChanged)
    _step(
        "SetRefreshInterval",
        SetRefreshInterval(seconds=3.0),
        RefreshIntervalChanged,
    )

    app.close()
    return steps


# =========================================================================
# Output
# =========================================================================


def _print_results(steps: list[_Step]) -> int:
    print("Phase D full-pipeline smoke")
    print(_LINE)
    passed = sum(1 for s in steps if s.passed)
    for step in steps:
        glyph = _OK if step.passed else _FAIL
        status = "PASS" if step.passed else "FAIL"
        print(f"  {step.label:<28s} {glyph} {status}")
        if not step.passed and step.detail:
            print(f"      {step.detail}")
    print(_LINE)
    print(f"Result: {passed} / {len(steps)} integration steps green")
    return 0 if passed == len(steps) else 1


def main() -> int:
    return _print_results(_run_steps())


if __name__ == "__main__":
    raise SystemExit(main())
