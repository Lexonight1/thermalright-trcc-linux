"""C# encode-rotation oracle — the ``directionB`` switches, transcribed.

Source: ``TRCC.CZTV/FormCZTV.cs`` ``ImageToJpg`` and ``ImageTo565`` of the
release named by ``core.csharp.ORACLE_VERSION``.  Verify the tree before
trusting a citation::

    grep -rh AssemblyVersion "$TRCC_DECOMPILE"/Properties/*.cs

**This file stores the switch arms LITERALLY** — one ``directionB -> angle``
map per arm, exactly the ``RotateImg`` argument the C# passes — and not the
``(base, invert)`` algebra ``trcc.core.protocol`` uses.  That is deliberate and
is the whole value of the file: an oracle written in the same form as the code
it audits can only catch typos, never a wrong model.  Stated as arms, it also
catches the sign error that shipped for months (854x480 counts UP with the
display angle; every other family counts down).

**Input is ``mySubMode``, never the handshake SUB byte.**  The switch tests
``mySubMode``, which ``FormCZTVInit`` derives from the SUB byte on some
branches and leaves at 0 on others.  That derivation is
``formcztv_init.form_cztv_init``'s job alone.  This file used to carry a second,
coarser copy of it, and the conformance gate fed it the emulator's already
derived value, so 854x480's ``mySubMode`` was zeroed a second time and the gate
compared against a release older than the one it named.

**Arms our port deliberately does not follow** carry ``not_ported`` — the reason,
on the row, so a test that finds a difference reads the excuse from the oracle
instead of from a second list.  See
``memory/project_2_1_8_encoder_arms_unported.md`` for the evidence.

PURE — no I/O, no framework.  Lives in ``dev/`` because it is a reference for
auditing, never shipped logic; nothing in ``src/trcc`` may import it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

# ── Why an arm is not ported — read by the parity tests ────────────────────
NOT_PORTED_NO_GLASS = (
    "a managed-assembly arm newer than the release this port was built on, "
    "on a fingerprint no reporter has posted: no glass to confirm it, and "
    "agreement with the managed app is not independent evidence "
    "(project_the_oracle_authority_is_asymmetric)"
)
NOT_PORTED_SUB3_GLASS_MIXED = (
    "1920x462/440 SUB 3 (LD7, LY PM 65) moved from 0 to 180.  Three reporters "
    "own it and their glass disagrees: #101 and #129 'work fine' at wire 0, "
    "#256 said wire 180 was correct on v9.0.2 and wire 0 'looks fine' on "
    "v9.9.7.  Held until #256 answers on real glass"
)

# ── The arms, as literal directionB -> RotateImg(angle) maps ────────────────
_A = {0: 0, 90: 270, 180: 180, 270: 90}      # the common "counts down" arm
_B = {0: 180, 90: 90, 180: 0, 270: 270}      # the same, offset by 180
_C = {0: 90, 90: 0, 180: 270, 270: 180}      # both switches' DEFAULT arm
_D = {0: 0, 90: 90, 180: 180, 270: 270}      # counts UP — 854/800 only
_E = {0: 180, 90: 270, 180: 0, 270: 90}      # counts up, offset — 854/800 alt
_F = {0: 270, 90: 180, 180: 90, 270: 0}      # ImageTo565 640x172


class Arm(NamedTuple):
    """One branch of a ``directionB`` switch.

    ``resolutions`` is ``None`` for a branch the C# tests before any resolution
    guard.  ``guard(myDevicePingMu, mySubMode)`` says whether it is taken.
    ``mirror`` is a ``RotateFlip(...FlipX)`` — GDI+ rotates clockwise for both
    ``RotateTransform`` (inside ``RotateImg``) and ``RotateFlipType``, so a
    mirror arm is its rotation in ``angles`` plus a horizontal flip.
    """
    resolutions: frozenset[tuple[int, int]] | None
    guard: Callable[[int, int], bool]
    angles: dict[int, int]
    mirror: bool = False
    not_ported: str | None = None


def _res(*resolutions: tuple[int, int]) -> frozenset[tuple[int, int]]:
    return frozenset(resolutions)


def _always(pm: int, sub: int) -> bool:
    return True


_SQUARE = _res((320, 320), (480, 480))
_WIDE_1920 = _res((1920, 462), (1920, 440))
_WIDE_854 = _res((854, 480), (800, 480))

# ImageToJpg, in the C#'s own branch order.  First match wins.
_JPG_ARMS: tuple[Arm, ...] = (
    # `is320x320 || is480x480`: `pm == 6 || mySubMode == 7` → offset.  PM 6 is
    # the FW360 mount, which our port applies as `encode_baseline`, a separate
    # rotation an auditor adds before comparing.  mySubMode is only ever
    # assigned for a square on the mode-2 pm 4 branch.
    Arm(_SQUARE, lambda pm, sub: pm == 6, _B),
    Arm(_SQUARE, lambda pm, sub: sub == 7, _B, not_ported=NOT_PORTED_NO_GLASS),
    # `else if (pm != 4 || mySubMode != 0)` → _A, else Rotate90/None/270/180FlipX.
    Arm(_SQUARE, lambda pm, sub: pm == 4 and sub == 0, _C, mirror=True,
        not_ported=NOT_PORTED_NO_GLASS),
    Arm(_SQUARE, _always, _A),
    # The Mjolnir: `myDevicePingMu == 5`, tested before every other resolution.
    Arm(None, lambda pm, sub: pm == 5, _A),
    # `is1600x720`: mySubMode == 3.
    Arm(_res((1600, 720)), lambda pm, sub: sub == 3, _A),
    Arm(_res((1600, 720)), _always, _B),
    # `is1280x480`: mySubMode == 2.
    Arm(_res((1280, 480)), lambda pm, sub: sub == 2, _C),
    Arm(_res((1280, 480)), _always, _A),
    # `is960x320`: mySubMode < 5 → _A, == 7 → _B, else _A (it was _B).
    Arm(_res((960, 320)), lambda pm, sub: sub < 5, _A),
    Arm(_res((960, 320)), lambda pm, sub: sub == 7, _B),
    Arm(_res((960, 320)), _always, _A, not_ported=NOT_PORTED_NO_GLASS),
    # `is960x540`: mySubMode == 5 || == 7.
    Arm(_res((960, 540)), lambda pm, sub: sub in (5, 7), _B),
    Arm(_res((960, 540)), _always, _A),
    # `is1920x462 || is1920x440`: `(pm != 66 || sub != 2) ? (sub == 2 || sub
    # == 4 ? _A : _B) : _B`.  SUB 3 is split out of the else only to label it.
    Arm(_WIDE_1920, lambda pm, sub: pm == 66 and sub == 2, _B,
        not_ported=NOT_PORTED_NO_GLASS),
    Arm(_WIDE_1920, lambda pm, sub: sub in (2, 4), _A),
    Arm(_WIDE_1920, lambda pm, sub: sub == 3, _B,
        not_ported=NOT_PORTED_SUB3_GLASS_MIXED),
    Arm(_WIDE_1920, _always, _B),
    # `is640x480 || is360x360 || is640x172` — no guard.
    Arm(_res((640, 480), (360, 360), (640, 172)), _always, _A),
    # `is854x480 || is800x480` (the final else): mySubMode == 2 → _E, else _D,
    # then `RotateFlip(RotateNoneFlipX)` when mySubMode == 0.
    Arm(_WIDE_854, lambda pm, sub: sub == 2, _E,
        not_ported=NOT_PORTED_NO_GLASS),
    Arm(_WIDE_854, lambda pm, sub: sub == 0, _D, mirror=True,
        not_ported=NOT_PORTED_NO_GLASS),
    Arm(_WIDE_854, _always, _D),
)

# ImageTo565.  `is240x240 || is320x320 || is480x480 || is360x360`, then
# `is640x172`: mySubMode == 5 → the default arm, else _F.
_565_ARMS: tuple[Arm, ...] = (
    Arm(_res((240, 240), (320, 320), (480, 480), (360, 360)), _always, _A),
    Arm(_res((640, 172)), lambda pm, sub: sub == 5, _C),
    Arm(_res((640, 172)), _always, _F),
)

# Both switches end in the same default arm.
_DEFAULT_ARM = Arm(None, _always, _C)


def csharp_encode(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0,
    my_sub_mode: int = 0,
) -> Arm:
    """The switch arm the C# takes for this panel — first match, C# order."""
    return next(
        (arm for arm in (_JPG_ARMS if jpeg else _565_ARMS)
         if (arm.resolutions is None or resolution in arm.resolutions)
         and arm.guard(pm, my_sub_mode)),
        _DEFAULT_ARM)


def csharp_encode_angles(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0,
    my_sub_mode: int = 0,
) -> dict[int, int]:
    """Every ``directionB -> rotation`` the C# would apply to this panel."""
    return dict(csharp_encode(
        resolution, jpeg=jpeg, pm=pm, my_sub_mode=my_sub_mode).angles)


def csharp_encode_base(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0,
    my_sub_mode: int = 0,
) -> int:
    """The rotation at ``directionB == 0`` — the panel's dir-0 mount offset."""
    return csharp_encode_angles(
        resolution, jpeg=jpeg, pm=pm, my_sub_mode=my_sub_mode)[0]


def csharp_wire_rotation(
    resolution: tuple[int, int], *, jpeg: bool, pm: int = 0,
    my_sub_mode: int = 0, orientation: int = 0,
) -> int:
    """The rotation the C# applies at one display angle."""
    return csharp_encode_angles(
        resolution, jpeg=jpeg, pm=pm, my_sub_mode=my_sub_mode)[orientation % 360]


def csharp_rgb565_big_endian(*, is320x320: bool, mode: int, spi_mode: int) -> bool:
    """Whether ``ImageTo565`` packs RGB565 big-endian -- FormCZTV.cs:4261-4275.

    ``if (is320x320 || myDeviceMode == 10)`` and ``else if (myDeviceSPIMode ==
    2)`` both write the RRRRRGGG byte first; the else branch writes it second.
    ``SPIMode = 2`` is set by FormCZTVInit for mode 2 PM 50 (:884), mode 1 FBL
    51 (:1065) and mode 3 FBL 49 (:1069).  ``GifTo565`` (the boot animation,
    :2027) is the same rule without the mode-10 case, which mode 1 never has.
    """
    return is320x320 or mode == 10 or spi_mode == 2
