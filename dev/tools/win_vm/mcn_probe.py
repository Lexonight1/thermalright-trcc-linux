"""ADMIN: why does Windows refuse IOCTL_STORAGE_MCN_CONTROL on the panel's drive?

TRCC sends it as it opens the panel, to stop disk.sys polling the drive for media
once a second (``WindowsScsiTransport._stop_media_polling``); on the win11 VM,
2026-10-20, the service's log said WinError 87.  This opens the drive with each
access mask and, on each handle, asks Windows what it thinks the drive is and
tries the switch -- nothing here sends the panel a SCSI command: hotplug info
and the device descriptor come from the class driver's cache, and MCN control
only moves a counter (classpnp ``ClasspMcnControl``).  Every handle it opens
it closes, which undoes any switch it set (``ClasspCleanupDisableMcn``).
"""
import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise SystemExit("mcn_probe runs on Windows, in the VM's admin shell")
sys.path.insert(0, r"C:\trccdev\tree\src")
from trcc.adapters.system._windows_drives import find_physical_drive

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.DeviceIoControl.restype = wintypes.BOOL
k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                ctypes.c_void_p]
INVALID = wintypes.HANDLE(-1).value

MCN_CONTROL = 0x2D0944
HOTPLUG_INFO = 0x2D0C14
QUERY_PROPERTY = 0x2D1400


def ioctl(handle, code, inbuf, out_len):
    out = (ctypes.c_ubyte * max(out_len, 1))()
    got = wintypes.DWORD(0)
    src = (ctypes.c_ubyte * len(inbuf)).from_buffer_copy(inbuf) if inbuf else None
    ok = k32.DeviceIoControl(handle, code, src, len(inbuf), out if out_len else None, out_len,
                             ctypes.byref(got), None)
    return bool(ok), (0 if ok else ctypes.get_last_error()), bytes(out[:got.value])


path = find_physical_drive(0x0402, 0x3922)
print("drive:", path)
if path is None:
    raise SystemExit("no drive for 0402:3922 -- is the panel passed through to the VM?")
for label, access in (("read+write", 0xC0000000), ("read", 0x80000000), ("none", 0)):
    handle = k32.CreateFileW(path, access, 3, None, 3, 0, None)
    if handle in (None, INVALID):
        print(f"[{label}] CreateFileW failed: WinError {ctypes.get_last_error()}")
        continue
    ok, err, out = ioctl(handle, HOTPLUG_INFO, b"", 8)
    print(f"[{label}] hotplug info ok={ok} err={err} "
          f"MediaRemovable={out[4] if len(out) > 4 else '?'} MediaHotplug={out[5] if len(out) > 5 else '?'} "
          f"DeviceHotplug={out[6] if len(out) > 6 else '?'}")
    ok, err, out = ioctl(handle, QUERY_PROPERTY, bytes(12), 64)   # StorageDeviceProperty, query
    print(f"[{label}] device descriptor ok={ok} err={err} "
          f"RemovableMedia={out[10] if len(out) > 10 else '?'} BusType={out[28] if len(out) > 28 else '?'}")
    for size in (1, 4):
        ok, err, _ = ioctl(handle, MCN_CONTROL, b"\x01".ljust(size, b"\0"), 0)
        print(f"[{label}] MCN_CONTROL disable, {size}-byte input: ok={ok} err={err}")
        if ok:
            ok2, err2, _ = ioctl(handle, MCN_CONTROL, b"\x00".ljust(size, b"\0"), 0)
            print(f"[{label}] MCN_CONTROL re-enable: ok={ok2} err={err2}")
    k32.CloseHandle(handle)
