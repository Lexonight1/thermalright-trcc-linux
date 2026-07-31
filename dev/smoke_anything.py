#!/usr/bin/env python3
"""Plug-in OS + device → see our bad code.

Parameterized stress harness and the engine behind the diagnostic loop
(see ``memory/project_diagnostic_loop``): a user downloads the app, it
"just works"; if it doesn't, they paste a ``trcc report`` into a GitHub
issue, and we run ::

    PYTHONPATH=src python3 dev/smoke_anything.py --from-report report.txt

to reproduce the failing path against the *real* composition stack —
``Platform`` → ``DEVICES[wire]`` → ``Device`` → a scripted
transport — and print "Bad code surfaced: <device> → <probe>: <detail>".

Each probe is a real-bug class we've already paid for.  If any probe
REPRODUCES, that's a code path that needs fixing.  Probes today cover
video decode geometry, factory wire-coverage, sensor-read permission
resilience, FBL geometry stability, handshake idempotency, sleep/resume
cycles, and the Windows COM-init invariant.  Drop a function in
``PROBES`` to add one — it runs in every future invocation.

Architecture note: devices are built exactly how the composition root
(``App.attach``) builds them — ``DEVICES[info.wire]`` picks
the class, a DI'd transport feeds it.  The only difference is the
transport is a ``tests/conftest`` fake whose ``read_script`` we prime
with a synthetic handshake.  Per-wire handshake shapes come from one
``WireDriver`` Strategy dict keyed by the same ``Wire`` enum the
production factory dispatches on — a new wire is one new row here, same
as it is one new ``class X(BaseDevice, wire=...)`` in the adapter layer.

Usage::

    PYTHONPATH=src python3 dev/smoke_anything.py
    PYTHONPATH=src python3 dev/smoke_anything.py --os linux --device 87ad:70db
    PYTHONPATH=src python3 dev/smoke_anything.py --device all
    PYTHONPATH=src python3 dev/smoke_anything.py --probe video.size.zero
    PYTHONPATH=src python3 dev/smoke_anything.py --from-report report.txt
    PYTHONPATH=src python3 dev/smoke_anything.py --list-probes

Flags:

    --os        linux | windows | macos | bsd  (default: linux)
                Instantiates the matching Platform subclass.  If the
                target OS can't be imported on this host (e.g. winreg
                on Linux), the harness reports the import failure and
                runs with platform=None; OS-specific probes SKIP.

    --device    VID:PID hex pair (e.g. 87ad:70db) or ``all``  (default: all)
                Limits the matrix to the chosen entry from the registry.

    --from-report  Path to a ``trcc report`` dump.  Overrides --os and
                --device with the system + VID:PIDs parsed from it.

    --probe     Probe name (see --list-probes)  (default: all)

    --verbose   Print each probe's full traceback when it ERRORs.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
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

# Real composition-layer types — the harness exercises these, not a
# parallel shim.  Imported at module scope so a missing ``src`` on the
# path fails fast and loud rather than mid-probe.
from trcc.core.models import ProductInfo, Wire
from trcc.core.registry import ALL_DEVICES

# Synthetic-handshake magics + sizes referenced (never copied) from the
# device modules, so a magic-byte change there can't silently rot this
# harness.
from trcc.adapters.device.hid_lcd import _TYPE2_MAGIC, _TYPE3_ACK_SIZE
from trcc.adapters.device.led import (
    _HID_REPORT_SIZE as _LED_REPORT_SIZE,
    _MAGIC as _LED_MAGIC,
)

# Transport fakes — the canonical ones the test suite drives connect()
# through.  ``tests/`` is on the path above.
from conftest import (  # type: ignore[import-not-found]
    FakeBulkTransport,
    FakeScsiTransport,
)


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
        "linux": ("trcc.adapters.system.linux", "LinuxPlatform"),
        "windows": ("trcc.adapters.system.windows", "WindowsPlatform"),
        "macos": ("trcc.adapters.system.macos", "MacOSPlatform"),
        "bsd": ("trcc.adapters.system.bsd", "BSDPlatform"),
    }
    if os_label not in matrix:
        return None, f"unknown OS {os_label!r}"

    module_name, cls_name = matrix[os_label]
    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, cls_name)
        return cls(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _is_linux_platform(platform) -> bool:
    return platform is not None and "Linux" in type(platform).__name__


def _is_windows_platform(platform) -> bool:
    return platform is not None and "Windows" in type(platform).__name__


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic handshake builders — byte shapes mirror the geometry tests
# ─────────────────────────────────────────────────────────────────────────────
#
# These reproduce exactly what a device reports at handshake so connect()
# parses a real PM/FBL.  Shapes are copied from the passing geometry
# tests (test_{scsi,hid,bulk,ly}_lcd_geometry / test_led_send), kept in
# sync by referencing the magic constants from the device modules.

_SCSI_POLL_SIZE = 0xE100


def _scsi_handshake(fbl: int) -> bytes:
    """SCSI poll response — FBL byte at offset 0."""
    resp = bytearray(_SCSI_POLL_SIZE)
    resp[0] = fbl
    return bytes(resp)


def _hid_type2_handshake(pm: int = 32, sub: int = 0) -> bytes:
    """HID Type 2 ("H" variant) handshake — magic + PM/SUB, resp[12]=1."""
    resp = bytearray(512)
    resp[0:4] = _TYPE2_MAGIC
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01   # required by the validator
    return bytes(resp)


def _hid_type3_handshake(fbl_indicator: int = 0x65) -> bytes:
    """HID Type 3 ("ALi") handshake — validator wants resp[0] in {0x65, 0x66}."""
    resp = bytearray(1024)
    resp[0] = fbl_indicator
    return bytes(resp)


def _bulk_handshake(pm: int = 32, sub: int = 0) -> bytes:
    """USBLCDNew bulk handshake — PM at resp[24] (must be != 0), SUB at resp[36]."""
    resp = bytearray(1024)
    resp[24] = pm
    resp[36] = sub
    return bytes(resp)


def _ly_handshake(resp20: int = 1, resp22: int = 0, resp36: int = 0) -> bytes:
    """LY handshake — validator wants resp[0]=3, resp[1]=0xFF, resp[8]=1."""
    resp = bytearray(64)
    resp[0] = 3
    resp[1] = 0xFF
    resp[8] = 1
    resp[20] = resp20
    resp[22] = resp22
    resp[36] = resp36
    return bytes(resp)


def _led_handshake(pm: int = 32, sub: int = 0) -> bytes:
    """LED HID handshake — magic + PM/SUB, resp[12]=1."""
    resp = bytearray(_LED_REPORT_SIZE)
    resp[0:4] = _LED_MAGIC
    resp[4] = sub
    resp[5] = pm
    resp[12] = 1
    return bytes(resp)


def _hid_type3_ack() -> bytes:
    """HID Type 3 per-frame ACK — ``send()`` reads this off EP_READ and
    treats any non-empty response as success.  Real hardware returns it;
    the fake transport must too, or the send path can't be exercised."""
    return b"\x01" * _TYPE3_ACK_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# Wire drivers — one Strategy per Wire, dispatched by the same enum the
# production DEVICES registry uses.  Each knows how to make the right fake
# transport and prime ONE handshake onto its read_script.
# ─────────────────────────────────────────────────────────────────────────────

def _new_scsi_transport() -> FakeScsiTransport:
    return FakeScsiTransport()


def _new_bulk_transport() -> FakeBulkTransport:
    return FakeBulkTransport()


def _prime_scsi(transport: Any, info: ProductInfo) -> None:
    transport.read_script.append(_scsi_handshake(info.fbl or 100))


def _prime_hid(transport: Any, info: ProductInfo) -> None:
    resp = (_hid_type3_handshake() if info.device_type == 3
            else _hid_type2_handshake())
    transport.read_script.append(resp)


def _prime_bulk(transport: Any, _info: ProductInfo) -> None:
    transport.read_script.append(_bulk_handshake())


def _prime_ly(transport: Any, _info: ProductInfo) -> None:
    transport.read_script.append(_ly_handshake())


def _prime_led(transport: Any, _info: ProductInfo) -> None:
    transport.read_script.append(_led_handshake())


def _noop_send_prime(_transport: Any, _info: ProductInfo) -> None:
    """Most wires' ``send()`` only writes — nothing to script."""


def _prime_hid_send(transport: Any, info: ProductInfo) -> None:
    """HID Type 3 ``send()`` reads a per-frame ACK; script one."""
    if info.device_type == 3:
        transport.read_script.append(_hid_type3_ack())


@dataclass(slots=True, frozen=True)
class WireDriver:
    """Build + prime a fake-transport handshake for one Wire family.

    ``make_transport`` returns the fake the wire's ``Device`` subclass
    reads through; ``prime`` appends exactly one synthetic handshake to
    its ``read_script`` (call once per ``connect()``); ``prime_send``
    appends whatever reads that wire's ``send()`` consumes (default
    none — only HID Type 3 reads a per-frame ACK).
    """
    make_transport: Callable[[], Any]
    prime: Callable[[Any, ProductInfo], None]
    prime_send: Callable[[Any, ProductInfo], None] = _noop_send_prime


_WIRE_DRIVERS: dict[Wire, WireDriver] = {
    Wire.SCSI: WireDriver(_new_scsi_transport, _prime_scsi),
    Wire.HID:  WireDriver(_new_bulk_transport, _prime_hid, _prime_hid_send),
    Wire.BULK: WireDriver(_new_bulk_transport, _prime_bulk),
    Wire.LY:   WireDriver(_new_bulk_transport, _prime_ly),
    Wire.LED:  WireDriver(_new_bulk_transport, _prime_led),
}


def _build_device(info: ProductInfo):
    """Construct a Device the way the composition root does.

    Returns ``(device, transport, driver)`` or ``None`` if the wire has
    no driver wired in this harness.  The transport is handed back so the
    probe can re-prime it before each ``connect()``.
    """
    driver = _WIRE_DRIVERS.get(info.wire)
    if driver is None:
        return None
    from trcc.adapters.device import DEVICES  # fires @register imports

    transport = driver.make_transport()
    device_cls = DEVICES[info.wire]
    device = device_cls(info, transport)
    return device, transport, driver


# ─────────────────────────────────────────────────────────────────────────────
# Probes
# ─────────────────────────────────────────────────────────────────────────────
#
# Each probe takes (platform, info) and returns ProbeResult.  ``info`` is
# the ProductInfo registry entry — it carries .wire / .fbl / .device_type
# / .key / .vid / .pid / .product directly, so no wrapper view is needed.


def probe_video_size_zero(_platform, _info) -> ProbeResult:
    """VideoDecoder must guard against a zero dimension in ``size``.

    Caught #136 questist territory.  ``_FRAME_SIZE_RGB24(0, h) == 0`` then
    ``len(raw) % 0`` raises ZeroDivisionError — there is no positive-dim
    guard on ``size`` today, so this reproduces a live latent crash.
    """
    from unittest.mock import patch

    from trcc.services.media import ThemeError, VideoDecoder

    def _fake_run(*_a, **_k):
        class _R:
            returncode = 0
            stdout = b""
            stderr = b""
        return _R()

    try:
        with patch("trcc.services.media.subprocess.run", side_effect=_fake_run), \
             patch("trcc.services.media._ffmpeg_available", return_value=True), \
             patch.object(Path, "exists", return_value=True):
            VideoDecoder(Path("/tmp/x.mp4"), size=(0, 480)).decode()
        return _bad("VideoDecoder accepted size=(0,480) silently — "
                    "no positive-dimension guard")
    except ZeroDivisionError:
        return _bad("ZeroDivisionError on size=(0,480): len(raw) % frame_size "
                    "with frame_size=0 — no zero-dim guard on size")
    except (ValueError, ThemeError) as e:
        return _ok(f"VideoDecoder rejects zero-dim size cleanly: {e}")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_video_size_portrait(_platform, _info) -> ProbeResult:
    """Portrait ``size`` decodes the expected frame count without collapse."""
    from unittest.mock import patch

    from trcc.services.media import VideoDecoder

    def _fake_run(*_a, **_k):
        class _R:
            returncode = 0
            stdout = b"\x00" * (320 * 480 * 3)  # one 320x480 RGB24 frame
            stderr = b""
        return _R()

    try:
        with patch("trcc.services.media.subprocess.run", side_effect=_fake_run), \
             patch("trcc.services.media._ffmpeg_available", return_value=True), \
             patch.object(Path, "exists", return_value=True):
            frames = VideoDecoder(Path("/tmp/x.mp4"), size=(320, 480)).decode()
        if len(frames) == 0:
            return _bad("portrait 320x480 decoded zero frames — pipeline drops portrait")
        return _ok(f"portrait 320x480 → {len(frames)} frame(s)")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def probe_factory_resolves(_platform, info: ProductInfo) -> ProbeResult:
    """Every registry wire resolves to a concrete Device subclass.

    The OCP invariant: a registry row's ``wire`` must dispatch through
    ``DEVICES[wire]`` to a real ``Device``.  A registry entry
    for a wire nobody registered would crash ``App.attach`` at runtime —
    this catches it statically across the whole registry.
    """
    from trcc.adapters.device import DEVICES
    from trcc.core.errors import DeviceNotFoundError
    from trcc.core.ports import Device

    try:
        cls = DEVICES[info.wire]
    except DeviceNotFoundError:
        return _bad(f"wire={info.wire.value} has no registered Device subclass "
                    "— App.attach would crash for this product")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")

    if not (isinstance(cls, type) and issubclass(cls, Device)):
        return _bad(f"for_wire({info.wire.value}) returned non-Device {cls!r}")
    return _ok(f"wire={info.wire.value} → {cls.__name__}")


def probe_sensor_read_permission(platform, _info) -> ProbeResult:
    """hwmon sysfs leaf reads survive PermissionError.

    Caught #139 Zombie-hive: a pipx install without ``trcc setup-udev``
    hit PermissionError reading ``/sys`` and the GUI launch crashed.  The
    live guard is ``hwmon._read_text`` catching ``OSError`` (PermissionError's
    parent); ``_read_int`` rides on top of it.  Narrowing that except would
    reintroduce the crash.
    """
    from unittest.mock import patch

    if not _is_linux_platform(platform):
        return _skip("hwmon sysfs is Linux-only")

    from trcc.adapters.sensors import hwmon

    def _denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    probe_path = Path("/sys/class/hwmon/hwmon0/temp1_input")
    try:
        with patch.object(Path, "read_text", _denied):
            text_val = hwmon._read_text(probe_path)
            int_val = hwmon._read_int(probe_path)
    except (PermissionError, OSError) as e:
        return _bad(f"hwmon leaf read crashed on PermissionError: {e}")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")

    if text_val is None and int_val is None:
        return _ok("hwmon _read_text/_read_int swallow PermissionError → None")
    return _bad(f"hwmon read returned {text_val!r}/{int_val!r} under denial "
                "instead of None")


def probe_canvas_size_stable(_platform, info: ProductInfo) -> ProbeResult:
    """Repeated fbl_to_resolution calls return the same non-zero value.

    Caught #137 satoru8 territory: cache-stale on rotation/handshake
    re-reads.  Every lookup must be deterministic.
    """
    from trcc.core.protocol import fbl_to_resolution

    if info.fbl is None:
        return _skip(f"{info.key} has no static FBL (resolution is handshake-derived)")
    a = fbl_to_resolution(info.fbl, 0)
    b = fbl_to_resolution(info.fbl, 0)
    if a != b:
        return _bad(f"FBL={info.fbl} drifted: {a} → {b}")
    if a[0] == 0 or a[1] == 0:
        return _bad(f"FBL={info.fbl} resolved to zero-dim {a}")
    return _ok(f"FBL={info.fbl} → {a} (stable)")


def probe_handshake_idempotent(_platform, info: ProductInfo) -> ProbeResult:
    """connect() twice in a row returns the same resolution / model_id."""
    built = _build_device(info)
    if built is None:
        return _skip(f"wire={info.wire.value} not wired in this harness")
    device, transport, driver = built
    try:
        driver.prime(transport, info)
        first = device.connect()
        driver.prime(transport, info)
        second = device.connect()
    except Exception as e:
        return _err(f"connect raised: {type(e).__name__}: {e}")
    finally:
        device.disconnect()

    if first is None or second is None:
        return _bad(f"connect returned None (1st={first}, 2nd={second})")
    if info.kind.value == "led":
        if first.model_id != second.model_id:
            return _bad(f"LED model_id drift: {first.model_id} → {second.model_id}")
    elif first.resolution != second.resolution:
        return _bad(f"resolution drift: {first.resolution} → {second.resolution}")
    return _ok("two consecutive connects returned identical results")


def probe_close_then_send(_platform, info: ProductInfo) -> ProbeResult:
    """connect → disconnect → connect → send (sleep/resume cycle, #144 territory)."""
    built = _build_device(info)
    if built is None:
        return _skip(f"wire={info.wire.value} not wired in this harness")
    device, transport, driver = built
    try:
        driver.prime(transport, info)
        first = device.connect()
        if first is None:
            return _err("first connect returned None")
        device.disconnect()
        driver.prime(transport, info)
        second = device.connect()
        if second is None:
            return _bad("post-disconnect connect returned None")
        if info.kind.value == "led":
            return _ok("LED disconnect+re-connect cycle clean (no frame send)")
        w, h = second.resolution if second.resolution else (0, 0)
        if w == 0 or h == 0:
            return _bad(f"post-disconnect resolution {second.resolution}")
        driver.prime_send(transport, info)
        sent = device.send(b"\x00" * (w * h * 2))
        if not sent:
            return _bad("post-disconnect send returned False")
        return _ok("connect → disconnect → connect → send clean")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")
    finally:
        device.disconnect()


def probe_windows_wmi_coinit(platform, _info) -> ProbeResult:
    """Every module calling ``wmi.WMI(`` also inits COM in-module (#131).

    Caught #131 lallemandgianni: ``wmi.x_wmi_uninitialised_thread`` because
    ``wmi.WMI()`` ran on a worker thread without ``pythoncom.CoInitialize()``.
    Sensor WMI (_lhm / _msacpi) runs on the polling thread, so a call site
    without CoInitialize in scope is the live regression shape.  Gated to
    Windows context — it's a COM invariant, irrelevant on a Linux run.
    """
    if not _is_windows_platform(platform):
        return _skip("WMI / COM init is Windows-only")

    src_root = _REPO / "src" / "trcc"
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"\bwmi\.WMI\s*\(", text):
            continue
        if "CoInitialize" not in text:
            offenders.append(path.relative_to(_REPO).as_posix())
    if offenders:
        return _bad(
            "wmi.WMI(...) called without pythoncom.CoInitialize in-module "
            f"(#131 worker-thread risk): {', '.join(sorted(offenders))}"
        )
    return _ok("every wmi.WMI() call site initializes COM in its module")


# ─────────────────────────────────────────────────────────────────────────────
# Probe registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class Probe:
    name: str
    description: str
    runner: Callable[[Any, ProductInfo], ProbeResult]


PROBES: list[Probe] = [
    Probe("video.size.zero",
          "VideoDecoder guards against a zero dimension in size",
          probe_video_size_zero),
    Probe("video.size.portrait",
          "VideoDecoder handles portrait dimensions",
          probe_video_size_portrait),
    Probe("device.factory.resolves",
          "every registry wire resolves to a concrete Device subclass",
          probe_factory_resolves),
    Probe("sensors.read.permission",
          "hwmon sysfs leaf reads survive PermissionError (#139)",
          probe_sensor_read_permission),
    Probe("geometry.canvas_size.stable",
          "fbl_to_resolution returns deterministic results across re-reads",
          probe_canvas_size_stable),
    Probe("device.handshake.idempotent",
          "connect() returns same value across repeated calls",
          probe_handshake_idempotent),
    Probe("device.close_then_send",
          "connect → disconnect → connect → send (sleep/resume cycle)",
          probe_close_then_send),
    Probe("windows.wmi.coinit",
          "every WMI call site inits COM in-module (#131)",
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


def _select_devices(spec: str) -> list[tuple[tuple[int, int], ProductInfo]]:
    items = sorted(ALL_DEVICES.items())
    if spec == "all":
        return list(items)
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
#
# debug_report.py emits a ``## Platform`` block of ``key  value`` rows
# (system / distro / python / …) and a ``## Devices`` block of
# ``vid:pid  product  wire=…`` rows.  We read the ``system`` field
# (py_platform.system(): Linux / Windows / Darwin / FreeBSD) for the OS
# and every registry-known VID:PID for the device matrix.


def _os_from_report(text: str) -> str:
    """Map the report's ``system`` / ``distro`` fields to an OS label."""
    system_match = re.search(r"^\s*system\s+(.+)$", text, re.MULTILINE)
    distro_match = re.search(r"^\s*distro\s+(.+)$", text, re.MULTILINE)
    haystack = " ".join(
        m.group(1).strip().lower()
        for m in (system_match, distro_match) if m
    )
    if "windows" in haystack:
        return "windows"
    if "darwin" in haystack or "macos" in haystack:
        return "macos"
    if "bsd" in haystack:
        return "bsd"
    return "linux"


def _vid_pids_from_report(text: str) -> list[tuple[int, int]]:
    """Every distinct VID:PID-shaped pair in the report, in first-seen order."""
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in re.finditer(r"\b([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\b", text):
        vp = (int(match.group(1), 16), int(match.group(2), 16))
        if vp not in seen:
            seen.add(vp)
            pairs.append(vp)
    return pairs


def _select_devices_from_report(
    report_path: Path,
) -> tuple[str, list[tuple[tuple[int, int], ProductInfo]]]:
    """Run probes against every registered device the report mentions."""
    text = report_path.read_text(errors="replace")
    os_label = _os_from_report(text)
    pairs = _vid_pids_from_report(text)
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
                        "--device with the system and VID:PIDs found inside")
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
        print(f"  → os={os_label}, devices="
              f"{[f'{v:04x}:{p:04x}' for (v, p), _ in devices]}\n")
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

    for (vid, pid), info in devices:
        device_label = f"{vid:04x}:{pid:04x} {info.product} ({info.wire.value.upper()})"
        print(f"{_BOLD}Device:{_RESET} {device_label}")

        for probe in probes:
            try:
                result = probe.runner(platform, info)
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


if __name__ == "__main__":
    raise SystemExit(main())
