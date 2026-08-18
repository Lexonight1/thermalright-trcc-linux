"""C# encode-rotation oracle — the ``directionB`` switches, transcribed.

Source: **TRCC 2.1.6**, ``TRCC.CZTV/FormCZTV.cs`` — ``ImageToJpg`` and
``ImageTo565``.  Verify the tree before trusting a citation::

    grep -rh AssemblyVersion ~/Downloads/TRCC_2.1.6_decompiled/Properties/*.cs
    # -> [assembly: AssemblyVersion("2.1.6.0")]

**This file stores the switch arms LITERALLY** — one ``directionB -> angle``
map per arm, exactly the ``RotateImg`` argument the C# passes — and not the
``(base, invert)`` algebra ``trcc.core.protocol`` uses.  That is deliberate and
is the whole value of the file: an oracle written in the same form as the code
it audits can only catch typos, never a wrong model.  Stated as arms, it also
catches the sign error that shipped for months (854x480 counts UP with the
display angle; every other family counts down).

The previous revision of this file was transcribed from a decompile that
predated the 2.1.6 installer by four months.  It had no SUB term at all, and
its 360x360 row was wrong in a way that put a permanent ``xfail`` on correct
shipping code.  See ``memory/project_csharp_oracle_was_the_wrong_version.md``.

PURE — no I/O, no framework.  Lives in ``dev/`` because it is a reference for
auditing, never shipped logic; nothing in ``src/trcc`` may import it.
"""
from __future__ import annotations

# ── mySubMode: DERIVED from the handshake SUB byte, not equal to it ─────────
#
# ``FormCZTVInit`` assigns ``mySubMode = pmSub`` on the branches that set
# 1920x462 (pm 65/66, pm1+sub49), 1920x440 (pm 69), 1280x480 (pm 68, and the
# mode-3 fbl-128 branch), 960x540 (pm 10/16) and 960x320 (pm 13/17/18).
#
# It does NOT assign it for **854x480** (pm 9/11) or **800x480** (pm 12) —
# every sibling branch does, those two do not — so it stays 0 for them and
# their ``mySubMode == 2`` arm is unreachable.  1600x720 is assigned nowhere in
# ``FormCZTVInit`` either (that branch sets ``myLddValSub``); it gets one later
# from ``SetThemeInfo_ThemeML``, which is called with the handshake ``pmSub``
# and assigns only for 2, 3 and 4.
#
# Encoding that here rather than in the arms below keeps the arms a literal
# transcription: the arms say what the switch tests, this says what can reach
# them.
_NEVER_ASSIGNED = frozenset({(854, 480), (800, 480)})


def csharp_my_sub_mode(resolution: tuple[int, int], sub: int) -> int:
    """``mySubMode`` for a panel that handshook with this SUB byte."""
    if resolution in _NEVER_ASSIGNED:
        return 0
    if resolution == (1600, 720):
        return sub if sub in (2, 3, 4) else 0
    return sub


# ── The arms, as literal directionB -> RotateImg(angle) maps ────────────────
_A = {0: 0, 90: 270, 180: 180, 270: 90}      # the common "counts down" arm
_B = {0: 180, 90: 90, 180: 0, 270: 270}      # the same, offset by 180
_C = {0: 90, 90: 0, 180: 270, 270: 180}      # both switches' DEFAULT arm
_D = {0: 0, 90: 90, 180: 180, 270: 270}      # counts UP — 854/800 only
_E = {0: 180, 90: 270, 180: 0, 270: 90}      # counts up, offset — 854/800 alt
_F = {0: 270, 90: 180, 180: 90, 270: 0}      # ImageTo565 640x172

# ImageToJpg, in the C#'s own branch order.  Each entry is
# (resolution(s), arm when the mySubMode guard holds, guard, arm otherwise).
_JPG_ARMS: tuple[tuple[frozenset[tuple[int, int]],
                       dict[int, int],
                       frozenset[int] | None,
                       dict[int, int]], ...] = (
    # `is320x320 || is480x480`.  The `myDevicePingMu == 6` sub-branch (arm _B)
    # is the FW360 mount offset and is handled by `pm`, below, not by SUB.
    (frozenset({(320, 320), (480, 480)}), _A, None, _A),
    # `is1600x720`: mySubMode == 3
    (frozenset({(1600, 720)}), _A, frozenset({3}), _B),
    # `is854x480 || is800x480`: mySubMode == 2 (unreachable — see above)
    (frozenset({(854, 480), (800, 480)}), _E, frozenset({2}), _D),
    # `is1280x480`: mySubMode == 2
    (frozenset({(1280, 480)}), _C, frozenset({2}), _A),
    # `is960x320`: mySubMode < 5
    (frozenset({(960, 320)}), _A, frozenset({0, 1, 2, 3, 4}), _B),
    # `is960x540`: mySubMode == 5 || mySubMode == 7
    (frozenset({(960, 540)}), _B, frozenset({5, 7}), _A),
    # `is1920x462 || is1920x440`: mySubMode < 2 || mySubMode > 4
    (frozenset({(1920, 462), (1920, 440)}), _A, frozenset({2, 3, 4}), _B),
    # `is640x480 || is360x360 || is640x172` — no guard.
    (frozenset({(640, 480), (360, 360), (640, 172)}), _A, None, _A),
)

# ImageTo565.  `is240x240 || is320x320 || is480x480`, then `is640x172`.
_565_ARMS: tuple[tuple[frozenset[tuple[int, int]], dict[int, int]], ...] = (
    (frozenset({(240, 240), (320, 320), (480, 480)}), _A),
    (frozenset({(640, 172)}), _F),
)

# The Mjolnir: `myDevicePingMu == 5` is tested BEFORE every resolution guard in
# ImageToJpg, so a 320x240 JPEG panel takes this arm and never the default.
_PM5_ARM = _A
# `myDevicePingMu == 6` inside the JPEG square guard — the FW360 Ultra's
# physical mount.  Our port applies this as `encode_baseline`, a separate
# rotation composed with the encode angle, so an auditor must add the two
# before comparing.
_PM6_ARM = _B


def csharp_encode_angles(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0, sub: int = 0,
) -> dict[int, int]:
    """Every ``directionB -> rotation`` the C# would apply to this panel.

    Branch ORDER is the C#'s, because order decides ties: a 480x480 JPEG panel
    with ``pm == 6`` hits the square guard's PM sub-branch, and a 320x240 JPEG
    panel hits ``myDevicePingMu == 5`` before any resolution is tested.
    """
    if not jpeg:
        for resolutions, arm in _565_ARMS:
            if resolution in resolutions:
                return dict(arm)
        return dict(_C)
    if resolution in ((320, 320), (480, 480)):
        return dict(_PM6_ARM if pm == 6 else _A)
    if pm == 5:
        return dict(_PM5_ARM)
    my_sub_mode = csharp_my_sub_mode(resolution, sub)
    for resolutions, guarded, guard, otherwise in _JPG_ARMS:
        if resolution not in resolutions:
            continue
        if guard is not None and my_sub_mode in guard:
            return dict(guarded)
        return dict(otherwise)
    return dict(_C)


def csharp_encode_base(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0, sub: int = 0,
) -> int:
    """The rotation at ``directionB == 0`` — the panel's dir-0 mount offset."""
    return csharp_encode_angles(resolution, jpeg=jpeg, pm=pm, sub=sub)[0]


def csharp_wire_rotation(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0, sub: int = 0,
    orientation: int = 0,
) -> int:
    """The rotation the C# applies at one display angle."""
    return csharp_encode_angles(
        resolution, jpeg=jpeg, pm=pm, sub=sub)[orientation % 360]
