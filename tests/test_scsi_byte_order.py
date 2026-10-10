"""The SCSI wire's RGB565 byte order follows the C#, poll byte by poll byte.

FormCZTVInit sets SPI mode 2 for mode 1 + FBL 51 (FormCZTV.cs:1065), so
ImageTo565 packs that panel big-endian, and USBLCD.exe (2024-03-25, older
than this project -- independent evidence) copies the frame to the panel
unswapped.  We sent it little-endian, because FBL_PROFILES[51] is the HID
wire's FBL 51 -- which IS little-endian on real glass (#65, #67).  So the rule
lives on the SCSI wire, and the table keeps HID's answer.

Confirmed on a SCSI FBL 51 panel by @PourrezJ (#313): green showed pink
until the frame went big-endian.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.adapters.device.scsi_lcd import scsi_profile
from trcc.core.protocol import FBL_PROFILES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev" / "decompiler"))
from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_rgb565_big_endian,
)
from formcztv_init import form_cztv_init  # pyright: ignore[reportMissingImports]

#: Every poll byte a SCSI panel has reported or the native USBLCD classifies.
_SCSI_POLL_BYTES = (36, 37, 50, 51, 100, 101, 102)


@pytest.mark.parametrize("fbl", _SCSI_POLL_BYTES)
def test_the_scsi_byte_order_is_the_csharps(fbl: int) -> None:
    """MUTATION CHECK: drop FBL 51 from the SCSI rule and fbl=51 fails."""
    st = form_cztv_init(fbl, m=1)

    assert scsi_profile(fbl).big_endian is csharp_rgb565_big_endian(
        is320x320=st.is320x320, mode=st.myDeviceMode,
        spi_mode=st.myDeviceSPIMode)


def test_the_hid_wires_fbl_51_stays_little_endian() -> None:
    """Glass-confirmed on HID (#65 riodevelop, #67 wobbegongus, v8.3.5): the
    fix must not move into the shared table."""
    assert FBL_PROFILES[51].big_endian is False


@pytest.mark.parametrize(("fbl", "first_pixel"), [
    (50, b"\x00\xf8"),     # little-endian, as GifTo565 sends FBL 50
    (100, b"\xf8\x00"),    # 320x320: big-endian
    (51, b"\xf8\x00"),     # SCSI FBL 51: SPI mode 2, big-endian
], ids=["fbl50", "fbl100", "fbl51"])
def test_a_boot_animation_is_packed_in_the_panels_byte_order(
    tmp_path: Path, fbl: int, first_pixel: bytes,
) -> None:
    """The boot animation was always encoded big-endian, whatever the panel:
    an FBL 36/37/50 panel got its boot animation with red and blue swapped.

    MUTATION CHECK: pass ``>`` again and fbl50 fails.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, UploadBootAnimation

    from .mock_platform import MockPlatform

    renderer = QtRenderer()
    red = tmp_path / "red.png"
    red.write_bytes(renderer.encode_png(renderer.create_surface(8, 8, color=(255, 0, 0, 255))))
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": fbl}], tmp_path / "root"),
              renderer=renderer)
    app.attach(0x0402, 0x3922)
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok
    sent: list[bytes] = []
    device = app.devices["0402:3922"]
    device.send_boot_animation = lambda frames, delays: sent.extend(frames) or len(frames)  # type: ignore[method-assign]

    assert app.dispatch(UploadBootAnimation(key="0402:3922", frame_paths=[red],
                                            delays_ds=[10])).ok

    assert sent[0][:2] == first_pixel
