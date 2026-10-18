#!/usr/bin/env python3
"""User-space instructions per SCSI frame, and per keepalive resend.

    PYTHONPATH=src python3.12 dev/tools/scsi_cost.py frame linux     # SG_IO stubbed
    PYTHONPATH=src python3.12 dev/tools/scsi_cost.py frame windows   # kernel32 stubbed
    PYTHONPATH=src python3.12 dev/tools/scsi_cost.py resend scsi|hid|led

Each runs the REAL transport / sender / device code with only the OS call
stubbed, at N=0 and N=big under ``perf stat``, and prints the difference per
unit.  The unit is counted independently (frames issued, writes recorded).

Measured 2026-10-18 on the dev box: frame linux 146 K (before BaseScsiTransport)
-> 172 K; frame windows ~298 M (byte-by-byte buffer rebuild) -> 188 K; resend
scsi 125 K, hid 1.7 M (~300 writes per frame, mock transport included), led 0.9 M.

Instrument rules (memory ``reference_cpu_and_profiling_tools``):

*  **Pin one P-core and read only its counter.**  This CPU is hybrid: unpinned,
   ``instructions:u`` multiplexes two PMUs and swung 60%% between identical
   runs.  ``taskset -c 0`` + ``cpu_core/instructions/u`` reproduce to 0.1%%.
*  **Warm the bytecode first** -- a cold tree's first run compiles everything and
   reads as cheaper-at-N than at zero.
*  **Never HOME=<scratch>**: it hides the user site-packages (numpy) and the
   mock platform will not import.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

CHUNKS = [b"\xf8" * 65536] * 3 + [b"\xf8" * 8192]       # one 320x320 RGB565 frame
CDB = b"\x00" * 16
REPO = Path(__file__).resolve().parents[2]


def _linux_frames(n: int) -> None:
    import fcntl

    from trcc.adapters.system.linux import LinuxScsiTransport

    fcntl.ioctl = lambda fd, req, buf: 0                  # type: ignore[assignment]
    node = Path(tempfile.mkdtemp()) / "sg0"
    node.write_bytes(b"")
    t = LinuxScsiTransport(str(node))
    assert t.open()
    for _ in range(n):
        for c in CHUNKS:
            t.send_cdb(CDB, c)


def _windows_frames(n: int) -> None:
    import trcc.adapters.system.windows as w

    class Kernel32:
        def CreateFileW(self, *a): return 7
        def CloseHandle(self, h): return 1
        def DeviceIoControl(self, *a): return 1

    k = Kernel32()
    w._kernel32 = lambda: k
    ctypes.GetLastError = lambda: 0                      # type: ignore[attr-defined]
    t = w.WindowsScsiTransport(r"\\.\PhysicalDrive1")
    assert t.open()
    for _ in range(n):
        for c in CHUNKS:
            t.send_cdb(CDB, c)


def _resends(family: str, n: int) -> None:
    sys.path.insert(0, str(REPO))
    from tests.mock_platform import MockPlatform
    from trcc.adapters.device import DEVICES
    from trcc.core.led_models import LedPayload
    from trcc.core.registry import ALL_DEVICES
    from trcc.services.device_sender import DeviceSender

    spec = {"scsi": {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100},
            "hid": {"type": "lcd", "vid": "0416", "pid": "5302", "pm": 5, "fbl": 50},
            "led": {"type": "led", "vid": "0416", "pid": "8001", "pm": 208}}[family]
    vid, pid = int(spec["vid"], 16), int(spec["pid"], 16)
    info = ALL_DEVICES[(vid, pid)]
    transport = MockPlatform([spec], Path(tempfile.mkdtemp())).open_transport(
        info.wire, vid, pid)
    device = DEVICES[info.wire](info, transport)
    device.connect()
    payload = {"scsi": b"\xf8\x00" * (320 * 320), "hid": b"\xf8\x00" * (240 * 320),
               "led": LedPayload(colors=[(255, 0, 0)] * 64)}[family]
    sender = DeviceSender(device, volatile=True, keepalive_interval=0.15)
    sender.submit(payload)
    sender.run_once(0.0)
    now = 0.0
    for _ in range(n):
        now += 0.25                                       # always due
        sender.run_once(now)


def _instructions(argv: list[str]) -> int:
    """User-space instructions of one child run, pinned to CPU 0."""
    out = subprocess.run(
        ["taskset", "-c", "0", "perf", "stat", "-x,", "-e", "cpu_core/instructions/u",
         sys.executable, *argv], capture_output=True, text=True, check=True)
    for line in out.stderr.splitlines():
        if "instructions" in line:
            return int(line.split(",")[0])
    raise SystemExit(f"perf printed no count:\n{out.stderr}")


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        logging.disable(logging.CRITICAL)
        what, arg, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
        if what == "frame":
            {"linux": _linux_frames, "windows": _windows_frames}[arg](n)
        else:
            _resends(arg, n)
        return 0
    if len(sys.argv) != 3 or sys.argv[1] not in ("frame", "resend"):
        print(__doc__)
        return 2
    what, arg = sys.argv[1], sys.argv[2]
    n = 3000 if what == "frame" else 2000
    child = [__file__, "--child", what, arg]
    _instructions([*child, "1"])                          # warm the bytecode
    lo = min(_instructions([*child, "0"]) for _ in range(2))
    hi = min(_instructions([*child, str(n)]) for _ in range(2))
    print(f"{what} {arg}: {(hi - lo) / n / 1000:.1f} K instructions per "
          f"{'frame' if what == 'frame' else 'resend'} (N={n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
