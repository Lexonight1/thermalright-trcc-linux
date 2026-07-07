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

import logging
from dataclasses import dataclass

from .models import LedStyle

log = logging.getLogger(__name__)

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
    log.info("resolve_pm: pm=%d sub_type=%d", pm, sub_type)
    _ = sub_type
    return _PM_REGISTRY.get(pm)


# =========================================================================
# Handshake fingerprint overrides
# =========================================================================
#
# A product can reuse another device's PM byte yet ship a distinct firmware
# handshake header, leaving the PM byte alone ambiguous.  The Thermalright
# Magic Qube reports PM=208 (same as CZ1) but answers the HID handshake with
# the header ``DC DD AA 01`` instead of the standard ``DA DB DC DD`` — the
# only wire-level signal that tells it apart from a genuine CZ1.  Header
# overrides are checked before the PM registry.

_HEADER_OVERRIDES: dict[bytes, PmEntry] = {
    bytes([0xDC, 0xDD, 0xAA, 0x01]): PmEntry(LedStyle.MAGIC_QUBE, "MAGIC_QUBE"),
}

_OVERRIDES_BY_MODEL: dict[str, PmEntry] = {
    entry.model_name: entry for entry in _HEADER_OVERRIDES.values()
}


def resolve_handshake(
    header: bytes, pm: int, sub_type: int = 0,
) -> PmEntry | None:
    """Resolve a device from its handshake — header fingerprint first.

    A handful of products reuse another device's PM byte but ship a distinct
    handshake header (firmware fingerprint).  When the leading four header
    bytes match a known override that entry wins; otherwise we fall back to
    the PM-byte registry (:func:`resolve_pm`).
    """
    entry = _HEADER_OVERRIDES.get(bytes(header[:4]))
    if entry is not None:
        log.info("resolve_handshake: header %s → %s (overrides pm=%d)",
                 bytes(header[:4]).hex(), entry.model_name, pm)
        return entry
    return resolve_pm(pm, sub_type)


def resolve_model_name(model_name: str) -> PmEntry | None:
    """Resolve a fingerprint-override entry by its model name.

    Used by the probe-cache fallback: the cache persists the model name, not
    the handshake header, so a cached fingerprint device is recovered by
    name.  Returns ``None`` for names not carried by an override — the caller
    then falls back to the PM registry.
    """
    return _OVERRIDES_BY_MODEL.get(model_name)


def is_fingerprint_header(header: bytes) -> bool:
    """Whether the handshake header matches a known device fingerprint.

    Lets the LED adapter treat a recognised non-standard header (e.g. the
    Magic Qube's ``DC DD AA 01``) as expected instead of logging it as an
    anomaly next to the standard ``DA DB DC DD`` magic.
    """
    return bytes(header[:4]) in _HEADER_OVERRIDES


# ═══════════════════════════════════════════════════════════════════════
# Wire-order remap tables
# ═══════════════════════════════════════════════════════════════════════
#
# Each per-style table maps physical-wire index → logical color index.
# For physical LED ``i``, the color sent is ``logical_colors[table[i]]``.
# Byte-for-byte port of legacy ``core/models/led.py`` LED_REMAP_TABLES
# and LED_REMAP_SUB_TABLES — same indices, same lengths.
#
# Styles without a remap table (AX120, CZ1, LC2 in some variants) pass
# colors through unchanged.

_REMAP_STYLE_PA120: tuple[int, ...] = (
    1, 0, 15, 10, 11, 16, 14, 13, 12,
    22, 17, 18, 23, 21, 20, 19,
    29, 24, 25, 30, 28, 27, 26,
    4, 5, 81, 80,
    36, 31, 32, 37, 35, 34, 33,
    43, 38, 39, 44, 42, 41, 40,
    6, 9,
    75, 76, 77, 79, 74, 73, 78,
    68, 69, 70, 72, 67, 66, 71,
    82, 83, 7, 8,
    61, 62, 63, 65, 60, 59, 64,
    54, 55, 56, 58, 53, 52, 57,
    47, 48, 49, 51, 46, 45, 50,
    2, 3,
)

_REMAP_STYLE_AK120: tuple[int, ...] = (
    1, 22, 23, 24, 26, 21, 20, 25,
    14, 0, 13, 18, 19, 15, 16, 17,
    7, 6, 11, 12, 8, 9, 10,
    32, 27, 28, 33, 31, 30, 29,
    39, 34, 35, 40, 38, 37, 36,
    46, 41, 42, 47, 45, 44, 43,
    2, 3, 4,
    57, 58, 59, 61, 56, 55, 60,
    50, 5, 51, 52, 54, 49, 48, 53,
    62, 63,
)

_REMAP_STYLE_LC1: tuple[int, ...] = (
    2, 1, 26, 27, 28, 30, 25, 24, 0,
    29, 19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_LF8: tuple[int, ...] = (
    6, 86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    11, 10, 9, 13, 12, 7, 0, 8,
    18, 17, 16, 20, 19, 14, 1, 15,
    25, 24, 23, 27, 26, 21, 22,
    3, 2,
    32, 31, 30, 34, 33, 28, 29,
    39, 38, 37, 41, 40, 35, 36,
    46, 45, 44, 48, 47, 42, 43,
    4, 92,
)

_REMAP_STYLE_LF8_SUB1: tuple[int, ...] = (
    4,
    43, 42, 47, 48, 44, 45, 46,
    36, 35, 40, 41, 37, 38, 39,
    29, 28, 33, 34, 30, 31, 32,
    3, 2,
    22, 21, 26, 27, 23, 24, 25,
    15, 1, 14, 19, 20, 16, 17, 18,
    8, 0, 7, 12, 13, 9, 10, 11,
    54, 49, 50, 55, 53, 52, 51,
    61, 56, 57, 62, 60, 59, 58,
    68, 63, 64, 69, 67, 66, 65,
    75, 70, 71, 76, 74, 73, 72,
    5, 92, 91,
    82, 77, 78, 83, 81, 80, 79,
    89, 84, 85, 90, 88, 87, 86,
    6,
    96, 95, 94, 93,
    169, 168, 167, 166, 165, 164, 163, 162, 161, 160,
    159, 158, 157, 156, 155, 154, 153, 152, 151, 150,
    149, 148, 147, 146, 145, 144, 143, 142, 141, 140,
    139, 138, 137, 136, 135, 134, 133, 132, 131, 130,
    129, 128, 127, 126, 125, 124, 123, 122, 121, 120,
    119, 118, 117, 116, 115, 114, 113, 112, 111, 110,
    109, 108, 107, 106, 105, 104, 103, 102, 101, 100,
    99, 98, 97,
)

_REMAP_STYLE_LF12: tuple[int, ...] = (
    119, 120, 121, 122, 123,
    122, 121, 120, 119,
    6, 6,
    86, 87, 88, 90, 85, 84, 89,
    79, 80, 81, 83, 78, 77, 82,
    92, 91, 5,
    72, 73, 74, 76, 71, 70, 75,
    65, 66, 67, 69, 64, 63, 68,
    58, 59, 60, 62, 57, 56, 61,
    51, 52, 53, 55, 50, 49, 54,
    105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
    4,
    44, 45, 46, 48, 43, 42, 47,
    37, 38, 39, 41, 36, 35, 40,
    30, 31, 32, 34, 29, 28, 33,
    2, 3,
    23, 24, 25, 27, 22, 21, 1, 26,
    16, 17, 18, 20, 15, 14, 19,
    9, 10, 11, 13, 8, 7, 12,
    0,
    93, 93, 94, 94, 95, 95, 96, 96, 97, 97, 98, 98,
    99, 99, 100, 100, 101, 101, 102, 102, 103, 103, 104, 104,
)

_REMAP_STYLE_LF10: tuple[int, ...] = (
    115, 114, 113, 112, 111, 110,
    110, 111, 112, 113, 114, 115,
    103, 102, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85, 84,
    104, 105, 106, 107, 108, 109,
    0, 0,
    17, 6, 7, 7, 8, 9, 10, 18, 18, 16, 15, 14, 13, 13, 12, 11,
    28, 27, 26, 26, 25, 24, 23, 31, 31, 29, 30, 19, 20, 20, 21, 22,
    43, 32, 33, 33, 34, 35, 36, 44, 44, 42, 41, 40, 39, 39, 38, 37,
    2, 2, 1, 1, 3, 3,
    56, 45, 46, 46, 47, 48, 49, 57, 57, 55, 54, 53, 52, 52, 51, 50,
    67, 66, 65, 65, 64, 63, 62, 70, 70, 68, 69, 58, 59, 59, 60, 61,
    82, 71, 72, 72, 73, 74, 75, 83, 83, 81, 80, 79, 78, 78, 77, 76,
    5, 5, 4, 4,
)

_REMAP_STYLE_LC2: tuple[int, ...] = (
    60, 59, 58, 57, 56, 55, 54,
    53, 52,
    36, 31, 32, 37, 35, 34, 33,
    2, 2, 2,
    43, 38, 39, 44, 42, 41, 40,
    50, 45, 46, 51, 49, 48, 47,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    0, 1,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)

_REMAP_STYLE_LF11: tuple[int, ...] = (
    2, 1,
    33, 34, 35, 37, 32, 31, 0, 36,
    26, 27, 28, 30, 25, 24, 29,
    19, 20, 21, 23, 18, 17, 22,
    12, 13, 14, 16, 11, 10, 15,
    5, 6, 7, 9, 4, 3, 8,
)


LED_REMAP_TABLES: dict[LedStyle, tuple[int, ...]] = {
    LedStyle.PA120: _REMAP_STYLE_PA120,
    LedStyle.AK120: _REMAP_STYLE_AK120,
    LedStyle.LC1:   _REMAP_STYLE_LC1,
    LedStyle.LF8:   _REMAP_STYLE_LF8,
    LedStyle.LF12:  _REMAP_STYLE_LF12,
    LedStyle.LF10:  _REMAP_STYLE_LF10,
    LedStyle.LC2:   _REMAP_STYLE_LC2,
    LedStyle.LF11:  _REMAP_STYLE_LF11,
}

LED_REMAP_SUB_TABLES: dict[tuple[LedStyle, int], tuple[int, ...]] = {
    (LedStyle.LF8, 1): _REMAP_STYLE_LF8_SUB1,   # LF25 variant
}


def remap_led_colors(
    colors: list[tuple[int, int, int]],
    style: LedStyle | None,
    style_sub: int = 0,
) -> list[tuple[int, int, int]]:
    """Reorder a logical color array to the physical wire order.

    Pure data transform — given ``colors`` keyed by logical (UI) index,
    return a new list keyed by physical (wire) index, using the per-style
    remap table. Styles without a table (AX120, CZ1, unknown) are passed
    through unchanged — caller gets the same list back by identity.

    Sub-tables (e.g. LF25 = LF8 with sub=1) take precedence over the
    base style table when both exist.
    """
    log.debug("remap_led_colors: style=%s sub=%d colors=%d",
              style, style_sub, len(colors))
    if style is None:
        return colors
    table = LED_REMAP_SUB_TABLES.get((style, style_sub))
    if table is None:
        table = LED_REMAP_TABLES.get(style)
    if table is None:
        return colors
    black = (0, 0, 0)
    return [colors[idx] if idx < len(colors) else black for idx in table]


__all__ = [
    "LED_REMAP_SUB_TABLES",
    "LED_REMAP_TABLES",
    "PmEntry",
    "is_fingerprint_header",
    "remap_led_colors",
    "resolve_handshake",
    "resolve_model_name",
    "resolve_pm",
]
