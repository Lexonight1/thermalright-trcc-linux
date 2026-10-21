"""Motherboard fans and board temperatures on Windows, from the Nuvoton Super I/O.

Most ASUS and ASRock boards, and many MSI ones, carry a Nuvoton NCT6791D-NCT6799D
"Super I/O" chip that counts every fan header and reads the board's thermal
inputs.  Linux's ``nct6775`` driver reads it; on Windows nothing did unless
LibreHardwareMonitor or HWiNFO ran beside TRCC (#319).  This reads it through
PawnIO's signed ``LpcIO`` module -- as the SYSTEM service for a normal App
(``superio.sample``), or directly when the App is elevated.

**Read-only, and the sequence is fixed here, not by any caller.**  The writes
are the ones every reader of this chip needs, and none of them changes a
setting: config-mode enter and exit (``0x87 0x87`` / ``0xAA``), the logical-
device select, and the hardware monitor's bank and index registers.  PawnIO's
own ``find_bars`` also steps the device select through every device, which is
how it learns which ports may be read.  Where the chip is switched off, or its
BIOS locked the monitor (config register ``0x28`` bit 4), this reads nothing
-- LibreHardwareMonitor and Linux CLEAR that lock; TRCC does not write it.

Registers from Linux ``drivers/hwmon/nct6775-core.c`` / ``nct6775-platform.c``
(GPL-2.0+; facts, no code): chip ids masked ``0xFFF8``; fan RPM in the 16-bit
registers ``NCT6779_REG_FAN`` (the value IS the RPM, ``fan_from_reg_rpm``); the
board inputs in ``NCT6779_REG_TEMP_ALTERNATE`` 0x490-0x495, one signed byte of
whole degrees each, the same on every chip here.  The mutex is the one
LibreHardwareMonitor, HWiNFO and FanControl hold around the ISA bus.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from ...core.logs import per_frame
from ...core.ports import BoardTempSource, FanSource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: ``ioctl_select_slot`` argument -> the chip's config index port.
_SLOTS = {0: 0x2E, 1: 0x4E}
_ENTER, _EXIT = 0x87, 0xAA
_DEVICE_ID, _DEVICE_SELECT, _ACTIVATE, _BASE = 0x20, 0x07, 0x30, 0x60
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
#: An unconnected input reads far outside this (LibreHardwareMonitor drops <= 0
#: and > 125 for these registers too).
_PLAUSIBLE_C = range(1, 126)
#: The service answers a client no faster than this; a reading moves slower.
_MIN_INTERVAL_S = 0.5
_SAMPLE_TTL_S = 0.9


class SuperIoReader:
    """The fixed read sequence on one loaded ``LpcIO`` module.

    *module* has ``PawnIoModule``'s ``execute``; *bus* is a context manager
    factory holding the ISA-bus mutex.  :meth:`sample` answers
    ``{"chip": name, "fans": [rpm x 7], "temps": {input: °C}}``, or
    ``{"chip": None, "reason": ...}`` where there is nothing to read.
    """

    def __init__(self, module: Any, bus: Callable[[], AbstractContextManager[None]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        log.info("SuperIoReader: on %s", getattr(module, "name", "LpcIO"))
        self._module, self._bus, self._clock = module, bus, clock
        self._lock = threading.Lock()
        self._found: tuple[str, int] | None = None     # chip name, monitor base
        self._reason = ""
        self._at = -1e9
        self._last: dict[str, Any] = {}

    def _call(self, function: str, *inputs: int, outputs: int = 0) -> tuple[int, ...]:
        frame_log.debug("SuperIoReader._call: %s %s", function, [hex(i) for i in inputs])
        return self._module.execute(function, list(inputs), outputs)

    def _config(self, register: int) -> int:
        value = self._call("ioctl_superio_inb", register, outputs=1)[0] & 0xFF
        frame_log.debug("SuperIoReader._config: 0x%02x -> 0x%02x", register, value)
        return value

    def _find(self) -> tuple[str, int] | None:
        """Look for the chip in each slot; its name and monitor base, or None
        with :attr:`_reason` saying why.  Under the bus mutex."""
        for slot, port in _SLOTS.items():
            self._call("ioctl_select_slot", slot)
            self._call("ioctl_pio_outb", port, _ENTER)
            self._call("ioctl_pio_outb", port, _ENTER)
            try:
                name, found = self._identify(port)
            finally:
                self._call("ioctl_pio_outb", port, _EXIT)
            if name is not None:
                # A chip TRCC knows ends the search, readable or not: the
                # other slot is not where its reason lies.
                return found
        log.info("SuperIoReader._find: no Nuvoton NCT679x -- %s", self._reason)
        return None

    def _identify(self, port: int) -> tuple[str | None, tuple[str, int] | None]:
        """In config mode on *port*: (chip name or None, (name, monitor base)
        or None where the chip's monitor cannot be read)."""
        chip_id = (self._config(_DEVICE_ID) << 8) | self._config(_DEVICE_ID + 1)
        if (name := CHIPS.get(chip_id & _ID_MASK)) is None:
            self._reason = f"chip id 0x{chip_id:04x} at 0x{port:02x} is not one TRCC reads"
            log.info("SuperIoReader: %s", self._reason)
            return None, None
        # find_bars first: it walks every device select, so the monitor's is
        # chosen after it, and it is what admits the monitor's ports.
        self._call("ioctl_find_bars")
        self._call("ioctl_superio_outb", _DEVICE_SELECT, _HWM_DEVICE)
        base = ((self._config(_BASE) << 8) | self._config(_BASE + 1)) & ~0x7
        active = self._config(_ACTIVATE) & 0x01
        locked = self._config(_IO_SPACE_LOCK) & _LOCKED
        log.info("SuperIoReader: %s at 0x%02x, monitor 0x%04x, active=%d locked=%d",
                 name, port, base, active, bool(locked))
        if base < 0x100 or not active or locked:
            self._reason = (f"{name}'s hardware monitor is "
                            + ("switched off" if not active or base < 0x100 else
                               "locked by the BIOS (TRCC does not clear it)"))
            return name, None
        return name, (name, base)

    def _read_bank4(self, base: int, registers: list[int]) -> dict[int, int]:
        """Each register's byte; every register here is in bank 4.  Bank 0 is
        selected again afterwards."""
        self._call("ioctl_pio_outb", base + _ADDRESS, _BANK_SELECT)
        self._call("ioctl_pio_outb", base + _DATA, 4)
        values: dict[int, int] = {}
        try:
            for register in registers:
                self._call("ioctl_pio_outb", base + _ADDRESS, register & 0xFF)
                values[register] = self._call("ioctl_pio_inb", base + _DATA,
                                              outputs=1)[0] & 0xFF
        finally:
            self._call("ioctl_pio_outb", base + _ADDRESS, _BANK_SELECT)
            self._call("ioctl_pio_outb", base + _DATA, 0)
        frame_log.debug("SuperIoReader._read_bank4: %d register(s)", len(values))
        return values

    def _measure(self, name: str, base: int) -> dict[str, Any]:
        wanted = [r for fan in FAN_REGISTERS for r in (fan, fan + 1)]
        wanted += list(TEMP_REGISTERS.values())
        raw = self._read_bank4(base, wanted)
        sample = {"chip": name,
                  "fans": [(raw[r] << 8) | raw[r + 1] for r in FAN_REGISTERS],
                  "temps": {label: raw[r] - 256 if raw[r] > 127 else raw[r]
                            for label, r in TEMP_REGISTERS.items()}}
        frame_log.debug("SuperIoReader._measure: %s", sample)
        return sample

    def sample(self) -> dict[str, Any]:
        """The chip's fans and inputs now -- at most every half second."""
        with self._lock:
            now = self._clock()
            if now - self._at < _MIN_INTERVAL_S and self._last:
                frame_log.debug("SuperIoReader.sample: %.2f s old -- reused", now - self._at)
                return self._last
            with self._bus():
                if self._found is None and not self._reason:
                    self._found = self._find()
                if self._found is None:
                    self._last = {"chip": None, "reason": self._reason}
                else:
                    self._last = self._measure(*self._found)
            self._at = now
        frame_log.debug("SuperIoReader.sample: %s", self._last)
        return self._last

    def close(self) -> None:
        log.debug("SuperIoReader.close")
        self._module.close()


class SuperIoBoard:
    """One chip's latest sample, shared by its fans and inputs.  *sample* is
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


class SuperIoFan(FanSource):
    """One fan header on the board's Super I/O."""

    def __init__(self, board: SuperIoBoard, chip: str, index: int) -> None:
        log.info("SuperIoFan: %s fan %d", chip, index + 1)
        self._board, self._chip, self._index = board, chip, index

    @property
    def key(self) -> str:
        frame_log.debug("SuperIoFan.key")
        return f"superio:fan{self._index + 1}"

    @property
    def name(self) -> str:
        frame_log.debug("SuperIoFan.name")
        return f"{self._chip} Fan {self._index + 1}"

    def rpm(self) -> int | None:
        fans = self._board.latest().get("fans") or []
        value = fans[self._index] if self._index < len(fans) else None
        frame_log.debug("SuperIoFan.rpm: fan%d -> %s", self._index + 1, value)
        return value


class SuperIoTemp(BoardTempSource):
    """One board input on the Super I/O (SYSTIN, CPUTIN, AUXTINn)."""

    def __init__(self, board: SuperIoBoard, chip: str, label: str) -> None:
        log.info("SuperIoTemp: %s %s", chip, label)
        self._board, self._chip, self._label = board, chip, label

    @property
    def key(self) -> str:
        frame_log.debug("SuperIoTemp.key")
        return f"superio:{self._label.lower()}"

    @property
    def name(self) -> str:
        frame_log.debug("SuperIoTemp.name")
        return f"{self._chip} {self._label}"

    def temp(self) -> float | None:
        value = (self._board.latest().get("temps") or {}).get(self._label)
        result = float(value) if isinstance(value, int) and value in _PLAUSIBLE_C else None
        frame_log.debug("SuperIoTemp.temp: %s -> %s", self._label, result)
        return result


def discover_superio(sample: Callable[[], dict[str, Any]] | None
                     ) -> tuple[list[FanSource], list[BoardTempSource]]:
    """The board's spinning fans and connected inputs, from one first sample;
    nothing without *sample* (no PawnIO here) or without a chip TRCC reads."""
    log.info("discover_superio: %s", "called" if sample else "no reader")
    if sample is None:
        return [], []
    board = SuperIoBoard(sample)
    first = board.latest()
    if not first.get("chip"):
        log.info("discover_superio: no board readings -- %s",
                 first.get("reason") or "the reader did not answer")
        return [], []
    chip = str(first["chip"])
    fans: list[FanSource] = [SuperIoFan(board, chip, i)
                             for i, rpm in enumerate(first.get("fans") or [])
                             if 0 < rpm < 0xFFFF]
    temps: list[BoardTempSource] = [SuperIoTemp(board, chip, label)
                                    for label, value in (first.get("temps") or {}).items()
                                    if value in _PLAUSIBLE_C]
    log.info("discover_superio: %s -- %d fan(s) spinning, %d input(s) connected",
             chip, len(fans), len(temps))
    return fans, temps
