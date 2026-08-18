"""Does our code DO what the C# audit says?  The gate that answers it.

``audit_coverage.py`` answers "did we READ this C# method" — 95%.  Nothing
answered "does our code match it", and that gap is not theoretical: the full
``ImageToJpg`` rotation table sat correctly in ``BEHAVIOR_DISCOVERY.md:227``
while we shipped a different one for months (``e8d6b30f``), and
``_BULK_KNOWN_PMS`` drifted out of step with ``_PM_TO_FBL_OVERRIDES`` with
nothing to notice.  A document cannot fail a build.  This can.

RUNS IN CI, which is the reason it is a test and not another dev tool.  Both
oracles — ``dev/decompiler/encode_reference.py`` and ``formcztv_init.py`` — are
pure transcriptions with no file reads, verified by running them under
``TRCC_DECOMPILE=/nonexistent``.  ``audit_release --check`` cannot do this; it
needs two decompiles on disk.

SCOPE IS BULK ONLY, deliberately.  The C# entry point differs per device class,
and using the wrong one MANUFACTURES divergences — feeding ``fbl=0`` instead of
``fbl=72`` invented twenty of them during this file's design.  Exactly one call
site is proven: ``Form1.cs:1071`` → ``FormCZTVInit(72, mode=2, pm=shm[4],
pmSub=shm[1])``, traced through ``USBLCDNEW``'s shared-memory packing
(``shm[4]=array[24]``, ``shm[1]=array[36]`` — our exact bulk offsets).
``Form1.cs:1604`` looks like the HID class and SCSI/LY/ALI are unpinned.
**Adding a wire means pinning its call site first.**  That rule is the point.

TWO COMPOSITION TRAPS, both of which bit during design and are handled here:

  * the C# bulk class STARTS at ``fbl=72`` and lets the pm ladder override it,
    which is what ``_BULK_BASE_FBL`` mirrors.  Start anywhere else and every
    unmatched PM reads as a divergence.
  * the wire angle is ``wire_angle + encode_baseline``.  Comparing either half
    alone reports a phantom 180 on the FW360 (PM 6) — ``audit_rotation`` carries
    the same warning.

THEME CATALOG IS NOT AN AXIS YET.  ``audit_devices`` compares our catalog at a
hardcoded angle 0 against the C#'s already-mount-seeded value; that is apples to
oranges, which is why it prints "[not a verdict axis]".  It needs the
seeded-angle comparison before it can fail anything.

MUTATION CHECK -- three ways, MEASURED 2026-08-18 (145 pass clean):

  1. delete the ``(13, 0, "resolution")`` row  →  **1 failed**, exactly that
     fingerprint, reported as a NEW divergence.
  2. revert ``(854, 480, _JPEG)`` to ``EncodeRotation(0)`` in
     ``ENCODE_ROTATIONS`` — i.e. reintroduce ``e8d6b30f``  →  **8 failed**.
     This is the headline: the defect that shipped for months, undetected by a
     green suite and a 95%-covered audit, now fails the build.
  3. empty ``_KNOWN_DIVERGENCES``  →  **30 failed, 1 skipped** — the gate cannot
     be neutered by clearing its own list, and the skip is the second
     parametrize going empty, which the not-silently-empty test also catches.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import is_portrait_mounted, wire_angle
from trcc.core.variants import _BULK_VARIANTS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_encode_angles,
)
from formcztv_init import (  # pyright: ignore[reportMissingImports]
    form_cztv_init,
)

_ANGLES = (0, 90, 180, 270)

# The bulk class's entry into FormCZTVInit — Form1.cs:1071.  Not a guess: the
# literal 72 is in the call, and it is why an unmatched PM lands on 480x480.
_BULK_START_FBL = 2 * 36
_BULK_MODE = 2

# ── The two root causes.  60 divergences, two reasons. ──────────────────────

BULK_PM_GAP = (
    "_BULK_KNOWN_PMS lists 9 PMs; the C# mode-2 ladder handles 20.  A PM "
    "outside that list falls back to _BULK_BASE_FBL (72 → 480x480) even when "
    "_PM_TO_FBL_OVERRIDES resolves it correctly — so the same fact lives in "
    "two tables that have drifted.  The fallback itself is FAITHFUL (the C# "
    "also starts at 72; PM 4 proves it, matching on both sides), the ALLOW-LIST "
    "is what is short.  LATENT, not live: no reporter has ever sent one of "
    "these PMs on the bulk wire — every observed bulk fingerprint across all "
    "issues is PM 4, 5, 7, 11, 32 or 64 (checked 2026-08-18)."
)

MOUNT_3_OF_9 = (
    "is_portrait_mounted models 3 resolutions; SetThemeInfo_ThemeML applies "
    "the pmSub mount test to 9 — 176x320, 1920x462, 1920x440, 640x480, "
    "640x172, 854x480, 960x320, 960x540, 800x480.  Note 1920x462 uses "
    "'pmSub <= 5', not '< 5', so SUB 5 lands portrait there and landscape in "
    "ours.  Closing this means widening is_portrait_mounted to the C#'s nine "
    "with the right boundary per family."
)

# (pm, sub, axis) → cause.  GENERATED from the live comparison, not hand-typed:
# a hand-written list of 60 rows is exactly how 37 non-existent method names
# reached a doc earlier this month.
_KNOWN_DIVERGENCES: dict[tuple[int, int, str], str] = {
    (1, 49, "mount"): MOUNT_3_OF_9,
    (13, 0, "resolution"): BULK_PM_GAP,
    (13, 0, "widescreen"): BULK_PM_GAP,
    (14, 1, "resolution"): BULK_PM_GAP,
    (14, 2, "resolution"): BULK_PM_GAP,
    (15, 0, "resolution"): BULK_PM_GAP,
    (15, 2, "resolution"): BULK_PM_GAP,
    (16, 0, "resolution"): BULK_PM_GAP,
    (16, 0, "widescreen"): BULK_PM_GAP,
    (17, 0, "resolution"): BULK_PM_GAP,
    (17, 0, "widescreen"): BULK_PM_GAP,
    (17, 1, "resolution"): BULK_PM_GAP,
    (17, 1, "widescreen"): BULK_PM_GAP,
    (17, 2, "resolution"): BULK_PM_GAP,
    (17, 2, "widescreen"): BULK_PM_GAP,
    (17, 3, "resolution"): BULK_PM_GAP,
    (17, 3, "widescreen"): BULK_PM_GAP,
    (17, 5, "resolution"): BULK_PM_GAP,
    (17, 5, "widescreen"): BULK_PM_GAP,
    (17, 5, "encode"): BULK_PM_GAP,
    (17, 5, "mount"): MOUNT_3_OF_9,
    (18, 0, "resolution"): BULK_PM_GAP,
    (18, 0, "widescreen"): BULK_PM_GAP,
    (18, 1, "resolution"): BULK_PM_GAP,
    (18, 1, "widescreen"): BULK_PM_GAP,
    (18, 2, "resolution"): BULK_PM_GAP,
    (18, 2, "widescreen"): BULK_PM_GAP,
    (50, 0, "resolution"): BULK_PM_GAP,
    (50, 0, "encode"): BULK_PM_GAP,
    (63, 0, "resolution"): BULK_PM_GAP,
    (63, 0, "widescreen"): BULK_PM_GAP,
    (63, 0, "encode"): BULK_PM_GAP,
    (63, 1, "resolution"): BULK_PM_GAP,
    (63, 1, "widescreen"): BULK_PM_GAP,
    (63, 1, "encode"): BULK_PM_GAP,
    (63, 2, "resolution"): BULK_PM_GAP,
    (63, 2, "widescreen"): BULK_PM_GAP,
    (63, 2, "encode"): BULK_PM_GAP,
    (63, 3, "resolution"): BULK_PM_GAP,
    (63, 3, "widescreen"): BULK_PM_GAP,
    (63, 4, "resolution"): BULK_PM_GAP,
    (63, 4, "widescreen"): BULK_PM_GAP,
    (63, 4, "encode"): BULK_PM_GAP,
    (65, 5, "mount"): MOUNT_3_OF_9,
    (66, 0, "resolution"): BULK_PM_GAP,
    (66, 0, "widescreen"): BULK_PM_GAP,
    (66, 0, "encode"): BULK_PM_GAP,
    (66, 1, "resolution"): BULK_PM_GAP,
    (66, 1, "widescreen"): BULK_PM_GAP,
    (66, 1, "encode"): BULK_PM_GAP,
    (66, 2, "resolution"): BULK_PM_GAP,
    (66, 2, "widescreen"): BULK_PM_GAP,
    (66, 3, "resolution"): BULK_PM_GAP,
    (66, 3, "widescreen"): BULK_PM_GAP,
    (66, 4, "resolution"): BULK_PM_GAP,
    (66, 4, "widescreen"): BULK_PM_GAP,
    (68, 0, "resolution"): BULK_PM_GAP,
    (68, 0, "widescreen"): BULK_PM_GAP,
    (69, 2, "resolution"): BULK_PM_GAP,
    (69, 2, "widescreen"): BULK_PM_GAP,
}

# Ratchet, in the shape MAX_SILENT already proves works: this number only ever
# goes DOWN.  Fixing a divergence means deleting its row and lowering this;
# a NEW divergence fails the build outright rather than being appended.
MAX_DIVERGENCES = 60


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


def _compare(pm: int, sub: int) -> dict[str, tuple[object, object]]:
    """Every axis for one fingerprint → {axis: (theirs, ours)} where they differ."""
    st = form_cztv_init(_BULK_START_FBL, m=_BULK_MODE, pm=pm, pmSub=sub)
    _, profile = bulk_profile(pm, sub)

    theirs_angles = csharp_encode_angles(
        st.resolution, jpeg=profile.jpeg, pm=pm, sub=st.mySubMode)
    ours_angles = {
        o: (wire_angle(profile, o, portrait_content=False)
            + profile.encode_baseline) % 360
        for o in _ANGLES
    }
    pairs: dict[str, tuple[object, object]] = {
        "resolution": (st.resolution, profile.resolution),
        "widescreen": (st.isBiliPingmu, profile.widescreen),
        "encode": (theirs_angles, ours_angles),
        "mount": (st.themeDirection == 90,
                  is_portrait_mounted(profile.resolution, sub)),
    }
    return {axis: v for axis, v in pairs.items() if v[0] != v[1]}


@pytest.mark.parametrize(("pm", "sub"), _bulk_fingerprints())
def test_bulk_fingerprint_matches_the_csharp(pm: int, sub: int) -> None:
    """Our shipping code must answer what TRCC 2.1.6 answers, or say why not."""
    for axis, (theirs, ours) in _compare(pm, sub).items():
        assert (pm, sub, axis) in _KNOWN_DIVERGENCES, (
            f"NEW divergence — bulk pm={pm} sub={sub} axis={axis}: "
            f"the C# says {theirs!r}, we say {ours!r}.  Either our code "
            f"drifted from TRCC 2.1.6, or this is a deliberate difference "
            f"that belongs in _KNOWN_DIVERGENCES with its reason and the "
            f"evidence it is not user-visible."
        )


@pytest.mark.parametrize(("pm", "sub", "axis"), sorted(_KNOWN_DIVERGENCES))
def test_a_fixed_divergence_is_removed_from_the_allowlist(
    pm: int, sub: int, axis: str,
) -> None:
    """A closed divergence must not linger — the other half of the ratchet.

    Without this the list only grows: someone fixes the PM gap, the rows stay,
    and the count keeps claiming debt that no longer exists.
    """
    assert axis in _compare(pm, sub), (
        f"bulk pm={pm} sub={sub} axis={axis} now MATCHES the C# — delete its "
        f"row from _KNOWN_DIVERGENCES and lower MAX_DIVERGENCES.  Leaving it "
        f"means the ratchet reports debt that is already paid."
    )


def test_the_allowlist_is_not_silently_empty() -> None:
    """Guard the guard: an emptied list makes every assertion above vacuous."""
    assert _KNOWN_DIVERGENCES, "the allow-list is empty — the gate guards nothing"
    assert len(_KNOWN_DIVERGENCES) <= MAX_DIVERGENCES, (
        f"{len(_KNOWN_DIVERGENCES)} known divergences exceeds the ratchet of "
        f"{MAX_DIVERGENCES}.  A NEW divergence is a bug to fix, not a row to add."
    )
    assert len(_bulk_fingerprints()) > 40, "the corpus collapsed"
    assert set(_KNOWN_DIVERGENCES.values()) == {BULK_PM_GAP, MOUNT_3_OF_9}, (
        "a divergence has a cause outside the two documented root causes — "
        "name it, or it is not understood"
    )
