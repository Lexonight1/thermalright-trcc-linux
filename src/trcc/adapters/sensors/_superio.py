"""Motherboard fans and board temperatures on Windows, from the Super I/O chip.

Most ASUS and ASRock boards, and many MSI ones, carry a Nuvoton NCT6791D-NCT6799D
"Super I/O" chip; most MSI boards a Nuvoton NCT6683D/6686D/6687D, whose monitor
is an embedded controller (``_nct668x``); Gigabyte boards carry ITE IT86xx
chips, often with a second IT879x beside them.  The chip counts every fan header and reads the board's
thermal inputs.  Linux's ``nct6775`` and ``it87`` drivers read them; on Windows
nothing did unless LibreHardwareMonitor or HWiNFO ran beside TRCC (#319).  This
reads them through PawnIO's signed ``LpcIO`` module -- as the SYSTEM service for
a normal App (``superio.sample``), or directly when the App is elevated.

**Read-only, and the sequence is fixed here, not by any caller.**  The writes
are the ones every reader of these chips needs, and none of them changes a
setting: config-mode enter and exit, the logical-device select, and the
hardware monitor's index register (and, on Nuvoton, its bank register).
PawnIO's own ``find_bars`` also steps the device select through every device,
which is how it learns which ports may be read.

**Before any key, the chip id is read as the port stands.**  A chip the BIOS
left in config mode answers then, and gets no key and no exit: on Gigabyte
boards the second ITE chip (IT8790E/IT8792E/IT87952E) is left there, and
"entering and/or exiting configuration mode is what causes the crash" (Linux
``it87.c``).  Such a chip is never taken out of config mode either, keyed or not.

Where the chip is switched off, its BIOS locked the monitor, another program
left an ITE monitor on a bank other than 0, or the readings may belong to
firmware TRCC cannot reach, this reads nothing there and says why -- it never
writes the setting that would make it readable.

Registers from Linux ``drivers/hwmon/nct6775-core.c`` / ``nct6775-platform.c``,
``drivers/hwmon/it87.c`` and the out-of-tree ``frankcrawford/it87`` (GPL-2.0+;
facts, no code), checked against LibreHardwareMonitor's ``Nct677X.cs`` /
``IT87XX.cs`` / ``LpcIO.cs`` (MPL-2.0).  The mutex is the one
LibreHardwareMonitor, HWiNFO and FanControl hold around the ISA bus.
"""
from __future__ import annotations

import enum
import itertools
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, ClassVar, TypeGuard

from ...core.logs import per_frame
from ...core.ports import BoardTempSource, FanSource
from ._nct668x import CHIPS as NCT668X_CHIPS
from ._nct668x import ID_MASK as NCT668X_ID_MASK
from ._nct668x import Nct668xMonitor

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: ``ioctl_select_slot`` argument -> the chip's config index port.
_SLOTS = {0: 0x2E, 1: 0x4E}
_DEVICE_ID, _REVISION, _DEVICE_SELECT, _ACTIVATE, _BASE = 0x20, 0x22, 0x07, 0x30, 0x60
#: What the id registers read where no chip answers on the port.
_NO_CHIP = 0xFFFF
#: An unconnected input reads far outside this (LibreHardwareMonitor drops <= 0
#: and > 125 for these registers too).  NCT668x inputs read in half degrees.
_PLAUSIBLE_MIN_C, _PLAUSIBLE_MAX_C = 1, 125
#: The service answers a client no faster than this; a reading moves slower.
_MIN_INTERVAL_S = 0.5
_SAMPLE_TTL_S = 0.9


class Lpc:
    """One opened ``LpcIO`` module, on one slot: every call a chip family makes.

    PawnIO keeps the selected slot and the ports ``find_bars`` admitted per
    module, and selecting a slot forgets them -- so each slot gets a module of
    its own, as LibreHardwareMonitor's one ``LpcPort`` per port does.
    """

    def __init__(self, module: Any, slot: int) -> None:
        log.debug("Lpc: slot %d on %s", slot, getattr(module, "name", "LpcIO"))
        self._module, self.slot, self.port = module, slot, _SLOTS[slot]

    def call(self, function: str, *inputs: int, outputs: int = 0) -> tuple[int, ...]:
        frame_log.debug("Lpc.call: %s %s", function, [hex(i) for i in inputs])
        return self._module.execute(function, list(inputs), outputs)

    def select_slot(self) -> None:
        log.debug("Lpc.select_slot: %d (0x%02x)", self.slot, self.port)
        self.call("ioctl_select_slot", self.slot)

    def outb(self, port: int, value: int) -> None:
        frame_log.debug("Lpc.outb: 0x%04x <- 0x%02x", port, value)
        self.call("ioctl_pio_outb", port, value)

    def inb(self, port: int) -> int:
        value = self.call("ioctl_pio_inb", port, outputs=1)[0] & 0xFF
        frame_log.debug("Lpc.inb: 0x%04x -> 0x%02x", port, value)
        return value

    def config(self, register: int) -> int:
        """One config-space register (index port, then data port)."""
        value = self.call("ioctl_superio_inb", register, outputs=1)[0] & 0xFF
        frame_log.debug("Lpc.config: 0x%02x -> 0x%02x", register, value)
        return value

    def config_word(self, register: int) -> int:
        word = (self.config(register) << 8) | self.config(register + 1)
        frame_log.debug("Lpc.config_word: 0x%02x -> 0x%04x", register, word)
        return word

    def chip_id(self) -> int:
        chip_id = self.config_word(_DEVICE_ID)
        log.debug("Lpc.chip_id: 0x%02x -> 0x%04x", self.port, chip_id)
        return chip_id

    def select_device(self, device: int) -> None:
        log.debug("Lpc.select_device: 0x%02x", device)
        self.call("ioctl_superio_outb", _DEVICE_SELECT, device)

    def find_bars(self) -> None:
        """PawnIO walks every device's base registers and admits them; it
        needs a valid chip id in config space, so call it in config mode."""
        log.debug("Lpc.find_bars: slot %d", self.slot)
        self.call("ioctl_find_bars")

    def close(self) -> None:
        log.debug("Lpc.close: slot %d", self.slot)
        self._module.close()


@dataclass(frozen=True)
class FoundChip:
    """A chip whose monitor TRCC reads: where it is, and which family reads it."""

    lpc: Lpc
    chip_id: int
    name: str
    base: int
    family: SuperIoChip
    #: What the family keeps between samples for this chip (the NCT668x EC's
    #: layout); None where it keeps nothing.
    state: Any = None


class SuperIoChip(ABC):
    """One vendor's chip family: its config-mode key, its chip ids, and how its
    monitor is found and read.  :class:`SuperIoReader` owns the order of
    everything; a family only knows its own chips.

    :meth:`identify` runs in config mode; :meth:`measure` runs after it on the
    monitor's own ports and answers one chip's entry of a sample.
    """

    VENDOR: ClassVar[str]
    #: A second chip of this family may sit at 0x4E beside one at 0x2E, so
    #: finding one at 0x2E does not end the search.
    PAIRED: ClassVar[bool] = False

    @abstractmethod
    def name(self, chip_id: int) -> str | None:
        """The chip's name, or None where *chip_id* is not this family's."""

    @abstractmethod
    def enter(self, lpc: Lpc) -> None:
        """Write this family's config-mode key on *lpc*'s port."""

    @abstractmethod
    def leave(self, lpc: Lpc, chip_id: int) -> None:
        """Undo :meth:`enter` -- or not, where this chip must stay entered."""

    @abstractmethod
    def identify(self, lpc: Lpc, chip_id: int) -> FoundChip | str:
        """In config mode: the readable chip, or why it cannot be read."""

    @abstractmethod
    def measure(self, found: FoundChip) -> dict[str, Any]:
        """``{"fans": [...], "temps": {...}}`` now, or ``{"reason": ...}``
        where this tick cannot be read."""


# ── Nuvoton NCT6791D-NCT6799D ─────────────────────────────────────────────────

_NUVOTON_ENTER, _NUVOTON_EXIT = 0x87, 0xAA
_HWM_DEVICE = 0x0B                      # NCT6775_LD_HWM
_IO_SPACE_LOCK, _LOCKED = 0x28, 0x10    # NCT6791_REG_HM_IO_SPACE_LOCK_ENABLE, bit 4
_ADDRESS, _DATA = 5, 6                  # IOREGION_OFFSET + ADDR / DATA_REG_OFFSET
_BANK_SELECT = 0x4E                     # NCT6775_REG_BANK
_ID_MASK = 0xFFF8                       # SIO_ID_MASK
#: nct6775-platform.c ``SIO_NCT67xx_ID``.
CHIPS = {0xC800: "NCT6791D", 0xC910: "NCT6792D", 0xD120: "NCT6793D", 0xD350: "NCT6795D",
         0xD420: "NCT6796D", 0xD450: "NCT6797D", 0xD428: "NCT6798D", 0xD800: "NCT6799D"}
#: NCT6779_REG_FAN: fans 1-7, 16-bit RPM, high byte first.
FAN_REGISTERS = (0x4C0, 0x4C2, 0x4C4, 0x4C6, 0x4C8, 0x4CA, 0x4CE)
#: NCT6779_REG_TEMP_ALTERNATE, sources 1-6: the board inputs, whole °C.
TEMP_REGISTERS = {"SYSTIN": 0x490, "CPUTIN": 0x491, "AUXTIN0": 0x492,
                  "AUXTIN1": 0x493, "AUXTIN2": 0x494, "AUXTIN3": 0x495}


class Nuvoton(SuperIoChip):
    """NCT6791D-NCT6799D: key ``0x87 0x87``, exit ``0xAA``; the monitor is
    device 0x0B, banked behind base+5 / base+6.  Fan registers hold the RPM
    itself (``fan_from_reg_rpm``); inputs are one signed byte of whole °C.
    Where the BIOS locked the monitor (config 0x28 bit 4) this reads nothing --
    LibreHardwareMonitor and Linux CLEAR that lock; TRCC does not write it."""

    VENDOR = "Nuvoton"

    def name(self, chip_id: int) -> str | None:
        name = CHIPS.get(chip_id & _ID_MASK)
        log.debug("Nuvoton.name: 0x%04x -> %s", chip_id, name)
        return name

    def enter(self, lpc: Lpc) -> None:
        log.debug("Nuvoton.enter: 0x%02x", lpc.port)
        lpc.outb(lpc.port, _NUVOTON_ENTER)
        lpc.outb(lpc.port, _NUVOTON_ENTER)

    def leave(self, lpc: Lpc, chip_id: int) -> None:
        log.debug("Nuvoton.leave: 0x%02x", lpc.port)
        lpc.outb(lpc.port, _NUVOTON_EXIT)

    def identify(self, lpc: Lpc, chip_id: int) -> FoundChip | str:
        name = CHIPS[chip_id & _ID_MASK]
        # find_bars first: it walks every device select, so the monitor's is
        # chosen after it, and it is what admits the monitor's ports.
        lpc.find_bars()
        lpc.select_device(_HWM_DEVICE)
        base = lpc.config_word(_BASE) & ~0x7
        active = lpc.config(_ACTIVATE) & 0x01
        locked = lpc.config(_IO_SPACE_LOCK) & _LOCKED
        log.info("Nuvoton: %s at 0x%02x, monitor 0x%04x, active=%d locked=%d",
                 name, lpc.port, base, active, bool(locked))
        if base < 0x100 or not active:
            return f"{name}'s hardware monitor is switched off"
        if locked:
            return f"{name}'s hardware monitor is locked by the BIOS (TRCC does not clear it)"
        return FoundChip(lpc, chip_id, name, base, self)

    def _read_bank4(self, found: FoundChip, registers: list[int]) -> dict[int, int]:
        """Each register's byte; every register here is in bank 4.  Bank 0 is
        selected again afterwards."""
        lpc, base = found.lpc, found.base
        lpc.outb(base + _ADDRESS, _BANK_SELECT)
        lpc.outb(base + _DATA, 4)
        values: dict[int, int] = {}
        try:
            for register in registers:
                lpc.outb(base + _ADDRESS, register & 0xFF)
                values[register] = lpc.inb(base + _DATA)
        finally:
            lpc.outb(base + _ADDRESS, _BANK_SELECT)
            lpc.outb(base + _DATA, 0)
        frame_log.debug("Nuvoton._read_bank4: %d register(s)", len(values))
        return values

    def measure(self, found: FoundChip) -> dict[str, Any]:
        wanted = [r for fan in FAN_REGISTERS for r in (fan, fan + 1)]
        wanted += list(TEMP_REGISTERS.values())
        raw = self._read_bank4(found, wanted)
        sample = {"fans": [(raw[r] << 8) | raw[r + 1] for r in FAN_REGISTERS],
                  "temps": {label: _signed(raw[r]) for label, r in TEMP_REGISTERS.items()}}
        frame_log.debug("Nuvoton.measure: %s %s", found.name, sample)
        return sample


# ── Nuvoton NCT6683D / NCT6686D / NCT6687D (MSI) ─────────────────────────────


class Nct668x(Nuvoton):
    """NCT6683D / NCT6686D / NCT6687D: Nuvoton's config key and exit (the base
    class's), the monitor device 0x0B -- but its monitor is an embedded
    controller behind a paged window at base+4/5/6, read by
    :class:`_nct668x.Nct668xMonitor`.  Linux ``nct6683`` neither clears nor
    checks config 0x28 on these chips, and neither does this."""

    VENDOR = "Nuvoton EC"

    def name(self, chip_id: int) -> str | None:
        name = NCT668X_CHIPS.get(chip_id & NCT668X_ID_MASK)
        log.debug("Nct668x.name: 0x%04x -> %s", chip_id, name)
        return name

    def identify(self, lpc: Lpc, chip_id: int) -> FoundChip | str:
        name = NCT668X_CHIPS[chip_id & NCT668X_ID_MASK]
        lpc.find_bars()
        lpc.select_device(_HWM_DEVICE)
        base = lpc.config_word(_BASE) & ~0x7
        active = lpc.config(_ACTIVATE) & 0x01
        log.info("Nct668x: %s at 0x%02x, EC base 0x%04x, active=%d", name, lpc.port, base, active)
        if base < 0x100 or not active:
            return f"{name}'s hardware monitor is switched off"
        return FoundChip(lpc, chip_id, name, base, self, Nct668xMonitor(lpc.call, name, base))

    def measure(self, found: FoundChip) -> dict[str, Any]:
        reading = found.state.sample()
        sample = ({"reason": reading["reason"]} if reading.get("chip") is None
                  else {"fans": reading["fans"], "temps": reading["temps"]})
        frame_log.debug("Nct668x.measure: %s %s", found.name, sample)
        return sample


def _signed(byte: int) -> int:
    """A two's-complement byte as whole degrees."""
    value = byte - 256 if byte > 127 else byte
    frame_log.debug("_signed: 0x%02x -> %d", byte, value)
    return value


# ── ITE IT86xx / IT87xx ───────────────────────────────────────────────────────

#: it87.c ``__superio_enter``: the last byte is 0xAA on the 0x4E port.
_ITE_KEY = (0x87, 0x01, 0x55)
_ITE_KEY_LAST = {0x2E: 0x55, 0x4E: 0xAA}
#: it87.c ``superio_exit``: config register 0x02 bit 1.
_ITE_CONFIG_CONTROL, _ITE_EXIT = 0x02, 0x02
_ITE_EC_DEVICE = 0x04                   # PME: "The device with the fan registers in it"
_ITE_MMIO_CONFIG = 0x24                 # IT87_EC_HWM_MIO_REG (frankcrawford)
_ITE_SPECIAL_CONFIG = 0xF3              # IT87_SPECIAL_CFG_REG (SMBus bits)
_ITE_ADDRESS, _ITE_DATA = 5, 6          # IT87_EC_OFFSET + ADDR / DATA_REG_OFFSET
# Environment-controller registers (it87.c).
_IT_CONFIG = 0x00                       # IT87_REG_CONFIG
_IT_CONFIG_RESET = 0x80                 # it87_probe: "& 0x80" means no chip
_IT_BANK = 0x06                         # IT87_REG_BANK, bits 5-7 (frankcrawford)
_IT_FAN6_ALT_ENABLE = 0x0B              # IT87_REG_FAN_DIV, bit 3: fan6 on 8625/8665
_IT_FAN_16BIT = 0x0C                    # IT87_REG_FAN_16BIT
_IT_FAN_MAIN_CTRL = 0x13                # IT87_REG_FAN_MAIN_CTRL, bits 4-6: fans 1-3
_IT_TEMP = 0x29                         # IT87_REG_TEMP(nr) = 0x29 + nr
_IT_VENDOR = 0x58                       # IT87_REG_CHIPID
_IT_VENDOR_ITE = 0x90
_IT_TEMP456_ENABLE = 0x77               # IT87_REG_TEMP456_ENABLE
#: Fans 1-6, low and high byte (IT87_REG_FAN / IT87_REG_FANX).
_IT_FAN = (0x0D, 0x0E, 0x0F, 0x80, 0x82, 0x4C)
_IT_FANX = (0x18, 0x19, 0x1A, 0x81, 0x83, 0x4D)
#: IT87_REG_FAN_8665 / IT87_REG_FANX_8665: fan 6 elsewhere on 8625/8655/8665.
_IT_FAN6_ALT = (0x93, 0x94)
#: FAN16_FROM_REG: "The divider is fixed to 2 in 16-bit mode".
_TACH_CLOCK = 1_350_000
_TACH_STOPPED = 0xFFFF
#: A count this low is above 10,546 RPM -- not a fan; LibreHardwareMonitor
#: drops it too (``value > 0x3f``).
_TACH_MIN_COUNT = 0x40


class ExtraTemps(enum.Enum):
    """Where TMPIN4-6 come from: none (three inputs), each pin's mode in
    register 0x77 (``(reg >> 2n) & 3 >= 2`` is a temperature; else a
    voltage on the same register), or always (frankcrawford: 8655/8665)."""

    NONE = "none"
    PIN_SELECT = "pin-select"
    ALWAYS = "always"


@dataclass(frozen=True)
class IteModel:
    """One ITE chip, as Linux's ``it87_devices`` table describes it."""

    name: str
    fans: int = 3
    extra_temps: ExtraTemps = ExtraTemps.NONE
    #: FEAT_BANK_SEL: registers 0x10-0xAF swap with the bank in reg 0x06.
    banked: bool = False
    #: FEAT_NOCONF: never take it out of config mode.
    noconf: bool = False
    #: FEAT_FAN16_CONFIG: fans 1-3 count in 16 bits only with reg 0x0C bits 0-2.
    fan16_config: bool = False
    #: IT87_REG_FAN_8665: fan 6 at 0x93/0x94, enabled by reg 0x0B bit 3.
    alt_fan6: bool = False
    #: Gigabyte SmartFan G3 firmware keeps this chip's tachometers in H2RAM,
    #: which LpcIO cannot reach ("G3 tachometers are authoritative in H2RAM").
    fans_in_h2ram: bool = False
    #: The index register reads back what was written (LHM: not on IT8688E).
    echoes_index: bool = True


#: Every chip that Linux (mainline ``it87`` or frankcrawford's) AND
#: LibreHardwareMonitor both read through these registers.
ITE_CHIPS = {
    0x8613: IteModel("IT8613E", fans=5),
    0x8620: IteModel("IT8620E", fans=6, extra_temps=ExtraTemps.PIN_SELECT),
    0x8625: IteModel("IT8625E", fans=6, banked=True, alt_fan6=True),
    0x8628: IteModel("IT8628E", fans=6, extra_temps=ExtraTemps.PIN_SELECT),
    0x8655: IteModel("IT8655E", extra_temps=ExtraTemps.ALWAYS, banked=True),
    0x8665: IteModel("IT8665E", fans=6, extra_temps=ExtraTemps.ALWAYS, banked=True,
                     alt_fan6=True),
    0x8686: IteModel("IT8686E", fans=6, extra_temps=ExtraTemps.PIN_SELECT, banked=True),
    0x8688: IteModel("IT8688E", fans=6, extra_temps=ExtraTemps.PIN_SELECT, banked=True,
                     echoes_index=False),
    0x8689: IteModel("IT8689E", fans=6, extra_temps=ExtraTemps.PIN_SELECT, banked=True),
    0x8696: IteModel("IT8696E", fans=6, extra_temps=ExtraTemps.PIN_SELECT, banked=True),
    0x8695: IteModel("IT87952E", noconf=True, fans_in_h2ram=True),
    0x8733: IteModel("IT8792E", noconf=True, fans_in_h2ram=True),     # also IT8795E
    0x8790: IteModel("IT8790E", noconf=True),
    0x8721: IteModel("IT8721F", fans=5, fan16_config=True),
    0x8728: IteModel("IT8728F", fans=5),
    0x8771: IteModel("IT8771E"),
    0x8772: IteModel("IT8772E"),
}


class _IteMonitor:
    """The environment controller behind base+5 (index) / base+6 (data).
    Nothing here writes the data port."""

    def __init__(self, found: FoundChip, model: IteModel) -> None:
        frame_log.debug("_IteMonitor: %s at 0x%04x", found.name, found.base)
        self._lpc, self._model = found.lpc, model
        self._index, self._data = found.base + _ITE_ADDRESS, found.base + _ITE_DATA

    def read(self, register: int) -> int | None:
        """The register's byte -- or None when the index register no longer
        holds *register* afterwards, so another agent moved it mid-read
        (LibreHardwareMonitor's ``ReadByte`` check)."""
        self._lpc.outb(self._index, register)
        value = self._lpc.inb(self._data)
        if self._model.echoes_index and self._lpc.inb(self._index) != register:
            frame_log.debug("_IteMonitor.read: 0x%02x moved under the read", register)
            return None
        frame_log.debug("_IteMonitor.read: 0x%02x -> 0x%02x", register, value)
        return value


class Ite(SuperIoChip):
    """ITE IT86xx / IT87xx: key ``0x87 0x01 0x55 0x55`` (``... 0xAA`` at 0x4E),
    exit config register 0x02 = 0x02; the monitor is device 4 behind
    base+5 / base+6.  Fans count in 16 bits, RPM = 1,350,000 / (2 x count);
    inputs are one signed byte of whole °C at 0x29 + n."""

    VENDOR = "ITE"
    PAIRED = True

    def name(self, chip_id: int) -> str | None:
        model = ITE_CHIPS.get(chip_id)
        log.debug("Ite.name: 0x%04x -> %s", chip_id, model.name if model else None)
        return model.name if model else None

    def enter(self, lpc: Lpc) -> None:
        log.debug("Ite.enter: 0x%02x", lpc.port)
        for byte in (*_ITE_KEY, _ITE_KEY_LAST[lpc.port]):
            lpc.outb(lpc.port, byte)

    def leave(self, lpc: Lpc, chip_id: int) -> None:
        if chip_id == _NO_CHIP:
            # Nothing answered the key, so nothing entered: no exit to write
            # (LibreHardwareMonitor's rule; Linux writes it regardless).
            log.debug("Ite.leave: nothing entered at 0x%02x", lpc.port)
            return
        model = ITE_CHIPS.get(chip_id)
        if model is not None and model.noconf:
            log.info("Ite.leave: %s stays in config mode -- Linux never takes it "
                     "out (FEAT_NOCONF)", model.name)
            return
        log.debug("Ite.leave: 0x%02x", lpc.port)
        lpc.outb(lpc.port, _ITE_CONFIG_CONTROL)
        lpc.outb(lpc.port + 1, _ITE_EXIT)

    def identify(self, lpc: Lpc, chip_id: int) -> FoundChip | str:
        model = ITE_CHIPS[chip_id]
        lpc.find_bars()
        lpc.select_device(_ITE_EC_DEVICE)
        active = lpc.config(_ACTIVATE) & 0x01
        base = lpc.config_word(_BASE) & ~0x7
        revision = lpc.config(_REVISION) & 0x0F
        # Read only to explain a report: MMIO decode (CR24 bit 5) and the EC's
        # SMBus bits (CRF3), which Linux clears around every access -- a write
        # TRCC does not make.
        mmio = lpc.config(_ITE_MMIO_CONFIG)
        special = lpc.config(_ITE_SPECIAL_CONFIG)
        log.info("Ite: %s rev %d at 0x%02x, monitor 0x%04x, active=%d, CR24=0x%02x, "
                 "CRF3=0x%02x", model.name, revision, lpc.port, base, active, mmio, special)
        if base < 0x100 or not active:
            return f"{model.name}'s hardware monitor is switched off"
        if model.fans_in_h2ram:
            log.info("Ite: %s -- its fans are not read: on Gigabyte boards SmartFan "
                     "firmware keeps them in H2RAM, which LpcIO cannot reach", model.name)
        return FoundChip(lpc, chip_id, model.name, base, self)

    def measure(self, found: FoundChip) -> dict[str, Any]:
        model = ITE_CHIPS[found.chip_id]
        monitor = _IteMonitor(found, model)
        if model.banked:
            bank = monitor.read(_IT_BANK)
            if bank is None or bank >> 5:
                reason = (f"{model.name}'s monitor is on bank "
                          f"{'?' if bank is None else bank >> 5} -- another program "
                          "switched it; TRCC reads bank 0 only and does not switch it back")
                frame_log.debug("Ite.measure: %s", reason)
                return {"reason": reason}
        config, vendor = monitor.read(_IT_CONFIG), monitor.read(_IT_VENDOR)
        if config is None or vendor != _IT_VENDOR_ITE or config & _IT_CONFIG_RESET:
            reason = (f"{model.name}'s monitor does not answer as an ITE one "
                      f"(config={config}, vendor={vendor})")
            frame_log.debug("Ite.measure: %s", reason)
            return {"reason": reason}
        sample = {"fans": [] if model.fans_in_h2ram else self._fans(monitor, model),
                  "temps": self._temps(monitor, model)}
        frame_log.debug("Ite.measure: %s %s", model.name, sample)
        return sample

    def _fans_enabled(self, monitor: _IteMonitor, model: IteModel) -> list[bool]:
        """Which tachometers the BIOS switched on -- Linux's ``has_fan``
        (it87_init_device), read and never set."""
        main, wide = monitor.read(_IT_FAN_MAIN_CTRL), monitor.read(_IT_FAN_16BIT)
        if main is None or wide is None:
            frame_log.debug("Ite._fans_enabled: enable registers moved under the read")
            return [False] * model.fans
        on = [bool(main & (0x10 << i)) for i in range(3)]
        on += [bool(wide & 0x10), bool(wide & 0x20)]
        if model.alt_fan6:
            on.append(bool((monitor.read(_IT_FAN6_ALT_ENABLE) or 0) & 0x08))
        else:
            on.append(bool(wide & 0x04))
        if model.fan16_config:
            # Fans 1-3 count in 8 bits until reg 0x0C bits 0-2 are set -- a
            # write Linux makes and TRCC does not; such a fan is not read.
            on[:3] = [on[i] and bool(wide & (1 << i)) for i in range(3)]
        enabled = on[:model.fans]
        frame_log.debug("Ite._fans_enabled: %s (0x13=0x%02x 0x0c=0x%02x)", enabled, main, wide)
        return enabled

    def _fans(self, monitor: _IteMonitor, model: IteModel) -> list[int | None]:
        rpms: list[int | None] = []
        for i, enabled in enumerate(self._fans_enabled(monitor, model)):
            low, high = (_IT_FAN6_ALT if model.alt_fan6 and i == 5
                         else (_IT_FAN[i], _IT_FANX[i]))
            rpms.append(_rpm(monitor.read(low), monitor.read(high)) if enabled else None)
        frame_log.debug("Ite._fans: %s", rpms)
        return rpms

    def _temps(self, monitor: _IteMonitor, model: IteModel) -> dict[str, int | None]:
        inputs = [0, 1, 2]
        if model.extra_temps is ExtraTemps.ALWAYS:
            inputs += [3, 4, 5]
        elif model.extra_temps is ExtraTemps.PIN_SELECT:
            pins = monitor.read(_IT_TEMP456_ENABLE)
            inputs += [3 + n for n in range(3)
                       if pins is not None and (pins >> (2 * n)) & 0x03 >= 0x02]
        temps: dict[str, int | None] = {}
        for n in inputs:
            raw = monitor.read(_IT_TEMP + n)
            temps[f"TMPIN{n + 1}"] = None if raw is None else _signed(raw)
        frame_log.debug("Ite._temps: %s", temps)
        return temps


def _rpm(low: int | None, high: int | None) -> int | None:
    """A 16-bit tachometer count as RPM: 0 for a stopped fan (0xFFFF), None
    for no count (0), a count too small to be a fan, or a torn read."""
    if low is None or high is None:
        frame_log.debug("_rpm: torn read")
        return None
    count = (high << 8) | low
    if count == _TACH_STOPPED:
        rpm: int | None = 0
    elif count < _TACH_MIN_COUNT:
        rpm = None
    else:
        rpm = _TACH_CLOCK // (2 * count)
    frame_log.debug("_rpm: count 0x%04x -> %s", count, rpm)
    return rpm


#: The key order LibreHardwareMonitor's ``Detect`` uses: Nuvoton, then ITE.
#: The two Nuvoton lines share a key; their chip ids do not overlap.
FAMILIES: tuple[SuperIoChip, ...] = (Nuvoton(), Nct668x(), Ite())


def _config_key(family: SuperIoChip) -> tuple[Any, Any]:
    """How *family* enters and leaves config mode -- families that share it
    share one key on a port, not one each."""
    key = (type(family).enter, type(family).leave)
    log.debug("_config_key: %s", type(family).__name__)
    return key


#: FAMILIES grouped by config key, in order: one key per group per port.
KEYED_FAMILIES: tuple[tuple[SuperIoChip, ...], ...] = tuple(
    tuple(group) for _, group in itertools.groupby(FAMILIES, key=_config_key))


# ── the reader ───────────────────────────────────────────────────────────────


class SuperIoReader:
    """The fixed read sequence on ``LpcIO`` modules, one per slot.

    *open_module()* opens one ``LpcIO`` (``PawnIoModule``'s ``execute``); the
    first is opened here, so a missing module fails now.  *bus* is a context
    manager factory holding the ISA-bus mutex.  :meth:`sample` answers
    ``{"chips": [{"slot", "chip", "fans", "temps"}, ...]}`` -- a chip that
    cannot be read this tick carries ``"reason"`` instead -- or
    ``{"chips": [], "reason": ...}`` where there is nothing to read.
    """

    def __init__(self, open_module: Callable[[], Any],
                 bus: Callable[[], AbstractContextManager[None]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._open_module, self._bus, self._clock = open_module, bus, clock
        self._lpcs: dict[int, Lpc] = {0: Lpc(open_module(), 0)}
        log.info("SuperIoReader: LpcIO opened")
        self._lock = threading.Lock()
        self._found: list[FoundChip] | None = None
        self._reason = ""
        self._at = -1e9
        self._last: dict[str, Any] = {}
        self._reported: set[int] = set()

    def _lpc(self, slot: int) -> Lpc:
        if slot not in self._lpcs:
            self._lpcs[slot] = Lpc(self._open_module(), slot)
        log.debug("SuperIoReader._lpc: slot %d", slot)
        return self._lpcs[slot]

    def _find(self) -> list[FoundChip]:
        """Every chip TRCC reads, slot 0 first; :attr:`_reason` says why
        where there is none.  Under the bus mutex."""
        found: list[FoundChip] = []
        reasons: list[str] = []
        errors: list[OSError] = []
        for slot in _SLOTS:
            try:
                family, result = self._probe(self._lpc(slot))
            except OSError as e:
                # One slot's failure must not cost the other slot's chip.
                log.warning("SuperIoReader._find: 0x%02x failed -- %s", _SLOTS[slot], e)
                errors.append(e)
                reasons.append(f"0x{_SLOTS[slot]:02x} could not be read: {e}")
                continue
            if isinstance(result, FoundChip):
                if found and result.base == found[0].base:
                    # Linux it87: "Don't register second chip if its ISA address
                    # matches the first chip's ISA address."
                    reasons.append(f"{result.name} at 0x{result.lpc.port:02x} shares "
                                   f"0x{result.base:04x} with {found[0].name}")
                else:
                    found.append(result)
            else:
                reasons.append(result)
            if family is not None and not family.PAIRED:
                # A chip TRCC knows ends the search, readable or not: the
                # other slot is not where its reason lies.
                break
        if errors and not found:
            # Nothing found and something failed: ask again next sample.
            raise errors[0]
        self._reason = "; ".join(reasons)
        for slot in [s for s in self._lpcs if s not in {f.lpc.slot for f in found}]:
            self._lpcs.pop(slot).close()
        log.info("SuperIoReader._find: %s -- %s",
                 [f.name for f in found] or "no chip TRCC reads", self._reason or "")
        return found

    def _probe(self, lpc: Lpc) -> tuple[SuperIoChip | None, FoundChip | str]:
        """The chip on *lpc*'s port: (its family, or None where no family
        knows it; the chip found, or why not)."""
        lpc.select_slot()
        chip_id = lpc.chip_id()
        if chip_id != _NO_CHIP:
            # Already in config mode: no key and no exit -- the Gigabyte
            # IT879x is left this way, and keying it is what crashes it.
            for family in FAMILIES:
                if family.name(chip_id) is not None:
                    log.info("SuperIoReader: 0x%04x at 0x%02x answers before any key",
                             chip_id, lpc.port)
                    return family, family.identify(lpc, chip_id)
            reason = (f"chip id 0x{chip_id:04x} answers at 0x{lpc.port:02x} before any "
                      "key and is not one TRCC reads -- left as it is")
            log.info("SuperIoReader: %s", reason)
            return None, reason
        seen = _NO_CHIP
        for group in KEYED_FAMILIES:
            keyer = group[0]
            keyer.enter(lpc)
            chip_id = _NO_CHIP
            try:
                chip_id = lpc.chip_id()
                for family in group:
                    if family.name(chip_id) is not None:
                        return family, family.identify(lpc, chip_id)
            finally:
                keyer.leave(lpc, chip_id)
            seen = chip_id if seen == _NO_CHIP else seen
        reason = (f"no Super I/O chip at 0x{lpc.port:02x}" if seen == _NO_CHIP else
                  f"chip id 0x{seen:04x} at 0x{lpc.port:02x} is not one TRCC reads")
        log.info("SuperIoReader: %s", reason)
        return None, reason

    def _measure(self, found: FoundChip) -> dict[str, Any]:
        try:
            reading = found.family.measure(found)
        except OSError as e:
            # This chip has no readings this tick; the other chip still does.
            frame_log.debug("SuperIoReader._measure: %s -- %s", found.name, e)
            reading = {"reason": f"{found.name} could not be read: {e}"}
        entry = {"slot": found.lpc.slot, "chip": found.name, **reading}
        if found.lpc.slot not in self._reported:
            self._reported.add(found.lpc.slot)
            log.info("SuperIoReader: first reading of %s at 0x%02x: %s",
                     found.name, found.lpc.port, entry)
        frame_log.debug("SuperIoReader._measure: %s", entry)
        return entry

    def sample(self) -> dict[str, Any]:
        """Every chip's fans and inputs now -- at most every half second."""
        with self._lock:
            now = self._clock()
            if now - self._at < _MIN_INTERVAL_S and self._last:
                frame_log.debug("SuperIoReader.sample: %.2f s old -- reused", now - self._at)
                return self._last
            with self._bus():
                if self._found is None and not self._reason:
                    self._found = self._find()
                if not self._found:
                    self._last = {"chips": [], "reason": self._reason}
                else:
                    self._last = {"chips": [self._measure(f) for f in self._found]}
            self._at = now
        frame_log.debug("SuperIoReader.sample: %s", self._last)
        return self._last

    def close(self) -> None:
        log.debug("SuperIoReader.close: %d module(s)", len(self._lpcs))
        for lpc in self._lpcs.values():
            lpc.close()
        self._lpcs.clear()


# ── the sources ──────────────────────────────────────────────────────────────


class SuperIoBoard:
    """The chips' latest sample, shared by their fans and inputs.  *sample* is
    a :meth:`SuperIoReader.sample` -- here, or across the service's pipe."""

    def __init__(self, sample: Callable[[], dict[str, Any]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        log.debug("SuperIoBoard.__init__")
        self._sample, self._clock = sample, clock
        self._lock = threading.Lock()
        self._at = -1e9
        self._latest: dict[str, Any] = {}

    def latest(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if now - self._at >= _SAMPLE_TTL_S:
                self._at = now
                try:
                    self._latest = self._sample()
                except OSError as e:
                    # The bus busy past its wait, or the service gone: this
                    # tick has no board readings; the next one asks again.
                    frame_log.debug("SuperIoBoard.latest: %s", e)
                    self._latest = {}
            latest = self._latest
        frame_log.debug("SuperIoBoard.latest: %s", latest)
        return latest

    def chip(self, slot: int) -> dict[str, Any]:
        """The latest entry for the chip in *slot*; empty when it has none."""
        entry = next((c for c in self.latest().get("chips") or [] if c.get("slot") == slot), {})
        frame_log.debug("SuperIoBoard.chip: slot %d -> %s", slot, entry.get("chip"))
        return entry


@dataclass(frozen=True)
class _ChipView:
    """Where a source's chip is: its board, slot, key prefix and name."""

    board: SuperIoBoard
    slot: int
    prefix: str
    chip: str


class SuperIoFan(FanSource):
    """One fan header on the board's Super I/O."""

    def __init__(self, view: _ChipView, index: int) -> None:
        log.info("SuperIoFan: %s fan %d", view.chip, index + 1)
        self._view, self._index = view, index

    @property
    def key(self) -> str:
        frame_log.debug("SuperIoFan.key")
        return f"{self._view.prefix}:fan{self._index + 1}"

    @property
    def name(self) -> str:
        frame_log.debug("SuperIoFan.name")
        return f"{self._view.chip} Fan {self._index + 1}"

    def rpm(self) -> int | None:
        fans = self._view.board.chip(self._view.slot).get("fans") or []
        value = fans[self._index] if self._index < len(fans) else None
        frame_log.debug("SuperIoFan.rpm: fan%d -> %s", self._index + 1, value)
        return value


class SuperIoTemp(BoardTempSource):
    """One board input on the Super I/O (SYSTIN, CPUTIN, AUXTINn, TMPINn)."""

    def __init__(self, view: _ChipView, label: str) -> None:
        log.info("SuperIoTemp: %s %s", view.chip, label)
        self._view, self._label = view, label

    @property
    def key(self) -> str:
        frame_log.debug("SuperIoTemp.key")
        return f"{self._view.prefix}:{key_of(self._label)}"

    @property
    def name(self) -> str:
        frame_log.debug("SuperIoTemp.name")
        return f"{self._view.chip} {self._label}"

    def temp(self) -> float | None:
        value = (self._view.board.chip(self._view.slot).get("temps") or {}).get(self._label)
        result = float(value) if _plausible(value) else None
        frame_log.debug("SuperIoTemp.temp: %s -> %s", self._label, result)
        return result


def key_of(label: str) -> str:
    """An input's label as a key: lower case, every run of other characters one
    ``_`` -- "SYSTIN" stays "systin", "PECI 0.0" becomes "peci_0_0"."""
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    frame_log.debug("key_of: %r -> %s", label, key)
    return key


def _plausible(value: Any) -> TypeGuard[int | float]:
    """A temperature worth showing: a number from 1 to 125 °C."""
    plausible = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and _PLAUSIBLE_MIN_C <= value <= _PLAUSIBLE_MAX_C)
    frame_log.debug("_plausible: %s -> %s", value, plausible)
    return plausible


def _spinning(rpm: Any) -> bool:
    """A fan worth offering: a reading, turning, not the 0xFFFF no-fan mark."""
    spinning = isinstance(rpm, int) and 0 < rpm < 0xFFFF
    log.debug("_spinning: %s -> %s", rpm, spinning)
    return spinning


def discover_superio(sample: Callable[[], dict[str, Any]] | None
                     ) -> tuple[list[FanSource], list[BoardTempSource]]:
    """The board's spinning fans and connected inputs, from one first sample;
    nothing without *sample* (no PawnIO here) or without a chip TRCC reads.
    The first chip's keys are ``superio:...``, a second chip's ``superio2:...``."""
    log.info("discover_superio: %s", "called" if sample else "no reader")
    if sample is None:
        return [], []
    board = SuperIoBoard(sample)
    first = board.latest()
    if not first.get("chips"):
        log.info("discover_superio: no board readings -- %s",
                 first.get("reason") or "the reader did not answer")
        return [], []
    fans: list[FanSource] = []
    temps: list[BoardTempSource] = []
    for order, entry in enumerate(first["chips"]):
        view = _ChipView(board, int(entry["slot"]), "superio" if order == 0
                         else f"superio{order + 1}", str(entry["chip"]))
        if "reason" in entry:
            log.info("discover_superio: %s not read -- %s", view.chip, entry["reason"])
        fans += [SuperIoFan(view, i) for i, rpm in enumerate(entry.get("fans") or [])
                 if _spinning(rpm)]
        temps += [SuperIoTemp(view, label)
                  for label, value in (entry.get("temps") or {}).items()
                  if _plausible(value)]
        log.info("discover_superio: %s -- %d fan(s) spinning, %d input(s) connected so far",
                 view.chip, len(fans), len(temps))
    return fans, temps
