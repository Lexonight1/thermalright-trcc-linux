"""Per-(PM, SUB) variant overrides — applied after handshake.

One (VID, PID) often ships as multiple physical products sharing a
chipset.  The USB descriptors don't distinguish them; only the
handshake fingerprint (PM byte + optional SUB byte) does.

This module mirrors legacy's variant registry verbatim — same C# case
tables, same product-id strings — so a real port preserves every
sidebar button mapping reporters have validated against hardware.

Lookup:

    from .variants import get_variant_override
    override = get_variant_override(vid, pid, pm, sub)
    if override and override.button_image:
        product_info.button_image = override.button_image

The override path runs once, post-handshake, when the App finalises
the ProductInfo for the connected device.  Without it every device
falls back to ``ProductInfo.button_image`` (registry default) and the
GUI sidebar shows the wrong product picture.

Source-of-truth comment from legacy ``device.py``:

    Bulk USBLCDNEW family (C# case 257) — the largest PM table.  Bulk
    (0x87AD:0x70DB) is the canonical home; SCSI thermalright VID/PIDs
    alias to this same dict because the C# ADDUserButton dispatcher
    routes SCSI handshakes through case 257 too.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PanelCutout:
    """Rectangular cutout on the panel (e.g. smartphone-display camera notch).

    Hardware fact — registry data, not user preference.  Populated on a
    ``VariantOverride`` when the panel has an obstructed region the
    render pipeline should mask.
    """
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class VariantOverride:
    """Per-(PM, SUB) overrides applied after handshake.

    Resolves cases where one (VID, PID) ships as multiple physical
    products sharing a chipset — the handshake fingerprint
    distinguishes them.
    """
    button_image: str = ""
    panel_cutout: PanelCutout | None = None
    display_name: str = ""


def _v(button_image: str) -> VariantOverride:
    """Shorthand for VariantOverride rows that carry only a button image."""
    return VariantOverride(button_image=button_image)


# Bulk USBLCDNEW family (C# case 257) — the largest PM table.  Bulk
# (0x87AD:0x70DB) is the canonical home; SCSI thermalright VID/PIDs alias
# to this same dict.
_BULK_VARIANTS: dict[int, dict[int | None, VariantOverride]] = {
    1:   {0: _v('A1GRAND VISION'), 1: _v('A1GRAND VISION'),
          48: _v('A1LM22'), 49: _v('A1LF14'), None: _v('A1GRAND VISION')},
    3:   {None: _v('A1CORE VISION')},
    4:   {1: _v('A1HYPER VISION'), 2: _v('A1RP130 VISION'), 3: _v('A1LM16SE'),
          4: _v('A1LF10V'), 5: _v('A1LM19SE'), 6: _v('A1LF014')},   # 2.1.6: +sub6
    5:   {None: _v('A1Mjolnir VISION')},
    6:   {1: _v('frozen_warframe_ultra'), 2: _v('A1FROZEN VISION V2')},
    7:   {1: _v('A1Stream Vision'), 2: _v('A1Mjolnir VISION PRO')},
    9:   {0: _v('A1LC2JD'), 1: _v('A1LC2JD'), 2: _v('A1LC2JD'), 3: _v('A1LC2JD'),
          4: _v('A1LC2JD'), None: _v('A1LF19')},
    10:  {5: _v('A1LF16'), 6: _v('A1LF18'), 7: _v('A1LD6'), None: _v('A1LC3')},
    11:  {6: _v('A1LD8'), None: _v('A1LF19')},
    12:  {None: _v('A1LF167')},
    13:  {None: _v('A1PC1')},
    14:  {1: _v('A1Stream Vision'), 2: _v('A1Mjolnir VISION PRO')},
    15:  {2: _v('A1LC8'), None: _v('A1LC7')},
    16:  {None: _v('A1CZ2')},
    # 2.1.6 ADDUserButton case 17: sub1|5→PC1, sub2→LC8, sub3→LC10, else PC1.
    # CHANGED vs our prior table (sub2 was LC9 — LC9 is 0416:5302 pm59, not here).
    17:  {1: _v('A1PC1'), 2: _v('A1LC8'), 3: _v('A1LC10'), 5: _v('A1PC1'),
          None: _v('A1PC1')},
    18:  {1: _v('A1LC8'), 2: _v('A1LC10'), None: _v('A1PC1')},   # 2.1.6: new pm
    19:  {None: _v('A1LF19')},                                   # 2.1.6: new pm
    32:  {0: _v('A1ELITE VISION'), 1: _v('A1FROZEN WARFRAME PRO'),
          None: _v('A1ELITE VISION')},
    50:  {None: _v('A1FROZEN WARFRAME')},
    51:  {None: _v('A1FROZEN WARFRAME')},
    53:  {1: _v('A1LF21'), 2: _v('A1LF22'), None: _v('A1LF20')},
    63:  {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'), 2: _v('A1LM27'),
          3: _v('A1LM30'), 4: _v('A1RX1')},   # 2.1.6: +sub4
    # PM=64 SUB=3: Levita (new product) handshakes identically to LM30
    # — same chipset, same wire bytes.  C# v2.1.4 doesn't have a Levita
    # case yet so it labels the button "A1LM30"; we keep that label and
    # attach a right-side panel_cutout because Levita is the variant
    # users enable split_mode on.
    64:  {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'), 2: _v('A1LM27'),
          3: VariantOverride(button_image='A1LM30',
                             panel_cutout=PanelCutout(x=1520, y=0, w=80, h=720)),
          4: _v('A1RX1')},   # 2.1.6: +sub4
    # 2.1.6 ADDUserButton case 65 sub4: A1LD11 (was A1LD10 in our prior table).
    # (case 66's LD11 branch is dead code — `else if(sub<=4)` precedes it.)
    65:  {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), 2: _v('A1LF14'),
          3: _v('A1LD7'), 4: _v('A1LD11'), 5: _v('A1LD7')},
    66:  {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), 2: _v('A1LF14'),
          3: _v('A1LD7'), 4: _v('A1LD7')},
    68:  {None: _v('A1LM24')},
    69:  {2: _v('A1LD9')},
    100: {0: _v('A1FROZEN WARFRAME PRO'), 1: _v('A1LM22'),
          None: _v('A1FROZEN WARFRAME PRO')},
    101: {0: _v('A1ELITE VISION'), 1: _v('A1LF14'), None: _v('A1ELITE VISION')},
    128: {None: _v('A1LM24')},
    129: {None: _v('A1GRAND VISION')},
}


_VARIANT_REGISTRY: dict[
    tuple[int, int], dict[int, dict[int | None, VariantOverride]],
] = {
    # ── LED (case 1) — 0x0416:0x8001 ────────────────────────────────────
    (0x0416, 0x8001): {
        1:   {None: _v('A1FROZEN HORIZON PRO')},
        2:   {None: _v('A1FROZEN MAGIC PRO')},
        3:   {None: _v('A1AX120 DIGITAL')},
        16:  {None: _v('A1PA120 DIGITAL')},
        23:  {None: _v('A1RK120 DIGITAL')},
        32:  {None: _v('A1AK120 Digital')},
        48:  {None: _v('A1LF8')},
        49:  {None: _v('A1LF10')},
        80:  {None: _v('A1LF12')},
        96:  {None: _v('A1LF10')},
        112: {None: _v('A1LC2')},
        128: {None: _v('A1LC1')},
        129: {None: _v('A1LF11')},
        144: {None: _v('A1LF15')},
        160: {None: _v('A1LF13')},
        176: {None: _v('A1LF25')},
        208: {None: _v('A1CZ1')},
        **{pm: {None: _v('A1PA120 DIGITAL')} for pm in range(17, 32) if pm != 23},
    },
    # ── HID T2 LCD (case 2) — 0x0416:0x5302 ─────────────────────────────
    (0x0416, 0x5302): {
        36:  {None: _v('A1AS120 VISION')},
        49:  {None: _v('A1FROZEN WARFRAME')},
        50:  {None: _v('A1FROZEN WARFRAME')},
        51:  {None: _v('A1FROZEN WARFRAME')},
        52:  {None: _v('A1BA120 VISION')},
        53:  {None: _v('A1LF20')},
        54:  {2: _v('A1LC13'), None: _v('A1LC5')},   # 2.1.6: +sub2
        58:  {0: _v('A1FROZEN WARFRAME SE'), None: _v('A1LM26')},
        59:  {0: _v('A1LC7'), 1: _v('A1LC7'), None: _v('A1LC9')},  # 2.1.6: new pm
        60:  {None: _v('A1LC15')},                                 # 2.1.6: new pm
        100: {None: _v('A1FROZEN WARFRAME PRO')},
        101: {None: _v('A1ELITE VISION')},
        128: {None: _v('A1LM24')},
    },
    # ── Bulk USBLCDNEW (case 257) + SCSI thermalright (case 257 in C#) ─
    # All share the _BULK_VARIANTS PM table — same dispatcher branch in C#.
    (0x87AD, 0x70DB): _BULK_VARIANTS,
    (0x87CD, 0x70DB): _BULK_VARIANTS,
    (0x0402, 0x3922): _BULK_VARIANTS,
    (0x0416, 0x5406): _BULK_VARIANTS,
}


def _resolve_variant(
    table: dict[int, dict[int | None, VariantOverride]],
    key: int,
    sub: int,
) -> VariantOverride | None:
    """Look up a VariantOverride by (PM key, SUB), preferring exact match."""
    match table.get(key):
        case None:
            return None
        case sub_map if sub in sub_map:
            return sub_map[sub]
        case sub_map:
            return sub_map.get(None)


def get_variant_override(
    vid: int, pid: int, key: int, sub: int = 0,
) -> VariantOverride | None:
    """Resolve full VariantOverride record for a handshake fingerprint.

    Args:
        vid: USB Vendor ID — scopes the lookup to one chipset family.
        pid: USB Product ID — scopes within the vendor.
        key: PM byte from handshake (or FBL fallback when PM=0).
        sub: SUB byte from handshake.

    Returns the override or ``None`` if no entry matches; callers
    should leave the registry-default values in place when ``None``.
    """
    log.info("get_variant_override: vid=%04x pid=%04x key=%d sub=%d",
             vid, pid, key, sub)
    family = _VARIANT_REGISTRY.get((vid, pid))
    if family is None:
        return None
    return _resolve_variant(family, key, sub)


def get_button_image(
    vid: int, pid: int, key: int, sub: int = 0,
) -> str | None:
    """Resolve device button image from (VID, PID, PM, SUB)."""
    log.info("get_button_image: vid=%04x pid=%04x key=%d sub=%d",
             vid, pid, key, sub)
    override = get_variant_override(vid, pid, key, sub)
    return override.button_image if override is not None else None


__all__ = [
    "PanelCutout",
    "VariantOverride",
    "get_button_image",
    "get_variant_override",
]
