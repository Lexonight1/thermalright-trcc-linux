"""CPU package temperature and power on Windows, read from the CPU itself.

Windows has no API for either, so this goes below it: PawnIO (``_pawnio``)
runs a signed module that reads the CPU's registers, and ``_cpu_registers``
says what they mean.  Two modules, one per vendor, shipped beside
``trcc.exe`` in ``pawnio/`` (PawnIO.Modules, LGPL-2.1, signed by namazso):

    IntelMSR.bin     MSR 0x1A2 TjMax, 0x1B1 package temp, 0x606/0x611 energy
    AMDFamily17.bin  SMN 0x59800 Tctl, MSR 0xC0010299/0xC001029B energy

Each module's ``main()`` refuses another vendor when it is loaded, so the
source simply tries Intel, then AMD.  Every register read is package-wide,
so any core answers the same on a single-socket machine; no thread is
pinned (multi-socket servers are out of scope).

Settled once, on the first read: when PawnIO is missing, TRCC is not
elevated or the modules are not shipped, the reason is logged once and kept
in :attr:`PawnioCpu.reason` for ``trcc doctor``, and every read answers
None so the chain falls through.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from ...core.logs import per_frame, recurring_failure
from ...core.ports import CpuSource
from ._cpu_registers import (
    AMD_PKG_ENERGY_STAT,
    AMD_RAPL_PWR_UNIT,
    AMD_THM_TCON_CUR_TMP,
    INTEL_PACKAGE_THERM_STATUS,
    INTEL_PKG_ENERGY_STATUS,
    INTEL_RAPL_POWER_UNIT,
    INTEL_TEMPERATURE_TARGET,
    EnergyMeter,
    amd_tctl,
    energy_unit,
    intel_package_temp,
    intel_tjmax,
)
from ._pawnio import PawnIoError, PawnIoModule, PciBusLock

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The module files, beside the frozen ``trcc.exe`` (windows.yml puts them).
MODULE_DIR_NAME = "pawnio"
#: Every module file the Windows build ships there, from PawnIO.Modules 0.2.11:
#: the CPU readers, and LpcIO for the motherboard's Super I/O chip.
#: ``tests/test_pawnio.py`` holds windows.yml and build_windows.ps1 to it.
BUNDLED_MODULES = ("IntelMSR.bin", "AMDFamily17.bin", "LpcIO.bin")
_NOT_FOR_THIS_CPU = 50              # ERROR_NOT_SUPPORTED from a module's main()


def module_dir() -> Path:
    """Where the signed PawnIO modules ship -- ``<trcc.exe dir>/pawnio``."""
    path = Path(sys.executable).parent / MODULE_DIR_NAME
    log.debug("module_dir: %s", path)
    return path


def _open_bundled(file_name: str) -> PawnIoModule:
    """Load the bundled module *file_name*; ``OSError`` when it is not shipped."""
    path = module_dir() / file_name
    log.info("_open_bundled: %s", path)
    return PawnIoModule(path.read_bytes(), name=path.stem)


def _read_msr(module: PawnIoModule, msr: int) -> int:
    """One MSR through the module's ``ioctl_read_msr`` (1 in, 1 out)."""
    frame_log.debug("_read_msr: 0x%x", msr)
    return module.execute("ioctl_read_msr", [msr], 1)[0]


class _IntelPackage:
    """An Intel package: TjMax read once, temperature and energy per tick."""

    MODULE = "IntelMSR.bin"
    LABEL = "Package"

    def __init__(self, module: PawnIoModule) -> None:
        log.debug("_IntelPackage.__init__")
        self._module = module
        self._tjmax = intel_tjmax(_read_msr(module, INTEL_TEMPERATURE_TARGET))
        log.info("_IntelPackage: TjMax %d degC", self._tjmax)
        if not self._tjmax:
            # coretemp treats 0 as unreadable too (coretemp.c:294).  Taken as
            # a TjMax it would read -N degC -- and a CPU that answers 0 here
            # answers 0 for energy too, which would read as 0 W.  Seen on a
            # QEMU vCPU, which emulates none of these registers.
            raise PawnIoError(0, "the CPU reports no TjMax (MSR 0x1A2 reads "
                                 "0) -- a virtual machine does")
        self._energy = EnergyMeter(energy_unit(
            _read_msr(module, INTEL_RAPL_POWER_UNIT)))

    def temp(self) -> float | None:
        frame_log.debug("_IntelPackage.temp")
        return intel_package_temp(
            _read_msr(self._module, INTEL_PACKAGE_THERM_STATUS), self._tjmax)

    def power(self) -> float | None:
        frame_log.debug("_IntelPackage.power")
        return self._energy.sample(
            _read_msr(self._module, INTEL_PKG_ENERGY_STATUS))


class _AmdPackage:
    """An AMD family 17h-1Ah package: Tctl through SMN, energy per tick."""

    MODULE = "AMDFamily17.bin"
    LABEL = "Tctl"

    def __init__(self, module: PawnIoModule,
                 bus: Callable[[], AbstractContextManager[None]]) -> None:
        log.debug("_AmdPackage.__init__")
        self._module = module
        self._bus = bus
        self._energy = EnergyMeter(energy_unit(
            _read_msr(module, AMD_RAPL_PWR_UNIT)))

    def temp(self) -> float | None:
        frame_log.debug("_AmdPackage.temp")
        with self._bus():
            raw = self._module.execute("ioctl_read_smn",
                                       [AMD_THM_TCON_CUR_TMP], 1)[0]
        return amd_tctl(raw)

    def power(self) -> float | None:
        frame_log.debug("_AmdPackage.power")
        return self._energy.sample(_read_msr(self._module, AMD_PKG_ENERGY_STAT))


def _windows_pci_bus() -> Callable[[], AbstractContextManager[None]]:
    """``Access_PCI`` held for each SMN read -- or nothing to hold off
    Windows, where this source never loads a module anyway."""
    log.debug("_windows_pci_bus: platform=%s", sys.platform)
    try:
        return PciBusLock().held
    except OSError as e:
        log.info("_windows_pci_bus: no Access_PCI -- %s", e)
        return nullcontext


class PawnioCpu(CpuSource):
    """Package temperature and power through PawnIO, Intel or AMD."""

    def __init__(self, *,
                 open_module: Callable[[str], PawnIoModule] = _open_bundled,
                 pci_bus: Callable[[], Callable[[], AbstractContextManager[None]]]
                 = _windows_pci_bus) -> None:
        log.debug("PawnioCpu.__init__")
        self._open = open_module
        self._pci_bus = pci_bus
        self._package: _IntelPackage | _AmdPackage | None = None
        self._settled = False
        #: Why there is no reading -- for ``trcc doctor``; empty when there is.
        self.reason = ""

    def _ready(self) -> _IntelPackage | _AmdPackage | None:
        """The package reader, settled on the first call."""
        if not self._settled:
            self._settled = True
            self._package, self.reason = self._load()
            if self._package is None:
                log.warning("PawnioCpu: no CPU temperature or power from "
                            "PawnIO -- %s", self.reason)
        return self._package

    def _load(self) -> tuple[_IntelPackage | _AmdPackage | None, str]:
        """Try each vendor's module; the reader, or why there is none."""
        log.info("PawnioCpu._load: trying %s, %s", _IntelPackage.MODULE,
                 _AmdPackage.MODULE)
        for reader in (_IntelPackage, _AmdPackage):
            try:
                module = self._open(reader.MODULE)
                package = self._reader(reader, module)
            except PawnIoError as e:
                if e.code == _NOT_FOR_THIS_CPU:
                    log.info("PawnioCpu._load: %s is not for this CPU",
                             reader.MODULE)
                    continue
                return None, str(e)
            except OSError as e:
                return None, f"{reader.MODULE} not loaded: {e}"
            log.info("PawnioCpu._load: reading through %s", reader.MODULE)
            return package, ""
        return None, "no PawnIO module for this CPU (Intel, or AMD 17h-1Ah)"

    def _reader(self, reader: type[_IntelPackage] | type[_AmdPackage],
                module: PawnIoModule) -> _IntelPackage | _AmdPackage:
        """*reader* on the loaded *module* -- closing the module when its
        first reads fail, so a refused setup never leaks the handle."""
        log.debug("PawnioCpu._reader: %s", reader.__name__)
        try:
            return (_IntelPackage(module) if reader is _IntelPackage
                    else _AmdPackage(module, self._pci_bus()))
        except PawnIoError:
            module.close()
            raise

    @property
    def name(self) -> str:
        frame_log.debug("PawnioCpu.name")
        return "PawnIO (CPU)"

    def temp_label(self) -> str:
        """``Package`` on Intel, ``Tctl`` on AMD -- as Linux labels them."""
        package = self._ready()
        frame_log.debug("PawnioCpu.temp_label: %s", type(package).__name__)
        return package.LABEL if package is not None else ""

    def temp(self) -> float | None:
        frame_log.debug("PawnioCpu.temp")
        package = self._ready()
        if package is None:
            return None
        try:
            return package.temp()
        except PawnIoError:
            recurring_failure(log, "PawnioCpu.temp: read failed")
            return None

    def power(self) -> float | None:
        frame_log.debug("PawnioCpu.power")
        package = self._ready()
        if package is None:
            return None
        try:
            return package.power()
        except PawnIoError:
            recurring_failure(log, "PawnioCpu.power: read failed")
            return None
