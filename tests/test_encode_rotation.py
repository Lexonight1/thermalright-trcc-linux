"""Wire rotation must equal the C# ``directionB`` switch, at every SUB byte.

Replaces ``test_wire_rotation.py`` + ``test_widescreen_rotation.py``, which
were two hand-copies of the same C# table that had drifted apart and, between
them, pinned the regression this file now guards.  The second of the two
asserted "the encode base is sub-INDEPENDENT" in its name, its docstring and a
parametrized test — a claim contradicted by six families of the real 2.1.6
switch, and the reason four correct table rows were deleted as a "phantom".

**The expected values are not written here.**  They come from
``dev/decompiler/encode_reference.py``, which transcribes the two switches as
literal ``directionB -> RotateImg(angle)`` maps — a different form from the
``(base, invert)`` algebra ``core.protocol`` uses, so agreement between them is
evidence rather than a tautology.  A third hand-copy in this file is exactly
what produced the drift.

MUTATION CHECK -- in ``ENCODE_ROTATIONS``, change ``(854, 480, _JPEG)`` from
``EncodeRotation(0, invert=False)`` to ``EncodeRotation(0)`` (the shape that
shipped, and #203/#169/#171).  MEASURED: **17 failures**, all 854x480 --
``test_encode_angles_match_the_csharp_switch`` for all 8 SUB bytes,
``test_854_takes_the_same_angles_at_every_sub`` for all 8, and
``test_854_and_800_count_up_with_the_display_angle`` once.  The reported diff
is ``90: 270`` and ``270: 90`` against the C#'s ``90: 90`` and ``270: 270``,
with 0 and 180 identical -- that half-agreement is the shape of the bug and is
why it survived every test that checked frame DIMENSIONS.

800x480 does not move, because no profile resolves to it without a PM byte;
it is covered by the sweep, not by the live-path tests.  If nothing fails,
this file is guarding nothing; if unrelated panels fail, the oracle has been
wired to the code it audits.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.protocol import (
    ENCODE_ROTATIONS,
    get_profile,
    resolve_encode_angle,
    resolve_encode_rotation,
    wire_angle,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_encode_angles,
    csharp_my_sub_mode,
)

_ORIENTATIONS = (0, 90, 180, 270)
# 0-7 covers every SUB byte the handshake tables name, plus the two either side
# of each C# threshold (`< 2`, `> 4`, `< 5`, `== 5 || == 7`).
_SUBS = tuple(range(8))
# Every (resolution, encoder) pair the C# switches name.  Taken from the
# shipping table's KEYS — which resolutions exist is not the thing under test,
# the ANGLES are, and hand-listing them is how the last two files went stale.
_PANELS = sorted(ENCODE_ROTATIONS)

# The C# tests `myDevicePingMu == 5` BEFORE any resolution guard, so the oracle
# needs the PM byte to reach that arm while our table keys the same panel on
# (320x240, JPEG).  The two select the same single device — enumerating every
# PM shows six resolve to 320x240 and only PM 5 is a bulk PM, so only PM 5 ever
# arrives with jpeg=True — and this is where that equivalence is asserted
# rather than assumed.  `pm == 6` (the FW360 mount offset) is NOT here: our
# port carries it as `encode_baseline`, a separate rotation, so it is not part
# of the encode table under test.
_CSHARP_PM: dict[tuple[int, int, bool], int] = {(320, 240, True): 5}


@pytest.mark.parametrize("sub", _SUBS)
@pytest.mark.parametrize(("width", "height", "jpeg"), _PANELS)
def test_encode_angles_match_the_csharp_switch(
    width: int, height: int, jpeg: bool, sub: int,
) -> None:
    """Every panel, every encoder, every SUB byte, all four display angles."""
    rotation = resolve_encode_rotation((width, height), jpeg, sub)
    ours = {
        deg: (rotation.base + (deg if not rotation.invert else -deg)) % 360
        for deg in _ORIENTATIONS
    }
    theirs = csharp_encode_angles(
        (width, height), jpeg=jpeg, sub=sub,
        pm=_CSHARP_PM.get((width, height, jpeg), 0))
    assert ours == theirs, (
        f"{width}x{height} {'JPEG' if jpeg else 'RGB565'} sub={sub}: we rotate "
        f"{ours}, the C# rotates {theirs}.  A frame at the wrong angle is "
        f"upside-down or sideways on the glass (#203/#169/#171)."
    )


def test_854_and_800_count_up_with_the_display_angle() -> None:
    """The two families that do NOT invert, stated outright.

    Everything else in both switches counts DOWN — the frame turns opposite to
    the dial — and a table that got 854/800 wrong still looked right at 0 and
    180, where the two models agree.  That symmetry is why this shipped: it is
    only detectable at 90 and 270, and only against the C#.
    """
    for resolution in ((854, 480), (800, 480)):
        rotation = resolve_encode_rotation(resolution, jpeg=True)
        assert rotation.invert is False, resolution
        assert rotation.base == 0, resolution
        angles = csharp_encode_angles(resolution, jpeg=True)
        assert angles == {0: 0, 90: 90, 180: 180, 270: 270}, resolution


@pytest.mark.parametrize("sub", _SUBS)
def test_854_takes_the_same_angles_at_every_sub(sub: int) -> None:
    """854x480 has a ``mySubMode == 2`` arm in the C# that can never fire.

    ``FormCZTVInit`` never assigns ``mySubMode`` on the branch that sets this
    resolution, so it stays 0 and the arm is dead code.  A port that keys on
    the RAW SUB byte fires it and rotates 180 wrong for that one panel — which
    is what a plain revert of the deleted rows would have done.
    """
    assert csharp_my_sub_mode((854, 480), sub) == 0
    rotation = resolve_encode_rotation((854, 480), jpeg=True, sub=sub)
    assert (rotation.base, rotation.invert) == (0, False), sub


@pytest.mark.parametrize(("resolution", "alt_subs", "alt_base"), [
    ((1600, 720), {3}, 0),
    ((1280, 480), {2}, 90),
    ((960, 540), {5, 7}, 180),
    ((960, 320), {0, 1, 2, 3, 4}, 0),
    ((1920, 462), {2, 3, 4}, 0),
    ((1920, 440), {2, 3, 4}, 0),
])
def test_the_families_whose_base_does_vary_by_sub(
    resolution: tuple[int, int], alt_subs: set[int], alt_base: int,
) -> None:
    """Six families DO vary the base by SUB — the fact whose deletion is #203.

    Named individually rather than swept, because the failure this guards
    against is a table that quietly loses one row: a sweep over whatever the
    table happens to contain cannot notice an entry that is gone.
    """
    for sub in _SUBS:
        rotation = resolve_encode_rotation(resolution, jpeg=True, sub=sub)
        want = alt_base if sub in alt_subs else None
        if want is not None:
            assert rotation.base == want, (
                f"{resolution} sub={sub}: base {rotation.base}, C# {want}")
        else:
            assert rotation.base != alt_base or alt_base == 0, (
                f"{resolution} sub={sub}: took the alt arm outside {alt_subs}")


@pytest.mark.parametrize("fbl", [50, 51, 52, 53, 58, 64, 114, 128, 192, 224])
@pytest.mark.parametrize("orientation", _ORIENTATIONS)
def test_wire_angle_delegates_to_the_encode_table(
    fbl: int, orientation: int,
) -> None:
    """Every ``rotate=True`` panel takes its wire angle from the one table.

    ``wire_angle`` used to send non-widescreen panels to a second function with
    its own resolution table.  Both were the same C# switch, and only one of
    them knew about the invert axis.
    """
    profile = get_profile(fbl)
    assert profile.rotate, fbl
    assert wire_angle(profile, orientation, portrait_content=False) == (
        resolve_encode_angle(profile, orientation))
