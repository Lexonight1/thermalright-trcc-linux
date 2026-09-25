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
    """Build the FakePlatform used by tests/conftest.py."""
    import atexit
    import shutil
    import tempfile

    sys.path.insert(0, str(_REPO_ROOT / "tests"))
    # ``tests/conftest.py`` exports FakePlatform; import path works
    # once the tests/ dir is on sys.path.  Static analyzers won't
    # resolve it from the dev script — runtime-only.
    from conftest import FakePlatform  # type: ignore[import-not-found]

    # Registered rather than wrapped in ``try/finally`` so it also fires when
    # a step raises, and under pytest, where this module is imported and
    # driven rather than run.  It was never cleaned at all: 22 abandoned
    # roots holding 537 MB were on the dev box when this was found.
    root = Path(tempfile.mkdtemp(prefix="trcc-smoke-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return FakePlatform(root)


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
    from trcc.app import App

    return App(platform=_platform(), renderer=_smoke_renderer())


def _seed_data_dirs(app: Any, resolution: tuple[int, int]) -> None:
    """Populate the three per-resolution dirs so ``ensure_all`` short-circuits.

    ``DataInstaller.install`` returns early on ``_is_populated`` — any single
    entry is enough — which is the whole point of the step below: prove the
    wiring, never fetch.
    """
    paths = app.platform.paths()
    w, h = resolution
    for directory in (paths.theme_dir(w, h),
                      paths.cloud_theme_dir(w, h),
                      paths.cloud_mask_dir(w, h)):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "00.png").touch()


def _run_steps() -> list[_Step]:
    """Drive one Command per family + capture which events fire.

    Each step builds a Command, dispatches it, asserts:
      (1) Result.ok is the expected value (most setters return ok=True)
      (2) the expected Event class shows up in the captured stream

    Failures are isolated per step — a broken Command doesn't mask
    the rest.
    """
    from trcc.core.commands import (
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
    from trcc.core.events import (
        BrightnessChanged,
        Event,
        GpuDeviceChanged,
        LanguageChanged,
        LedColorsChanged,
        OrientationChanged,
        RefreshIntervalChanged,
        TempUnitChanged,
    )
    from trcc.core.led_models import LEDMode
    from trcc.core.variants import get_button_image, get_variant_override

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

    # ── Variant override lookup (recently-ported feature, table-only
    #     so a fake handshake can't drive it).  Sanity-check the
    #     registry directly so a missing entry surfaces here, not in a
    #     reporter's GUI as "wrong button".
    variant_step = _Step(label="VariantOverride lookup")
    try:
        # Frozen Warframe SCSI: PM=51 must resolve to A1FROZEN WARFRAME,
        # PM=64 SUB=3 (Levita) to its A1LM30 button.  (Its panel_cutout went
        # with the island mirror it served — the island is DisplayService's
        # SUB 3 rule now, #149.)
        bi = get_button_image(0x0402, 0x3922, 51, 0)
        levita = get_variant_override(0x0402, 0x3922, 64, 3)
        assert bi == "A1FROZEN WARFRAME", f"PM=51 got {bi!r}"
        assert levita is not None and levita.button_image == "A1LM30", (
            f"PM=64 SUB=3 (Levita) resolved to {levita!r}"
        )
        variant_step.passed = True
        variant_step.detail = f"PM=51→{bi}, Levita→A1LM30"
    except Exception as e:
        variant_step.passed = False
        variant_step.detail = f"{type(e).__name__}: {e}"
    steps.append(variant_step)

    # ── EnsureData install pipeline (per-resolution archives).  Hits
    #     the just-ported DataInstallService.  The dirs are SEEDED first so
    #     it short-circuits — proves the wiring, no actual download.
    #
    #     That is what this comment always claimed, and it was false: the
    #     platform above hands out a fresh ``mkdtemp``, which can never be
    #     populated, so every run fetched 23.4 MB from GitHub (theme 26 KB +
    #     web 6.9 MB + masks 17.5 MB) and the suite FAILED outright with no
    #     route.  Nothing caught it because the pytest wrapper's fixture
    #     (``tests/test_integration_pipeline.py``) is module-scoped, and
    #     conftest's autouse offline stub is function-scoped — pytest sets
    #     higher scopes up first, so the guard was not yet in effect.
    install_step = _Step(label="DataInstallService.ensure_all")
    try:
        _seed_data_dirs(app, (320, 320))
        result = app.data_install.ensure_all((320, 320))
        install_step.passed = result.ok
        install_step.detail = (
            f"themes={result.themes_ok} web={result.web_ok} "
            f"masks={result.masks_ok}"
        )
    except Exception as e:
        install_step.passed = False
        install_step.detail = f"{type(e).__name__}: {e}"
    steps.append(install_step)

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
    # Test mode is a momentary diagnostic — turn it back off so the harness
    # never leaves it persisted in the config (a stale True paints PAGE-style
    # panels near-black on the next summon).  Mirrors smoke_real_hardware.
    _step(
        "EnableLedTestMode (off)",
        EnableLedTestMode(key="0416:8001", enabled=False),
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
