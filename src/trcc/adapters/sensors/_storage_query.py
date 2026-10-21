"""``WindowsDisk`` -- drive temperatures on Windows, through Windows itself.

``IOCTL_STORAGE_QUERY_PROPERTY`` with ``StorageDeviceTemperatureProperty``
(52, Windows 10+) returns a drive's temperature from its own driver: the NVMe
driver reads the health log, the SATA driver SMART.  Asked on a handle opened
with NO access rights, which needs no admin -- LibreHardwareMonitor, the only
other reader TRCC had for disks, runs elevated.  Until 2026-10-20 a Windows
disk showed no temperature unless LHM ran beside TRCC.

**A USB drive is never asked.**  The LCD panel itself is a USB mass-storage
drive, and a property the driver must turn into a SCSI command is exactly what
stalled it into port resets on the win11 VM (2026-10-20).  Only the bus type
is read from every drive -- ``StorageDeviceProperty`` (0), which the class
driver answers from the descriptor it already holds, with no device I/O --
and the temperature only from the buses in :data:`_ASKED_BUSES`.

Layouts from ``ntddstor.h`` (``STORAGE_PROPERTY_QUERY``,
``STORAGE_DEVICE_DESCRIPTOR``, ``STORAGE_TEMPERATURE_DATA_DESCRIPTOR``).
Keys are ``drive:<n>``, n being the ``\\\\.\\PhysicalDriveN`` number.
"""
from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame
from ...core.ports import DiskSource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_PROPERTY_DEVICE, _PROPERTY_TEMPERATURE = 0, 52
_STANDARD_QUERY = 0
_SHARE_READ_WRITE, _OPEN_EXISTING = 0x3, 3
#: ``STORAGE_BUS_TYPE`` values whose drives are asked their temperature.
#: USB (7) is absent on purpose -- see the module docstring.
_ASKED_BUSES = {3: "ATA", 8: "RAID", 10: "SAS", 11: "SATA", 17: "NVMe"}
_NOT_REPORTED = -32768                  # 0x8000: the slot has no reading
_MAX_DRIVES = 32
_BUFFER = 1024


class _PropertyQuery(ctypes.Structure):
    """``STORAGE_PROPERTY_QUERY``."""

    _fields_ = [("PropertyId", ctypes.c_uint32), ("QueryType", ctypes.c_uint32),
                ("AdditionalParameters", ctypes.c_uint8 * 4)]


class _DeviceDescriptor(ctypes.Structure):
    """``STORAGE_DEVICE_DESCRIPTOR`` up to its variable tail; the strings sit
    in the same buffer at the offsets it names (0 = absent)."""

    _fields_ = [("Version", ctypes.c_uint32), ("Size", ctypes.c_uint32),
                ("DeviceType", ctypes.c_uint8), ("DeviceTypeModifier", ctypes.c_uint8),
                ("RemovableMedia", ctypes.c_uint8), ("CommandQueueing", ctypes.c_uint8),
                ("VendorIdOffset", ctypes.c_uint32), ("ProductIdOffset", ctypes.c_uint32),
                ("ProductRevisionOffset", ctypes.c_uint32),
                ("SerialNumberOffset", ctypes.c_uint32), ("BusType", ctypes.c_uint32),
                ("RawPropertiesLength", ctypes.c_uint32)]


class _TemperatureHeader(ctypes.Structure):
    """``STORAGE_TEMPERATURE_DATA_DESCRIPTOR`` up to its ``TemperatureInfo[]``."""

    _fields_ = [("Version", ctypes.c_uint32), ("Size", ctypes.c_uint32),
                ("CriticalTemperature", ctypes.c_int16), ("WarningTemperature", ctypes.c_int16),
                ("InfoCount", ctypes.c_uint16), ("Reserved0", ctypes.c_uint8 * 2),
                ("Reserved1", ctypes.c_uint32 * 2)]


class _TemperatureInfo(ctypes.Structure):
    """``STORAGE_TEMPERATURE_INFO``: one sensor of the drive, °C."""

    _fields_ = [("Index", ctypes.c_uint16), ("Temperature", ctypes.c_int16),
                ("OverThreshold", ctypes.c_int16), ("UnderThreshold", ctypes.c_int16),
                ("OverThresholdChangable", ctypes.c_uint8),
                ("UnderThresholdChangable", ctypes.c_uint8),
                ("EventGenerated", ctypes.c_uint8), ("Reserved0", ctypes.c_uint8),
                ("Reserved1", ctypes.c_uint32)]


def _string_at(data: bytes, offset: int) -> str:
    """The NUL-terminated ASCII string at *offset*, or '' for offset 0."""
    if not 0 < offset < len(data):
        frame_log.debug("_string_at: offset %d -- absent", offset)
        return ""
    text = data[offset:].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    frame_log.debug("_string_at: %d -> %r", offset, text)
    return text


def describe(data: bytes) -> tuple[int, str] | None:
    """(bus type, model name) from a ``STORAGE_DEVICE_DESCRIPTOR``, or None
    when the answer is too short to be one."""
    if len(data) < ctypes.sizeof(_DeviceDescriptor):
        log.debug("describe: %d bytes -- not a device descriptor", len(data))
        return None
    head = _DeviceDescriptor.from_buffer_copy(data)
    name = " ".join(part for part in (_string_at(data, head.VendorIdOffset),
                                      _string_at(data, head.ProductIdOffset)) if part)
    log.debug("describe: bus %d %r", head.BusType, name)
    return head.BusType, name


def temperature(data: bytes) -> float | None:
    """The drive's temperature in °C: the first sensor that reports one (the
    NVMe composite comes first)."""
    header = ctypes.sizeof(_TemperatureHeader)
    if len(data) < header:
        frame_log.debug("temperature: %d bytes -- no descriptor", len(data))
        return None
    count = _TemperatureHeader.from_buffer_copy(data).InfoCount
    entry = ctypes.sizeof(_TemperatureInfo)
    for i in range(count):
        start = header + i * entry
        if start + entry > len(data):
            break
        value = _TemperatureInfo.from_buffer_copy(data, start).Temperature
        if value != _NOT_REPORTED:
            frame_log.debug("temperature: sensor %d of %d -> %d °C", i, count, value)
            return float(value)
    frame_log.debug("temperature: none of %d sensor(s) report", count)
    return None


class _Drives:
    """``\\\\.\\PhysicalDriveN`` through kernel32 -- the only Windows-bound part."""

    def __init__(self) -> None:
        log.debug("_Drives: binding kernel32")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        k32.CreateFileW.restype = vp
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, u32, u32, vp, u32, u32, vp]
        k32.DeviceIoControl.argtypes = [vp, u32, vp, u32, vp, u32, ctypes.POINTER(u32), vp]
        k32.CloseHandle.argtypes = [vp]
        self._k32 = k32
        self._invalid = vp(-1).value

    def query(self, number: int, property_id: int) -> bytes | None:
        """One property of drive *number*; None when there is no such drive or
        it does not answer.  The handle asks for no access rights."""
        handle = self._k32.CreateFileW(rf"\\.\PhysicalDrive{number}", 0, _SHARE_READ_WRITE,
                                       None, _OPEN_EXISTING, 0, None)
        if handle in (None, self._invalid):
            frame_log.debug("_Drives.query: no PhysicalDrive%d", number)
            return None
        try:
            request = _PropertyQuery(PropertyId=property_id, QueryType=_STANDARD_QUERY)
            out = ctypes.create_string_buffer(_BUFFER)
            returned = ctypes.c_uint32()
            if not self._k32.DeviceIoControl(
                    handle, _IOCTL_STORAGE_QUERY_PROPERTY, ctypes.byref(request),
                    ctypes.sizeof(request), out, _BUFFER, ctypes.byref(returned), None):
                frame_log.debug("_Drives.query: PhysicalDrive%d property %d -> error %d",
                                number, property_id, ctypes.get_last_error())  # pyright: ignore[reportAttributeAccessIssue]
                return None
            return out.raw[:returned.value]
        finally:
            self._k32.CloseHandle(handle)


class WindowsDisk(DiskSource):
    """One drive's temperature, asked of Windows each read."""

    def __init__(self, drives: Any, number: int, name: str, bus: str) -> None:
        log.info("WindowsDisk: drive:%d %s (%s)", number, name, bus)
        self._drives, self._number, self._name = drives, number, name

    @property
    def key(self) -> str:
        frame_log.debug("WindowsDisk.key")
        return f"drive:{self._number}"

    @property
    def name(self) -> str:
        frame_log.debug("WindowsDisk.name")
        return self._name

    def temp(self) -> float | None:
        data = self._drives.query(self._number, _PROPERTY_TEMPERATURE)
        value = temperature(data) if data is not None else None
        frame_log.debug("WindowsDisk.temp: drive:%d -> %s", self._number, value)
        return value


def discover_windows_disks(drives_factory: Callable[[], Any] = _Drives) -> list[DiskSource]:
    """One :class:`WindowsDisk` per internal drive that reports a temperature."""
    log.info("discover_windows_disks: called")
    try:
        drives = drives_factory()
    except (OSError, AttributeError) as e:
        log.info("discover_windows_disks: not Windows (%s)", e)
        return []
    disks: list[DiskSource] = []
    for number in range(_MAX_DRIVES):
        data = drives.query(number, _PROPERTY_DEVICE)
        described = describe(data) if data is not None else None
        if described is None:
            continue
        bus_type, name = described
        if (bus := _ASKED_BUSES.get(bus_type)) is None:
            log.info("discover_windows_disks: PhysicalDrive%d %r is on bus %d -- not "
                     "asked (USB drives never are: the LCD panel is one)",
                     number, name, bus_type)
            continue
        reading = drives.query(number, _PROPERTY_TEMPERATURE)
        if reading is None or temperature(reading) is None:
            log.warning("discover_windows_disks: PhysicalDrive%d %r (%s) reports no "
                        "temperature to Windows -- skipped", number, name, bus)
            continue
        disks.append(WindowsDisk(drives, number, name or f"Drive {number}", bus))
    log.info("discover_windows_disks: %d drive(s)", len(disks))
    return disks
