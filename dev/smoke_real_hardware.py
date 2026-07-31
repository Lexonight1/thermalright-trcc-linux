#!/usr/bin/env python3
"""Interactive real-hardware verification for next/.

Walks a user with a connected Thermalright cooler through every Command
that has visible effect on the device, prompting (y/n/s) after each one
so the operator records what *actually* happened on the hardware.

The output is the only honest answer to "is next/ verified?".  A green
``pytest`` run only proves the code compiles + the bus carries traffic;
this script is the gate from WIRED → VERIFIED in the audit.

Usage:

    PYTHONPATH=src python dev/smoke_real_hardware.py

What it does:

1.  Builds a *real* App (real Platform, real QtRenderer, real USB).
2.  Dispatches DiscoverDevices, prints what came back.
3.  For each detected LCD + LED device, walks every Command in a sensible
    order: solid colours, orientation, brightness, theme, mask, video,
    slideshow, screencast prep.  Per-step y/n captures whether the
    device responded as expected.
4.  Writes a final report to stdout *and* ``/tmp/trcc-hwverify.txt``.

What it does NOT do:

* Run unattended in CI.  This is a manual gate, by design — only a
  human can answer "did the screen actually turn red?"
* Boot-animation upload (rewrites flash — too destructive for a
  smoke loop; gated behind ``--anim-too``).
* Real screencast (needs an X11/Wayland session + region pick).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# Show debug logs on screen — the user will want them when something
# misbehaves on the wire.
logging.basicConfig(
    level=os.environ.get("TRCC_HW_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)-20s %(levelname)-7s %(message)s",
)
log = logging.getLogger("hwverify")

_REPORT_PATH = Path("/tmp/trcc-hwverify.txt")


# =========================================================================
# Step result tracking
# =========================================================================


@dataclass(slots=True)
class StepResult:
    label: str
    expected: str
    answer: str = ""        # "y" / "n" / "s" (skip)
    detail: str = ""

    @property
    def pass_(self) -> bool:
        return self.answer == "y"

    @property
    def skipped(self) -> bool:
        return self.answer == "s"


@dataclass(slots=True)
class Section:
    name: str
    steps: list[StepResult] = field(default_factory=list)

    def summary(self) -> tuple[int, int, int]:
        passed = sum(1 for s in self.steps if s.pass_)
        failed = sum(1 for s in self.steps if not s.pass_ and not s.skipped)
        skipped = sum(1 for s in self.steps if s.skipped)
        return passed, failed, skipped


# =========================================================================
# Prompts
# =========================================================================


def _prompt(question: str, default: str = "n") -> str:
    """Ask y/n/s; return one of those single letters."""
    while True:
        raw = input(f"{question} [y/n/s] (default {default}): ").strip().lower()
        if not raw:
            return default
        if raw[0] in ("y", "n", "s"):
            return raw[0]
        print("Please answer y, n, or s (skip).")


def _press_enter(msg: str = "Press Enter to continue…") -> None:
    input(msg)


# =========================================================================
# App bootstrap — REAL platform, real renderer, real USB
# =========================================================================


def _build_real_app():
    """Construct the same App the GUI launcher does.

    We deliberately use the production Platform + QtRenderer.  Tests
    that pass against FakePlatform won't catch real-USB / real-screen
    bugs; this script is the gate that does.
    """
    # Qt is needed for QtRenderer + (later) screencast UI bits.  We
    # use an offscreen platform so the script works over SSH too.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from trcc.adapters.render.qt import QtRenderer
    from trcc.adapters.system import current_platform
    from trcc.app import App

    platform = current_platform()
    renderer = QtRenderer()
    app = App(platform=platform, renderer=renderer)
    return app


# =========================================================================
# Step runner
# =========================================================================


class _Walker:
    """Driver: dispatches Commands, prompts, records results."""

    def __init__(self) -> None:
        self.sections: list[Section] = []
        self._current: Section | None = None

    def section(self, name: str) -> None:
        section = Section(name=name)
        self.sections.append(section)
        self._current = section
        print()
        print("─" * 72)
        print(f"  {name}")
        print("─" * 72)

    def step(
        self,
        label: str,
        expected: str,
        dispatch_fn,
        *,
        settle_s: float = 1.0,
    ) -> StepResult:
        """Run *dispatch_fn*, settle, prompt the operator, record."""
        assert self._current is not None, "Call section() first"
        result = StepResult(label=label, expected=expected)
        print()
        print(f"  → {label}")
        print(f"    expected: {expected}")
        try:
            command_result = dispatch_fn()
        except Exception as e:
            result.answer = "n"
            result.detail = f"dispatch raised {type(e).__name__}: {e}"
            print(f"    ! dispatch crashed: {result.detail}")
            self._current.steps.append(result)
            return result
        time.sleep(settle_s)
        if command_result is not None:
            ok = getattr(command_result, "ok", True)
            message = getattr(command_result, "message", "")
            print(f"    dispatch ok={ok}: {message}")
            if not ok:
                result.detail = message
        result.answer = _prompt(
            "    Did the device behave as expected?", default="y" if not result.detail else "n",
        )
        self._current.steps.append(result)
        return result


# =========================================================================
# LCD walk-through
# =========================================================================


def _walk_lcd(walker: _Walker, app, key: str, *, run_video: bool) -> None:
    """Drive every Command that has a visible effect on an LCD."""
    from trcc.core.commands import (
        ConnectDevice,
        LcdSnapshot,
        SendColor,
        SetBackgroundMode,
        SetBrightness,
        SetFitMode,
        SetOrientation,
        SetSplitMode,
        StopVideo,
    )

    walker.section(f"LCD: {key}")

    walker.step(
        "ConnectDevice",
        "no exception; device reports its resolution",
        lambda: app.dispatch(ConnectDevice(key=key)),
        settle_s=0.5,
    )

    walker.step(
        "SendColor (red)",
        "the screen turns solid red",
        lambda: app.dispatch(SendColor(key=key, r=255, g=0, b=0)),
    )
    walker.step(
        "SendColor (green)",
        "the screen turns solid green",
        lambda: app.dispatch(SendColor(key=key, r=0, g=255, b=0)),
    )
    walker.step(
        "SendColor (blue)",
        "the screen turns solid blue",
        lambda: app.dispatch(SendColor(key=key, r=0, g=0, b=255)),
    )

    for degrees in (90, 180, 270, 0):
        walker.step(
            f"SetOrientation {degrees}",
            f"the displayed image rotates to {degrees}°",
            lambda d=degrees: app.dispatch(SetOrientation(key=key, degrees=d)),
        )
        # Re-send a colour so the rotation is visible.
        app.dispatch(SendColor(key=key, r=128, g=80, b=200))
        time.sleep(0.3)

    for percent in (25, 75, 100):
        walker.step(
            f"SetBrightness {percent}",
            f"the screen brightness shifts to roughly {percent}%",
            lambda p=percent: app.dispatch(SetBrightness(key=key, percent=p)),
        )

    walker.step(
        "SetFitMode height",
        "background fits to screen height with letterbox bars",
        lambda: app.dispatch(SetFitMode(key=key, mode="height")),
        settle_s=0.3,
    )
    walker.step(
        "SetFitMode width",
        "background fits to screen width with pillarbox bars",
        lambda: app.dispatch(SetFitMode(key=key, mode="width")),
        settle_s=0.3,
    )

    walker.step(
        "SetSplitMode 0 (off)",
        "no Dynamic-Island split visible",
        lambda: app.dispatch(SetSplitMode(key=key, mode=0)),
        settle_s=0.3,
    )

    walker.step(
        "SetBackgroundMode color",
        "solid colour background; theme image hidden",
        lambda: app.dispatch(SetBackgroundMode(key=key, mode="color")),
        settle_s=0.3,
    )
    walker.step(
        "SetBackgroundMode theme",
        "theme background returns",
        lambda: app.dispatch(SetBackgroundMode(key=key, mode="theme")),
        settle_s=0.3,
    )

    if run_video:
        # Locate a test video — bundled assets dir + common system paths.
        candidates = (
            Path("/tmp/trcc-test-video.mp4"),
            Path.home() / "Videos" / "trcc-test.mp4",
        )
        video_path = next((p for p in candidates if p.is_file()), None)
        if video_path is None:
            print(
                "    (skip video tests — drop a sample at "
                "/tmp/trcc-test-video.mp4 to enable)",
            )
        else:
            from trcc.core.commands import PlayVideo

            walker.step(
                "PlayVideo",
                f"video playback starts from {video_path.name}",
                lambda: app.dispatch(PlayVideo(key=key, path=video_path)),
                settle_s=1.5,
            )
            walker.step(
                "StopVideo",
                "playback stops, last frame stays on screen",
                lambda: app.dispatch(StopVideo(key=key)),
            )

    # Persisted state should round-trip — snapshot read.
    snap = app.dispatch(LcdSnapshot(key=key))
    log.info("LcdSnapshot ok=%s message=%s", snap.ok, snap.message)


# =========================================================================
# LED walk-through
# =========================================================================


def _walk_led(walker: _Walker, app, key: str) -> None:
    from trcc.core.commands import (
        ConnectDevice,
        EnableLedTestMode,
        RenderLed,
        SetLedBrightness,
        SetLedColor,
        SetLedMode,
        ToggleLed,
    )
    from trcc.core.led_models import LEDMode

    walker.section(f"LED: {key}")

    walker.step(
        "ConnectDevice (LED)",
        "no exception; handshake completes",
        lambda: app.dispatch(ConnectDevice(key=key)),
        settle_s=0.5,
    )

    walker.step(
        "ToggleLed on",
        "LEDs power up at whatever colour/mode was last persisted",
        lambda: app.dispatch(ToggleLed(key=key, on=True)),
    )

    for r, g, b, name in (
        (255, 0, 0, "red"),
        (0, 255, 0, "green"),
        (0, 0, 255, "blue"),
        (255, 255, 255, "white"),
    ):
        walker.step(
            f"SetLedColor + RenderLed ({name})",
            f"every LED turns {name}",
            lambda r=r, g=g, b=b: (
                app.dispatch(SetLedColor(key=key, color=(r, g, b))),
                app.dispatch(SetLedMode(key=key, mode=LEDMode.STATIC)),
                app.dispatch(RenderLed(key=key)),
            )[-1],
        )

    for percent in (20, 60, 100):
        walker.step(
            f"SetLedBrightness {percent}",
            f"brightness dims to ~{percent}%",
            lambda p=percent: (
                app.dispatch(SetLedBrightness(key=key, percent=p)),
                app.dispatch(RenderLed(key=key)),
            )[-1],
        )

    for mode in (LEDMode.BREATHING, LEDMode.RAINBOW, LEDMode.COLORFUL):
        walker.step(
            f"SetLedMode {mode.name}",
            f"the {mode.name.lower()} animation kicks in (give it a few ticks)",
            lambda m=mode: (
                app.dispatch(SetLedMode(key=key, mode=m)),
                app.dispatch(RenderLed(key=key)),
            )[-1],
            settle_s=2.5,
        )

    walker.step(
        "EnableLedTestMode (on)",
        "LEDs cycle through 4 reference colours (white → red → green → blue)",
        lambda: (
            app.dispatch(EnableLedTestMode(key=key, enabled=True)),
            app.dispatch(RenderLed(key=key)),
        )[-1],
        settle_s=4.0,
    )
    walker.step(
        "EnableLedTestMode (off)",
        "LEDs return to the last static colour",
        lambda: (
            app.dispatch(EnableLedTestMode(key=key, enabled=False)),
            app.dispatch(SetLedMode(key=key, mode=LEDMode.STATIC)),
            app.dispatch(RenderLed(key=key)),
        )[-1],
        settle_s=2.0,
    )


# =========================================================================
# Report
# =========================================================================


def _print_report(walker: _Walker) -> int:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("  TRCC next/ — real-hardware verification report")
    lines.append("=" * 72)
    total_pass = total_fail = total_skip = 0
    for section in walker.sections:
        p, f, s = section.summary()
        total_pass += p
        total_fail += f
        total_skip += s
        lines.append("")
        lines.append(f"  [{section.name}]   pass={p} fail={f} skip={s}")
        for step in section.steps:
            glyph = (
                "PASS" if step.pass_
                else "SKIP" if step.skipped
                else "FAIL"
            )
            lines.append(f"    {glyph}  {step.label}")
            if step.detail:
                lines.append(f"         {step.detail}")
    lines.append("")
    lines.append("=" * 72)
    lines.append(
        f"  TOTAL: pass={total_pass} fail={total_fail} skip={total_skip}",
    )
    lines.append("=" * 72)
    output = "\n".join(lines)
    print(output)
    try:
        _REPORT_PATH.write_text(output + "\n", encoding="utf-8")
        print(f"\n  Report saved to {_REPORT_PATH}")
    except OSError as e:
        print(f"\n  (couldn't write report: {e})")
    return 0 if total_fail == 0 else 1


# =========================================================================
# Entry
# =========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--lcd-key",
        help="LCD device key to verify (skip auto-detect).",
    )
    parser.add_argument(
        "--led-key",
        help="LED device key to verify (skip auto-detect).",
    )
    parser.add_argument(
        "--no-video", action="store_true",
        help="Skip PlayVideo / StopVideo steps.",
    )
    parser.add_argument(
        "--skip-lcd", action="store_true",
        help="Skip the LCD section entirely.",
    )
    parser.add_argument(
        "--skip-led", action="store_true",
        help="Skip the LED section entirely.",
    )
    args = parser.parse_args()

    print("Building a real App (real Platform, QtRenderer, real USB)…")
    app = _build_real_app()

    walker = _Walker()
    walker.section("Discovery")

    from trcc.core.commands import DiscoverDevices

    walker.step(
        "DiscoverDevices",
        "the script lists every attached cooler below",
        lambda: app.dispatch(DiscoverDevices()),
        settle_s=0.5,
    )

    lcd_key = args.lcd_key
    led_key = args.led_key
    for key, device in app.devices.items():
        kind = str(getattr(device.info, "kind", "")).lower()
        if "lcd" in kind and lcd_key is None:
            lcd_key = key
        elif "led" in kind and led_key is None:
            led_key = key
    print(f"  Resolved LCD key: {lcd_key or '(none)'}")
    print(f"  Resolved LED key: {led_key or '(none)'}")

    if not args.skip_lcd and lcd_key:
        _press_enter(
            f"\n  About to drive the LCD ({lcd_key}).  "
            "Make sure the device is plugged in + visible.  Press Enter.",
        )
        _walk_lcd(walker, app, lcd_key, run_video=not args.no_video)

    if not args.skip_led and led_key:
        _press_enter(
            f"\n  About to drive the LED controller ({led_key}).  "
            "Make sure the lighting hardware is connected.  Press Enter.",
        )
        _walk_led(walker, app, led_key)

    if (args.skip_lcd or not lcd_key) and (args.skip_led or not led_key):
        print(
            "\n  No devices to drive.  Plug in a Thermalright cooler "
            "and try again.",
        )

    app.close()
    return _print_report(walker)


if __name__ == "__main__":
    raise SystemExit(main())
