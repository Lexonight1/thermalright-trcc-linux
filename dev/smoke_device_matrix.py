#!/usr/bin/env python3
"""Per-device smoke matrix — every registered VID:PID gets a full lifecycle test.

Goal: turn each entry in ``ALL_DEVICES`` into a green checkmark before a
release ships.  If a reporter on GitHub has VID:PID X, this run will say
PASS or FAIL for X — no guessing whether their device works.

What each device gets:

  1. Handshake — protocol parses canned response, returns a resolution.
  2. Idempotent handshake — calling it twice doesn't corrupt state.
  3. Frame send — synthetic RGB565 / JPEG-shaped bytes go through send_data.
  4. Sleep/resume cycle — close() + re-handshake (Tee86 #144).
  5. Send after close — protocol auto-recovers or fails clean (no silent
     corruption).

Bugs the matrix is designed to catch (proactive — not user-reported yet):

  * Cached protocol instances surviving a transport.close() and then
    silently sending into a closed fd.
  * Handshake leaking state on second call.
  * Resolution / FBL drift when handshake bytes match a different model.
  * Cross-protocol inconsistency: SCSI/HID/Bulk/LY behaving differently
    on the same lifecycle sequence.

Usage::
    PYTHONPATH=src python3 dev/smoke_device_matrix.py

Exit 0 on all PASS, 1 on any FAIL.  Bug-hunt findings (yellow) don't
fail the run on their own but are flagged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Headless — no Qt event loop needed
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

from noop_transports import (  # type: ignore[import-not-found]
    NoopBulkLikeDevice,
    NoopScsiTransport,
    NoopUsbTransport,
    build_hid_type2_response,
    build_hid_type3_response,
    build_led_response,
)

# factory must import first — it registers all protocol subclasses at the
# bottom of its module body.  Pulling BulkProtocol/LyProtocol directly from
# their modules before factory loads triggers a circular import.
from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol
from trcc.legacy.adapters.device.ly_protocol import LyProtocol
from trcc.legacy.core.models import (
    ALL_DEVICES,
    DetectedDevice,
    DeviceInfo,
)


# ── Reporter map — turn PASS/FAIL into "user X is happy now" ─────────────────

REPORTERS: dict[tuple[int, int], list[str]] = {
    (0x0402, 0x3922): ["Tee86 #144", "k1w3l #143"],
    (0x87AD, 0x70DB): ["Civilgrain #142", "questist #136", "TimG-NL #134"],
    (0x0416, 0x5408): ["Zombie-hive #139", "bktdrinal #129"],
    (0x0416, 0x8001): ["juanito54jm #130"],
}


# ── Per-protocol noop wiring ─────────────────────────────────────────────────

def _wire_scsi(entry) -> None:
    """Inject a SCSI noop transport that returns FBL = entry.fbl."""
    fbl = entry.fbl

    def _factory(path, vid=0, pid=0):
        return NoopScsiTransport(fbl=fbl)
    DeviceProtocolFactory.set_scsi_transport(_factory)


def _wire_usb(resp: bytes) -> None:
    """Inject a USB noop transport returning ``resp`` on first read."""
    def _factory(vid, pid, **_ignore):
        return NoopUsbTransport(resp)
    DeviceProtocolFactory.create_usb_transport = staticmethod(_factory)


def _wire_bulk_like(protocol_cls, fbl: int) -> None:
    """Patch ``_make_device`` on Bulk/LY protocols.

    Bulk/LY wrap a Device that owns its pyusb endpoints directly — the
    transport-factory injection point used for SCSI/HID/LED doesn't reach
    them.  The cleanest seam is the protocol's ``_make_device`` staticmethod.
    """
    from trcc.legacy.core.models import fbl_to_resolution
    resolution = fbl_to_resolution(fbl, 32)

    def _factory(vid, pid, *, usb_address=None):
        return NoopBulkLikeDevice(vid, pid, usb_address=usb_address, resolution=resolution)
    protocol_cls._make_device = staticmethod(_factory)


def _wire_for(vid: int, pid: int, entry) -> None:
    """Pick the right canned response for the device's protocol."""
    proto = entry.protocol
    if proto == "scsi":
        _wire_scsi(entry)
    elif proto == "hid":
        if entry.device_type == 3:
            _wire_usb(build_hid_type3_response(fbl=entry.fbl))
        else:
            _wire_usb(build_hid_type2_response(pm=32, sub=0))
    elif proto == "led":
        _wire_usb(build_led_response(pm=32, sub=0))
    elif proto == "bulk":
        _wire_bulk_like(BulkProtocol, entry.fbl)
    elif proto == "ly":
        _wire_bulk_like(LyProtocol, entry.fbl)
    else:
        raise ValueError(f"unknown protocol: {proto!r}")


# ── DetectedDevice / DeviceInfo construction ─────────────────────────────────

def _make_info(vid: int, pid: int, entry) -> DeviceInfo:
    proto = entry.protocol
    path = "/dev/sg2" if proto == "scsi" else f"usb:{vid:04x}:{pid:04x}"
    detected = DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor, product_name=entry.product,
        usb_path=path,
        scsi_device=path if proto == "scsi" else None,
        protocol=proto, device_type=entry.device_type,
        implementation=entry.implementation,
        model=entry.model, button_image=entry.button_image,
    )
    return DeviceInfo.from_detected(detected)


# ── Per-device run ───────────────────────────────────────────────────────────

class DeviceResult:
    __slots__ = ('bugs', 'fails', 'label', 'passes', 'reporters')

    def __init__(self, label: str, reporters: list[str]) -> None:
        self.label = label
        self.reporters = reporters
        self.passes: list[str] = []
        self.bugs: list[str] = []
        self.fails: list[str] = []

    def ok(self, msg: str) -> None: self.passes.append(msg)
    def bug(self, msg: str) -> None: self.bugs.append(msg)
    def fail(self, msg: str) -> None: self.fails.append(msg)

    @property
    def status(self) -> str:
        if self.fails:
            return "FAIL"
        if self.bugs:
            return "BUG-HUNT"
        return "PASS"


def _frame_bytes(width: int, height: int) -> bytes:
    # Worst-case: RGB565 = 2 bytes per pixel
    return b'\x00' * (width * height * 2)


def _run_device(vid: int, pid: int, entry) -> DeviceResult:
    label = f"{vid:04x}:{pid:04x} {entry.vendor} {entry.product} ({entry.protocol.upper()})"
    reporters = REPORTERS.get((vid, pid), [])
    result = DeviceResult(label, reporters)

    _wire_for(vid, pid, entry)
    info = _make_info(vid, pid, entry)

    # Step 1: clean cache, fresh protocol, handshake
    DeviceProtocolFactory._protocols.clear()
    try:
        protocol = DeviceProtocolFactory.create_protocol(info)
    except Exception as e:
        result.fail(f"create_protocol raised: {type(e).__name__}: {e}")
        return result

    try:
        hs1 = protocol.handshake()
    except Exception as e:
        result.fail(f"handshake() raised: {type(e).__name__}: {e}")
        return result

    if hs1 is None:
        result.fail("handshake returned None")
        return result

    # LED devices don't have a screen resolution — model_id + style is the
    # success signal.  LCD protocols always carry a (w, h) pair.
    is_led = entry.protocol == "led"
    if is_led:
        if hs1.model_id == 0:
            result.fail(f"LED handshake returned model_id=0 (got {hs1!r})")
            return result
        w, h = 0, 0  # placeholder for the LCD-only frame send below
        result.ok(f"handshake → LED model_id={hs1.model_id}")
    else:
        if hs1.resolution is None:
            result.fail(f"handshake returned no resolution (got {hs1!r})")
            return result
        w, h = hs1.resolution
        if w == 0 or h == 0:
            result.fail(f"handshake returned zero resolution {hs1.resolution}")
            return result
        result.ok(f"handshake → {w}x{h}")

    # Step 2: idempotent handshake — calling twice should not break state
    try:
        hs2 = protocol.handshake()
        if hs2 is None or hs2.resolution != hs1.resolution:
            result.bug(
                f"handshake idempotency: 2nd call gave {hs2.resolution if hs2 else None} "
                f"vs 1st {hs1.resolution}",
            )
        else:
            result.ok("idempotent handshake")
    except Exception as e:
        result.bug(f"2nd handshake raised: {type(e).__name__}: {e}")

    # Step 3: send_data — LCD only (LED.send_data has a different signature)
    if not is_led:
        frame = _frame_bytes(w, h)
        try:
            sent = protocol.send_data(frame, w, h)
            if not sent:
                result.bug(f"send_data returned False ({len(frame)} bytes)")
            else:
                result.ok(f"send_data → {len(frame)} bytes")
        except Exception as e:
            result.fail(f"send_data raised: {type(e).__name__}: {e}")
            return result

    # Step 4: sleep/resume cycle — close, re-handshake, send again (Tee86 #144)
    try:
        protocol.close()
        result.ok("close() did not raise")
    except Exception as e:
        result.bug(f"close raised: {type(e).__name__}: {e}")

    try:
        hs3 = protocol.handshake()
        post_resume_ok = (
            hs3 is not None
            and (hs3.model_id == hs1.model_id if is_led
                 else hs3.resolution == hs1.resolution)
        )
        if not post_resume_ok:
            result.bug(
                f"post-resume handshake mismatch: got {hs3!r} "
                f"(expected {'model_id=' + str(hs1.model_id) if is_led else hs1.resolution})",
            )
        else:
            result.ok("post-resume handshake")
    except Exception as e:
        result.bug(f"post-resume handshake raised: {type(e).__name__}: {e}")

    if not is_led:
        try:
            sent2 = protocol.send_data(frame, w, h)
            if not sent2:
                result.bug("post-resume send_data returned False")
            else:
                result.ok("post-resume send_data")
        except Exception as e:
            result.bug(f"post-resume send_data raised: {type(e).__name__}: {e}")

    # Cleanup
    try:
        protocol.close()
    except Exception:
        pass

    return result


# ── Reporting ────────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"
_BOLD = "\033[1m"

_COLOR = {
    "PASS": _GREEN,
    "BUG-HUNT": _YELLOW,
    "FAIL": _RED,
}


def _print_result(r: DeviceResult) -> None:
    color = _COLOR[r.status]
    rep = f"  [{', '.join(r.reporters)}]" if r.reporters else ""
    print(f"{color}{r.status:<10}{_RESET}{r.label}{rep}")
    for msg in r.passes:
        print(f"            ok    {msg}")
    for msg in r.bugs:
        print(f"  {_YELLOW}!{_RESET}         hunt  {msg}")
    for msg in r.fails:
        print(f"  {_RED}X{_RESET}         FAIL  {msg}")


def main() -> int:
    print(f"{_BOLD}TRCC device-matrix smoke{_RESET}")
    print(f"  registry: {len(ALL_DEVICES)} devices across "
          f"{len({e.protocol for e in ALL_DEVICES.values()})} protocols\n")

    results: list[DeviceResult] = []
    for (vid, pid), entry in sorted(ALL_DEVICES.items()):
        results.append(_run_device(vid, pid, entry))

    for r in results:
        _print_result(r)

    pass_n = sum(1 for r in results if r.status == "PASS")
    bug_n = sum(1 for r in results if r.status == "BUG-HUNT")
    fail_n = sum(1 for r in results if r.status == "FAIL")

    print()
    print("=" * 72)
    print(f"  {_GREEN}PASS{_RESET}    {pass_n}/{len(results)}")
    if bug_n:
        print(f"  {_YELLOW}HUNT{_RESET}    {bug_n}/{len(results)}  (bugs to investigate, "
              "users haven't reported)")
    if fail_n:
        print(f"  {_RED}FAIL{_RESET}    {fail_n}/{len(results)}  (broken — fix before release)")
    print("=" * 72)

    if results:
        happy = {u for r in results if r.status == "PASS" for u in r.reporters}
        sad = {u for r in results if r.status != "PASS" for u in r.reporters}
        if happy:
            print(f"\n  Happy: {', '.join(sorted(happy))}")
        if sad:
            print(f"  {_RED}Still waiting{_RESET}: {', '.join(sorted(sad))}")

    return 1 if fail_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
