"""The SMBus, as Linux's i2c-dev exposes it (``/dev/i2c-N``).

The same ioctl ``i2cget`` / ``i2cset`` use (``I2C_SLAVE`` then ``I2C_SMBUS``),
from the standard library alone -- no smbus2, no i2c-tools at run time.
``I2C_SLAVE``, not ``I2C_SLAVE_FORCE``: an address a kernel driver owns
answers ``EBUSY``, as ``i2cset`` without ``-f`` does, so the memory chips'
own ``spd5118`` driver keeps its addresses to itself.

Constants and struct layouts: ``linux/i2c-dev.h`` and ``linux/i2c.h``.
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

from ...core.logs import per_frame
from ...core.ports import SMBUS_BLOCK_MAX, SmBus

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

I2C_SLAVE = 0x0703
I2C_SMBUS = 0x0720
_READ, _WRITE = 1, 0
_BYTE_DATA, _BLOCK_DATA = 2, 5
#: The chipset SMBus controllers' kernel names start with this -- ``SMBus I801
#: adapter at efa0``, ``SMBus PIIX4 adapter port 0 at 0b00`` -- and the GPU's
#: DDC and the DesignWare buses' never do.  The RAM-lighting udev rule matches
#: the same prefix, so access is granted to exactly the bus TRCC drives.
SMBUS_PREFIX = "SMBus"

#: Every I2C bus and client the kernel knows.  Not ``/sys/class/i2c-adapter``:
#: that class is gone on current kernels (absent on 7.2, measured 2026-10-08).
ADAPTERS = Path("/sys/bus/i2c/devices")


class _Data(ctypes.Union):
    _fields_ = [("byte", ctypes.c_uint8), ("word", ctypes.c_uint16),
                ("block", ctypes.c_uint8 * (SMBUS_BLOCK_MAX + 2))]


class _Request(ctypes.Structure):
    _fields_ = [("read_write", ctypes.c_uint8), ("command", ctypes.c_uint8),
                ("size", ctypes.c_uint32), ("data", ctypes.POINTER(_Data))]


def find_smbus(root: Path = ADAPTERS) -> tuple[int, ...]:
    """The bus numbers of the chipset SMBus controllers (i801, PIIX4, ...).

    Their kernel name starts with ``SMBus`` -- ``SMBus I801 adapter at efa0``,
    ``SMBus PIIX4 adapter port 0 at 0b00`` -- which the GPU's DDC and display
    buses never do, so only the bus the memory sits on is ever probed.
    """
    found = []
    for name_file in sorted(root.glob("i2c-*/name")):
        number = name_file.parent.name.removeprefix("i2c-")
        if not number.isdigit():                 # a client: i2c-MSFT8000:00
            continue
        try:
            name = name_file.read_text(encoding="utf-8").strip()
        except OSError as e:
            log.debug("find_smbus: %s unreadable (%s)", name_file, e)
            continue
        if name.startswith(SMBUS_PREFIX):
            found.append(int(number))
    log.info("find_smbus: %s", found or "none")
    return tuple(found)


#: The memory's own temperature sensors, bound by the kernel on the bus the
#: RGB controllers share: DDR5's SPD hub, DDR4's thermal sensor.
MEMORY_SENSOR_DRIVERS = Path("/sys/bus/i2c/drivers")
_MEMORY_SENSORS = ("spd5118", "jc42")


def silent_memory_sensors(drivers: Path = MEMORY_SENSOR_DRIVERS) -> list[str]:
    """The memory sensors (``3-0051``) whose temperature reads fail.

    A sensor that is bound and will not answer is a stuck SPD hub -- what
    another program's probe left on 2026-10-09.  Reading it is the kernel
    driver's ordinary path, the same one every temperature display takes.
    """
    silent = []
    for driver in _MEMORY_SENSORS:
        for device in sorted((drivers / driver).glob("*-00*")):
            for temp in device.glob("hwmon/hwmon*/temp1_input"):
                try:
                    temp.read_text(encoding="utf-8")
                except OSError as e:
                    log.warning("silent_memory_sensors: %s %s -- %s", driver,
                                device.name, e)
                    silent.append(device.name)
    log.info("silent_memory_sensors: %s", silent or "none")
    return silent


class LinuxSmBus(SmBus):
    """``/dev/i2c-<bus>``.  Every failure is an ``OSError``."""

    def __init__(self, bus: int, dev: Path = Path("/dev")) -> None:
        log.info("LinuxSmBus: opening i2c-%d", bus)
        super().__init__(bus)
        import fcntl  # POSIX only; this adapter is reached on Linux alone
        self._ioctl = fcntl.ioctl
        self._fd = os.open(dev / f"i2c-{bus}", os.O_RDWR)
        self._address: int | None = None

    def read_byte_data(self, address: int, register: int) -> int:
        frame_log.debug("read_byte_data: 0x%02x reg 0x%02x", address, register)
        data = _Data()
        self._transfer(address, _READ, register, _BYTE_DATA, data)
        return data.byte

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        frame_log.debug("write_byte_data: 0x%02x reg 0x%02x = 0x%02x",
                        address, register, value)
        data = _Data()
        data.byte = value
        self._transfer(address, _WRITE, register, _BYTE_DATA, data)

    def write_block_data(self, address: int, register: int,
                         data: bytes) -> None:
        frame_log.debug("write_block_data: 0x%02x reg 0x%02x %d byte(s)",
                        address, register, len(data))
        if not 0 < len(data) <= SMBUS_BLOCK_MAX:
            raise ValueError(f"an SMBus block is 1-{SMBUS_BLOCK_MAX} bytes, "
                             f"not {len(data)}")
        block = _Data()
        block.block[0] = len(data)
        for i, value in enumerate(data, start=1):
            block.block[i] = value
        self._transfer(address, _WRITE, register, _BLOCK_DATA, block)

    def close(self) -> None:
        log.debug("LinuxSmBus.close: fd=%d", self._fd)
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def _transfer(self, address: int, read_write: int, register: int,
                  size: int, data: _Data) -> None:
        frame_log.debug("_transfer: 0x%02x", address)
        if address != self._address:
            self._ioctl(self._fd, I2C_SLAVE, address)
            self._address = address
        request = _Request(read_write, register, size, ctypes.pointer(data))
        self._ioctl(self._fd, I2C_SMBUS, request)
