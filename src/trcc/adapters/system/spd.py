"""DDR5 SPD (Serial Presence Detect) block-0 decoder — pure, no root.

The DDR5 SPD-hub EEPROM is exposed by the kernel ``spd5118`` driver at
``/sys/class/hwmon/hwmonN/device/eeprom`` (equivalently
``/sys/bus/i2c/devices/<bus>-00<addr>/eeprom``).  Reading the FULL 1024-byte
region fails with ``ENXIO`` on many boards (the SPD hub paginates), but the
first 128 bytes — JEDEC "block 0", which holds every base timing — read
reliably and **without root** (the sysfs attribute is world-readable).

This module reads only those 128 bytes and decodes the JEDEC base-profile
timings (JESD400-5).  The values are the SPD-programmed JEDEC profile, NOT the
live XMP/EXPO timings the memory controller may actually run — Linux has no
HWiNFO-equivalent live IMC read, so the SPD profile is the best rootless source.

Byte map (DDR5, JESD400-5; all 16-bit little-endian):

    2       DRAM type (0x12 = DDR5)
    20-21   tCKAVGmin       (picoseconds)  → clock / data rate
    30-31   tAAmin (tCAS)   (picoseconds)
    32-33   tRCDmin         (picoseconds)
    34-35   tRPmin          (picoseconds)
    36-37   tRASmin         (picoseconds)
    38-39   tRCmin          (picoseconds)
    42-43   tRFC1min        (NANOSECONDS — note the unit differs from the above)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_BLOCK0_LEN = 128
_DDR5_TYPE = 0x12

# Glob roots for the spd5118 SPD-hub EEPROM (both views point at one inode).
_I2C_ROOT = Path("/sys/bus/i2c/devices")
_HWMON_ROOT = Path("/sys/class/hwmon")


@dataclass(frozen=True)
class SpdTimings:
    """Decoded DDR5 base-profile speed + primary timings (cycle counts)."""

    dram_type: str
    mhz: int          # channel clock (MHz)
    mts: int          # effective data rate (MT/s)
    tcas: int
    trcd: int
    trp: int
    tras: int
    trc: int
    trfc: int


def discover_spd_eeprom_paths() -> list[Path]:
    """Existing SPD-hub ``eeprom`` files, de-duplicated by resolved target.

    Matched DIMMs share identical timings, so callers may decode ``[0]``.
    """
    log.info("discover_spd_eeprom_paths: scanning")
    candidates: list[Path] = []
    candidates.extend(_I2C_ROOT.glob("*-005[0-9a-f]/eeprom"))
    candidates.extend(_HWMON_ROOT.glob("hwmon*/device/eeprom"))
    seen: set[Path] = set()
    paths: list[Path] = []
    for c in candidates:
        if not c.is_file():
            continue
        try:
            target = c.resolve()
        except OSError:
            target = c
        if target in seen:
            continue
        seen.add(target)
        paths.append(c)
    log.info("discover_spd_eeprom_paths: %d eeprom(s)", len(paths))
    return paths


def read_spd_block0(path: Path) -> bytes | None:
    """Read EXACTLY the first 128 bytes of an SPD EEPROM, or ``None``.

    Never reads the whole file — a full read triggers ``ENXIO`` on paginated
    SPD hubs.  Returns ``None`` on any I/O error or a short read.
    """
    try:
        with path.open("rb", buffering=0) as f:
            f.seek(0)
            raw = f.read(_BLOCK0_LEN)
    except OSError as e:
        log.debug("read_spd_block0: %s unreadable (%s)", path, type(e).__name__)
        return None
    if len(raw) < _BLOCK0_LEN:
        log.debug("read_spd_block0: %s short read (%d bytes)", path, len(raw))
        return None
    return raw


def _u16(raw: bytes, off: int) -> int:
    """16-bit little-endian value at ``off``."""
    return raw[off] | (raw[off + 1] << 8)


def decode_block0(raw: bytes) -> SpdTimings | None:
    """Decode DDR5 block-0 timings, or ``None`` for non-DDR5 / short input.

    Timings are picosecond values divided by the cycle time and rounded to the
    nearest whole cycle — round-to-nearest is robust against the ±1 ps
    quantisation of the stored ``tCKAVGmin`` (a plain ``ceil`` would report
    CL41 where the JEDEC profile is CL40).
    """
    if len(raw) < _BLOCK0_LEN:
        log.debug("decode_block0: short input (%d bytes)", len(raw))
        return None
    if raw[2] != _DDR5_TYPE:
        # DDR4 (ee1004) uses a different byte map — not decoded yet.
        log.debug("decode_block0: dram type 0x%02x not DDR5 — skipped", raw[2])
        return None

    tck = _u16(raw, 20)
    if tck <= 0:
        log.warning("decode_block0: invalid tCKAVGmin=%d", tck)
        return None

    def cycles_from_ps(off: int) -> int:
        return round(_u16(raw, off) / tck)

    timings = SpdTimings(
        dram_type="DDR5",
        mhz=round(1_000_000 / tck),
        mts=round(2_000_000 / tck),
        tcas=cycles_from_ps(30),
        trcd=cycles_from_ps(32),
        trp=cycles_from_ps(34),
        tras=cycles_from_ps(36),
        trc=cycles_from_ps(38),
        # tRFC1 (byte 42) is stored in NANOSECONDS, unlike the ps fields above.
        trfc=round(_u16(raw, 42) * 1000 / tck),
    )
    log.info("decode_block0: DDR5 %d MT/s  %d-%d-%d-%d tRC=%d tRFC=%d",
             timings.mts, timings.tcas, timings.trcd, timings.trp,
             timings.tras, timings.trc, timings.trfc)
    return timings


def read_spd_timings() -> SpdTimings | None:
    """Decode the first discoverable DDR5 SPD EEPROM, or ``None``."""
    for path in discover_spd_eeprom_paths():
        if (raw := read_spd_block0(path)) is not None:
            if (decoded := decode_block0(raw)) is not None:
                return decoded
    log.info("read_spd_timings: no decodable DDR5 SPD found")
    return None
