"""LED handshake PM-byte → device-metadata resolver.

Symmetric with ``core/protocol.py`` (which resolves LCD handshake FBL
bytes into ``DeviceProfile``). The LED equivalent:

  * a frozen ``PmEntry`` dataclass carrying ``style`` + ``model_name`` +
    ``style_sub`` (FormLED ``nowLedStyleSub`` — a wire-remap variant
    within the same style),
  * a ``_PM_REGISTRY`` dict keyed by the firmware PM byte (raw ``resp[5]``
    from the Led handshake),
  * a ``resolve_pm(pm, sub_type=0)`` lookup that returns the entry or
    ``None`` if the PM byte is unknown.

The registry is the byte-for-byte port of legacy ``PmRegistry`` in
``core/models/led.py`` — every entry kept, every style_sub kept, every
PA120 variant in the 17..31 range expanded.

LED segment math (the per-style wire-remap tables + segment carousel
state) is a separate gap and lives elsewhere — this module is purely
the PM → metadata lookup.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import LedStyle

# =========================================================================
# Entry shape
# =========================================================================


@dataclass(frozen=True, slots=True)
class PmEntry:
    """One PM-registry row: device style + readable model name + sub variant.

    ``style_sub`` corresponds to legacy ``nowLedStyleSub`` — a variant
    within the same style that swaps the wire-remap table used during
    color transmission (e.g. LF25 is style LF8 with sub=1; LF11 ships
    on style LF11 with sub=1).
    """
    style: LedStyle
    model_name: str
    style_sub: int = 0


# =========================================================================
# Base registry — single PM byte → PmEntry
# =========================================================================
#
# Lines 256-277 of legacy ``core/models/led.py``. PA120 variants (PMs
# 17-22, 24-31) are unrolled here instead of being computed at class-load
# time — the data is small enough that "data describes itself" beats
# the dict-comprehension cleverness.

_PM_REGISTRY: dict[int, PmEntry] = {
    1:   PmEntry(LedStyle.AX120, "FROZEN_HORIZON_PRO"),
    2:   PmEntry(LedStyle.AX120, "FROZEN_MAGIC_PRO"),
    3:   PmEntry(LedStyle.AX120, "AX120_DIGITAL"),
    16:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    17:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    18:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    19:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    20:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    21:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    22:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    23:  PmEntry(LedStyle.PA120, "RK120_DIGITAL"),
    24:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    25:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    26:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    27:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    28:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    29:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    30:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    31:  PmEntry(LedStyle.PA120, "PA120_DIGITAL"),
    32:  PmEntry(LedStyle.AK120, "AK120_DIGITAL"),
    48:  PmEntry(LedStyle.LF8,   "LF8"),
    49:  PmEntry(LedStyle.LF8,   "LF10"),
    80:  PmEntry(LedStyle.LF12,  "LF12"),
    96:  PmEntry(LedStyle.LF10,  "LF10"),
    112: PmEntry(LedStyle.LC2,   "LC2"),
    128: PmEntry(LedStyle.LC1,   "LC1"),
    129: PmEntry(LedStyle.LF11,  "LF11", style_sub=1),
    144: PmEntry(LedStyle.LF15,  "LF15"),
    160: PmEntry(LedStyle.LF13,  "LF13"),
    176: PmEntry(LedStyle.LF8,   "LF25", style_sub=1),
    208: PmEntry(LedStyle.CZ1,   "CZ1"),
}


# =========================================================================
# Public lookup
# =========================================================================


def resolve_pm(pm: int, sub_type: int = 0) -> PmEntry | None:
    """Resolve a PM byte (+ optional SUB) to a ``PmEntry``.

    Returns ``None`` when the PM byte isn't in the registry — callers
    fall back to whatever per-product default they hold (today that's
    the ``ProductInfo.led_style`` field on the registry row, which is
    ``None`` for every LED product so the caller logs an unknown PM).

    The ``sub_type`` parameter is reserved for future per-(pm, sub)
    overrides — legacy ``PmRegistry`` exposed an ``_OVERRIDES`` table
    that was always empty in the shipping codebase. Kept in the
    signature so callers don't change shape when an override lands.
    """
    _ = sub_type
    return _PM_REGISTRY.get(pm)
