"""Linux SG_IO ioctl bridge — direct SCSI passthrough (no subprocess fork).

Provides LinuxScsiTransport: a class-based SCSI transport matching the
interface of MacOSScsiTransport and BSDScsiTransport. Uses fcntl.ioctl
with the kernel SG_IO interface for zero-copy SCSI command execution.
"""
from __future__ import annotations

import ctypes
import logging
import os

from trcc.legacy.adapters.device.scsi import ScsiTransport

log = logging.getLogger(__name__)

# =========================================================================
# SG_IO ioctl constants and structures
# =========================================================================

_SG_IO = 0x2285
_SG_DXFER_TO_DEV = -2
_SG_DXFER_FROM_DEV = -3


class _SgIoHdr(ctypes.Structure):
    """Linux sg_io_hdr_t for SG_IO ioctl."""

    _fields_ = [
        ('interface_id', ctypes.c_int),
        ('dxfer_direction', ctypes.c_int),
        ('cmd_len', ctypes.c_ubyte),
        ('mx_sb_len', ctypes.c_ubyte),
        ('iovec_count', ctypes.c_ushort),
        ('dxfer_len', ctypes.c_uint),
        ('dxferp', ctypes.c_void_p),
        ('cmdp', ctypes.c_void_p),
        ('sbp', ctypes.c_void_p),
        ('timeout', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        ('pack_id', ctypes.c_int),
        ('usr_ptr', ctypes.c_void_p),
        ('status', ctypes.c_ubyte),
        ('masked_status', ctypes.c_ubyte),
        ('msg_status', ctypes.c_ubyte),
        ('sb_len_wr', ctypes.c_ubyte),
        ('host_status', ctypes.c_ushort),
        ('driver_status', ctypes.c_ushort),
        ('resid', ctypes.c_int),
        ('duration', ctypes.c_uint),
        ('info', ctypes.c_uint),
    ]


_SG_HDR_SIZE = ctypes.sizeof(_SgIoHdr)


class LinuxScsiTransport(ScsiTransport):
    """Send raw SCSI commands to a /dev/sgX device on Linux via SG_IO ioctl.

    Usage:
        transport = LinuxScsiTransport('/dev/sg0')
        if transport.open():
            transport.send_cdb(cdb_bytes, data_bytes)
            result = transport.read_cdb(cdb_bytes, length)
            transport.close()
    """

    def __init__(self, device_path: str) -> None:
        self._path = device_path
        self._fd: int | None = None
        # Pre-allocated write buffers keyed by data length (avoids alloc per frame)
        self._write_bufs: dict[int, tuple] = {}

    def open(self) -> bool:
        """Open the SCSI generic device file descriptor."""
        if self._fd is not None:
            return True
        try:
            self._fd = os.open(self._path, os.O_RDWR | os.O_NONBLOCK)
            return True
        except OSError as e:
            log.error("Failed to open %s: %s", self._path, e)
            return False

    def close(self) -> None:
        """Close the device file descriptor."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._write_bufs.clear()

    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        """Send a SCSI CDB with write data via SG_IO ioctl.

        Returns True if the ioctl succeeded (status == 0).
        Raises OSError if SG_IO is unavailable (caller falls back to sg_raw).
        """
        import fcntl  # Unix-only

        if self._fd is None:
            raise OSError("Device not open")

        cdb_len = len(cdb)
        data_len = len(data)
        bufs = self._write_bufs.get(data_len)
        if bufs is None:
            cdb_buf = (ctypes.c_ubyte * cdb_len)()
            data_buf = (ctypes.c_ubyte * data_len)()
            sense_buf = (ctypes.c_ubyte * 32)()
            hdr = _SgIoHdr()
            ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
            hdr.interface_id = ord('S')
            hdr.dxfer_direction = _SG_DXFER_TO_DEV
            hdr.cmd_len = cdb_len
            hdr.mx_sb_len = 32
            hdr.dxfer_len = data_len
            hdr.dxferp = ctypes.addressof(data_buf)
            hdr.cmdp = ctypes.addressof(cdb_buf)
            hdr.sbp = ctypes.addressof(sense_buf)
            hdr.timeout = 10000
            bufs = (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)
            self._write_bufs[data_len] = bufs

        cdb_buf, data_buf, _sense, hdr, ioctl_buf = bufs
        ctypes.memmove(cdb_buf, cdb, cdb_len)
        ctypes.memmove(data_buf, data, data_len)
        ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
        fcntl.ioctl(self._fd, _SG_IO, ioctl_buf)
        ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)
        return hdr.status == 0

    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        """Send a SCSI CDB and read back data via SG_IO ioctl.

        Returns the response bytes (may be shorter than length on partial read).
        Raises OSError if SG_IO is unavailable (caller falls back to sg_raw).
        """
        import fcntl  # Unix-only

        if self._fd is None:
            raise OSError("Device not open")

        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        data_buf = (ctypes.c_ubyte * length)()
        sense_buf = (ctypes.c_ubyte * 32)()

        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_FROM_DEV
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = 32
        hdr.dxfer_len = length
        hdr.dxferp = ctypes.addressof(data_buf)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.sbp = ctypes.addressof(sense_buf)
        hdr.timeout = 10000

        buf = ctypes.create_string_buffer(ctypes.sizeof(hdr))
        ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))
        fcntl.ioctl(self._fd, _SG_IO, buf)
        ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))

        if hdr.status != 0:
            return b''
        actual = length - hdr.resid
        return bytes(data_buf[:actual])

    def __enter__(self) -> LinuxScsiTransport:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
