#!/usr/bin/env python3
"""Ask an ASUS Aura motherboard controller what its ARGB headers detected.

READ-ONLY: the firmware (0x82), the config table (0xB0), and the records
Armoury Crate reads on a Gen2 rescan -- each a select (``EC 3x n``) then a
fetch (``EC Bx``).  No colours, no effects, no save (0x3F), and none of the
session-opening writes Armoury Crate sends first (``3F AA``, ``3E 52 53 01``,
``3F 55``): what those do is not known, and ``3F 55`` is the save.

The read pairs were decoded from Armoury Crate USB captures posted by
TenkenNoSoujiro in OpenRGB issue #3929
(https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/3929):

    EC 31 n / EC B1 -> EC 31 n <type> 00 <leds> 25 FF FF FF ... <max> <detected u16>
        <type> 0x10 an onboard zone, 0x20 + k ARGB header k + 1 (they follow
        the onboard zones, so their n differs by board: 4-6 on the Strix
        Z790-F in the captures, 1-3 on a PRIME Z790-V AX), and from n 0x10
        one record per Gen2 device found on a header
    EC 3D n / EC BD -> EC 3D 00 n <id> <id> 00 <leds> ...   (the Gen2 devices)
    EC 32 n / EC B2 -> not decoded yet; printed raw

    PYTHONPATH=src python3 dev/tools/aura_probe.py            # the first Aura found
    PYTHONPATH=src python3 dev/tools/aura_probe.py --pid 19af

Prints every reply in hex, the header records decoded.  Exit 1 when no Aura
controller is plugged in or it cannot be opened.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

#: The records worth reading: select opcode, fetch opcode, the indices.
_READS = (
    (0x31, 0xB1, (*range(0x00, 0x07), *range(0x10, 0x18))),
    (0x3D, 0xBD, tuple(range(0x01, 0x09))),
    (0x32, 0xB2, (0x00, 0x01, 0x02, *range(0x10, 0x14))),
)
#: A record's type byte for ARGB header 1; header k is this + k - 1.
_HEADER_TYPE = 0x20


def _plugged_pid(vid: int, pids: tuple[int, ...]) -> int | None:
    """The first Aura motherboard controller on the USB bus."""
    for device in Path("/sys/bus/usb/devices").iterdir():
        try:
            if int((device / "idVendor").read_text(), 16) != vid:
                continue
            pid = int((device / "idProduct").read_text(), 16)
        except (OSError, ValueError):
            continue
        if pid in pids:
            return pid
    return None


def _trimmed(reply: bytes) -> str:
    return reply.rstrip(b"\x00").hex(" ") or "(nothing)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pid", type=lambda s: int(s, 16),
                    help="the controller's USB product id, hex (default: find it)")
    args = ap.parse_args()

    from trcc.adapters.rgb import asus_aura
    from trcc.adapters.system import current_platform
    from trcc.core.models import Wire

    pid = args.pid or _plugged_pid(asus_aura.AURA_VID, asus_aura.MAINBOARD_PIDS)
    if pid is None:
        print("no ASUS Aura motherboard controller on the USB bus")
        return 1
    hid = current_platform().open_transport(
        Wire.HID, asus_aura.AURA_VID, pid, hid_reports=True,
        report_id=asus_aura.REPORT_ID)
    try:
        hid.open()
    except OSError as e:
        print(f"cannot open 0b05:{pid:04x}: {e}")
        return 1

    def ask(*payload: int) -> bytes:
        """Write *payload* (after the report id); the whole reply."""
        hid.write(0, bytes(payload).ljust(asus_aura.PAYLOAD, b"\x00"))
        reply = b""
        while len(reply) < asus_aura.PAYLOAD + 1:
            piece = hid.read(0, asus_aura.PAYLOAD + 1, asus_aura.READ_TIMEOUT_MS)
            if not piece:
                break
            reply += piece
        return reply

    def select(*payload: int) -> None:
        """A select: written, no reply expected."""
        hid.write(0, bytes(payload).ljust(asus_aura.PAYLOAD, b"\x00"))

    try:
        print(f"0b05:{pid:04x}")
        print(f"firmware  {_trimmed(ask(0x82))}")
        config = ask(0xB0)
        print(f"config    {_trimmed(config)}")
        for chosen, fetch, indices in _READS:
            print(f"\n-- EC {chosen:02X} n / EC {fetch:02X}")
            for n in indices:
                select(chosen, n)
                reply = ask(fetch)
                note = ""
                if (chosen == 0x31 and n < 0x10 and len(reply) > 19
                        and reply[3] & 0xF0 == _HEADER_TYPE):
                    detected = int.from_bytes(reply[18:20], "big")
                    note = (f"   <- ARGB header {reply[3] - _HEADER_TYPE + 1}:"
                            f" leds {reply[5]} max {reply[17]} detected {detected}")
                print(f"n=0x{n:02x}  {_trimmed(reply)}{note}")
    finally:
        hid.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
