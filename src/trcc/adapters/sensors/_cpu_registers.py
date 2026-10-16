"""What the CPU's own registers mean -- temperature and package energy.

Pure arithmetic on raw register values: no I/O, no OS.  The reads come from
PawnIO on Windows (``_pawnio``); the meaning is the CPU's, so it is written
once here and checked against the two independent readers of the same bits:
the Linux kernel's ``coretemp`` / ``k10temp`` / RAPL drivers and
LibreHardwareMonitor (MPL-2.0, read as a reference, never copied).

Intel (SDM vol. 4):
    MSR 0x1A2 TEMPERATURE_TARGET   TjMax in bits 23:16
    MSR 0x1B1 PACKAGE_THERM_STATUS reading valid in bit 31; degrees BELOW
                                    TjMax in bits 22:16
    MSR 0x606 RAPL_POWER_UNIT      energy unit 1/2^ESU J, ESU in bits 12:8
    MSR 0x611 PKG_ENERGY_STATUS    a 32-bit counter of those units

AMD family 17h-1Ah (PPR; k10temp):
    SMN 0x00059800 THM_TCON_CUR_TMP  Tctl = bits 31:21 x 0.125 degC; with the
                                     range-select bit 19 set, 49 degC lower
    MSR 0xC0010299 RAPL_PWR_UNIT     the same ESU field as Intel
    MSR 0xC001029B PKG_ENERGY_STAT   the same 32-bit counter
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ...core.logs import per_frame

log = logging.getLogger(__name__)
#: Per-tick decoders: every poll runs them.
frame_log = per_frame(__name__)

INTEL_TEMPERATURE_TARGET = 0x1A2
INTEL_PACKAGE_THERM_STATUS = 0x1B1
INTEL_RAPL_POWER_UNIT = 0x606
INTEL_PKG_ENERGY_STATUS = 0x611
AMD_THM_TCON_CUR_TMP = 0x0005_9800          # SMN address
AMD_RAPL_PWR_UNIT = 0xC001_0299
AMD_PKG_ENERGY_STAT = 0xC001_029B

_COUNTER = 0xFFFF_FFFF                      # the energy counters are 32-bit
#: A shorter interval is mostly rounding -- LibreHardwareMonitor skips it too.
_MIN_INTERVAL_S = 0.01


def intel_tjmax(temperature_target: int) -> int:
    """TjMax in degC from MSR 0x1A2."""
    tjmax = (temperature_target >> 16) & 0xFF
    log.debug("intel_tjmax: 0x%x -> %d", temperature_target, tjmax)
    return tjmax


def intel_package_temp(package_therm_status: int, tjmax: int) -> float | None:
    """Package degC from MSR 0x1B1 -- None while the reading is not valid."""
    frame_log.debug("intel_package_temp: 0x%x tjmax=%d", package_therm_status,
                    tjmax)
    if not package_therm_status >> 31 & 1:
        return None
    return float(tjmax - (package_therm_status >> 16 & 0x7F))


def amd_tctl(thm_tcon_cur_tmp: int) -> float:
    """Tctl degC from SMN 0x59800 -- what TRCC shows on AMD on every OS
    (Linux reads k10temp's temp1, the same Tctl).  A few models run Tctl
    hotter than the die by design (k10temp's tctl_offset_table); showing the
    one Tctl everywhere keeps a CPU's number the same on Linux and Windows."""
    frame_log.debug("amd_tctl: 0x%x", thm_tcon_cur_tmp)
    tctl = (thm_tcon_cur_tmp >> 21) * 0.125
    return tctl - 49.0 if thm_tcon_cur_tmp & 0x80000 else tctl


def energy_unit(power_unit: int) -> float:
    """Joules per count, from RAPL_POWER_UNIT (Intel 0x606, AMD 0xC0010299)."""
    unit = 1.0 / (1 << (power_unit >> 8 & 0x1F))
    log.debug("energy_unit: 0x%x -> %g J", power_unit, unit)
    return unit


class EnergyMeter:
    """Package watts from a 32-bit energy counter read over time.

    Power is energy over an interval, so the first sample only primes it.
    The counter wraps -- at 61 µJ a count a 300 W package wraps in about
    15 minutes -- and modular subtraction counts across one wrap; a poll
    interval is seconds, far below the next.
    """

    def __init__(self, unit_joules: float, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        log.debug("EnergyMeter.__init__: %g J/count", unit_joules)
        self._unit = unit_joules
        self._clock = clock
        self._last: tuple[int, float] | None = None
        self._watts: float | None = None

    def sample(self, counter: int) -> float | None:
        """Take a counter reading; the package watts since the last one."""
        now = self._clock()
        counter &= _COUNTER
        frame_log.debug("EnergyMeter.sample: %d at %.3f", counter, now)
        if self._last is not None:
            last_counter, last_time = self._last
            interval = now - last_time
            if interval < _MIN_INTERVAL_S:
                return self._watts
            self._watts = ((counter - last_counter) & _COUNTER) * self._unit \
                / interval
        self._last = (counter, now)
        return self._watts
