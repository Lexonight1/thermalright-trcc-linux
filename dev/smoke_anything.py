#!/usr/bin/env python3
"""Plug-in OS + device → see our bad code.

Parameterized stress harness.  Pick an OS, pick a device (or ``all``),
and the harness runs a battery of probes through the fully DI'd stack —
Platform → ControllerBuilder → Protocol → Transport.  Each probe is a
real-bug class we've already paid for; if any probe REPRODUCES, that's
a code path that needs fixing.

Probes today cover (rotation/cache, geometry, video target dims, sensor
discovery, device-info shape, lifecycle idempotency, send-before-handshake)
and are easy to add to — drop a function in ``PROBES`` with a one-line
description and it runs in every future invocation.

Usage::

    PYTHONPATH=src python3 dev/smoke_anything.py
    PYTHONPATH=src python3 dev/smoke_anything.py --os linux --device 87ad:70db
    PYTHONPATH=src python3 dev/smoke_anything.py --device all
    PYTHONPATH=src python3 dev/smoke_anything.py --probe video.target.zero
    PYTHONPATH=src python3 dev/smoke_anything.py --list-probes

Flags:

    --os        linux | windows | macos | bsd  (default: linux)
                Instantiates the matching Platform subclass.  If the
                target OS can't be imported on this host (e.g. winreg
                on Linux), the harness reports the import failure and
                skips OS-specific probes.

    --device    VID:PID hex pair (e.g. 87ad:70db) or ``all``  (default: all)
                Limits the matrix to the chosen entry from ALL_DEVICES.

    --probe     Probe name (see --list-probes)  (default: all)

    --verbose   Print each probe's full traceback when it fails.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

# Status grades:
#   PASS   — probe ran, behavior is correct.
#   BAD    — probe reproduced a real code defect.  Fix needed.
#   ERROR  — probe itself blew up (smoke bug, not a TRCC bug).  Triage.
#   SKIP   — probe doesn't apply to this OS/device combination.

PASS, BAD, ERROR, SKIP = "PASS", "BAD", "ERROR", "SKIP"


@dataclass(slots=True, frozen=True)
class ProbeResult:
    status: str
    detail: str


def _ok(detail: str) -> ProbeResult: return ProbeResult(PASS, detail)
def _bad(detail: str) -> ProbeResult: return ProbeResult(BAD, detail)
def _err(detail: str) -> ProbeResult: return ProbeResult(ERROR, detail)
def _skip(detail: str) -> ProbeResult: return ProbeResult(SKIP, detail)


# ─────────────────────────────────────────────────────────────────────────────
# OS injection
# ─────────────────────────────────────────────────────────────────────────────

def _make_platform(os_label: str):
    """Instantiate the requested Platform subclass.

    Returns (platform, error_str | None).  On import failure (e.g. winreg
    on Linux) returns (None, error_str) so the caller can fall back.
    """
    matrix = {
        "linux": ("trcc.adapters.system.linux_platform", "LinuxPlatform"),
        "windows": ("trcc.adapters.system.windows_platform", "WindowsPlatform"),
        "macos": ("trcc.adapters.system.macos_platform", "MacOSPlatform"),
        "bsd": ("trcc.adapters.system.bsd_platform", "BSDPlatform"),
    }
    if os_label not in matrix:
        return None, f"unknown OS {os_label!r}"

    module_name, cls_name = matrix[os_label]
    try:
        import importlib
        mod = importlib.import_module(module_name)
        cls = getattr(mod, cls_name)
        return cls(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Probes
# ─────────────────────────────────────────────────────────────────────────────
#
# Each probe takes (platform, device_entry) and returns ProbeResult.
# device_entry is from ALL_DEVICES — has .protocol, .fbl, .device_type, etc.

def probe_video_target_zero(_platform, _device) -> ProbeResult:
    """VideoDecoder must guard against zero-dim target_size.

    Caught #136 questist: ``range() arg 3 must not be zero`` when bulk
    handshake didn't extract PM and the device resolution collapsed to
    a zero dimension.  ``frame_size = w * h * 3 == 0`` then ``range(...,
    frame_size)`` raises.
    """
    from unittest.mock import patch
    from trcc.adapters.infra.media_player import VideoDecoder

    def _fake_run(*_a, **_k):
        class _R:
            returncode = 0
            stdout = b'\x00' * (480 * 480 * 3)
            stderr = b''
        return _R()

    try:
        with patch('trcc.adapters.infra.media_player.subprocess.run',
                   side_effect=_fake_run):
            VideoDecoder("/tmp/x.mp4", target_size=(0, 480), fit_mode='fill')
        return _bad("VideoDecoder accepted target_size=(0,480) silently — "
                    "should raise on non-positive dimension")
    except ValueError as e:
        msg = str(e)
        if "range()" in msg and "zero" in msg:
            return _bad("range() arg 3 must not be zero — no guard on zero-dim target")
        if "non-positive dimension" in msg or "target_size" in msg:
            return _ok("VideoDecoder rejects zero-dim target with clear ValueError")
        return _err(f"unexpected ValueError: {e}")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_video_target_portrait(_platform, _device) -> ProbeResult:
    """Portrait target dimensions decode without crash or aspect collapse."""
    from unittest.mock import patch
    from trcc.adapters.infra.media_player import VideoDecoder

    def _fake_run(*_a, **_k):
        class _R:
            returncode = 0
            stdout = b'\x00' * (320 * 480 * 3)  # one frame at 320x480
            stderr = b''
        return _R()

    try:
        with patch('trcc.adapters.infra.media_player.subprocess.run',
                   side_effect=_fake_run):
            d = VideoDecoder("/tmp/x.mp4", target_size=(320, 480), fit_mode='fill')
        if d.frame_count == 0:
            return _bad("portrait 320x480 decoded zero frames — pipeline drops portrait")
        return _ok(f"portrait 320x480 → {d.frame_count} frame(s)")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_deviceinfo_usb_address(_platform, _device) -> ProbeResult:
    """DeviceInfo must carry a ``usb_address`` field.

    Caught #131 lallemandgianni / #130 juanito54jm:
    ``'DeviceInfo' object has no attribute 'addr'`` on v9.5.0/v9.5.2
    (the field was missing for the LED protocol path). Renamed to
    ``usb_address`` in Phase 2 with the conversion chokepoint locked
    in ``DeviceInfo.from_detected``.
    """
    from trcc.core.models import DetectedDevice, DeviceInfo
    detected = DetectedDevice(
        vid=0x0416, pid=0x8001,
        vendor_name="Mock", product_name="AX120",
        usb_path="usb:1:5", scsi_device=None,
        protocol="led", device_type=1,
        implementation="hid_led", model="AX120", button_image="",
    )
    try:
        info = DeviceInfo.from_detected(detected)
        _ = info.usb_address
        return _ok(f"DeviceInfo.usb_address = {info.usb_address}")
    except AttributeError as e:
        return _bad(f"AttributeError on DeviceInfo.usb_address: {e}")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_rapl_permission(platform, _device) -> ProbeResult:
    """Linux RAPL discovery handles PermissionError silently.

    Caught #139 Zombie-hive: pipx install on Pop!_OS without
    ``trcc setup-udev`` had ``Path.exists()`` raising PermissionError on
    ``/sys/class/powercap/intel-rapl:*/energy_uj`` and the GUI launch
    crashed.  Fix added try/except guards in linux_sensors._discover_rapl.
    """
    from unittest.mock import patch
    if not _is_linux_platform(platform):
        return _skip("RAPL is Linux-only")

    from trcc.adapters.system.linux_sensors import SensorEnumerator

    def _denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    try:
        enum = SensorEnumerator()
        with patch.object(Path, 'exists', side_effect=_denied), \
             patch.object(Path, 'glob', side_effect=_denied):
            enum._discover_rapl()
        return _ok("_discover_rapl swallowed PermissionError")
    except (PermissionError, OSError) as e:
        return _bad(f"_discover_rapl crashed on permission denial: {e}")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_canvas_size_stable(_platform, device) -> ProbeResult:
    """Repeated fbl_to_resolution calls return the same value.

    Caught #137 satoru8 territory: cache-stale on rotation/handshake
    re-reads.  v9.5.4+ fix should make every lookup deterministic.
    """
    from trcc.core.models import fbl_to_resolution
    a = fbl_to_resolution(device.fbl, 0)
    b = fbl_to_resolution(device.fbl, 0)
    if a != b:
        return _bad(f"FBL={device.fbl} drifted: {a} → {b}")
    if a[0] == 0 or a[1] == 0:
        return _bad(f"FBL={device.fbl} resolved to zero-dim {a}")
    return _ok(f"FBL={device.fbl} → {a} (stable)")


def probe_handshake_idempotent(_platform, device) -> ProbeResult:
    """Calling handshake() twice in a row returns the same resolution."""
    proto = _make_protocol(device)
    if proto is None:
        return _skip(f"protocol={device.protocol} not wired in this harness")
    try:
        first = proto.handshake()
        second = proto.handshake()
    except Exception as e:
        return _err(f"handshake raised: {type(e).__name__}: {e}")
    finally:
        proto.close()

    if first is None or second is None:
        return _bad(f"handshake returned None (1st={first}, 2nd={second})")
    if device.protocol == "led":
        if first.model_id != second.model_id:
            return _bad(f"LED model_id drift: {first.model_id} → {second.model_id}")
    else:
        if first.resolution != second.resolution:
            return _bad(f"resolution drift: {first.resolution} → {second.resolution}")
    return _ok("two consecutive handshakes returned identical results")


def probe_windows_wmi_coinit(_platform, _device) -> ProbeResult:
    """Every WMI call site initializes COM for its thread.

    Caught #131 lallemandgianni: ``wmi.x_wmi_uninitialised_thread`` on
    ``trcc detect`` because ``wmi.WMI()`` was called from a worker thread
    without ``pythoncom.CoInitialize()`` first.  Reporter even submitted a
    fix.  This probe statically scans every Windows-specific source file,
    finds each ``wmi.WMI()`` call site, and asserts that
    ``pythoncom.CoInitialize`` appears in the same module.
    """
    import re
    src_root = _REPO / "src" / "trcc"
    helper = "_windows_wmi.py"
    bad_files: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == helper:
            continue
        if "next" in path.parts:
            continue  # next/ is a separate rebuild tree
        text = path.read_text()
        if re.search(r"\bwmi\.WMI\s*\(", text):
            bad_files.append(path.relative_to(_REPO).as_posix())
    if bad_files:
        return _bad(
            "wmi.WMI(...) called outside _windows_wmi.wmi_handle helper: "
            + ", ".join(bad_files)
        )
    return _ok("all WMI calls go through _windows_wmi.wmi_handle()")


def probe_close_then_send(_platform, device) -> ProbeResult:
    """Close + handshake + send (sleep/resume cycle, Tee86 #144 territory)."""
    proto = _make_protocol(device)
    if proto is None:
        return _skip(f"protocol={device.protocol} not wired in this harness")
    try:
        first = proto.handshake()
        if first is None:
            return _err("first handshake returned None")
        proto.close()
        second = proto.handshake()
        if second is None:
            return _bad("post-close handshake returned None")
        if device.protocol == "led":
            return _ok("LED close+re-handshake cycle clean (no send_data probe)")
        w, h = second.resolution if second.resolution else (0, 0)
        if w == 0 or h == 0:
            return _bad(f"post-close handshake resolution {second.resolution}")
        sent = proto.send_data(b'\x00' * (w * h * 2), w, h)
        if not sent:
            return _bad("post-close send_data returned False")
        return _ok("close → handshake → send_data clean")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")
    finally:
        proto.close()


# ─────────────────────────────────────────────────────────────────────────────
# Probe wiring helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_linux_platform(platform) -> bool:
    return platform is not None and "Linux" in type(platform).__name__


def _make_protocol(device):
    """Build a real Protocol for ``device`` with noop transports wired up.

    Returns the protocol instance or None if the protocol isn't wired.
    """
    from noop_transports import (  # type: ignore[import-not-found]
        NoopBulkLikeDevice, NoopScsiTransport, NoopUsbTransport,
        build_hid_type2_response, build_hid_type3_response,
        build_led_response,
    )
    # factory must import before BulkProtocol/LyProtocol — it registers all
    # protocol subclasses at the bottom of its module body, so importing the
    # subclass modules first triggers the partial-init circular ImportError.
    from trcc.adapters.device.factory import DeviceProtocolFactory
    from trcc.adapters.device.bulk_protocol import BulkProtocol
    from trcc.adapters.device.ly_protocol import LyProtocol
    from trcc.core.models import DetectedDevice, DeviceInfo, fbl_to_resolution

    proto_name = device.protocol

    if proto_name == "scsi":
        DeviceProtocolFactory.set_scsi_transport(
            lambda *_a, **_k: NoopScsiTransport(fbl=device.fbl)
        )
    elif proto_name == "hid":
        resp = (build_hid_type3_response(fbl=device.fbl)
                if device.device_type == 3
                else build_hid_type2_response(pm=32, sub=0))
        DeviceProtocolFactory.create_usb_transport = staticmethod(  # type: ignore[method-assign]
            lambda vid, pid, *, usb_address=None: NoopUsbTransport(resp)
        )
    elif proto_name == "led":
        DeviceProtocolFactory.create_usb_transport = staticmethod(  # type: ignore[method-assign]
            lambda vid, pid, *, usb_address=None: NoopUsbTransport(
                build_led_response(pm=32, sub=0))
        )
    elif proto_name in ("bulk", "ly"):
        cls = BulkProtocol if proto_name == "bulk" else LyProtocol
        resolution = fbl_to_resolution(device.fbl, 32)
        cls._make_device = staticmethod(  # type: ignore[method-assign]
            lambda vid, pid, *, usb_address=None: NoopBulkLikeDevice(
                vid, pid, usb_address=usb_address, resolution=resolution)
        )
    else:
        return None

    DeviceProtocolFactory._protocols.clear()

    detected = DetectedDevice(
        vid=device.vid if hasattr(device, 'vid') else 0,
        pid=device.pid if hasattr(device, 'pid') else 0,
        vendor_name=device.vendor, product_name=device.product,
        usb_path=f"usb:{0xffff & getattr(device, 'vid', 0):04x}",
        scsi_device="/dev/sg0" if proto_name == "scsi" else None,
        protocol=proto_name, device_type=device.device_type,
        implementation=device.implementation,
        model=device.model, button_image=device.button_image,
    )
    info = DeviceInfo.from_detected(detected)
    return DeviceProtocolFactory.create_protocol(info)


# ─────────────────────────────────────────────────────────────────────────────
# Probe registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class Probe:
    name: str
    description: str
    runner: Callable[[Any, Any], ProbeResult]


PROBES: list[Probe] = [
    Probe("video.target.zero",
          "VideoDecoder guards against zero-dim target_size",
          probe_video_target_zero),
    Probe("video.target.portrait",
          "VideoDecoder handles portrait dimensions",
          probe_video_target_portrait),
    Probe("model.deviceinfo.usb_address",
          "DeviceInfo carries the usb_address field for non-SCSI devices",
          probe_deviceinfo_usb_address),
    Probe("sensors.rapl.permission",
          "RAPL discovery survives PermissionError on /sys",
          probe_rapl_permission),
    Probe("geometry.canvas_size.stable",
          "fbl_to_resolution returns deterministic results across re-reads",
          probe_canvas_size_stable),
    Probe("device.handshake.idempotent",
          "handshake() returns same value across repeated calls",
          probe_handshake_idempotent),
    Probe("device.close_then_send",
          "close → handshake → send (sleep/resume cycle)",
          probe_close_then_send),
    Probe("windows.wmi.coinit",
          "every WMI call site has pythoncom.CoInitialize in scope (#131)",
          probe_windows_wmi_coinit),
]


# ─────────────────────────────────────────────────────────────────────────────
# Device selection
# ─────────────────────────────────────────────────────────────────────────────

def _parse_vid_pid(spec: str) -> tuple[int, int]:
    parts = spec.split(":")
    if len(parts) != 2:
        raise ValueError(f"--device {spec!r} not in VID:PID form")
    return int(parts[0], 16), int(parts[1], 16)


def _select_devices(spec: str):
    from trcc.core.models import ALL_DEVICES
    items = sorted(ALL_DEVICES.items())
    if spec == "all":
        return [(vp, e) for vp, e in items]
    vid, pid = _parse_vid_pid(spec)
    if (vid, pid) not in ALL_DEVICES:
        raise SystemExit(
            f"device {vid:04x}:{pid:04x} not in registry — known devices:\n  "
            + "\n  ".join(f"{v:04x}:{p:04x} {e.product}" for (v, p), e in items)
        )
    return [((vid, pid), ALL_DEVICES[(vid, pid)])]


# ─────────────────────────────────────────────────────────────────────────────
# trcc report parser
# ─────────────────────────────────────────────────────────────────────────────

# Distro string → which Platform subclass to ask the harness to instantiate.
# Match by substring; first hit wins.  "linux" is the catch-all default.
_DISTRO_TO_OS: tuple[tuple[str, str], ...] = (
    ("windows",  "windows"),
    ("macos",    "macos"),
    ("darwin",   "macos"),
    ("freebsd",  "bsd"),
    ("openbsd",  "bsd"),
    ("netbsd",   "bsd"),
    ("dragonfly", "bsd"),
)


def _parse_report(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Pull (os_label, [(vid, pid), ...]) out of a ``trcc report`` dump.

    The diagnostics tool emits a Version section with ``Distro:`` and an
    ``lsusb (filtered)`` section like::

        Bus 002 Device 003: ID 87ad:70db ChiZhu Tech ...

    We grab the first Distro line and every VID:PID-shaped pair we see.
    Anything ambiguous defaults to ``linux``.
    """
    import re

    distro_match = re.search(r"Distro:\s*(.+)", text)
    distro = (distro_match.group(1).strip().lower() if distro_match else "")
    os_label = "linux"
    for needle, label in _DISTRO_TO_OS:
        if needle in distro:
            os_label = label
            break

    # VID:PID — accept ``ID 87ad:70db`` (lsusb form) and bare ``87ad:70db`` lines.
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in re.finditer(r"\b([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\b", text):
        try:
            vp = (int(match.group(1), 16), int(match.group(2), 16))
        except ValueError:
            continue
        if vp not in seen:
            seen.add(vp)
            pairs.append(vp)

    return os_label, pairs


def _select_devices_from_report(report_path: Path):
    """Run probes against every registered device the report mentions."""
    from trcc.core.models import ALL_DEVICES

    text = report_path.read_text(errors="replace")
    os_label, pairs = _parse_report(text)
    matched = [(vp, ALL_DEVICES[vp]) for vp in pairs if vp in ALL_DEVICES]

    if not matched:
        raise SystemExit(
            f"no registered devices found in {report_path}.\n"
            f"  parsed VID:PIDs: {[f'{v:04x}:{p:04x}' for v, p in pairs] or '(none)'}\n"
            "  → not in the device registry?  Pass --device manually."
        )
    return os_label, matched


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

_COLOR = {
    PASS:  "\033[32m",
    BAD:   "\033[31m",
    ERROR: "\033[33m",
    SKIP:  "\033[90m",
}
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _list_probes() -> int:
    print(f"{_BOLD}Available probes:{_RESET}\n")
    for p in PROBES:
        print(f"  {p.name:<32}  {p.description}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Plug-in OS + device → see our bad code.",
    )
    p.add_argument("--os", default="linux",
                   choices=("linux", "windows", "macos", "bsd"))
    p.add_argument("--device", default="all",
                   help="VID:PID hex pair (e.g. 87ad:70db) or 'all'")
    p.add_argument("--from-report", type=Path, default=None,
                   help="path to a `trcc report` dump — overrides --os and "
                        "--device with the Distro and VID:PIDs found inside")
    p.add_argument("--probe", default=None,
                   help="run only the named probe (see --list-probes)")
    p.add_argument("--list-probes", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    if args.list_probes:
        return _list_probes()

    if args.from_report:
        os_label, devices = _select_devices_from_report(args.from_report)
        print(f"{_BOLD}from-report:{_RESET} {args.from_report}")
        print(f"  → os={os_label}, devices={[f'{v:04x}:{p:04x}' for (v, p), _ in devices]}\n")
    else:
        os_label = args.os
        devices = _select_devices(args.device)

    platform, plat_err = _make_platform(os_label)
    if plat_err:
        print(f"{_COLOR[ERROR]}OS load failed{_RESET}: --os {os_label} → {plat_err}")
        print("  → continuing with platform=None; OS-specific probes will SKIP.\n")
    probes = [pr for pr in PROBES if args.probe in (None, pr.name)]
    if args.probe and not probes:
        raise SystemExit(f"unknown probe {args.probe!r} — see --list-probes")

    print(f"{_BOLD}TRCC any-OS-any-device smoke{_RESET}")
    print(f"  OS     : {os_label} ({type(platform).__name__ if platform else 'load failed'})")
    print(f"  devices: {len(devices)}")
    print(f"  probes : {len(probes)}\n")

    counts = {PASS: 0, BAD: 0, ERROR: 0, SKIP: 0}
    bad_rows: list[tuple[str, str, str]] = []

    for (vid, pid), entry in devices:
        # Each probe wants device.vid/.pid too — slot them on (frozen-friendly
        # since DeviceProfile is whatever the registry returns).
        device = entry  # noqa: F841 — alias for readability
        device_label = f"{vid:04x}:{pid:04x} {entry.product} ({entry.protocol.upper()})"
        print(f"{_BOLD}Device:{_RESET} {device_label}")

        # Inject vid/pid onto entry view for probes (read-only access).
        device_view = _DeviceView(entry, vid, pid)

        for probe in probes:
            try:
                result = probe.runner(platform, device_view)
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                if args.verbose:
                    detail += "\n" + traceback.format_exc()
                result = _err(detail)
            color = _COLOR[result.status]
            print(f"  {color}{result.status:<6}{_RESET} {probe.name:<32}  {result.detail}")
            counts[result.status] += 1
            if result.status == BAD:
                bad_rows.append((device_label, probe.name, result.detail))
        print()

    print("=" * 76)
    line = (f"  PASS={counts[PASS]:<3}  "
            f"{_COLOR[BAD]}BAD={counts[BAD]:<3}{_RESET}  "
            f"ERROR={counts[ERROR]:<3}  "
            f"{_COLOR[SKIP]}SKIP={counts[SKIP]:<3}{_RESET}")
    print(line)
    if bad_rows:
        print()
        print(f"{_COLOR[BAD]}Bad code surfaced:{_RESET}")
        for dev, probe_name, detail in bad_rows:
            print(f"  • {dev} → {probe_name}: {detail}")
    print("=" * 76)
    return 1 if counts[BAD] else 0


@dataclass(slots=True, frozen=True)
class _DeviceView:
    """Wraps a DeviceProfile entry with vid/pid attached for probes."""
    _entry: Any
    vid: int
    pid: int

    @property
    def protocol(self): return self._entry.protocol
    @property
    def fbl(self): return self._entry.fbl
    @property
    def device_type(self): return self._entry.device_type
    @property
    def vendor(self): return self._entry.vendor
    @property
    def product(self): return self._entry.product
    @property
    def implementation(self): return self._entry.implementation
    @property
    def model(self): return self._entry.model
    @property
    def button_image(self): return self._entry.button_image


if __name__ == "__main__":
    raise SystemExit(main())
