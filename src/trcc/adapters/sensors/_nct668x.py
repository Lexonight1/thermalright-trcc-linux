"""Board fans and temperatures from a Nuvoton NCT6683D, NCT6686D or NCT6687D.

These chips sit on most MSI boards and some Intel and ASRock ones.  Their
hardware monitor is an embedded controller (EC), and the vendor's firmware sets
its register map.  ``_superio``'s ``Nct668x`` family finds the chip and its
monitor base; this module then reads the EC through its paged window at
base+4/5/6, read-only, under the same ISA-bus mutex.

Facts come from Linux ``drivers/hwmon/nct6683.c`` (GPL-2.0+; facts only, no
code).  The access handshake comes from LibreHardwareMonitor's
``Nct677X.ReadByte``:

* chip IDs ``0xC730`` / ``0xD440`` / ``0xD590``, masked ``0xFFF0``
  (``SIO_NCT668x_ID``, ``SIO_ID_MASK``); the EC window is base +
  ``IOREGION_OFFSET`` 4: page, index, data (``EC_PAGE_REG`` 0, ``EC_INDEX_REG``
  1, ``EC_DATA_REG`` 2);
* one byte is read as ``0xFF`` to the page port ("unlock"), then the page, then
  the index, then the data port is read (``nct6683_read``);
* the customer ID is the 16-bit ``0x602``.  Linux attaches only to the IDs it
  lists unless ``force`` is set, and TRCC reads only those IDs;
* ``0x180`` bit 7 says the monitor is running (``nct6683_init_device`` sets it
  if it is clear).  TRCC never sets it.  If it is clear, TRCC reads nothing;
* fans: 16 RPM words at ``0x140 + 2i``, the value IS the RPM.  A fan is present
  if bit 7 of ``0x1C0 + i`` is set (``nct6683_setup_fans``);
* temperatures: 32 monitor words at ``0x100 + 2i``.  The source of slot *i* is
  ``0x1A0 + i`` & ``0x7F``.  A source below ``0x60`` with a label is a
  temperature (``nct6683_setup_sensors``).  The value in degrees C is
  ``(s16 / 128) * 0.5`` (``show_temp16``).

**The handshake is honoured, never forced.**  LibreHardwareMonitor reads the
page port, waits up to 500 ms for ``0xFF`` ("free"), and writes ``0xFF`` again
after each byte ("free access for other instances").  When the EC never frees
it, LHM writes ``0xFF`` anyway ("force register access").  TRCC waits a
bounded time and then skips this sample; it never forces the port.  Linux
writes the "unlock" ``0xFF`` without checking.  TRCC writes it only once the
port already reads ``0xFF``, so the write changes nothing.

**Every write here:** the page port (``0xFF``, then the page), and the index
port.  The data port is never written.  ``0x180``, the fan and PWM registers,
the voltage-slot registers LHM rewrites (``0x1BB``-``0x1BF``), and CR30 are
never written either.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: nct6683.c ``SIO_NCT6683_ID`` / ``SIO_NCT6686_ID`` / ``SIO_NCT6687_ID``.
CHIPS = {0xC730: "NCT6683D", 0xD440: "NCT6686D", 0xD590: "NCT6687D"}
ID_MASK = 0xFFF0                        # SIO_ID_MASK
_PAGE, _INDEX, _DATA = 4, 5, 6          # IOREGION_OFFSET + EC_PAGE/INDEX/DATA_REG
_FREE = 0xFF                            # the page port at rest; "unlock"
_CUSTOMER_ID = 0x602                    # NCT6683_REG_CUSTOMER_ID, 16 bit
#: The customer IDs nct6683_probe attaches to without ``force``.
CUSTOMERS = {0x805: "Intel", 0xA0E: "MiTAC", 0x201: "MSI", 0x200: "MSI",
             0x207: "MSI", 0x20D: "MSI", 0x162B: "AMD", 0xE2C: "ASRock",
             0xE1B: "ASRock", 0x1631: "ASRock", 0x163E: "ASRock",
             0x1621: "ASRock", 0x1633: "ASRock", 0x163D: "ASRock"}
_HWM_CFG, _STARTED = 0x180, 0x80        # NCT6683_HWM_CFG, "start monitoring"
_BUILD, _VERSION = 0x604, 0x608         # NCT6683_REG_BUILD_YEAR, _VERSION_HI
FAN_COUNT, MON_COUNT = 16, 32           # NCT6683_NUM_REG_FAN / _MON
FAN_RPM, FANIN_CFG = 0x140, 0x1C0       # NCT6683_REG_FAN_RPM(0), _FANIN_CFG(0)
MON, MON_CFG = 0x100, 0x1A0             # NCT6683_REG_MON(0), _MON_CFG(0)
_PRESENT, _SOURCE = 0x80, 0x7F
#: nct6683_mon_label below MON_VOLTAGE_START (0x60): the temperature sources.
TEMP_SOURCES: dict[int, str] = {
    0x01: "Local",
    **{0x02 + i: f"Diode {i} (curr)" for i in range(3)},
    **{0x05 + i: f"Diode {i} (volt)" for i in range(3)},
    **{0x08 + i: f"Thermistor {14 + i}" for i in range(3)},
    **{0x0B + i: f"Thermistor {i}" for i in range(14)},
    **{0x20 + i: f"PECI {i % 4}.{i // 4}" for i in range(8)},
    **{0x28 + i: f"PECI DIMM {i}" for i in range(4)},
    0x30: "PCH CPU", 0x31: "PCH CHIP", 0x32: "PCH CHIP CPU MAX", 0x33: "PCH MCH",
    **{0x34 + i: f"PCH DIMM {i}" for i in range(4)},
    **{0x38 + i: f"SMBus {i}" for i in range(6)},
    **{0x3E + i: f"DIMM {i}" for i in range(4)},
    **{0x42 + i: f"AMD TSI Addr {a:x}h"
       for i, a in enumerate((0x90, 0x92, 0x94, 0x96, 0x98, 0x9A, 0x9C, 0x9D))},
    **{0x50 + i: f"Virtual {i}" for i in range(8)},
}
#: How long one byte waits for the EC to free its page port.  Then this sample
#: is skipped: LHM waits 500 ms per byte and then forces it, but TRCC holds the
#: ISA bus while it waits and never forces.
_EC_WAIT_S = 0.05
_EC_POLL_S = 0.001


def celsius(word: int) -> float:
    """One monitor word in degrees C: ``show_temp16``'s ``(s16 / 128) * 500``
    millidegrees, which truncates toward zero as C does."""
    signed = word - 0x10000 if word & 0x8000 else word
    value = int(signed / 128) * 0.5
    frame_log.debug("celsius: 0x%04x -> %s", word, value)
    return value


class Nct668xMonitor:
    """The EC of one NCT668x, read-only.  *call* is ``Lpc.call``.
    :meth:`sample` runs under the ISA-bus mutex the caller holds.

    The first sample checks the chip and records its layout: customer ID,
    whether monitoring runs, and which fans and temperature sources exist.
    Linux does the same once, at probe.  A refusal is kept.  A busy EC is not
    kept: the next sample tries again.
    """

    def __init__(self, call: Callable[..., tuple[int, ...]], name: str, base: int,
                 clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        log.info("Nct668xMonitor: %s, EC window 0x%04x", name, base + _PAGE)
        self._call, self._name, self._base = call, name, base
        # Looked up now, not at import, so a test can stand in for ``time``.
        self._clock, self._sleep = clock or time.monotonic, sleep or time.sleep
        self._fans: list[int] | None = None        # present fan indices
        self._temps: dict[str, int] = {}           # label -> monitor slot
        self._refused = ""
        self._warned_busy = False

    def _inb(self, port: int) -> int:
        value = self._call("ioctl_pio_inb", port, outputs=1)[0] & 0xFF
        frame_log.debug("Nct668xMonitor._inb: 0x%04x -> 0x%02x", port, value)
        return value

    def _outb(self, port: int, value: int) -> None:
        frame_log.debug("Nct668xMonitor._outb: 0x%04x <- 0x%02x", port, value)
        self._call("ioctl_pio_outb", port, value)

    def _wait_free(self) -> None:
        """Until the page port reads ``0xFF``, or ``TimeoutError``.  The port
        is only read here, never forced."""
        deadline = self._clock() + _EC_WAIT_S
        while (seen := self._inb(self._base + _PAGE)) != _FREE:
            if self._clock() >= deadline:
                reason = (f"{self._name}'s EC stayed busy (page port 0x{seen:02x}) "
                          f"-- this sample is skipped; TRCC does not force it")
                if not self._warned_busy:
                    self._warned_busy = True
                    log.warning("Nct668xMonitor: %s", reason)
                frame_log.debug("Nct668xMonitor._wait_free: %s", reason)
                raise TimeoutError(reason)
            self._sleep(_EC_POLL_S)
        frame_log.debug("Nct668xMonitor._wait_free: free")

    def _read(self, register: int) -> int:
        """One EC byte: wait for the page port to be free, "unlock", page,
        index, read the data port, then free the page port again."""
        self._wait_free()
        page_port = self._base + _PAGE
        self._outb(page_port, _FREE)
        try:
            self._outb(page_port, register >> 8)
            self._outb(self._base + _INDEX, register & 0xFF)
            value = self._inb(self._base + _DATA)
        finally:
            self._outb(page_port, _FREE)
        frame_log.debug("Nct668xMonitor._read: 0x%03x -> 0x%02x", register, value)
        return value

    def _read16(self, register: int) -> int:
        value = (self._read(register) << 8) | self._read(register + 1)
        frame_log.debug("Nct668xMonitor._read16: 0x%03x -> 0x%04x", register, value)
        return value

    def _refuse(self, reason: str) -> None:
        log.warning("Nct668xMonitor: %s", reason)
        self._refused = reason

    def _setup(self) -> None:
        """Check the chip once and record its layout.  It sets :attr:`_fans`
        only when every byte was read, so a busy EC leaves nothing half-set."""
        customer = self._read16(_CUSTOMER_ID)
        if (vendor := CUSTOMERS.get(customer)) is None:
            self._refuse(f"{self._name}'s customer ID 0x{customer:04x} is not one "
                         f"Linux's nct6683 reads without 'force' -- TRCC reads only those")
            return
        if not self._read(_HWM_CFG) & _STARTED:
            self._refuse(f"{self._name}'s hardware monitor is not started "
                         f"(TRCC does not start it)")
            return
        version = (self._read(_VERSION), self._read(_VERSION + 1))
        build = [self._read(_BUILD + i) for i in (1, 2, 0)]     # month, day, year
        fans = [i for i in range(FAN_COUNT) if self._read(FANIN_CFG + i) & _PRESENT]
        temps: dict[str, int] = {}
        for slot in range(MON_COUNT):
            if (label := TEMP_SOURCES.get(self._read(MON_CFG + slot) & _SOURCE)) is None:
                continue                    # disabled, reserved, or a voltage
            unique, n = label, 2
            while unique in temps:
                unique, n = f"{label} #{n}", n + 1
            temps[unique] = slot
        self._fans, self._temps = fans, temps
        log.info("Nct668xMonitor: %s, customer 0x%04x (%s), EC firmware %d.%d "
                 "build %02x/%02x/%02x -- fans %s, temperatures %s", self._name,
                 customer, vendor, *version, *build, [i + 1 for i in fans], temps)

    def sample(self) -> dict[str, Any]:
        """``SuperIoReader.sample``'s shape, 16 fans (0 where there is no
        header) and the temperature sources the firmware assigned."""
        if self._fans is None and not self._refused:
            self._setup()
        if self._fans is None:
            frame_log.debug("Nct668xMonitor.sample: refused -- %s", self._refused)
            return {"chip": None, "reason": self._refused}
        fans = [0] * FAN_COUNT
        for i in self._fans:
            fans[i] = self._read16(FAN_RPM + 2 * i)
        temps = {label: celsius(self._read16(MON + 2 * slot))
                 for label, slot in self._temps.items()}
        sample = {"chip": self._name, "fans": fans, "temps": temps}
        frame_log.debug("Nct668xMonitor.sample: %s", sample)
        return sample
