"""Finding a panel's \\\\.\\PhysicalDriveN through SetupAPI -- the pure walk.

The ctypes calls are Windows-only and checked on the win11 VM; the decisions
-- which instance ID is the USB device, how far up to look -- run anywhere.
"""
from __future__ import annotations

import pytest

from trcc.adapters.system._windows_drives import usb_ancestor, usb_ids

# The chain a USB SCSI panel's disk sits on, as Windows names it.
_DISK = r"USBSTOR\DISK&VEN_USBLCD&PROD_USB_PRC_SYSTEM&REV_1.00\7&2A3B4C5D&0"
_IFACE = r"USB\VID_0402&PID_3922&MI_00\6&1234ABCD&0&0000"
_DEVICE = r"USB\VID_0402&PID_3922\5&ABCDEF01&0&4"


@pytest.mark.parametrize(("instance", "ids"), [
    (_DEVICE, (0x0402, 0x3922)),
    (_IFACE, (0x0402, 0x3922)),
    (r"usb\vid_87cd&pid_70db\x", (0x87CD, 0x70DB)),
    (_DISK, None),
    (r"USB\ROOT_HUB30\4&1", None),
    (r"HID\VID_0402&PID_3922\x", None),          # not a USB device ID
])
def test_usb_ids_reads_vid_pid_off_a_usb_instance_id(instance, ids) -> None:
    assert usb_ids(instance) == ids


def test_the_walk_climbs_from_the_disk_to_its_usb_device() -> None:
    """disk -> USB interface (composite) -> device; the first USB ID wins."""
    parents = {_DISK: _IFACE, _IFACE: _DEVICE, _DEVICE: r"USB\ROOT_HUB30\4&1"}
    assert usb_ancestor(_DISK, parents.get) == (0x0402, 0x3922)


def test_a_disk_with_no_usb_ancestor_is_not_a_panel() -> None:
    """An internal drive: SCSI/NVMe up to the root, no USB\\VID anywhere."""
    parents = {r"SCSI\DISK&VEN_NVME\4&1": r"PCI\VEN_8086&DEV_A0F3\3&1",
               r"PCI\VEN_8086&DEV_A0F3\3&1": r"ACPI\PNP0A08\0"}
    assert usb_ancestor(r"SCSI\DISK&VEN_NVME\4&1", parents.get) is None


def test_the_walk_stops_after_a_few_parents() -> None:
    """A cycle (or a deep tree) cannot loop: a bounded climb.  MUTATION CHECK:
    unbound the loop -- this hangs."""
    calls: list[str] = []

    def parent_of(instance: str) -> str:
        calls.append(instance)
        return instance                                   # its own parent
    assert usb_ancestor(_DISK, parent_of) is None
    assert len(calls) <= 5
