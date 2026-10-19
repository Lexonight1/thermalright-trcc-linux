"""ADMIN, while TRCCService holds the panel for a non-admin App: does a second
owner in another session see the claim?  Prints whether a direct open was
refused, and what CreateSemaphoreW said."""
import ctypes
import sys

sys.path.insert(0, r"C:\trccdev\tree\src")
import trcc.adapters.system.windows as w
from trcc.adapters.system._windows_drives import find_physical_drive

path = find_physical_drive(0x0402, 0x3922)
print("drive:", path)
name = w._CLAIM_PREFIX + path.rsplit("\\", 1)[-1].upper()
handle = ctypes.windll.kernel32.CreateSemaphoreW(None, 1, 1, name)
print("semaphore", name, "handle:", handle, "last error:", ctypes.GetLastError())
if handle:
    ctypes.windll.kernel32.CloseHandle(handle)
t = w.WindowsScsiTransport(path)
opened = t.open()
print("second owner open() ->", opened, "(False = refused, the guard works)")
if opened:
    t.close()
