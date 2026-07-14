"""Widescreen encode rotation (#203 / #169).

The C# ``ImageToJpg`` rotation switch is keyed ONLY on the resolution flag +
the user orientation (TRCC.decompiled.cs:65285+) — the encode base is FIXED per
resolution and **sub-independent**:

    is854x480 / is960x540 / is1280x480 / is800x480 → base 0  → 0, 270, 180, 90
    is1600x720 / is1920x462                        → base 180 → 180, 90, 0, 270

These pin the shipping ``wire_angle`` to that switch so the phantom
``encode_sub_bases`` overrides (which had no C# basis and put the frame 180° off
at 90°/270° for some subs — #203 on 854×480, #169 on 1600×720 sub=3) can never
creep back.
"""
from __future__ import annotations

import dataclasses

import pytest

from trcc.core.protocol import get_profile, resolve_encode_sub, wire_angle

# fbl → the C# ImageToJpg wire angle per user orientation (0, 90, 180, 270).
_CSHARP_WIRE = {
    224: (0, 270, 180, 90),    # 854×480  (base 0)
    128: (0, 270, 180, 90),    # 1280×480 (base 0)
    114: (180, 90, 0, 270),    # 1600×720 (base 180)
    192: (180, 90, 0, 270),    # 1920×462 (base 180)
}
_ORIENTATIONS = (0, 90, 180, 270)


@pytest.mark.parametrize("portrait_content", [False, True])
@pytest.mark.parametrize("fbl,expected", _CSHARP_WIRE.items())
def test_widescreen_wire_angle_matches_csharp(
    fbl: int, expected: tuple[int, ...], portrait_content: bool,
) -> None:
    """The wire angle must match the C# switch REGARDLESS of portrait_content.

    The live render path composes a widescreen 90/270 frame on the portrait
    canvas and passes ``portrait_content=True``; the old test only checked
    ``False``, so wire_angle silently returned 0 (unrotated) on the live path and
    the frame shipped as an unrotated 720×1600 portrait (#169).  Both values must
    now resolve to the C# rotation.
    """
    profile = get_profile(fbl)
    assert profile.widescreen and profile.jpeg
    for orientation, want in zip(_ORIENTATIONS, expected, strict=True):
        got = wire_angle(profile, orientation, portrait_content=portrait_content)
        assert got == want, (
            f"FBL {fbl} @ {orientation}° (portrait_content={portrait_content}): "
            f"got {got}, want {want}"
        )


@pytest.mark.parametrize("fbl", _CSHARP_WIRE)
@pytest.mark.parametrize("sub", [0, 1, 2, 3, 4, 5])
def test_widescreen_wire_angle_is_sub_independent(fbl: int, sub: int) -> None:
    """The C# base never varies by sub, so folding any sub via resolve_encode_sub
    must leave the wire angle unchanged (the bug was sub-dependent overrides)."""
    base = get_profile(fbl)
    folded = dataclasses.replace(base, encode_base=resolve_encode_sub(base, sub))
    for orientation in _ORIENTATIONS:
        assert wire_angle(folded, orientation, portrait_content=False) == wire_angle(
            base, orientation, portrait_content=False
        ), f"FBL {fbl} sub={sub} @ {orientation}° drifted from the sub-0 angle"
