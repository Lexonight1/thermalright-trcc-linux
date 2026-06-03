"""Shared bootstrap for ``dev/mock_*.py`` — runs the real next/ GUI / CLI
/ API but rooted at ``dev/.trcc/`` instead of ``~/.trcc/`` so a dev
session never touches the user's real config.

The post-cutover ``Platform`` port is the only seam we need.
``DevPlatform`` subclasses the host's production platform (just
``LinuxPlatform`` here — Mac/Windows/BSD can extend later) and overrides
only ``paths()`` to point at the dev directories.  Real USB, real
sensors, real autostart — same code paths a packaged install runs, just
isolated to a throwaway data root.

For a hardware-less smoke (CI, ergonomics dev box without a real LCD),
plug in ``FakePlatform`` from ``tests/conftest.py`` instead — the
shape is identical.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trcc.core.ports import Platform

# Make src/ importable without requiring the caller to set PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src'))
sys.path.insert(0, str(_REPO_ROOT))


# ─── Dev paths (every mock_* script writes here, not ~/.trcc) ────────────────

_DEV_DIR = Path(__file__).resolve().parent
DEV_TRCC = _DEV_DIR / '.trcc'
DEV_DATA = DEV_TRCC / 'data'
DEV_USER = _DEV_DIR / '.trcc-user'
DEV_TRCC.mkdir(exist_ok=True)
DEV_DATA.mkdir(exist_ok=True)
DEV_USER.mkdir(exist_ok=True)

DEVICES_JSON = _DEV_DIR / 'devices.json'  # survives .trcc wipe

log = logging.getLogger(__name__)


# ─── Device spec loading ─────────────────────────────────────────────────────

def _specs_from_report(report_path: str) -> list[dict]:
    """Parse a ``trcc report`` file into device-spec dicts.

    Kept for callers that want to record + replay a user's hardware set
    — the diagnose tool already understands the file format.  Not used
    by ``mock_gui.py`` against real hardware (Platform.scan_devices
    returns the real attached set), only by ``mock_cli`` / ``mock_api``
    style harnesses that want a fixed device list.
    """
    sys.path.insert(0, str(_REPO_ROOT / 'tools'))
    from diagnose import parse_report  # type: ignore[import-not-found]

    text = Path(report_path).read_text()
    report = parse_report(text)
    if report.os_name:
        print(f"User OS: {report.os_name}")
    if report.trcc_version:
        print(f"User trcc version: {report.trcc_version}")
    specs: list[dict] = []
    for dev in report.devices:
        spec: dict[str, Any] = {
            "vid": f"{dev.vid:04x}",
            "pid": f"{dev.pid:04x}",
            "name": f"User device ({dev.vid:04x}:{dev.pid:04x})",
        }
        if dev.width and dev.height:
            spec["resolution"] = f"{dev.width}x{dev.height}"
        specs.append(spec)
    return specs


def load_device_specs(report_path: str | None = None) -> list[dict]:
    """Resolve device specs from a report file or ``dev/devices.json``.

    Returns an empty list when neither source is present — the real
    ``Platform.scan_devices`` then enumerates whatever's attached.
    """
    if report_path:
        return _specs_from_report(report_path)
    if DEVICES_JSON.exists():
        try:
            raw = json.loads(DEVICES_JSON.read_text())
            if isinstance(raw, list):
                return raw
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: bad devices.json: {e} — ignoring")
    return []


# ─── DevPaths / DevPlatform — production Platform with paths redirected ──────

def _build_dev_platform() -> Platform:
    """Return a Platform rooted at ``dev/.trcc/`` for the host OS.

    Subclasses the production OS Platform so real USB enumeration, real
    sensor reads, and real autostart all run against the dev box — only
    the filesystem layout is redirected.  That keeps mock_gui useful as
    a "drive the real GUI against my own hardware without polluting
    ~/.trcc" tool; for hardware-less work, ``tests.conftest.FakePlatform``
    is the right substitute (same Port).
    """
    from trcc.core.ports import Paths

    class DevPaths(Paths):
        """Paths port rooted at the dev tree.

        Subpaths (``theme_dir`` / ``cloud_theme_dir`` / etc.) inherit
        their concrete behaviour from the ABC, so we only override the
        four roots.
        """

        def config_dir(self) -> Path:
            return DEV_TRCC

        def data_dir(self) -> Path:
            return DEV_DATA

        def user_content_dir(self) -> Path:
            return DEV_USER

        def log_file(self) -> Path:
            return DEV_TRCC / "trcc.log"

    # Pick the host's production Platform impl as the base so USB +
    # sensors + autostart + hotplug all work the way the packaged app
    # does.  Override only ``paths()``.  Mirrors production launch
    # (ui/gui/__init__.launch + _boot) which builds via PlatformFactory.
    from trcc.adapters.system import PlatformFactory
    host = PlatformFactory.current()
    host_cls = type(host)

    class DevPlatform(host_cls):  # type: ignore[valid-type, misc]
        """Production host platform with paths redirected to ``dev/.trcc/``."""

        _dev_paths: Paths = DevPaths()

        def paths(self) -> Paths:
            return self._dev_paths

    return DevPlatform()


# ─── Main bootstrap ──────────────────────────────────────────────────────────

def bootstrap(report_path: str | None = None,
              verbosity: int = 0) -> Platform:
    """Wire up logging + paths and return a ``Platform`` rooted at
    ``dev/.trcc/``.

    The caller (``mock_gui.py`` / ``mock_cli.py`` / ``mock_api.py``)
    drives the rest of the composition root — same shape production
    uses, just with the dev platform instance.
    """
    from trcc.adapters.infra.logging import configure_logging

    platform = _build_dev_platform()
    configure_logging(
        platform.paths().log_file(),
        level=logging.DEBUG if verbosity >= 1 else logging.INFO,
    )
    log.info(
        "dev bootstrap: platform=%s paths.config=%s",
        type(platform).__name__, platform.paths().config_dir(),
    )

    # Hint about spec inputs.  Only informational — scan_devices is
    # the source of truth for what shows up in the GUI sidebar.
    specs = load_device_specs(report_path)
    if specs:
        print(f"Hint: {len(specs)} device spec(s) loaded from "
              f"{'--report' if report_path else 'devices.json'}; "
              "the GUI itself enumerates real attached hardware via "
              "Platform.scan_devices.")
    return platform
