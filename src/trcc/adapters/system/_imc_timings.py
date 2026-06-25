"""Decode Intel (Alder/Raptor Lake) MCHBAR memory-timing registers — pure.

The privileged ``trcc-imc`` helper reads four raw MCHBAR registers as root and
prints them; this module turns those integers into cycle-count timings.  No
I/O, no root, no OS calls — just bit math — so it is unit-testable with the
register values captured from real hardware.

Register map (channel 0; CoreFreq ``x86_64/intel_reg.h``):

    BIOS_DDR @0x5E00  mult[7:0]                       → MT/s = mult * 100 * 2
    TC_PRE   @0xE000  tRP[7:0]  tRAS[50:42] tRCD[58:51]
    ODT      @0xE070  tCL[22:16] tCWL[31:24]
    REFRESH  @0xE43C  tREFI[17:0] tRFC[30:18]

``tRC`` has no register — it is the standard ``tRAS + tRP``.  The ``tRFC`` here
is the controller's live tRFC2 (2x fine-grained refresh), distinct from the
SPD tRFC1; callers decide which to display.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImcTimings:
    """Live primary timings (cycle counts) read from the IMC."""

    mts: int          # effective data rate (MT/s)
    tcas: int
    tcwl: int
    trcd: int
    trp: int
    tras: int
    trc: int
    trfc: int         # tRFC2 (live refresh) — NOT the SPD tRFC1


def _bits(value: int, hi: int, lo: int) -> int:
    return (value >> lo) & ((1 << (hi - lo + 1)) - 1)


def decode_adl(tc_pre: int, odt: int, refresh: int, bios_ddr: int) -> ImcTimings | None:
    """Decode the four ADL/RPL MCHBAR registers, or ``None`` if implausible.

    The range checks are the caller's defence-in-depth bounds check: a misread
    register (wrong CPU map, garbage MCHBAR base) yields absurd values, which we
    reject rather than show.
    """
    timings = ImcTimings(
        mts=(bios_ddr & 0xFF) * 100 * 2,
        tcas=_bits(odt, 22, 16),
        tcwl=_bits(odt, 31, 24),
        trcd=_bits(tc_pre, 58, 51),
        trp=_bits(tc_pre, 7, 0),
        tras=_bits(tc_pre, 50, 42),
        trc=_bits(tc_pre, 50, 42) + _bits(tc_pre, 7, 0),
        trfc=_bits(refresh, 30, 18),
    )
    if not (3200 <= timings.mts <= 8400
            and 0 < timings.tcas < 128 and 0 < timings.trcd < 128
            and 0 < timings.trp < 128 and 0 < timings.tras < 256
            and 0 < timings.trfc < 4096):
        log.warning("decode_adl: implausible timings %s — rejected", timings)
        return None
    log.info("decode_adl: %d MT/s  %d-%d-%d-%d tRC=%d tRFC2=%d",
             timings.mts, timings.tcas, timings.trcd, timings.trp,
             timings.tras, timings.trc, timings.trfc)
    return timings
