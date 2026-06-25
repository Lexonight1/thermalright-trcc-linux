"""Intel ADL/RPL MCHBAR timing decoder — pure, fixture-driven (no root)."""
from __future__ import annotations

from trcc.adapters.system._imc_timings import decode_adl

# Real MCHBAR registers captured from the dev box (Alder Lake, DDR5-4800 JEDEC).
_TC_PRE = 0x0141307500422028
_ODT = 0x0000008026280000
_REFRESH = 0x05FC1249
_BIOS_DDR = 0x79B81118


def test_decode_adl_matches_hardware() -> None:
    t = decode_adl(_TC_PRE, _ODT, _REFRESH, _BIOS_DDR)
    assert t is not None
    assert t.mts == 4800
    assert (t.tcas, t.tcwl, t.trcd, t.trp, t.tras, t.trc, t.trfc) == (
        40, 38, 40, 40, 76, 116, 383,
    )


def test_decode_adl_trc_is_tras_plus_trp() -> None:
    t = decode_adl(_TC_PRE, _ODT, _REFRESH, _BIOS_DDR)
    assert t is not None
    assert t.trc == t.tras + t.trp


def test_decode_adl_rejects_implausible_zero_registers() -> None:
    # All-zero → mts 0, tcas 0 → outside the plausibility bounds → None.
    assert decode_adl(0, 0, 0, 0) is None


def test_decode_adl_rejects_implausible_mts() -> None:
    # bios_ddr mult that yields an absurd data rate is rejected.
    assert decode_adl(_TC_PRE, _ODT, _REFRESH, 0x000000FF) is None  # 255*200=51000
