"""The motherboard RGB catalog generated from OpenRGB's detectors."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.registry import ALL_DEVICES
from trcc.core.rgb_catalog import MOTHERBOARD_CONTROLLERS

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_rgb_catalog  # noqa: E402

_DETECT = '''/*---------------------------------------------------------*\\
| FamilyDetect.cpp                                          |
|                                                           |
|   Detector for a family                                   |
|                                                           |
|   Ed Kambulow (dredvard)                      20 Dec 2020 |
|   Dongguan Yonghang Electronic Technology Co., Ltd        |
|                                                           |
|   This file is part of the OpenRGB project                |
|   SPDX-License-Identifier: GPL-2.0-or-later               |
\\*---------------------------------------------------------*/
#define AURA_USB_VID            0x0B05
#define AURA_MOTHERBOARD_3_PID  0x19AF
#define DETECTOR_NAME "Gigabyte RGB Fusion 2 USB"
#define IT8297_UPG 0xFF89
// REGISTER_HID_DETECTOR("commented out", DetectAsusAuraUSBMotherboards, 1, 2);
REGISTER_HID_DETECTOR_PU("ASUS Aura Motherboard", DetectAsusAuraUSBMotherboards,
                         AURA_USB_VID, AURA_MOTHERBOARD_3_PID,   0xFF72, 0x00A1);
REGISTER_HID_DETECTOR   ("ASUS Aura Keyboard", DetectAsusAuraUSBKeyboards, AURA_USB_VID, 0x1866);
REGISTER_HID_DETECTOR_DISABLED("off", DetectAsusAuraUSBMotherboards, AURA_USB_VID, 0x1111);
REGISTER_HID_DETECTOR_IPU(DETECTOR_NAME, DetectGigabyteRGBFusion2USBControllers, 0x048D, 0x5702, 0, IT8297_UPG, 0xCC);
'''


def _tree(tmp_path: Path) -> Path:
    family = tmp_path / "Controllers" / "Family"
    family.mkdir(parents=True)
    (family / "FamilyDetect.cpp").write_text(_DETECT)
    return tmp_path / "Controllers"


def test_registrations_become_rows_through_their_defines(tmp_path, monkeypatch) -> None:
    """Defines followed, a wrapped macro read whole; a comment, a disabled
    registration and a keyboard are not motherboards.

    MUTATION CHECK: drop the ``func not in MOTHERBOARD_DETECTORS`` filter."""
    monkeypatch.setattr(gen_rgb_catalog, "MOTHERBOARD_DETECTORS", frozenset({
        "DetectAsusAuraUSBMotherboards",
        "DetectGigabyteRGBFusion2USBControllers"}))
    rows, seen, authors = gen_rgb_catalog._rows(_tree(tmp_path))
    assert rows == [
        "    RgbControllerId(0x048D, 0x5702, 'Gigabyte RGB Fusion 2 USB', "
        "'DetectGigabyteRGBFusion2USBControllers', interface=0x0000, "
        "usage_page=0xFF89, usage=0x00CC),",
        "    RgbControllerId(0x0B05, 0x19AF, 'ASUS Aura Motherboard', "
        "'DetectAsusAuraUSBMotherboards', usage_page=0xFF72, usage=0x00A1),"]
    assert seen == {"DetectAsusAuraUSBMotherboards",
                    "DetectGigabyteRGBFusion2USBControllers"}
    assert authors == {"FamilyDetect.cpp": [
        "Ed Kambulow (dredvard) 20 Dec 2020",
        "Dongguan Yonghang Electronic Technology Co., Ltd"]}


def test_the_committed_catalog_names_every_motherboard_detector() -> None:
    detectors = {row.detector for row in MOTHERBOARD_CONTROLLERS}
    assert detectors == gen_rgb_catalog.MOTHERBOARD_DETECTORS
    assert len(MOTHERBOARD_CONTROLLERS) == len(set(MOTHERBOARD_CONTROLLERS))


@pytest.mark.parametrize("row", MOTHERBOARD_CONTROLLERS,
                         ids=lambda r: f"{r.vid:04x}:{r.pid:04x}")
def test_a_catalog_row_is_never_a_trcc_cooler(row) -> None:
    """A cooler's id in the catalog would list the cooler as a motherboard.
    JGINYUE shares Thermalright's vendor 0x0416; Gigabyte has 048D:5711."""
    assert (row.vid, row.pid) not in ALL_DEVICES
    assert 0 < row.vid <= 0xFFFF and 0 <= row.pid <= 0xFFFF and row.name


def test_the_aura_driver_takes_its_boards_from_the_catalog() -> None:
    from trcc.adapters.rgb.asus_aura import AURA_VID, MAINBOARD_PIDS

    assert 0x19AF in MAINBOARD_PIDS        # the maintainer's PRIME Z790-V AX
    assert all((AURA_VID, pid) in {(r.vid, r.pid) for r in MOTHERBOARD_CONTROLLERS}
               for pid in MAINBOARD_PIDS)
