"""PawnioCpu end to end on Linux: the real PawnIoModule over a fake driver
that behaves as PawnIO does -- a module refuses the other vendor at load
(ERROR_NOT_SUPPORTED) and answers its ioctls from a table of registers.
The Intel registers are the maintainer's i9-12900K, read 2026-10-16."""
from __future__ import annotations

import logging
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

import pytest

from trcc.adapters.sensors._pawnio import (
    IOCTL_EXECUTE,
    IOCTL_LOAD,
    PawnIoError,
    PawnIoModule,
    _DeviceIoPort,
)
from trcc.adapters.sensors._pawnio_cpu import PawnioCpu

I9_12900K = {("ioctl_read_msr", 0x1A2): 0x0064_1400,
             ("ioctl_read_msr", 0x1B1): 0x882F_0000,     # 53 degC
             ("ioctl_read_msr", 0x606): 0x000A_0E03,     # 1/16384 J
             ("ioctl_read_msr", 0x611): 0x8CA4_76CD}
RYZEN = {("ioctl_read_smn", 0x59800): 440 << 21,         # Tctl 55 degC
         ("ioctl_read_msr", 0xC0010299): 0x000A_1003,    # 1/65536 J
         ("ioctl_read_msr", 0xC001029B): 1_000_000}


class _FakePawnIo(_DeviceIoPort):
    """PawnIO for one CPU: *vendor_module* loads, any other is refused."""

    def __init__(self, vendor_module: str, registers: dict) -> None:
        self.vendor_module, self.registers = vendor_module, dict(registers)
        self.loaded: list[str] = []
        self.closed = 0
        self.fail_reads = False

    def open(self, path: str) -> int:
        return 7

    def ioctl(self, handle: int, code: int, payload: bytes,
              out_size: int) -> bytes:
        if code == IOCTL_LOAD:
            module = payload.decode()
            self.loaded.append(module)
            if module != self.vendor_module:
                raise PawnIoError(50, "this PawnIO module is not for this CPU")
            return b""
        assert code == IOCTL_EXECUTE
        if self.fail_reads:
            raise PawnIoError(5, "the PawnIO module refused that register")
        name = payload[:32].rstrip(b"\0").decode()
        (address,) = struct.unpack_from("<Q", payload, 32)
        return struct.pack("<Q", self.registers[(name, address)])

    def close(self, handle: int) -> None:
        self.closed += 1


def _cpu(device: _FakePawnIo, held: list[int] | None = None) -> PawnioCpu:
    entered = held if held is not None else []

    @contextmanager
    def bus() -> Iterator[None]:
        entered.append(1)
        yield

    return PawnioCpu(
        open_module=lambda file: PawnIoModule(file.encode(), name=file,
                                              device=device),
        pci_bus=lambda: bus)


def test_an_intel_cpu_reads_its_package_through_intel_msr() -> None:
    device = _FakePawnIo("IntelMSR.bin", I9_12900K)
    cpu = _cpu(device)
    assert (cpu.temp(), cpu.temp_label(), cpu.reason) == (53.0, "Package", "")
    assert device.loaded == ["IntelMSR.bin"]


def test_an_amd_cpu_falls_through_to_amd_family17_under_access_pci() -> None:
    """The Intel module refuses an AMD CPU at load; that is how the right
    one is found.  Tctl is read holding the PCI bus.

    MUTATION CHECK: stop at the first refused module."""
    device = _FakePawnIo("AMDFamily17.bin", RYZEN)
    held: list[int] = []
    cpu = _cpu(device, held)
    assert (cpu.temp(), cpu.temp_label()) == (55.0, "Tctl")
    assert device.loaded == ["IntelMSR.bin", "AMDFamily17.bin"]
    assert held == [1]


def test_power_needs_two_reads_then_follows_the_counter() -> None:
    device = _FakePawnIo("IntelMSR.bin", I9_12900K)
    cpu = _cpu(device)
    assert cpu.power() is None                               # primes
    device.registers[("ioctl_read_msr", 0x611)] += 16384 * 5   # 5 J later
    time.sleep(0.05)
    watts = cpu.power()
    assert watts is not None and 5.0 / 1.0 < watts < 5.0 / 0.05


@pytest.mark.parametrize(("opened", "reason"), [
    (PawnIoError(2, "PawnIO is not installed"), "PawnIO is not installed"),
    (FileNotFoundError("no such file"), "IntelMSR.bin not loaded: no such file"),
])
def test_no_pawnio_says_why_once_and_reads_nothing(
        opened: OSError, reason: str, caplog: pytest.LogCaptureFixture) -> None:
    """Missing driver or unshipped module: the chain falls through, and the
    reason is kept for trcc doctor.  Settled once -- never re-tried per tick.

    MUTATION CHECK: re-load on every read (drop the _settled latch)."""
    calls: list[str] = []

    def refuse(file: str) -> PawnIoModule:
        calls.append(file)
        raise opened

    cpu = PawnioCpu(open_module=refuse, pci_bus=lambda: nullcontext)
    with caplog.at_level(logging.WARNING):
        readings = [cpu.temp(), cpu.power(), cpu.temp(), cpu.temp_label()]
    assert readings == [None, None, None, ""]
    assert cpu.reason == reason
    assert calls == ["IntelMSR.bin"]
    assert [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING] == [
        f"PawnioCpu: no CPU temperature or power from PawnIO -- {reason}"]


def test_a_cpu_neither_module_takes_says_so() -> None:
    device = _FakePawnIo("ZhaoxinMSR.bin", {})
    cpu = _cpu(device)
    assert cpu.temp() is None
    assert cpu.reason == "no PawnIO module for this CPU (Intel, or AMD 17h-1Ah)"


def test_a_refused_read_is_no_reading_not_an_error() -> None:
    device = _FakePawnIo("IntelMSR.bin", I9_12900K)
    cpu = _cpu(device)
    assert cpu.temp() == 53.0
    device.fail_reads = True
    assert (cpu.temp(), cpu.power()) == (None, None)


def test_a_module_whose_first_reads_fail_is_closed() -> None:
    """MUTATION CHECK: drop module.close() in PawnioCpu._reader."""
    device = _FakePawnIo("IntelMSR.bin", I9_12900K)
    device.fail_reads = True
    cpu = _cpu(device)
    assert cpu.temp() is None
    assert cpu.reason == "the PawnIO module refused that register"
    assert device.closed == 1


def test_off_windows_there_is_simply_no_reading() -> None:
    """The real defaults on Linux: no module file beside the interpreter."""
    cpu = PawnioCpu()
    assert cpu.temp() is None
    assert cpu.reason.startswith("IntelMSR.bin not loaded:")


# ── trcc doctor ──────────────────────────────────────────────────────

def test_doctor_is_silent_off_windows() -> None:
    from trcc.adapters.diagnostics.health import check_cpu_sensors_windows
    assert check_cpu_sensors_windows().message == "Not applicable on this OS"


def test_doctor_reports_the_temperature_pawnio_gives(monkeypatch) -> None:
    import sys

    from trcc.adapters.diagnostics.health import check_cpu_sensors_windows
    monkeypatch.setattr(sys, "platform", "win32")
    result = check_cpu_sensors_windows(
        lambda: _cpu(_FakePawnIo("IntelMSR.bin", I9_12900K)))
    assert (result.severity, result.message) == (
        "OK", "CPU temperature through PawnIO: 53 °C (Package)")


@pytest.mark.parametrize(("refusal", "fix"), [
    (PawnIoError(2, "PawnIO is not installed"),
     "Reinstall TRCC -- its installer lays down PawnIO -- or install PawnIO "
     "from https://pawnio.eu"),
    (PawnIoError(5, "PawnIO admits only administrators -- TRCC is not "
                    "elevated"),
     "Start TRCC as administrator; it asks when it launches"),
    (FileNotFoundError("missing"),
     "Reinstall TRCC: the pawnio folder beside trcc.exe is missing"),
])
def test_doctor_says_what_fixes_a_missing_reading(
        monkeypatch, refusal: OSError, fix: str) -> None:
    import sys

    from trcc.adapters.diagnostics.health import check_cpu_sensors_windows

    def refuse(file: str) -> PawnIoModule:
        raise refusal

    monkeypatch.setattr(sys, "platform", "win32")
    result = check_cpu_sensors_windows(
        lambda: PawnioCpu(open_module=refuse, pci_bus=lambda: nullcontext))
    assert (result.severity, result.fix_hint) == ("WARN", fix)
