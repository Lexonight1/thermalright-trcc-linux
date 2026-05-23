"""Shared bootstrap for dev/mock_{gui,cli,api}.py — debug entry points
that run the real GUI/CLI/API against MockPlatform via clean DI.

Three responsibilities:
  1. Patch paths so config + logs go to dev/.trcc/, not the user's ~/.trcc.
  2. Run a factory-registry preflight on the REAL protocol lambdas
     BEFORE MockPlatform's _register_noop_protocols overwrites them — so
     bugs like #133 (DeviceInfo data threading) surface as a startup
     gate, not as silent skips inside MockPlatform's noops.
  3. Build MockPlatform and return it. Caller (mock_gui/mock_cli/mock_api)
     pre-seeds the appropriate boot cache (`_boot.trcc(platform)` /
     `_boot.get_trcc(platform)`) — pure DI, no monkey-patching.

Cross-OS: only stdlib + project modules. The Linux-specific sensor
enumerator inside MockPlatform itself is pre-existing tech debt
(separate refactor).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.mock_platform import MockPlatform

# Make src/ and the repo root importable. Repo root lets `tests.mock_platform`
# resolve as a package — same import path used by `make_platform()` so both
# resolve to the SAME module object (critical for isinstance checks).
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

os.environ['TRCC_CONFIG_DIR'] = str(DEV_TRCC)


# ─── Device spec loading ─────────────────────────────────────────────────────

def _specs_from_report(report_path: str) -> list[dict]:
    """Parse a `trcc report` file into MockPlatform device specs.

    Lets the dev reproduce a user's exact device set from their bug report.
    """
    sys.path.insert(0, str(_REPO_ROOT / 'tools'))
    from diagnose import parse_report  # type: ignore[import-not-found]

    from trcc.core.models import LED_DEVICES

    text = Path(report_path).read_text()
    report = parse_report(text)

    if report.os_name:
        print(f"User OS: {report.os_name}")
    if report.trcc_version:
        print(f"User trcc version: {report.trcc_version}")

    specs: list[dict] = []
    for dev in report.devices:
        is_led = (dev.vid, dev.pid) in LED_DEVICES
        spec: dict[str, Any] = {
            "type": "led" if is_led else "lcd",
            "vid": f"{dev.vid:04x}",
            "pid": f"{dev.pid:04x}",
            "name": f"User {dev.protocol.upper()} ({dev.vid:04x}:{dev.pid:04x})",
        }
        if dev.pm:
            spec["pm"] = dev.pm
        if dev.sub:
            spec["sub"] = dev.sub
        if dev.width and dev.height:
            spec["resolution"] = f"{dev.width}x{dev.height}"
        specs.append(spec)

    if not specs:
        print("Warning: no devices found in report — using defaults")
        from tests.mock_platform import DEFAULT_DEVICES
        return list(DEFAULT_DEVICES)
    return specs


def load_device_specs(report_path: str | None = None) -> list[dict]:
    """Resolve device specs in this priority order:
        --report <file>  →  parsed from a user's trcc report
        dev/devices.json →  custom specs from disk
        DEFAULT_DEVICES  →  baked-in mix of LCDs + LEDs
    """
    if report_path:
        return _specs_from_report(report_path)
    if DEVICES_JSON.exists():
        try:
            specs = json.loads(DEVICES_JSON.read_text())
            if isinstance(specs, list) and specs:
                return specs
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: bad devices.json: {e} — using defaults")
    from tests.mock_platform import DEFAULT_DEVICES
    return list(DEFAULT_DEVICES)


# ─── Path patching (must happen before any trcc.* import that reads them) ────

def patch_paths() -> None:
    """Redirect all trcc paths to dev/.trcc — protects ~/.trcc from
    test runs, keeps user config untouched."""
    import trcc.conf as _conf_mod
    import trcc.core.paths as _paths

    _paths.USER_CONFIG_DIR = str(DEV_TRCC)
    _paths.USER_DATA_DIR = str(DEV_DATA)
    _paths.DATA_DIR = str(DEV_DATA)
    _paths.USER_CONTENT_DIR = str(DEV_USER)
    _paths.USER_CONTENT_DATA_DIR = str(DEV_USER / 'data')
    _paths.USER_MASKS_WEB_DIR = str(DEV_USER / 'data' / 'web')

    _conf_mod.CONFIG_DIR = str(DEV_TRCC)
    _conf_mod.CONFIG_PATH = str(DEV_TRCC / 'config.json')

    # Logging defaults to ~/.trcc/trcc.log — redirect to dev/.trcc/trcc.log
    from trcc.adapters.infra.diagnostics import StandardLoggingConfigurator
    StandardLoggingConfigurator.__init__.__defaults__ = (DEV_TRCC / 'trcc.log',)


# ─── Factory registry preflight (must run BEFORE MockPlatform installs) ──────

def preflight_factory_registry() -> None:
    """Verify every ALL_DEVICES entry constructs through its real factory
    lambda. Catches DeviceInfo data-threading bugs (#133/#131) before
    MockPlatform overwrites the registry with noops.

    Pure construction check — no I/O. Mirrors
    tests/adapters/device/test_factory.py for pytest-less developers.
    """
    from trcc.adapters.device.factory import DeviceProtocolFactory
    from trcc.core.models import ALL_DEVICES, DetectedDevice, DeviceInfo

    failures: list[tuple[str, str]] = []
    for (vid, pid), entry in ALL_DEVICES.items():
        label = f"{entry.protocol}:{vid:04x}:{pid:04x} ({entry.model})"
        try:
            detected = DetectedDevice(
                vid=vid, pid=pid,
                vendor_name=entry.vendor, product_name=entry.product,
                usb_path="usb:1:5",
                scsi_device="/dev/sg0" if entry.protocol == "scsi" else None,
                protocol=entry.protocol, device_type=entry.device_type,
                implementation=entry.implementation,
                button_image=entry.button_image, model=entry.model,
            )
            info = DeviceInfo.from_detected(detected)
            DeviceProtocolFactory.create_protocol(info)
        except Exception as e:
            failures.append((label, f"{type(e).__name__}: {e}"))

    if failures:
        print("\n  [PREFLIGHT FAILED] factory registry has broken lambdas:",
              file=sys.stderr)
        for label, err in failures:
            print(f"    {label} → {err}", file=sys.stderr)
        print("\n  Real bug — mock_* would have masked it (MockPlatform "
              "overwrites the registry with noops). Likely a missing "
              "DeviceInfo field. Fix before launching.", file=sys.stderr)
        sys.exit(1)
    print(f"[preflight OK] factory registry: {len(ALL_DEVICES)} devices "
          "construct cleanly")


# ─── Main bootstrap ──────────────────────────────────────────────────────────

def bootstrap(report_path: str | None = None) -> MockPlatform:
    """Set up the mock environment for any frontend (GUI/CLI/API):
        1. Patch paths to dev/.trcc/.
        2. Load device specs.
        3. Run preflight on the real factory registry.
        4. Build MockPlatform (which registers noop protocols on the
           DeviceProtocolFactory at construction time).
        5. Pre-seed the CLI/API boot caches with the mock-backed Trcc so
           every composition root picks it up via clean DI — no
           monkey-patching of `ControllerBuilder.for_current_os`.

    Returns the MockPlatform instance for callers that need direct access.
    """
    patch_paths()
    preflight_factory_registry()

    specs = load_device_specs(report_path)
    print(f"Devices: {len(specs)}")
    for i, spec in enumerate(specs):
        dtype = spec.get('type', 'lcd')
        name = spec.get('name', f'Device {i}')
        detail = spec.get('resolution', '') or spec.get('model', '')
        print(f"  [{i}] {dtype.upper()} {name} {detail}")

    from tests.mock_platform import MockPlatform
    platform = MockPlatform(specs, root=DEV_TRCC)

    return platform

    return platform
