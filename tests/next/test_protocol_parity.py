"""Parity tests: next/core/protocol.py matches legacy core/models/protocol.py.

Locks the byte-equivalence claim from the 2026-05-18 byte-diff audit: the FBL
profile table, PM→FBL overrides, disambiguation dicts, and lookup functions
in next/ must produce identical results to legacy for every value in the
in-the-wild registry. If a divergence is ever introduced, these tests fail
loudly and name the FBL / PM / SUB that broke.

Both modules are imported and compared field-by-field — no copy-and-pray.
"""
from __future__ import annotations

import pytest

from trcc.core.models import protocol as legacy
from trcc.next.core import protocol as nxt

# =============================================================================
# pm_to_fbl()
# =============================================================================


@pytest.mark.parametrize("pm", sorted(legacy._PM_TO_FBL_OVERRIDES.keys()))
def test_pm_to_fbl_override_matches(pm: int) -> None:
    """Every PM in legacy's override table maps to the same FBL in next/."""
    assert nxt.pm_to_fbl(pm, 0) == legacy.pm_to_fbl(pm, 0)


@pytest.mark.parametrize("pm,sub", sorted(legacy._PM_SUB_TO_FBL.keys()))
def test_pm_sub_compound_matches(pm: int, sub: int) -> None:
    """Compound (PM, SUB) keys map to the same FBL in both."""
    assert nxt.pm_to_fbl(pm, sub) == legacy.pm_to_fbl(pm, sub)


@pytest.mark.parametrize("pm", [0, 1, 2, 3, 4, 8, 99, 200, 255])
def test_pm_to_fbl_default_equals_pm(pm: int) -> None:
    """PM values without an override map to themselves (SCSI convention)."""
    if pm in legacy._PM_TO_FBL_OVERRIDES:
        pytest.skip(f"PM {pm} has an override — covered by override test")
    assert nxt.pm_to_fbl(pm, 0) == pm
    assert nxt.pm_to_fbl(pm, 0) == legacy.pm_to_fbl(pm, 0)


# =============================================================================
# FBL_PROFILES — every field of every entry
# =============================================================================


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_profile_resolution_matches(fbl: int) -> None:
    """Width/height of each FBL profile is identical."""
    nxt_p = nxt.FBL_PROFILES[fbl]
    leg_p = legacy.FBL_PROFILES[fbl]
    assert (nxt_p.width, nxt_p.height) == (leg_p.width, leg_p.height), (
        f"FBL {fbl}: next/={nxt_p.width}x{nxt_p.height} "
        f"legacy={leg_p.width}x{leg_p.height}"
    )


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_profile_encoding_flags_match(fbl: int) -> None:
    """jpeg, big_endian, rotate flags identical for each FBL."""
    nxt_p = nxt.FBL_PROFILES[fbl]
    leg_p = legacy.FBL_PROFILES[fbl]
    assert nxt_p.jpeg == leg_p.jpeg, f"FBL {fbl}: jpeg mismatch"
    assert nxt_p.big_endian == leg_p.big_endian, f"FBL {fbl}: big_endian mismatch"
    assert nxt_p.rotate == leg_p.rotate, f"FBL {fbl}: rotate mismatch"


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_profile_encode_rotation_fields_match(fbl: int) -> None:
    """encode_base, encode_invert, encode_sub_bases, encode_pm_bases identical."""
    nxt_p = nxt.FBL_PROFILES[fbl]
    leg_p = legacy.FBL_PROFILES[fbl]
    assert nxt_p.encode_base == leg_p.encode_base
    assert nxt_p.encode_invert == leg_p.encode_invert
    assert tuple(nxt_p.encode_sub_bases) == tuple(leg_p.encode_sub_bases)
    assert tuple(nxt_p.encode_pm_bases) == tuple(leg_p.encode_pm_bases)


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_profile_byte_order_matches(fbl: int) -> None:
    """byte_order property derives identically."""
    assert nxt.FBL_PROFILES[fbl].byte_order == legacy.FBL_PROFILES[fbl].byte_order


def test_fbl_profiles_keys_are_identical() -> None:
    """Neither side has FBL entries the other lacks."""
    assert set(nxt.FBL_PROFILES.keys()) == set(legacy.FBL_PROFILES.keys())


# =============================================================================
# get_profile() + fbl_to_resolution() — including PM disambiguation
# =============================================================================


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_get_profile_resolution_matches(fbl: int) -> None:
    """Default PM=0 path returns the same resolution."""
    assert nxt.get_profile(fbl).resolution == legacy.get_profile(fbl).resolution


@pytest.mark.parametrize("pm", sorted(legacy._FBL_224_BY_PM.keys()))
def test_fbl_224_pm_disambiguation_matches(pm: int) -> None:
    """FBL 224 + PM produces the right disambiguated resolution."""
    assert nxt.get_profile(224, pm).resolution == legacy.get_profile(224, pm).resolution


@pytest.mark.parametrize("pm", sorted(legacy._FBL_192_BY_PM.keys()))
def test_fbl_192_pm_disambiguation_matches(pm: int) -> None:
    """FBL 192 + PM produces the right disambiguated resolution."""
    assert nxt.get_profile(192, pm).resolution == legacy.get_profile(192, pm).resolution


def test_unknown_fbl_falls_back_identically() -> None:
    """Unknown FBLs hit the default profile (320x320 big-endian) on both sides."""
    for fbl in (0, 1, 99, 200, 250, 255):
        if fbl in legacy.FBL_PROFILES:
            continue
        assert nxt.get_profile(fbl).resolution == legacy.get_profile(fbl).resolution
        assert nxt.get_profile(fbl).big_endian == legacy.get_profile(fbl).big_endian


@pytest.mark.parametrize("fbl", sorted(legacy.FBL_PROFILES.keys()))
def test_fbl_to_resolution_matches(fbl: int) -> None:
    """fbl_to_resolution() agrees with legacy at PM=0."""
    assert nxt.fbl_to_resolution(fbl) == legacy.fbl_to_resolution(fbl)


# =============================================================================
# get_encode_rotation() — exercise each branch of the angle formula
# =============================================================================


# (fbl, sub, direction, pm) — covers PM override, SUB override, no-override,
# and the encode_invert=False branch (FBL 224).
@pytest.mark.parametrize("fbl,sub,direction,pm", [
    # FBL 72 — PM=6 hits encode_pm_bases override (180° baseline)
    (72,   0,   0, 6),
    (72,   0,  90, 6),
    (72,   0, 180, 6),
    (72,   0, 270, 6),
    # FBL 72 — PM≠6 uses default encode_base=0
    (72,   0,  90, 0),
    # FBL 114 — sub=3 hits encode_sub_bases (0° instead of 180°)
    (114,  3,   0, 0),
    (114,  3, 180, 0),
    (114,  0,  90, 0),
    # FBL 128 — sub=2 → 90° baseline
    (128,  2,   0, 0),
    (128,  0,  90, 0),
    # FBL 192 — multi-sub overrides + encode_base=180
    (192,  2, 180, 0),
    (192,  3,  90, 0),
    (192,  4, 270, 0),
    (192,  0,  90, 0),
    # FBL 224 — encode_invert=False (sign flips)
    (224,  2,  90, 0),
    (224,  0,  90, 0),
    # Square panels, default profile, every direction
    (100,  0,   0, 0),
    (100,  0,  90, 0),
    (100,  0, 180, 0),
    (100,  0, 270, 0),
])
def test_encode_rotation_matches(fbl: int, sub: int, direction: int, pm: int) -> None:
    """Device-side encode rotation angle agrees across all branches."""
    nxt_profile = nxt.get_profile(fbl, pm)
    leg_profile = legacy.get_profile(fbl, pm)
    assert nxt.get_encode_rotation(nxt_profile, sub, direction, pm) == \
        legacy.get_encode_rotation(leg_profile, sub, direction, pm)


# =============================================================================
# Disambiguation dicts — direct equality
# =============================================================================


def test_pm_to_fbl_overrides_dict_matches() -> None:
    """The override dict has the same keys and values."""
    assert nxt._PM_TO_FBL_OVERRIDES == legacy._PM_TO_FBL_OVERRIDES


def test_fbl_224_by_pm_dict_matches() -> None:
    assert nxt._FBL_224_BY_PM == legacy._FBL_224_BY_PM


def test_fbl_192_by_pm_dict_matches() -> None:
    assert nxt._FBL_192_BY_PM == legacy._FBL_192_BY_PM


def test_pm_sub_to_fbl_dict_matches() -> None:
    assert nxt._PM_SUB_TO_FBL == legacy._PM_SUB_TO_FBL
