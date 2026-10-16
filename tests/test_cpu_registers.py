"""The CPU's own registers, decoded -- checked against the Linux kernel's
readers of the same bits (coretemp, k10temp, RAPL), not against our own
arithmetic.  Kernel references: drivers/hwmon/k10temp.c:68-191 (ZEN_*),
drivers/hwmon/coretemp.c:294,415 (TjMax, readout)."""
from __future__ import annotations

import pytest

from trcc.adapters.sensors._cpu_registers import (
    EnergyMeter,
    amd_tctl,
    energy_unit,
    intel_package_temp,
    intel_tjmax,
)


def test_tjmax_is_bits_23_to_16_whatever_the_offset_bits_hold() -> None:
    """Bits 15:8 are coretemp's undocumented ttarget offset -- a different
    number (the 'max' warning), never part of TjMax."""
    assert intel_tjmax(0x0064_0000) == 100
    assert intel_tjmax(0x0A64_0A00) == 100


@pytest.mark.parametrize(("status", "expected"), [
    (0x8823_0000, 65.0),        # valid, 35 below TjMax 100
    (0x8800_0000, 100.0),       # valid, at TjMax
    (0x0823_0000, None),        # not valid: no reading, never a guess
])
def test_the_package_temperature_is_tjmax_minus_the_readout(
        status: int, expected: float | None) -> None:
    assert intel_package_temp(status, 100) == expected


def _k10temp(regval: int) -> float:
    """k10temp's own formula (k10temp.c:188-191), in millidegrees."""
    temp = (regval >> 21) * 125
    if regval & (1 << 19):
        temp -= 49000
    return temp / 1000


@pytest.mark.parametrize("regval", [
    440 << 21,                       # 55.0 degC
    (832 << 21) | 0x80000,           # 104.0 - 49 = 55.0 degC, range-select
    (2047 << 21),                    # the field's top
    0,
])
def test_tctl_agrees_with_k10temp(regval: int) -> None:
    """MUTATION CHECK: drop the -49 for the range-select bit."""
    assert amd_tctl(regval) == _k10temp(regval)


def test_the_energy_unit_is_one_over_two_to_the_esu() -> None:
    """0xA0E03 is the typical Intel RAPL_POWER_UNIT: ESU 14 -> 61 µJ."""
    assert energy_unit(0x000A_0E03) == 1 / 16384


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_watts_need_two_samples_then_follow_the_counter() -> None:
    clock = _Clock()
    meter = EnergyMeter(1 / 16384, clock=clock)
    assert meter.sample(1_000_000) is None                  # primes
    clock.now += 2.0
    assert meter.sample(1_000_000 + 2 * 16384 * 65) == pytest.approx(65.0)


def test_the_counter_wrapping_is_counted_across() -> None:
    """MUTATION CHECK: subtract without the 32-bit mask."""
    clock = _Clock()
    meter = EnergyMeter(1.0, clock=clock)
    meter.sample(0xFFFF_FF00)
    clock.now += 1.0
    assert meter.sample(0x0000_0100) == pytest.approx(0x200)


def test_a_sample_too_soon_keeps_the_last_watts() -> None:
    clock = _Clock()
    meter = EnergyMeter(1.0, clock=clock)
    meter.sample(0)
    clock.now += 1.0
    assert meter.sample(50) == pytest.approx(50.0)
    clock.now += 0.001
    assert meter.sample(51) == pytest.approx(50.0)


def test_the_maintainers_i9_12900k_decodes_as_its_kernel_reads_it() -> None:
    """Raw MSRs read on CPU 0 of an i9-12900K, 2026-10-16, beside coretemp
    and powercap RAPL (scratchpad msr_crosscheck / msr_temp_pairs): power
    71.74 W both; package temp 53 where coretemp's formula on the same raw
    value is also 53 -- coretemp itself read 44 a moment later, and 20
    back-to-back pairs gave a gap of mean -0.65 (min -2, max +1, 7/20 equal),
    which is coretemp's 1 s cache, not an offset.  Bit 31 was set."""
    assert intel_tjmax(0x0064_1400) == 100
    assert intel_package_temp(0x882F_0000, 100) == 53.0
    assert energy_unit(0x000A_0E03) == 1 / 16384
