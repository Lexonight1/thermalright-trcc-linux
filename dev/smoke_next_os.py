#!/usr/bin/env python3
"""Runtime OS-parity smoke for ``src/trcc/next/``.

Run on whichever OS you're on — Linux dev box, Windows VM, macOS
reporter laptop, FreeBSD VM.  The script auto-detects the OS, picks
the matching :class:`Platform` adapter, exercises the imports +
discovery + sensor + GUI-widget surface, and prints a structured
report.

What it covers:

* Imports — every per-OS Platform / SensorSource pair loads without
  raising.
* :func:`current_platform` returns the right concrete class on the
  current OS (no silent fallback to a stub).
* App boots end-to-end against the real Platform (no FakePlatform).
* ``DiscoverDevices`` returns a list (count is informational —
  reporters without hardware will see 0).
* ``ReadSensors`` returns at least one CPU reading on every supported
  OS.
* G2–G5 GUI bits import cleanly under the offscreen Qt platform —
  catches "I added a Linux-specific Qt API" regressions before
  Windows / macOS reporters do.

Pasteable into a GitHub issue when a reporter hits "doesn't run on
my OS"; the section/probe structure makes diffs across reporters
obvious.

Usage::

    PYTHONPATH=src python3 dev/smoke_next_os.py

Exit code 0 on no FAIL, 1 otherwise.  WARN is informational.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "dev"))

# Offscreen Qt so we can import GUI widgets in CI / SSH sessions
# without an X server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _smoke_runtime import (
    Section,
    print_header,
    print_section,
    print_summary_and_exit,
)


# =========================================================================
# Sections
# =========================================================================


def _probe_imports() -> Section:
    s = Section("imports (per-OS Platform adapters)")
    for module, label in (
        ("trcc.adapters.system.linux", "LinuxOS"),
        ("trcc.adapters.system.windows", "WindowsPlatform"),
        ("trcc.adapters.system.macos", "MacOSPlatform"),
        ("trcc.adapters.system.bsd", "BSDPlatform"),
    ):
        try:
            __import__(module)
            s.ok(label, f"{module} imports cleanly")
        except BaseException as exc:
            # An OS-specific import that *fails* on the wrong host is
            # only a problem when its parent module probes hardware
            # eagerly.  We expect every Platform adapter to be importable
            # on every host (sensor sources may probe lazily); flag any
            # failure for follow-up.
            s.fail(label, exc)
    return s


def _probe_sensor_imports() -> Section:
    s = Section("imports (sensor adapters)")
    for module, label in (
        ("trcc.adapters.sensors.aggregator", "SensorAggregator"),
        ("trcc.adapters.sensors.chain", "SensorChain"),
        ("trcc.adapters.sensors.hwmon", "HwmonSensorSource"),
        ("trcc.adapters.sensors.psutil_sources", "psutil sources"),
        ("trcc.adapters.sensors.nvml", "NvmlGpuSource"),
        ("trcc.adapters.sensors.windows", "WindowsSensorSource"),
        ("trcc.adapters.sensors.macos", "MacOSSensorSource"),
        ("trcc.adapters.sensors.bsd", "BSDSensorSource"),
    ):
        try:
            __import__(module)
            s.ok(label, f"{module} imports cleanly")
        except BaseException as exc:
            s.fail(label, exc)
    return s


def _probe_gui_imports() -> Section:
    """Catch G1–G5 widgets that accidentally use platform-specific APIs."""
    s = Section("imports (G1–G5 GUI surface)")
    for module, label in (
        ("trcc.ui.qtgui.sensor_picker",     "G1 sensor_picker"),
        ("trcc.ui.qtgui.splash",            "G1 splash"),
        # Shared by both skins since the UI-edge unification — they live in
        # ``ui/``, not under ``ui/qtgui/``.  The old paths sat here failing
        # silently because nothing ran this harness.
        ("trcc.ui.eyedropper",              "G2 eyedropper"),
        ("trcc.ui.qtgui.color_wheel",       "G2 color_wheel"),
        ("trcc.ui.qtgui.image_crop",        "G2 image_crop"),
        ("trcc.ui.qtgui.video_crop",        "G2 video_crop"),
        ("trcc.ui.screen_overlay",          "G2 screen_overlay"),
        ("trcc.ui.qtgui.panels.led",        "G3 LED sub-tabs"),
        ("trcc.ui.qtgui.device_picker",     "G4 device_picker"),
        ("trcc.ui.qtgui.region_overlay",    "G5 region_overlay"),
        ("trcc.ui.qtgui.panels.screencast_panel", "G5 screencast_panel"),
        ("trcc.adapters.screencast.qt",   "G5 QtScreenCapture"),
    ):
        try:
            __import__(module)
            s.ok(label, f"{module} imports cleanly")
        except BaseException as exc:
            s.fail(label, exc)
    return s


def _probe_factory() -> Section:
    """``current_platform()`` must return the OS the registry serves this host.

    This asked ``"LinuxOS" in type(obj).__name__`` against a hand-written table
    of class NAMES until 2026-08-19, when it failed on a Fedora box that had
    just started returning ``DnfLinux`` -- the package-manager family split.  Its
    BSD rows were stale too, naming ``BSDPlatform`` for a class since renamed
    ``BsdOS``, so the same failure was waiting on every BSD; nothing noticed
    because nothing ran this file.

    A name is prose.  ``PLATFORMS`` is the authority -- it holds the base class
    registered for each ``sys.platform`` key -- and ``isinstance`` is the actual
    contract, so family subclasses and renames both pass without an edit here.
    """
    s = Section("current_platform()")
    try:
        from trcc.adapters.system import PLATFORMS, current_platform
    except BaseException as exc:
        s.fail("current_platform import", exc)
        return s

    try:
        platform_obj = current_platform()
        cls_name = type(platform_obj).__name__
        s.ok("current()", f"returned {cls_name}")
    except BaseException as exc:
        s.fail("current()", exc)
        return s

    # The one derived key in OS dispatch, mirroring ``current_platform``:
    # every BSD registers its own class but they share the "bsd" registry key.
    key = "bsd" if "bsd" in sys.platform else sys.platform
    if key not in PLATFORMS:
        s.warn(
            "registered for sys.platform",
            f"sys.platform={sys.platform!r} has no registered OS; "
            f"the registry falls back to linux",
        )
        return s

    base = PLATFORMS[key]
    if isinstance(platform_obj, base):
        s.ok(
            "instance of the registered OS",
            f"{cls_name} is a {base.__name__} — registered for {key!r}",
        )
    else:
        s.fail(
            "instance of the registered OS",
            RuntimeError(
                f"got {cls_name} on {sys.platform!r}; {key!r} is registered "
                f"to {base.__name__}, and the object is not one",
            ),
        )

    return s


def _probe_app_boot() -> Section:
    """Build a real App + dispatch DiscoverDevices + ReadSensors."""
    s = Section("App boot + dispatch")
    try:
        from trcc.adapters.render.qt import QtRenderer
        from trcc.app import App
        from trcc.adapters.system import current_platform
        from trcc.core.commands import DiscoverDevices, ReadSensors
    except BaseException as exc:
        s.fail("imports", exc)
        return s

    try:
        platform_obj = current_platform()
        renderer = QtRenderer()
        app = App(platform=platform_obj, renderer=renderer)
    except BaseException as exc:
        s.fail("App()", exc)
        return s

    try:
        result = app.dispatch(DiscoverDevices())
        n = len(getattr(result, "devices", []) or [])
        s.ok("DiscoverDevices", f"returned {n} device(s)")
    except BaseException as exc:
        s.fail("DiscoverDevices", exc)

    try:
        result = app.dispatch(ReadSensors())
        readings = list(getattr(result, "readings", []) or [])
        if readings:
            s.ok(
                "ReadSensors",
                f"{len(readings)} reading(s); first: "
                f"{readings[0].sensor_id}={readings[0].value:.2f}",
            )
        else:
            s.warn(
                "ReadSensors",
                "no readings — sensor adapter found nothing on this host",
            )
    except BaseException as exc:
        s.fail("ReadSensors", exc)

    try:
        app.close()
        s.ok("App.close()", "closed cleanly")
    except BaseException as exc:
        s.fail("App.close()", exc)

    return s


def _probe_paths() -> Section:
    """``platform.paths()`` must point at a writable, OS-appropriate location."""
    s = Section("Paths")
    try:
        from trcc.adapters.system import current_platform
        platform_obj = current_platform()
        paths = platform_obj.paths()
    except BaseException as exc:
        s.fail("paths()", exc)
        return s

    for name in ("config_dir", "user_content_dir", "log_dir"):
        try:
            target = getattr(paths, name)()
            target.mkdir(parents=True, exist_ok=True)
            # Write a probe file to confirm the dir is writable.
            probe = target / ".trcc-smoke-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            s.ok(name, str(target))
        except AttributeError:
            s.skip(name, "method not defined on Paths port")
        except BaseException as exc:
            s.fail(name, exc)
    return s


def _probe_screencast_capability() -> Section:
    """Document which capture path will run on this host without testing it."""
    s = Section("screencast capability (advisory)")
    import shutil
    if sys.platform.startswith("linux"):
        if shutil.which("grim") is not None:
            s.ok("grim", "found — Wayland capture available")
        else:
            s.warn(
                "grim",
                "not on PATH — install for Wayland screencast support",
            )
        if shutil.which("scrot") is not None:
            s.ok("scrot", "found — X11 fallback available")
        else:
            s.warn(
                "scrot",
                "not on PATH — X11 fallback unavailable; "
                "Qt native grab will be the only path",
            )
    elif sys.platform.startswith(("freebsd", "openbsd", "netbsd")):
        s.warn(
            "screencast",
            "BSD: screencast uses Qt grabWindow on Xorg; "
            "Wayland on BSD is uncommon",
        )
    elif sys.platform == "darwin":
        s.warn(
            "screencast",
            "macOS: Qt grabWindow needs Screen Recording permission; "
            "the system prompt fires on first use",
        )
    elif sys.platform == "win32":
        s.ok(
            "screencast",
            "Qt grabWindow on Windows uses BitBlt; no external tools needed",
        )
    else:
        s.skip("screencast", f"unknown sys.platform={sys.platform!r}")
    return s


def _probe_tempdirs_ok() -> Section:
    """Sanity: tempdir + cwd are writable (devs sometimes break this)."""
    s = Section("environment")
    try:
        tmp = Path(tempfile.gettempdir())
        probe = tmp / ".trcc-smoke"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        s.ok("tempfile.gettempdir", str(tmp))
    except BaseException as exc:
        s.fail("tempfile.gettempdir", exc)

    s.ok("PYTHONPATH", os.environ.get("PYTHONPATH", "(unset)"))
    s.ok("sys.platform", sys.platform)
    return s


# =========================================================================
# Driver
# =========================================================================


def main() -> int:
    print_header("next/ OS parity")
    sections = (
        _probe_tempdirs_ok(),
        _probe_imports(),
        _probe_sensor_imports(),
        _probe_gui_imports(),
        _probe_factory(),
        _probe_paths(),
        _probe_app_boot(),
        _probe_screencast_capability(),
    )
    for section in sections:
        print_section(section)
    return print_summary_and_exit(list(sections))


if __name__ == "__main__":
    raise SystemExit(main())
