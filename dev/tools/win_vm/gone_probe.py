"""ADMIN: what Windows answers when a SCSI disk vanishes mid-stream.

    python gone_probe.py VID PID [SECONDS]      # hex; default 46f4 0001 60

Opens the disk with TRCC's own ``WindowsScsiTransport`` and alternates a 32 KB
WRITE(10) and READ(10) at LBA 0 -- data-out like a frame, data-in like the
poll -- logging each change in (ok, WinError, SCSI status) per direction.  Unplug the
disk from the host while it runs; it runs the full time, so a code Windows
only returns once removal completes shows too.  Then it reopens the drive and
looks the disk up again, logging both answers.  (A QEMU disk now and then fails
one READ with 121 and recovers on the next -- so the first failure is not the
unplug.)

Measured 2026-10-18 (QEMU disk, device_del mid-stream): the command in flight
times out (WinError 121, status GOOD), every later one is 1167
ERROR_DEVICE_NOT_CONNECTED; the reopen is WinError 2; the lookup finds none.

The ``_GONE_ERRORS`` table in ``windows.py`` is what this measures.  Use a
scratch disk (QEMU usb-storage, see drive_probe.py): it WRITES LBA 0.

Given a TRCC SCSI panel's ids instead, it drives the panel as TRCC does -- the
real ``ScsiLcd`` handshake, then red frames -- and logs each change in what
``send`` does, so a pull shows whether TRCC calls it unplugged
(``is_disconnect_error``).  Detach the panel from the VM while it runs, then
``win_vm.py panel`` to give it back.
"""
import ctypes
import struct
import sys
import time

sys.path.insert(0, r"C:\trccdev\tree\src")
from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.adapters.system._service_broker import SCSI_PANELS
from trcc.adapters.system._windows_drives import find_physical_drive
from trcc.adapters.system.windows import WindowsScsiTransport
from trcc.core.device_recovery import is_disconnect_error
from trcc.core.registry import ALL_DEVICES

vid, pid = (int(v, 16) for v in (sys.argv[1:3] or ["46f4", "0001"]))
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
drive = find_physical_drive(vid, pid)
print(f"drive {drive}")
t = WindowsScsiTransport(drive)


def panel_frames() -> None:
    """The panel, driven as TRCC drives it, until the time is up."""
    device = ScsiLcd(ALL_DEVICES[(vid, pid)], t)
    shake = device.connect()
    print(f"handshake -> {shake.resolution}")
    w, h = shake.resolution
    frame = b"\x00\xf8" * (w * h)                      # red, RGB565 little-endian
    start, count, last = time.perf_counter(), 0, None
    while time.perf_counter() - start < seconds:
        try:
            outcome = f"sent={device.send(frame)}"
        except Exception as e:                         # what the App would see
            outcome = f"{type(e).__name__}: {e} disconnect={is_disconnect_error(e)}"
            time.sleep(0.05)
        count += 1
        if outcome != last:
            print(f"{time.perf_counter() - start:7.3f}s frame #{count}: {outcome}")
            last = outcome
    print(f"{count} frames in {time.perf_counter() - start:.1f}s")


if (vid, pid) in SCSI_PANELS:
    panel_frames()
    sys.exit(0)
print(f"open -> {t.open()}")
blocks = 64                                           # 64 x 512 = 32 KB
write10 = struct.pack(">BBIBHB", 0x2A, 0, 0, 0, blocks, 0)
read10 = struct.pack(">BBIBHB", 0x28, 0, 0, 0, blocks, 0)
payload = bytes(range(256)) * (blocks * 2)
start, count = time.perf_counter(), 0
last: dict[str, tuple] = {}
while time.perf_counter() - start < seconds:
    for name, cdb, data_in in (("write", write10, False), ("read", read10, True)):
        _, buf = t._buffers(len(payload))
        if not data_in:
            ctypes.memmove(buf, payload, len(payload))
        ok, err, sptd, _ = t._pass_through(cdb, len(payload), data_in, 2000)
        state = (ok, err, sptd.ScsiStatus)
        count += 1
        if state != last.get(name):
            print(f"{time.perf_counter() - start:7.3f}s #{count} {name}: ok={ok} "
                  f"WinError={err} status={sptd.ScsiStatus} "
                  f"transferred={sptd.DataTransferLength}")
            last[name] = state
print(f"{count} commands, stopped after {time.perf_counter() - start:.1f}s")
t.close()
k32 = ctypes.windll.kernel32
handle = k32.CreateFileW(drive, 0xC0000000, 3, None, 3, 0, None)
print(f"reopen: handle={handle} WinError={ctypes.GetLastError()}")
if handle not in (-1, 0xFFFFFFFF, ctypes.c_void_p(-1).value):
    k32.CloseHandle(handle)
print(f"lookup again -> {find_physical_drive(vid, pid)}")
