"""DDR5 SPD block-0 decoder — pure, fixture-driven (no sysfs, no root)."""
from __future__ import annotations

from pathlib import Path

from trcc.adapters.system import spd

# Real 128-byte DDR5 block 0 captured from a spd5118 SPD hub (DDR5-4800 kit).
_DDR5_BLOCK0 = bytes.fromhex(
    "3010120204002062000000006000000000000000a001f203720d000000001a41"
    "1a411a41007d1abe30752701a000820000000000000000000000000000000000"
    "0000000000881308881308204e20102710153420102710c409044c1d0c000000"
    "00000000000000000000000000000000000000000000000000000000000000"
    "00"
)


def test_block0_fixture_is_128_bytes() -> None:
    assert len(_DDR5_BLOCK0) == 128


def test_decode_ddr5_speed() -> None:
    t = spd.decode_block0(_DDR5_BLOCK0)
    assert t is not None
    assert t.dram_type == "DDR5"
    assert t.mhz == 2404
    assert t.mts == 4808


def test_decode_ddr5_primary_timings() -> None:
    """Round-to-nearest yields the JEDEC base profile (CL40, not CL41)."""
    t = spd.decode_block0(_DDR5_BLOCK0)
    assert t is not None
    assert (t.tcas, t.trcd, t.trp, t.tras, t.trc, t.trfc) == (40, 40, 40, 77, 117, 709)


def test_decode_non_ddr5_returns_none() -> None:
    raw = bytearray(_DDR5_BLOCK0)
    raw[2] = 0x0C   # DDR4
    assert spd.decode_block0(bytes(raw)) is None


def test_decode_short_input_returns_none() -> None:
    assert spd.decode_block0(_DDR5_BLOCK0[:64]) is None


def test_decode_zero_tck_returns_none() -> None:
    raw = bytearray(_DDR5_BLOCK0)
    raw[20] = raw[21] = 0x00
    assert spd.decode_block0(bytes(raw)) is None


def test_read_spd_block0_caps_at_128_bytes(tmp_path: Path) -> None:
    """A full read ENXIOs on real SPD hubs — we must read exactly 128 bytes."""
    eeprom = tmp_path / "eeprom"
    eeprom.write_bytes(_DDR5_BLOCK0 + b"\x00" * 896)   # 1024 total

    raw = spd.read_spd_block0(eeprom)

    assert raw is not None
    assert len(raw) == 128
    assert raw == _DDR5_BLOCK0


def test_read_spd_block0_missing_file_returns_none(tmp_path: Path) -> None:
    assert spd.read_spd_block0(tmp_path / "absent") is None


def test_read_spd_block0_short_file_returns_none(tmp_path: Path) -> None:
    eeprom = tmp_path / "eeprom"
    eeprom.write_bytes(b"\x30\x10\x12")
    assert spd.read_spd_block0(eeprom) is None
