"""Windows SCSI passthrough transport.

Replaces Linux sg_raw subprocess calls with Windows DeviceIoControl
IOCTL_SCSI_PASS_THROUGH_DIRECT for sending raw SCSI CDBs to USB
mass-storage LCD devices.

References:
    - C# TRCC: USBLCDNEW.SendSCSICommand() uses CreateFile + DeviceIoControl
    - Windows API: IOCTL_SCSI_PASS_THROUGH_DIRECT (0x4D014)
    - ctypes structs: SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes  # pyright: ignore[reportMissingImports]
import logging

from trcc.legacy.adapters.device.scsi import ScsiTransport

log = logging.getLogger(__name__)

# Windows IOCTL codes
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014  # METHOD_OUT_DIRECT — DMA (writes)
IOCTL_SCSI_PASS_THROUGH        = 0x4D004  # METHOD_BUFFERED   — copied (reads)

# SCSI directions
SCSI_IOCTL_DATA_OUT = 0  # Host → Device (sending frame data)
SCSI_IOCTL_DATA_IN = 1   # Device → Host (reading response)

_SENSE_LENGTH = 32


class SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    """Windows SCSI_PASS_THROUGH_DIRECT structure."""
    _fields_ = [
        ('Length', ctypes.wintypes.USHORT),
        ('ScsiStatus', ctypes.c_ubyte),
        ('PathId', ctypes.c_ubyte),
        ('TargetId', ctypes.c_ubyte),
        ('Lun', ctypes.c_ubyte),
        ('CdbLength', ctypes.c_ubyte),
        ('SenseInfoLength', ctypes.c_ubyte),
        ('DataIn', ctypes.c_ubyte),
        ('DataTransferLength', ctypes.wintypes.ULONG),
        ('TimeOutValue', ctypes.wintypes.ULONG),
        ('DataBuffer', ctypes.c_void_p),
        ('SenseInfoOffset', ctypes.wintypes.ULONG),
        ('Cdb', ctypes.c_ubyte * 16),
    ]


class SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(ctypes.Structure):
    """SCSI_PASS_THROUGH_DIRECT + sense buffer."""
    _fields_ = [
        ('sptd', SCSI_PASS_THROUGH_DIRECT),
        ('sense', ctypes.c_ubyte * _SENSE_LENGTH),
    ]


class SCSI_PASS_THROUGH(ctypes.Structure):
    """SCSI_PASS_THROUGH — buffered (METHOD_BUFFERED), used for reads.

    DataBufferOffset/SenseInfoOffset are byte offsets from the start of the
    IOCTL buffer, not pointers.  DataBufferOffset is ULONG_PTR (8 bytes on
    x64); c_size_t matches the platform so ctypes alignment is correct.

    Buffer layout: [SCSI_PASS_THROUGH][sense data][data buffer]
    Windows copies the whole buffer through kernel space — no DMA, no
    page-locking needed.  This avoids error 121 (ERROR_SEM_TIMEOUT) that
    IOCTL_SCSI_PASS_THROUGH_DIRECT produces on reads because ctypes heap
    memory is not in non-paged pool.
    """
    _fields_ = [
        ('Length',             ctypes.c_uint16),
        ('ScsiStatus',         ctypes.c_uint8),
        ('PathId',             ctypes.c_uint8),
        ('TargetId',           ctypes.c_uint8),
        ('Lun',                ctypes.c_uint8),
        ('CdbLength',          ctypes.c_uint8),
        ('SenseInfoLength',    ctypes.c_uint8),
        ('DataIn',             ctypes.c_uint8),
        ('DataTransferLength', ctypes.c_uint32),
        ('TimeOutValue',       ctypes.c_uint32),
        ('DataBufferOffset',   ctypes.c_size_t),  # ULONG_PTR: 8 bytes on x64
        ('SenseInfoOffset',    ctypes.c_uint32),
        ('Cdb',                ctypes.c_uint8 * 16),
    ]


class WindowsScsiTransport(ScsiTransport):
    """Send raw SCSI commands to a USB device on Windows.

    Equivalent to Linux's `sg_raw` subprocess calls but using
    Windows DeviceIoControl with IOCTL_SCSI_PASS_THROUGH_DIRECT.

    Usage:
        transport = WindowsScsiTransport('\\\\.\\PhysicalDrive2')
        transport.open()
        transport.send_cdb(cdb_bytes, data_bytes)
        transport.close()
    """

    def __init__(self, device_path: str) -> None:
        self._device_path = device_path
        self._handle: int | None = None

    def open(self) -> bool:
        """Open the physical drive for SCSI passthrough."""
        try:
            kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
            GENERIC_READ_WRITE = 0xC0000000
            FILE_SHARE_READ_WRITE = 0x3
            OPEN_EXISTING = 3

            self._handle = kernel32.CreateFileW(
                self._device_path,
                GENERIC_READ_WRITE,
                FILE_SHARE_READ_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if self._handle == -1:
                self._handle = None
                log.error("Failed to open %s", self._device_path)
                return False
            return True
        except Exception:
            log.exception("Failed to open SCSI device %s", self._device_path)
            return False

    def close(self) -> None:
        """Close the device handle."""
        if self._handle is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)  # pyright: ignore[reportAttributeAccessIssue]
            except (OSError, AttributeError) as e:
                log.debug("CloseHandle raised: %s", e)
            self._handle = None

    def send_cdb(
        self,
        cdb: bytes,
        data: bytes,
        *,
        timeout: int = 5,
    ) -> bool:
        """Send a SCSI CDB with data payload.

        Args:
            cdb: SCSI Command Descriptor Block (6-16 bytes)
            data: Data to send (frame bytes for LCD)
            timeout: Timeout in seconds

        Returns:
            True if DeviceIoControl succeeded
        """
        if self._handle is None:
            log.error("SCSI device not open")
            return False

        # Allocate data buffer
        data_buf = (ctypes.c_ubyte * len(data))(*data)

        # Build SCSI_PASS_THROUGH_DIRECT
        sptdwb = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
        sptd = sptdwb.sptd
        sptd.Length = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
        sptd.CdbLength = len(cdb)
        sptd.SenseInfoLength = 32
        sptd.DataIn = SCSI_IOCTL_DATA_OUT
        sptd.DataTransferLength = len(data)
        sptd.TimeOutValue = timeout
        sptd.DataBuffer = ctypes.addressof(data_buf)
        sptd.SenseInfoOffset = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)

        # Copy CDB bytes
        for i, b in enumerate(cdb[:16]):
            sptd.Cdb[i] = b

        # DeviceIoControl
        bytes_returned = ctypes.wintypes.DWORD(0)
        try:
            ok = ctypes.windll.kernel32.DeviceIoControl(  # pyright: ignore[reportAttributeAccessIssue]
                self._handle,
                IOCTL_SCSI_PASS_THROUGH_DIRECT,
                ctypes.byref(sptdwb),
                ctypes.sizeof(sptdwb),
                ctypes.byref(sptdwb),
                ctypes.sizeof(sptdwb),
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                error = ctypes.GetLastError()  # pyright: ignore[reportAttributeAccessIssue]
                log.error("DeviceIoControl failed: error %d", error)
                return False
            if sptd.ScsiStatus != 0:
                log.warning("SCSI status %d", sptd.ScsiStatus)
                return False
            return True
        except Exception:
            log.exception("SCSI passthrough failed")
            return False

    def read_cdb(
        self,
        cdb: bytes,
        length: int,
        *,
        timeout: int = 5,
    ) -> bytes:
        """Send a SCSI CDB and read response data.

        Uses IOCTL_SCSI_PASS_THROUGH (buffered) instead of the DIRECT variant
        so that the data buffer does not need to be in non-paged pool.
        The DIRECT variant fails with error 121 (ERROR_SEM_TIMEOUT) on reads
        because ctypes heap memory cannot be page-locked for DMA.

        Buffer layout: [SCSI_PASS_THROUGH struct][sense data][data buffer]

        Args:
            cdb: SCSI Command Descriptor Block (6-16 bytes)
            length: Expected response length in bytes
            timeout: Timeout in seconds

        Returns:
            Response bytes, or empty bytes on failure.
        """
        if self._handle is None:
            log.error("SCSI device not open")
            return b''

        spt_size = ctypes.sizeof(SCSI_PASS_THROUGH)
        sense_offset = spt_size
        data_offset = sense_offset + _SENSE_LENGTH
        total = data_offset + length

        buf = (ctypes.c_uint8 * total)()

        # Overlay the struct onto the buffer
        spt = SCSI_PASS_THROUGH.from_buffer(buf)
        spt.Length = spt_size
        spt.CdbLength = len(cdb)
        spt.SenseInfoLength = _SENSE_LENGTH
        spt.DataIn = SCSI_IOCTL_DATA_IN
        spt.DataTransferLength = length
        spt.TimeOutValue = timeout
        spt.SenseInfoOffset = sense_offset
        spt.DataBufferOffset = data_offset
        for i, b in enumerate(cdb[:16]):
            spt.Cdb[i] = b

        bytes_returned = ctypes.wintypes.DWORD(0)
        try:
            ok = ctypes.windll.kernel32.DeviceIoControl(  # pyright: ignore[reportAttributeAccessIssue]
                self._handle,
                IOCTL_SCSI_PASS_THROUGH,
                buf,
                total,
                buf,
                total,
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                error = ctypes.GetLastError()  # pyright: ignore[reportAttributeAccessIssue]
                log.error("DeviceIoControl read failed: error %d", error)
                return b''
            if spt.ScsiStatus != 0:
                log.warning("SCSI read status %d", spt.ScsiStatus)
                return b''
            return bytes(buf[data_offset:data_offset + length])
        except Exception:
            log.exception("SCSI read passthrough failed")
            return b''

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
