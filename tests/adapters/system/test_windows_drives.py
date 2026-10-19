"""Finding a panel's \\\\.\\PhysicalDriveN through SetupAPI -- the pure walk.

The ctypes calls are Windows-only and checked on the win11 VM; the decisions
-- which instance ID is the USB device, which socket it is in, how far up to
look -- run anywhere.  The instance IDs and location paths below are the ones
the VM reported (2026-10-18): the panel, and two identical QEMU USB disks on
port 5 of two controllers, which libusb named ``1-5`` and ``2-5``.
"""
from __future__ import annotations

from itertools import pairwise

import pytest

from trcc.adapters.system._windows_drives import (
    UsbDisk,
    ancestors,
    port_chain,
    usb_disk,
    usb_ids,
)

# The chain a USB SCSI panel's disk sits on, as Windows names it.
_DISK = r"USBSTOR\DISK&VEN_USBLCD&PROD_USB_PRC_SYSTEM&REV_\7&11AEA3E&0"
_IFACE = r"USB\VID_0402&PID_3922&MI_00\6&1234ABCD&0&0000"
_DEVICE = r"USB\VID_0402&PID_3922\6&1C4D2F9B&0&18"
_ROOT_1 = r"USB\ROOT_HUB30\5&1E461E56&0&0"
_ROOT_2 = r"USB\ROOT_HUB30\5&2C9B21AE&0&0"
_HCD_1 = r"PCI\VEN_1B36&DEV_000D&SUBSYS_11001AF4&REV_01\4&15F05328&0&0011"


@pytest.mark.parametrize(("instance", "ids"), [
    (_DEVICE, (0x0402, 0x3922)),
    (_IFACE, (0x0402, 0x3922)),
    (r"usb\vid_87cd&pid_70db\x", (0x87CD, 0x70DB)),
    (_DISK, None),
    (_ROOT_1, None),
    (r"HID\VID_0402&PID_3922\x", None),          # not a USB device ID
])
def test_usb_ids_reads_vid_pid_off_a_usb_instance_id(instance, ids) -> None:
    assert usb_ids(instance) == ids


@pytest.mark.parametrize(("paths", "chain"), [
    (["PCIROOT(0)#PCI(0201)#PCI(0000)#USBROOT(0)#USB(18)",
      "ACPI(_SB_)#ACPI(PCI0)#ACPI(S11_)#ACPI(S00_)#USBROOT(0)#USB(18)"], "18"),
    (["PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)#USB(2)"], "3.2"),     # behind a hub
    (["PCIROOT(0)#PCI(0201)#PCI(0000)#USBROOT(0)"], None),         # the root hub
    (["PCIROOT(0)#PCI(0201)"], None),
    ([], None),
])
def test_the_port_chain_is_the_usb_hops_below_the_root_hub(paths, chain) -> None:
    assert port_chain(paths) == chain


def _paths(port: int, pci: str = "0201") -> list[str]:
    return [f"PCIROOT(0)#PCI({pci})#PCI(0000)#USBROOT(0)#USB({port})"]


def test_a_disk_is_named_by_its_socket_as_libusb_names_it() -> None:
    """The panel on the VM: libusb ``1-18``."""
    chain = [_DISK, _DEVICE, _ROOT_1, _HCD_1]
    assert usb_disk(chain, {_DEVICE: _paths(18)}.__getitem__,
                    [_ROOT_1, _ROOT_2]) == UsbDisk(0x0402, 0x3922, "1-18")


def test_twins_differ_only_by_bus_and_get_their_own_names() -> None:
    """Two identical disks on port 5 of two controllers: libusb ``1-5`` /
    ``2-5``.  The bus is the root hub's place in the list, plus one.

    MUTATION CHECK: number buses from 0 -- both names shift and fail."""
    twin_a = r"USB\VID_46F4&PID_0001\6&A&0&5"
    twin_b = r"USB\VID_46F4&PID_0001\6&B&0&5"
    paths = {twin_a: _paths(5), twin_b: _paths(5, "0204")}.__getitem__
    roots = [_ROOT_1, _ROOT_2]
    a = usb_disk([_DISK, twin_a, _ROOT_1], paths, roots)
    b = usb_disk([_DISK, twin_b, _ROOT_2], paths, roots)
    assert (a, b) == (UsbDisk(0x46F4, 0x0001, "1-5"), UsbDisk(0x46F4, 0x0001, "2-5"))


def test_a_composite_device_is_named_by_its_first_usb_node() -> None:
    """disk -> interface -> device: the interface carries the ids, and its
    location path is the device's port."""
    chain = [_DISK, _IFACE, _DEVICE, _ROOT_1]
    assert usb_disk(chain, {_IFACE: _paths(7)}.__getitem__,
                    [_ROOT_1]) == UsbDisk(0x0402, 0x3922, "1-7")


@pytest.mark.parametrize(("chain", "paths"), [
    ([_DISK, _DEVICE, _ROOT_2], _paths(18)),           # a root hub not listed
    ([_DISK, _DEVICE], _paths(18)),                    # no root hub in reach
    ([_DISK, _DEVICE, _ROOT_1], []),                   # no location path
])
def test_a_disk_windows_cannot_place_has_no_unit(chain, paths) -> None:
    """Unnamed, so it only matches an open that names no unit -- never the
    wrong twin."""
    assert usb_disk(chain, lambda _: paths, [_ROOT_1]) == UsbDisk(0x0402, 0x3922, "")


def test_a_disk_with_no_usb_ancestor_is_not_a_panel() -> None:
    """An internal drive: SCSI/NVMe up to the root, no USB\\VID anywhere."""
    chain = [r"SCSI\DISK&VEN_NVME\4&1", r"PCI\VEN_8086&DEV_A0F3\3&1", r"ACPI\PNP0A08\0"]
    assert usb_disk(chain, lambda _: [], [_ROOT_1]) is None


def test_the_walk_climbs_to_the_root() -> None:
    parents = {_DISK: _DEVICE, _DEVICE: _ROOT_1, _ROOT_1: _HCD_1}
    assert list(ancestors(_DISK, parents.get)) == [_DISK, _DEVICE, _ROOT_1, _HCD_1]


def test_the_walk_stops_after_a_bounded_climb() -> None:
    """A cycle cannot loop, yet five hubs deep still reaches the root hub.

    MUTATION CHECK: unbound the loop -- this hangs."""
    assert len(list(ancestors(_DISK, lambda node: node))) == 10
    deep = [_DISK, _DEVICE, *(f"USB\\HUB\\{n}" for n in range(5)), _ROOT_1]
    assert list(ancestors(_DISK, dict(pairwise(deep)).get)) == deep
