"""Spotting RGB controllers from sysfs -- what Find reads, opening nothing."""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.system._rgb_hardware import SysfsRgbHardware, first_usage
from trcc.adapters.system.linux import LinuxOS
from trcc.core.models import BoardIdentity, HidInterface

#: The maintainer's Aura controller's descriptor, as sysfs gave it.
AURA_DESCRIPTOR = bytes.fromhex("0672ff09a1a101")


@pytest.mark.parametrize(("descriptor", "pair"), [
    (AURA_DESCRIPTOR, (0xFF72, 0x00A1)),
    (bytes.fromhex("05010906a101"), (0x0001, 0x0006)),      # a keyboard
    (bytes.fromhex("05010902a101050c0901"), (0x0001, 0x0002)),  # after: ignored
    (bytes.fromhex("fe0200aabb0672ff09a1a101"), (0xFF72, 0x00A1)),  # long item
    (b"", (0, 0)),
])
def test_the_first_usage_is_the_one_hidapi_reports(descriptor: bytes,
                                                   pair: tuple[int, int]) -> None:
    assert first_usage(descriptor) == pair


def _hid(sys_root: Path, name: str, bus: int, interface: int,
         descriptor: bytes) -> None:
    """One HID device the way sysfs lays it out: a link under bus/hid into
    its USB interface, whose directory holds ``bInterfaceNumber``."""
    usb_interface = sys_root / "devices" / f"1-1:1.{interface}-{name}"
    device = usb_interface / name
    (device / "hidraw" / "hidraw0").mkdir(parents=True)
    (usb_interface / "bInterfaceNumber").write_text(f"{interface:02x}\n")
    vid, pid = name.split(":")[1:3]
    (device / "uevent").write_text(
        f"DRIVER=hid-generic\nHID_ID={bus:04X}:0000{vid}:0000{pid[:4]}\n"
        f"HID_NAME=Board {vid}\n")
    (device / "report_descriptor").write_bytes(descriptor)
    links = sys_root / "bus" / "hid" / "devices"
    links.mkdir(parents=True, exist_ok=True)
    (links / name).symlink_to(device)


def test_sysfs_lists_usb_hid_interfaces_and_names_the_board(tmp_path) -> None:
    dmi = tmp_path / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "board_vendor").write_text("ASUSTeK COMPUTER INC.\n")
    (dmi / "board_name").write_text("PRIME Z790-V AX\n")
    _hid(tmp_path, "0003:0B05:19AF.0001", 0x0003, 2, AURA_DESCRIPTOR)
    _hid(tmp_path, "0005:046D:B023.0002", 0x0005, 0, AURA_DESCRIPTOR)  # bluetooth
    hardware = SysfsRgbHardware(tmp_path)
    assert hardware.board() == BoardIdentity("ASUSTeK COMPUTER INC.",
                                             "PRIME Z790-V AX")
    assert hardware.hid_interfaces() == (HidInterface(
        0x0B05, 0x19AF, 2, 0xFF72, 0x00A1, "Board 0B05", "/dev/hidraw0"),)


def test_no_dmi_and_no_hid_bus_find_nothing(tmp_path) -> None:
    hardware = SysfsRgbHardware(tmp_path)
    assert hardware.board() is None
    assert hardware.hid_interfaces() == ()


def test_linux_names_the_sysfs_reader_and_other_platforms_find_nothing(fake_platform) -> None:
    assert LinuxOS.RGB_HARDWARE is SysfsRgbHardware
    assert fake_platform.rgb_hardware().hid_interfaces() == ()
    assert fake_platform.rgb_hardware().board() is None
