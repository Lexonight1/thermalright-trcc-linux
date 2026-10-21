"""W3: drive temperatures on Windows, through IOCTL_STORAGE_QUERY_PROPERTY.

The native call needs Windows; pinned here, on any OS: the layouts against
``ntddstor.h``, how an answer becomes a name and a temperature, and -- the
rule that protects the panel -- that a USB drive is never asked anything but
its bus type.
"""
from __future__ import annotations

import ctypes
import struct
from typing import Any

import pytest

from trcc.adapters.sensors import _storage_query as sq
from trcc.adapters.sensors import windows as win


@pytest.mark.parametrize(("layout", "size"), [
    (sq._PropertyQuery, 12),            # STORAGE_PROPERTY_QUERY (1-byte tail, padded)
    (sq._DeviceDescriptor, 36),         # STORAGE_DEVICE_DESCRIPTOR before RawDeviceProperties[1] (40 with it)
    (sq._TemperatureHeader, 24),        # STORAGE_TEMPERATURE_DATA_DESCRIPTOR header
    (sq._TemperatureInfo, 16),          # STORAGE_TEMPERATURE_INFO
])
def test_layouts_match_ntddstor(layout: type, size: int) -> None:
    assert ctypes.sizeof(layout) == size


def test_fields_sit_where_the_header_puts_them() -> None:
    assert (sq._DeviceDescriptor.BusType.offset, sq._TemperatureHeader.InfoCount.offset,
            sq._TemperatureInfo.Temperature.offset) == (28, 12, 2)


def _device(bus: int, product: str, vendor: str = "") -> bytes:
    """A STORAGE_DEVICE_DESCRIPTOR answer with its strings after the head."""
    head = ctypes.sizeof(sq._DeviceDescriptor)
    strings = b""
    offsets = []
    for text in (vendor, product):
        if text:
            offsets.append(head + len(strings))
            strings += text.encode() + b"\0"
        else:
            offsets.append(0)
    desc = sq._DeviceDescriptor(Version=40, Size=head + len(strings), BusType=bus,
                                VendorIdOffset=offsets[0], ProductIdOffset=offsets[1])
    return bytes(desc) + strings


def _temps(*values: int) -> bytes:
    """A STORAGE_TEMPERATURE_DATA_DESCRIPTOR answer with one entry per value."""
    head = sq._TemperatureHeader(Version=24, InfoCount=len(values))
    return bytes(head) + b"".join(
        bytes(sq._TemperatureInfo(Index=i, Temperature=v)) for i, v in enumerate(values))


def test_a_descriptor_names_its_drive_and_bus() -> None:
    assert sq.describe(_device(17, "Samsung SSD 980 PRO 1TB")) == (17, "Samsung SSD 980 PRO 1TB")
    assert sq.describe(_device(11, "ST2000DM008-2FR102 ", vendor="ATA")) == (
        11, "ATA ST2000DM008-2FR102")
    assert sq.describe(b"\0" * 8) is None


def test_the_first_reporting_sensor_is_the_temperature() -> None:
    """0x8000 marks a sensor with no reading; the NVMe composite comes first.

    MUTATION CHECK: drop the 0x8000 test -- reads -32768."""
    assert sq.temperature(_temps(41, 55)) == 41.0
    assert sq.temperature(_temps(-32768, 38)) == 38.0
    assert sq.temperature(_temps(-32768)) is None
    assert sq.temperature(struct.pack("<II", 24, 24)) is None       # short: no header


class _FakeDrives:
    """PhysicalDriveN -> {property: answer}; records every question asked."""

    def __init__(self, drives: dict[int, dict[int, bytes]]) -> None:
        self._drives = drives
        self.asked: list[tuple[int, int]] = []

    def query(self, number: int, property_id: int) -> bytes | None:
        self.asked.append((number, property_id))
        return self._drives.get(number, {}).get(property_id)


_DEVICE, _TEMP = sq._PROPERTY_DEVICE, sq._PROPERTY_TEMPERATURE


def test_a_usb_drive_is_never_asked_its_temperature() -> None:
    """The LCD panel IS a USB drive.  A temperature query the driver turns into
    a SCSI command is what stalled it into port resets on the win11 VM.

    MUTATION CHECK: add USB (7) to _ASKED_BUSES -- drive 1 is asked."""
    drives = _FakeDrives({
        0: {_DEVICE: _device(17, "WD_BLACK SN850X 2TB"), _TEMP: _temps(44)},
        1: {_DEVICE: _device(7, "USBLCD", vendor="ALi"), _TEMP: _temps(30)},
    })
    disks = sq.discover_windows_disks(lambda: drives)
    assert [(d.key, d.name, d.temp()) for d in disks] == [("drive:0", "WD_BLACK SN850X 2TB", 44.0)]
    assert (1, _TEMP) not in drives.asked
    assert (1, _DEVICE) in drives.asked


def test_a_drive_that_reports_no_temperature_is_left_for_lhm() -> None:
    """No answer, or an answer whose every sensor is 0x8000, is no temperature.

    MUTATION CHECK: skip only a drive that does not answer -- drive 1 is kept."""
    drives = _FakeDrives({
        0: {_DEVICE: _device(11, "Old SATA HDD")},
        1: {_DEVICE: _device(11, "SATA SSD"), _TEMP: _temps(-32768)},
        2: {_DEVICE: _device(17, "NVMe disk"), _TEMP: _temps(39)},
    })
    assert [d.key for d in sq.discover_windows_disks(lambda: drives)] == ["drive:2"]


def test_off_windows_there_are_no_drives() -> None:
    def missing() -> Any:
        raise AttributeError("module 'ctypes' has no attribute 'WinDLL'")

    assert sq.discover_windows_disks(missing) == []


class _Named:
    def __init__(self, key: str, name: str) -> None:
        self.key, self.name = key, name


def test_lhm_adds_only_drives_windows_did_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """One drive must not appear twice in the disk picker.

    MUTATION CHECK: keep every LHM drive -- the 980 PRO appears twice."""
    native = [_Named("drive:0", "Samsung SSD 980 PRO 1TB")]
    lhm = [_Named("lhm:nvme:0", "Samsung  SSD 980 PRO 1TB"), _Named("lhm:hdd:1", "ST2000DM008")]
    monkeypatch.setattr(win, "discover_windows_disks", lambda: native)
    monkeypatch.setattr(win, "discover_lhm_disks", lambda: lhm)
    assert [d.key for d in win._build_windows_disks()] == ["drive:0", "lhm:hdd:1"]
