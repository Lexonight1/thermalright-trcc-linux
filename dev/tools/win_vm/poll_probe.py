"""ADMIN, one-off: does a 'failed' (121) DIRECT poll still deliver the reply?
Pre-fills the data buffer with 0xAA, polls once, prints what the buffer holds."""
import ctypes
import struct
import sys
sys.path.insert(0, r"C:\trccdev\tree\src")
import trcc.adapters.system.windows as w

path = w.find_physical_drive(0x0402, 0x3922)
print("drive:", path)
if path is None:
    sys.exit("no 0402:3922 drive -- is the panel passed through to this VM?")
t = w.WindowsScsiTransport(path)
print("open:", t.open())
cdb = struct.pack("<I", 0xF5) + bytes(8) + struct.pack("<I", 0xE100)
_sptdwb, buf = t._buffers(0xE100)
ctypes.memset(buf, 0xAA, 0xE100)
ok, err, sptd, data = t._pass_through(cdb, 0xE100, True, 10000)
raw = bytes(data[:64])
print("ok:", ok, "err:", err, "status:", sptd.ScsiStatus, "transferred:", sptd.DataTransferLength)
print("first 64:", raw.hex())
changed = sum(1 for b in bytes(data[:0xE100]) if b != 0xAA)
print("bytes changed from 0xAA:", changed)
t.close()
