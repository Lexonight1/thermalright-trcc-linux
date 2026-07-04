"""Phase 1: ``wire_rotation()`` locked to the C# ``ImageToJpg`` / ``ImageTo565``
``directionB`` switch tables (TRCC 2.x decompile, FormCZTV.cs).

Pure table assertion — no device, no render.  ``wire_rotation`` is the single
source of the whole-composite rotation applied to the wire frame before encode;
these tests pin every panel class × orientation to the exact angle the C#
rotates by.  The angle a panel rotates by at ``directionB == 0`` is its BASE,
and ``wire_angle = (BASE - orientation) mod 360``:

    base 0   → {0: 0,   90: 270, 180: 180, 270: 90}
    base 90  → {0: 90,  90: 0,   180: 270, 270: 180}
    base 180 → {0: 180, 90: 90,  180: 0,   270: 270}
"""
from __future__ import annotations

import pytest

from trcc.core.protocol import DeviceProfile, wire_rotation

_BASE0 = {0: 0, 90: 270, 180: 180, 270: 90}
_BASE90 = {0: 90, 90: 0, 180: 270, 270: 180}
_BASE180 = {0: 180, 90: 90, 180: 0, 270: 270}

# (label, DeviceProfile, pm, expected-angle-by-orientation) — one row per C#
# switch branch in ImageToJpg (2655-2711) / ImageTo565 (2976-2990).
_CASES = [
    # Squares → BASE 0 (RGB565 ImageTo565:2977-2982; JPEG pm!=6 ImageToJpg:2664-2668).
    ("square_240_rgb565", DeviceProfile(240, 240), 0, _BASE0),
    ("square_320_rgb565", DeviceProfile(320, 320), 0, _BASE0),
    ("square_480_rgb565", DeviceProfile(480, 480), 0, _BASE0),
    ("square_360_jpeg", DeviceProfile(360, 360, jpeg=True), 0, _BASE0),
    ("square_480_jpeg_pm5", DeviceProfile(480, 480, jpeg=True), 5, _BASE0),
    # Square JPEG pm==6 → BASE 180 (ImageToJpg:2655-2661).
    ("square_480_jpeg_pm6", DeviceProfile(480, 480, jpeg=True), 6, _BASE180),
    ("square_320_jpeg_pm6", DeviceProfile(320, 320, jpeg=True), 6, _BASE180),
    # 320×240 splits on the encoder, NOT the FBL (both are FBL 50).
    ("small_320x240_rgb565_fw", DeviceProfile(320, 240, rotate=True), 0, _BASE90),
    ("small_320x240_jpeg_mjolnir",
     DeviceProfile(320, 240, jpeg=True, rotate=True), 5, _BASE0),
    # Standard non-square panels → BASE 0 (ImageToJpg:2683-2704 / pm5 2669-2675).
    ("wide_640x480", DeviceProfile(640, 480, rotate=True), 0, _BASE0),
    ("wide_854x480_jpeg", DeviceProfile(854, 480, jpeg=True, rotate=True), 0, _BASE0),
    ("wide_1280x480_jpeg", DeviceProfile(1280, 480, jpeg=True, rotate=True), 0, _BASE0),
    ("wide_800x480", DeviceProfile(800, 480, rotate=True), 0, _BASE0),
    ("wide_960x540", DeviceProfile(960, 540, rotate=True), 0, _BASE0),
    # 1600×720 / 1920×462 → BASE 180 (ImageToJpg:2678 / 2692).
    ("wide_1600x720_jpeg", DeviceProfile(1600, 720, jpeg=True, rotate=True), 0, _BASE180),
    ("wide_1920x462_jpeg", DeviceProfile(1920, 462, jpeg=True, rotate=True), 0, _BASE180),
]


@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
@pytest.mark.parametrize(
    "profile,pm,expected",
    [(p, pm, exp) for _, p, pm, exp in _CASES],
    ids=[label for label, *_ in _CASES],
)
def test_wire_rotation_matches_cs_table(
    profile: DeviceProfile, pm: int, expected: dict[int, int], orientation: int,
) -> None:
    assert wire_rotation(profile, orientation, pm) == expected[orientation]


def test_mjolnir_and_frozen_warframe_rotate_oppositely_at_native() -> None:
    """FBL 50 collapses pm==5 (Mjolnir, JPEG) and pm==50 (Frozen Warframe,
    RGB565); the C# rotates them OPPOSITELY.  At orientation 0 the JPEG Mjolnir
    stays at 0° while the RGB565 panel rotates 90° — proving the encoder, not
    the FBL, is the discriminator (the flaw in the reverted ``widescreen`` flag).
    """
    mjolnir = DeviceProfile(320, 240, jpeg=True, rotate=True)
    frozen = DeviceProfile(320, 240, rotate=True)  # RGB565
    assert wire_rotation(mjolnir, 0, pm=5) == 0
    assert wire_rotation(frozen, 0, pm=50) == 90


def test_854x480_at_90_rotates_270_per_cs() -> None:
    """C# ``ImageToJpg`` rotates 854×480 by 270° at directionB==90
    (FormCZTV.cs:2686) — the value the current ``encode_invert=False`` path
    does NOT produce (it yields 90°).  Locks the C# value; the live wire switch
    to it is Phase 2 and release-gated on a real 854×480 device.
    """
    p = DeviceProfile(854, 480, jpeg=True, rotate=True)
    assert wire_rotation(p, 90) == 270
    assert wire_rotation(p, 270) == 90


def test_1600x720_inverted_mount_base_180() -> None:
    """1600×720 / 1920×462 carry a 180° mount offset (ImageToJpg:2678/2692):
    at native orientation the composite is rotated 180°, not left upright.
    """
    for res in ((1600, 720), (1920, 462)):
        p = DeviceProfile(*res, jpeg=True, rotate=True)
        assert wire_rotation(p, 0) == 180
        assert wire_rotation(p, 90) == 90
