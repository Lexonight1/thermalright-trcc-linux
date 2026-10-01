"""The mode-3 640x172 panel ships RGB565 rows centred in a 176-px row.

TRCC 2.1.8 ``ImageTo565`` (FormCZTV.cs:4285), after the rotate and the 565
pack, for ``is640x172`` only:

    byte[] array3 = new byte[225280];
    for (num3 = 0; num3 < 640; num3++)
        Array.Copy(array2, num3 * 172 * 2, array3, num3 * 176 * 2 + 4, 344);

FBL 59 is that panel on the HID mode-3 route, which never reaches
``ImageToJpg`` (every encode site is ``myDeviceMode == 2 ? Jpg : 565``).  It
shipped JPEG until 2026-10-01; no reporter has the device.

MUTATION CHECK -- drop ``wire_row_px=176`` from the FBL 59 row: the wire test
fails on length (220160 != 225280); make ``pad_rows`` copy at offset 0: the
C#-loop test fails on the first row.
"""
from __future__ import annotations

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.core._frames import pad_rows
from trcc.core.models import RawFrame
from trcc.core.protocol import get_profile

_ROWS, _ROW_PX, _WIRE_ROW_PX = 640, 172, 176


def _csharp_repack(array2: bytes) -> bytes:
    """The C# loop above, transcribed literally — the oracle for these tests."""
    array3 = bytearray(225280)
    for num3 in range(640):
        array3[num3 * 176 * 2 + 4:num3 * 176 * 2 + 4 + 344] = \
            array2[num3 * 172 * 2:num3 * 172 * 2 + 344]
    return bytes(array3)


def _pattern(n: int) -> bytes:
    """Bytes with no repeating row, so a wrong row offset cannot pass."""
    return bytes((i * 7 + i // 344) % 251 for i in range(n))


def test_pad_rows_is_the_csharp_repack() -> None:
    data = _pattern(_ROWS * _ROW_PX * 2)
    assert pad_rows(data, _ROW_PX * 2, _WIRE_ROW_PX * 2) == _csharp_repack(data)


@pytest.mark.parametrize("stride", [0, 344])
def test_pad_rows_leaves_a_matching_row_alone(stride: int) -> None:
    data = _pattern(344 * 3)
    assert pad_rows(data, 344, stride) is data


@pytest.mark.parametrize("sub", [0, 5])
def test_fbl_59_wire_payload_is_the_csharp_buffer(sub: int) -> None:
    """Through the real renderer, with the profile a handshake produces."""
    renderer = QtRenderer()
    profile = get_profile(59, 59, sub)
    assert not profile.jpeg
    # The encoder receives the frame already turned to the wire angle: at the
    # 270/90 bases a 640x172 composition arrives 172 wide and 640 tall.
    surface = renderer.from_raw_rgb24(
        RawFrame(_pattern(_ROW_PX * _ROWS * 3), _ROW_PX, _ROWS))
    plain = renderer.encode_rgb565(surface, profile.byte_order)
    payload = renderer.encode_payload(surface, profile)
    assert len(payload) == 225280
    assert payload == _csharp_repack(plain)
