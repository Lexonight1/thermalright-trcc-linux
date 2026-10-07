"""Does our code DO what the C# audit says?  The gate that answers it.

``audit_coverage.py`` answers "did we READ this C# method" — 95%.  Nothing
answered "does our code match it", and that gap is not theoretical: the full
``ImageToJpg`` rotation table sat correctly in ``BEHAVIOR_DISCOVERY.md:227``
while we shipped a different one for months (``e8d6b30f``), and the bulk wire's
hand-written PM allow-list drifted to 11 of the C# ladder's 21 PMs with nothing
to notice.  A document cannot fail a build.  This can.

RUNS IN CI, which is the reason it is a test and not another dev tool.  Both
oracles — ``dev/decompiler/encode_reference.py`` and ``formcztv_init.py`` — are
pure transcriptions with no file reads, verified by running them under
``TRCC_DECOMPILE=/nonexistent``.  ``audit_release --check`` cannot do this; it
needs two decompiles on disk.

SCOPE IS THE ONE PINNED CALL SITE.  The C# entry point differs per device
class, and using the wrong one MANUFACTURES divergences — feeding ``fbl=0``
instead of ``fbl=72`` invented twenty of them during this file's design.
``Form1.cs:1071`` → ``FormCZTVInit(72, mode=2, pm=shm[4], pmSub=shm[1])`` is
traced through ``USBLCDNEW``'s shared-memory packing for TWO wires: bulk
(``shm[4]=array[24]``, ``shm[1]=array[36]``) and LY/LY1 (``64 + [20]`` /
``1 + [22]``, and ``49 + [20]`` / ``[22]``).  Both are swept here, LY through
the REAL ``LyLcd.connect()``.  The HID route (``Form1.cs:1604``) is swept by
``test_mode3_fingerprint_matches_the_csharp``.  **Adding a wire means pinning
its call site first.**  That rule is the point.

NO ALLOW-LIST.  Every axis must match the C# exactly, with one exception that
is not a list: an encode difference passes when the C# arm itself is labelled
``not_ported`` in ``encode_reference.py`` — and then it must still DIFFER, so a
label cannot outlive the divergence it excuses.  This replaced a 63-row table
whose reasons restated those labels a second time.

TWO COMPOSITION TRAPS, both of which bit during design and are handled here:

  * the C# mode-2 class STARTS at ``fbl=72`` and lets the pm ladder override
    it (``in_pm_ladder``).  Start anywhere else and every unmatched PM reads as
    a divergence.
  * the wire angle is ``wire_angle + encode_baseline``.  Comparing either half
    alone reports a phantom 180 on the FW360 (PM 6) — ``audit_rotation`` carries
    the same warning.

Two axes compare like with like only conditionally, and both say so:
resolution only where the C# set a resolution flag (``models_geometry``), and
widescreen not on ``WIDESCREEN_SEMANTIC_SPLIT`` (two correct flags, two
meanings).

THEME CATALOG IS NOT AN AXIS YET.  ``audit_devices`` compares our catalog at a
hardcoded angle 0 against the C#'s already-mount-seeded value; that is apples to
oranges, which is why it prints "[not a verdict axis]".  It needs the
seeded-angle comparison before it can fail anything.

MUTATION CHECK -- five ways, MEASURED 2026-10-01 (349 pass clean); failures in
this file only:

  1. ``in_pm_ladder`` forgets the PM+SUB table (``(1, 48)``, ``(1, 49)``)
     →  **2** (bulk).
  2. ``bulk_profile`` echoes an unknown PM as an FBL — the #176 shape LY
     shipped  →  **178** (34 bulk, 144 LY).
  3. ``_RGB565_PMS`` back to ``{32}``  →  **9** (1 bulk, 8 LY1 at PM 50).
  4. drop the 960x320 ``not_ported`` label  →  **3** (bulk PM 17/18).
  5. ``bulk_profile`` resolves the mount without the SUB byte  →  **34**
     (11 bulk, 23 LY).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import DeviceProfile, wire_angle
from trcc.core.variants import _BULK_VARIANTS

from .conftest import FakeBulkTransport
from .test_ly_lcd_geometry import _ly_response, _make_ly

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from audit_devices import (  # pyright: ignore[reportMissingImports]
    WIDESCREEN_SEMANTIC_SPLIT,
)
from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_encode,
    csharp_rgb565_big_endian,
)
from formcztv_init import (  # pyright: ignore[reportMissingImports]
    form_cztv_init,
)

_ANGLES = (0, 90, 180, 270)

# The mode-2 class's entry into FormCZTVInit — Form1.cs:1071.  Not a guess: the
# literal 72 is in the call, and it is why an unmatched PM lands on 480x480.
_MODE2_START_FBL = 2 * 36
_MODE2 = 2

_LY_PID, _LY1_PID = 0x5408, 0x5409


def _bulk_fingerprints() -> list[tuple[int, int]]:
    """Every catalogued bulk (pm, sub).

    ``sub=None`` in the variant table means "any sub" and enters as 0 — it was
    skipped during design, which silently dropped PMs 13/16/50/68 from the
    corpus and undercounted the gap.
    """
    out: list[tuple[int, int]] = []
    for pm, submap in sorted(_BULK_VARIANTS.items()):
        for raw in sorted(submap, key=lambda s: (s is not None, s)):
            out.append((pm, 0 if raw is None else raw))
    return out


def _ly_fingerprints() -> list[tuple[int, int, int]]:
    """(pid, resp[20], resp[22]) covering every PM the two LY formulas reach.

    LY is ``64 + resp[20]`` with 0-3 clamped to 1, so 0-7 reaches 65, 68, 69
    and the first PMs past the ladder; LY1 is ``49 + resp[20]``, so 0-22
    reaches 49-71 — every ladder PM from 50 up.  SUB spans 0-7 for both.
    """
    return ([(_LY_PID, r20, r22) for r20 in range(8) for r22 in range(8)]
            + [(_LY1_PID, r20, r22) for r20 in range(23) for r22 in range(8)])


def _divergences(pm: int, sub: int,
                 profile: DeviceProfile) -> dict[str, tuple[object, object]]:
    """Every axis where *profile* is not what the C# makes of (pm, sub).

    An encode difference on a ``not_ported`` arm is not returned here; it is
    checked by :func:`_assert_matches_the_csharp`, which also demands it still
    differ.
    """
    st = form_cztv_init(_MODE2_START_FBL, m=_MODE2, pm=pm, pmSub=sub)
    jpeg = st.myDeviceMode == 2
    arm = csharp_encode(st.resolution, jpeg=jpeg, pm=pm,
                        my_sub_mode=st.mySubMode)
    ours_encode = ({
        o: (wire_angle(profile, o, portrait_content=False)
            + profile.encode_baseline) % 360
        for o in _ANGLES
    }, False)          # we never mirror
    pairs: dict[str, tuple[object, object]] = {
        "encoder": (jpeg, profile.jpeg),
        "mount": (st.themeDirection == 90, profile.portrait_mounted),
        "encode": ((arm.angles, arm.mirror), ours_encode),
    }
    if not jpeg:
        pairs["byte_order"] = (csharp_rgb565_big_endian(
            is320x320=st.is320x320, mode=st.myDeviceMode,
            spi_mode=st.myDeviceSPIMode), profile.big_endian)
    if st.models_geometry:
        pairs["resolution"] = (st.resolution, profile.resolution)
    if profile.resolution not in WIDESCREEN_SEMANTIC_SPLIT:
        pairs["widescreen"] = (st.isBiliPingmu, profile.widescreen)
    differ = {axis: v for axis, v in pairs.items() if v[0] != v[1]}
    if arm.not_ported:
        assert "encode" in differ, (
            f"pm={pm} sub={sub} now matches a C# arm labelled not_ported -- "
            f"delete the label in encode_reference.py")
        del differ["encode"]
    return differ


def _assert_matches_the_csharp(pm: int, sub: int, profile: DeviceProfile,
                               wire: str) -> None:
    """Our shipping profile answers what the C# answers, on every axis."""
    for axis, (theirs, ours) in _divergences(pm, sub, profile).items():
        raise AssertionError(
            f"{wire} pm={pm} sub={sub} axis={axis}: the C# says {theirs!r}, "
            f"we say {ours!r}.  Either our code drifted from the C#, or the "
            f"C# arm needs a not_ported label with its evidence in "
            f"encode_reference.py.")


@pytest.mark.parametrize(("pm", "sub"), _bulk_fingerprints())
def test_bulk_fingerprint_matches_the_csharp(pm: int, sub: int) -> None:
    """The bulk wire's profile is what FormCZTVInit(72, 2, pm, sub) makes."""
    _assert_matches_the_csharp(pm, sub, bulk_profile(pm, sub)[1], "bulk")


@pytest.mark.parametrize(("pid", "resp20", "resp22"), _ly_fingerprints())
def test_ly_fingerprint_matches_the_csharp(
    fake_bulk: FakeBulkTransport, pid: int, resp20: int, resp22: int,
) -> None:
    """The REAL ``LyLcd.connect()`` lands where the C# does, for both PIDs.

    LY used to carry its own copy of the bulk rules: an unknown PM echoed as an
    FBL (PM 70 → FBL 70 → 320x320 RGB565 where the C# says 480x480 JPEG), and
    no portrait mount at all.  Measured before the fix: ~430 differing axes
    over this corpus; after: none outside the labelled arms.
    """
    fake_bulk.read_script.append(_ly_response(resp20=resp20, resp22=resp22))
    device = _make_ly(fake_bulk, pid=pid)
    result = device.connect()
    assert device.profile is not None
    _assert_matches_the_csharp(result.pm_byte, result.sub_byte, device.profile,
                               f"LY pid={pid:04x} resp20={resp20} resp22={resp22}")


def test_the_corpus_reaches_what_it_claims() -> None:
    """Guard the guard: a collapsed corpus or a dead label makes this vacuous."""
    bulk = _bulk_fingerprints()
    assert len(bulk) > 40, "the bulk corpus collapsed"
    assert len(_ly_fingerprints()) == 8 * 8 + 23 * 8
    labelled = {
        (pm, sub) for pm, sub in bulk
        if csharp_encode(
            (st := form_cztv_init(_MODE2_START_FBL, m=_MODE2, pm=pm,
                                  pmSub=sub)).resolution,
            jpeg=st.myDeviceMode == 2, pm=pm,
            my_sub_mode=st.mySubMode).not_ported}
    assert len(labelled) >= 10, (
        f"only {len(labelled)} bulk fingerprints meet a not_ported arm -- the "
        f"exception path is barely exercised, so a broken label check would "
        f"go unnoticed")
