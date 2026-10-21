"""W4: motherboard fans and inputs on Windows, from the Super I/O chip.

Nothing here can reach a real chip -- the win11 VM has none -- so fake chips
stand in, built to behave as the hardware and PawnIO's ``LpcIO`` module do:
config space answers only behind the vendor's own key (or when the BIOS left
it entered), a banked monitor sits behind base+5/base+6, and each opened
module keeps its own slot and its own port whitelist (a monitor port is
refused until ``find_bars`` has seen it, and selecting a slot forgets it).
Register facts are Linux ``nct6775``'s and ``it87``'s.

The tests that matter most are the WRITE lists: every byte the reader puts on
the bus is one the plan allows, the Nuvoton I/O-space lock is never touched,
an ITE monitor's data port is never written, and a Gigabyte IT879x left in
config mode is never keyed or taken out of it.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from trcc.adapters.sensors import _superio as sio
from trcc.adapters.sensors import windows as win
from trcc.adapters.system._service_broker import PrivilegedBroker, Session

_BASE = 0x290


class _Handle:
    """One opened ``LpcIO`` module: its own slot and its own whitelist."""

    def __init__(self, board: Any) -> None:
        self.board = board
        self.slot: int | None = None
        self.port = 0
        self.bars: set[int] = set()

    def allowed(self, port: int) -> bool:
        return port in (self.port, self.port + 1) or (port & 0xFFF8) in self.bars

    def execute(self, function: str, inputs: list[int], outputs: int) -> tuple[int, ...]:
        self.board.calls.append((function, list(inputs)))
        if function == "ioctl_select_slot":
            self.slot, self.port, self.bars = inputs[0], sio._SLOTS[inputs[0]], set()
            return ()
        if function in ("ioctl_pio_outb", "ioctl_pio_inb") and not self.allowed(inputs[0]):
            raise OSError("STATUS_ACCESS_DENIED")
        return self.board.execute(self, function, inputs)

    def close(self) -> None:
        self.board.calls.append(("close", []))
        self.board.closed += 1


class _FakeChip:
    """A Nuvoton chip behind PawnIO's LpcIO, recording every call.  Its config
    space answers only after ``0x87 0x87`` (or when *preentered*)."""

    def __init__(self, *, slot_ids: dict[int, int] | None = None, base: int = _BASE,
                 active: int = 1, lock: int = 0, monitor: dict[int, int] | None = None,
                 preentered: bool = False) -> None:
        self.slot_ids = slot_ids if slot_ids is not None else {0: 0xD42B}
        self.config = {0x0B: {0x60: base >> 8, 0x61: base & 0xFF, 0x30: active, 0x28: lock}}
        self.monitor = monitor or {}
        self.calls: list[tuple[str, list[int]]] = []
        self.preentered = preentered
        self.in_config: dict[int, bool] = {}
        self.keyed = 0
        self.device = 0
        self.base = base
        self.hwm_index = 0
        self.bank = 0
        self.opened = self.closed = 0

    def open(self) -> _Handle:
        self.opened += 1
        return _Handle(self)

    def _entered(self, handle: _Handle) -> bool:
        return self.in_config.get(handle.port, self.preentered)

    def _chip_id(self, handle: _Handle) -> int:
        return self.slot_ids.get(handle.slot if handle.slot is not None else -1, 0xFFFF)

    def _cfg_read(self, handle: _Handle, register: int) -> int:
        if not self._entered(handle) or self._chip_id(handle) == 0xFFFF:
            return 0xFF
        if register == 0x20:
            return self._chip_id(handle) >> 8
        if register == 0x21:
            return self._chip_id(handle) & 0xFF
        if register == 0x07:
            return self.device
        return self.config.get(self.device, {}).get(register, 0xFF)

    def _index_port(self, handle: _Handle, value: int) -> None:
        if self._entered(handle):
            if value == 0xAA:
                self.in_config[handle.port] = False
            return
        self.keyed = self.keyed + 1 if value == 0x87 else 0
        if self.keyed == 2:
            self.in_config[handle.port], self.keyed = True, 0

    def execute(self, handle: _Handle, function: str, inputs: list[int]) -> tuple[int, ...]:
        match function:
            case "ioctl_find_bars":
                if self._cfg_read(handle, 0x20) in (0x00, 0xFF):
                    raise OSError("STATUS_NOT_FOUND")
                handle.bars.add(self.base & 0xFFF8)
                self.device = 0xFE                   # it walks every device select
                return ()
            case "ioctl_superio_inb":
                return (self._cfg_read(handle, inputs[0]),)
            case "ioctl_superio_outb":
                if self._entered(handle):
                    if inputs[0] == 0x07:
                        self.device = inputs[1]
                    else:
                        self.config.setdefault(self.device, {})[inputs[0]] = inputs[1]
                return ()
            case "ioctl_pio_outb":
                port, value = inputs
                if port == handle.port:
                    self._index_port(handle, value)
                elif port == self.base + 5:
                    self.hwm_index = value
                elif port == self.base + 6:
                    if self.hwm_index == 0x4E:
                        self.bank = value
                    else:
                        raise AssertionError(f"wrote 0x{value:02x} into monitor register "
                                             f"0x{self.bank:x}{self.hwm_index:02x}")
                return ()
            case "ioctl_pio_inb":
                return (self.monitor.get((self.bank << 8) | self.hwm_index, 0),)
        raise AssertionError(f"unexpected {function}")


def _monitor(fans: dict[int, int], temps: dict[str, int]) -> dict[int, int]:
    regs: dict[int, int] = {}
    for index, rpm in fans.items():
        reg = sio.FAN_REGISTERS[index]
        regs[reg], regs[reg + 1] = rpm >> 8, rpm & 0xFF
    for label, value in temps.items():
        regs[sio.TEMP_REGISTERS[label]] = value & 0xFF
    return regs


class _Bus:
    """The ISA-bus mutex, counted."""

    def __init__(self) -> None:
        self.held = 0

    def __call__(self) -> Any:
        @contextmanager
        def held() -> Any:
            self.held += 1
            yield
        return held()


def _reader(chip: Any, now: list[float] | None = None,
            bus: _Bus | None = None) -> sio.SuperIoReader:
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return sio.SuperIoReader(chip.open, bus or _Bus(), clock=clock)


def _writes(board: Any) -> list[tuple[str, list[int]]]:
    return [(f, i) for f, i in board.calls if f in ("ioctl_pio_outb", "ioctl_superio_outb")]


def test_a_sample_reads_the_fans_and_inputs_linux_reads() -> None:
    """NCT6798D (0xD42B masked to 0xD428); RPM is the register itself;
    inputs are one signed byte of whole degrees."""
    chip = _FakeChip(monitor=_monitor({0: 1250, 1: 0, 6: 812},
                                      {"SYSTIN": 34, "CPUTIN": 41, "AUXTIN0": -128}))
    assert _reader(chip).sample() == {"chips": [{
        "slot": 0, "chip": "NCT6798D", "fans": [1250, 0, 0, 0, 0, 0, 812],
        "temps": {"SYSTIN": 34, "CPUTIN": 41, "AUXTIN0": -128,
                  "AUXTIN1": 0, "AUXTIN2": 0, "AUXTIN3": 0}}]}


def test_every_write_is_one_the_plan_allows() -> None:
    """Config enter/exit on the index port, the monitor's device select, the
    bank and index registers -- and nothing else.  Never config 0x28.

    MUTATION CHECK: clear the lock (superio_outb 0x28) in identify -- listed."""
    chip = _FakeChip(monitor=_monitor({0: 900}, {"SYSTIN": 30}))
    reader = _reader(chip)
    reader.sample()
    reader.sample()
    allowed_pio = {(0x2E, 0x87), (0x2E, 0xAA), (_BASE + 5, 0x4E), (_BASE + 6, 4), (_BASE + 6, 0)}
    allowed_index = {r & 0xFF for fan in sio.FAN_REGISTERS for r in (fan, fan + 1)} | {
        r & 0xFF for r in sio.TEMP_REGISTERS.values()}
    stray = [(f, i) for f, i in _writes(chip)
             if not (f == "ioctl_superio_outb" and i == [0x07, 0x0B])
             and not (f == "ioctl_pio_outb" and tuple(i) in allowed_pio)
             and not (f == "ioctl_pio_outb" and i[0] == _BASE + 5 and i[1] in allowed_index)]
    assert not stray, stray
    assert chip.bank == 0, "bank 0 was not selected again"
    assert chip.in_config == {0x2E: False}, "config mode was left entered"


def test_the_chip_id_is_read_before_any_key() -> None:
    """The first thing on a port is the chip id as it stands: a key is never
    the first write.

    MUTATION CHECK: drop the pre-read in _probe -- the first call is a key."""
    chip = _FakeChip(monitor=_monitor({0: 900}, {}))
    _reader(chip).sample()
    after_slot = chip.calls[chip.calls.index(("ioctl_select_slot", [0])) + 1:]
    assert after_slot[:2] == [("ioctl_superio_inb", [0x20]), ("ioctl_superio_inb", [0x21])]


def test_a_chip_already_in_config_mode_gets_no_key_and_no_exit() -> None:
    """Whatever left it entered, TRCC reads it as it is and leaves it so --
    the rule the Gigabyte IT879x needs, applied to every vendor.

    MUTATION CHECK: always key in _probe -- 0x87 is written."""
    chip = _FakeChip(monitor=_monitor({0: 900}, {}), preentered=True)
    sample = _reader(chip).sample()
    assert sample["chips"][0]["fans"][0] == 900
    assert not [i for f, i in _writes(chip) if f == "ioctl_pio_outb" and i[0] == 0x2E]
    assert chip.in_config == {}


def test_a_locked_monitor_is_reported_and_left_locked() -> None:
    """LHM and Linux clear 0x28 bit 4; TRCC reads nothing instead."""
    chip = _FakeChip(lock=0x10, monitor=_monitor({0: 900}, {}))
    sample = _reader(chip).sample()
    assert sample == {"chips": [], "reason": "NCT6798D's hardware monitor is locked "
                                             "by the BIOS (TRCC does not clear it)"}
    assert chip.config[0x0B][0x28] == 0x10
    assert not any(f == "ioctl_pio_inb" for f, _ in chip.calls)


def test_a_switched_off_monitor_reads_nothing() -> None:
    chip = _FakeChip(active=0)
    assert _reader(chip).sample() == {
        "chips": [], "reason": "NCT6798D's hardware monitor is switched off"}
    assert chip.config[0x0B][0x30] == 0                # never forced on


def test_the_chip_may_sit_at_0x4e() -> None:
    chip = _FakeChip(slot_ids={0: 0xFFFF, 1: 0xC803})
    assert _reader(chip).sample()["chips"][0] | {"fans": None, "temps": None} == {
        "slot": 1, "chip": "NCT6791D", "fans": None, "temps": None}
    assert (chip.opened, chip.closed) == (2, 1), "slot 0's empty module was kept open"


def test_a_known_chip_that_cannot_be_read_ends_the_search() -> None:
    """The second slot's "no chip" must not replace the first slot's reason,
    and a Nuvoton at 0x2E means nothing is keyed at 0x4E.

    MUTATION CHECK: make Nuvoton PAIRED -- slot 1 is selected."""
    chip = _FakeChip(slot_ids={0: 0xD42B, 1: 0xFFFF}, lock=0x10)
    assert "locked by the BIOS" in _reader(chip).sample()["reason"]
    assert ("ioctl_select_slot", [1]) not in chip.calls


def test_another_vendors_chip_is_not_read() -> None:
    """A Fintek F71882 answers the same 0x87 0x87 key; neither family reads it."""
    chip = _FakeChip(slot_ids={0: 0x0541, 1: 0xFFFF})
    sample = _reader(chip).sample()
    assert sample == {"chips": [], "reason": "chip id 0x0541 at 0x2e is not one TRCC "
                                             "reads; no Super I/O chip at 0x4e"}
    assert not any(f == "ioctl_find_bars" for f, _ in chip.calls)


def test_the_service_answers_no_faster_than_twice_a_second() -> None:
    chip = _FakeChip(monitor=_monitor({0: 900}, {}))
    now = [100.0]
    reader = _reader(chip, now)
    reader.sample()
    reads = len(chip.calls)
    reader.sample()
    assert len(chip.calls) == reads
    now[0] += 0.6
    reader.sample()
    assert len(chip.calls) > reads


def test_every_sample_holds_the_isa_bus() -> None:
    """The first sample finds the chip under the mutex too; a reused sample
    touches nothing and needs no bus."""
    chip = _FakeChip(monitor=_monitor({0: 900}, {}))
    now, bus = [100.0], _Bus()
    reader = _reader(chip, now, bus)
    reader.sample()
    reader.sample()
    now[0] += 0.6
    reader.sample()
    assert bus.held == 2


def test_only_spinning_fans_and_connected_inputs_are_offered() -> None:
    """An unconnected input reads -128 or 0; a fan with no header reads 0."""
    chip = _FakeChip(monitor=_monitor({0: 1250, 3: 640},
                                      {"SYSTIN": 34, "CPUTIN": 41, "AUXTIN0": -128}))
    fans, temps = sio.discover_superio(_reader(chip).sample)
    assert [(f.key, f.name, f.rpm()) for f in fans] == [
        ("superio:fan1", "NCT6798D Fan 1", 1250), ("superio:fan4", "NCT6798D Fan 4", 640)]
    assert [(t.key, t.name, t.temp()) for t in temps] == [
        ("superio:systin", "NCT6798D SYSTIN", 34.0), ("superio:cputin", "NCT6798D CPUTIN", 41.0)]


def test_no_reader_or_no_chip_is_no_sources() -> None:
    assert sio.discover_superio(None) == ([], [])
    assert sio.discover_superio(_reader(_FakeChip(slot_ids={})).sample) == ([], [])


def test_a_busy_bus_is_a_tick_without_readings() -> None:
    def busy() -> dict[str, Any]:
        raise OSError("the ISA bus is busy")

    assert sio.SuperIoBoard(busy).latest() == {}


# ── ITE ──────────────────────────────────────────────────────────────────────

_EC1, _EC2 = 0xA40, 0xA60


class _IteChip:
    """One ITE chip: its own key machine (``87 01 55 55``, ``... AA`` at
    0x4E), config space by logical device, and a banked environment
    controller whose data port must never be written."""

    def __init__(self, chip_id: int, *, base: int = _EC1, preentered: bool = False,
                 active: int = 1, ec: dict[int, int] | None = None, bank: int = 0,
                 echoes: bool = True, moves: frozenset[int] = frozenset()) -> None:
        self.chip_id, self.base = chip_id, base
        self.in_config, self.progress = preentered, 0
        self.keyed = self.exits = 0
        self.ldn, self.cfg_index = 0, 0
        self.ldn_regs = {4: {0x30: active, 0x60: base >> 8, 0x61: base & 0xFF, 0xF3: 0x06}}
        self.globals = {0x22: 0x02, 0x24: 0x00}
        self.config_writes: list[tuple[int, int]] = []
        self.ec = dict(ec or {})
        self.ec[0x06] = bank << 5
        self.banks: dict[int, dict[int, int]] = {}
        self.ec_index = 0
        self.echoes, self.moves = echoes, moves
        self.broken = False                            # reads fail as a dead bus does

    def key(self, port: int, value: int) -> None:
        expected = (0x87, 0x01, 0x55, 0x55 if port == 0x2E else 0xAA)
        if value == expected[self.progress]:
            self.progress += 1
            if self.progress == 4:
                self.in_config, self.progress = True, 0
                self.keyed += 1
        else:
            self.progress = 1 if value == 0x87 else 0

    def cfg(self, register: int) -> int:
        if not self.in_config:
            return 0xFF
        if register in (0x20, 0x21):
            return (self.chip_id >> 8) if register == 0x20 else self.chip_id & 0xFF
        if register == 0x07:
            return self.ldn
        if register < 0x30:
            return self.globals.get(register, 0x00)
        return self.ldn_regs.get(self.ldn, {}).get(register, 0xFF)

    def cfg_write(self, register: int, value: int) -> None:
        if register == 0x07:
            self.ldn = value
        elif register == 0x02 and value & 0x02:
            self.in_config = False
            self.exits += 1
        else:
            self.config_writes.append((register, value))

    def ec_read(self) -> int:
        if self.broken:
            raise OSError("STATUS_IO_DEVICE_ERROR")
        # Only FEAT_BANK_SEL chips swap 0x10-0xAF; elsewhere 0x06 is no bank.
        banked = sio.ITE_CHIPS[self.chip_id].banked if self.chip_id in sio.ITE_CHIPS else False
        bank = (self.ec[0x06] >> 5) & 0x3 if banked else 0
        index = self.ec_index
        value = (self.banks.get(bank, {}) if bank and 0x10 <= index <= 0xAF
                 else self.ec).get(index, 0x00)
        if index in self.moves:
            self.ec_index = 0xFF                       # another agent's index write
        return value


class _IteBoard:
    """The LPC bus of a board with ITE chips at 0x2E and/or 0x4E."""

    def __init__(self, chips: dict[int, _IteChip], opens: int = 2) -> None:
        self.chips, self.opens = chips, opens
        self.calls: list[tuple[str, list[int]]] = []
        self.opened = self.closed = 0

    def open(self) -> _Handle:
        if self.opened == self.opens:
            raise OSError("PawnIO: no more modules")
        self.opened += 1
        return _Handle(self)

    def _monitor(self, port: int) -> _IteChip | None:
        return next((c for c in self.chips.values() if c.base == port & 0xFFF8), None)

    def execute(self, handle: _Handle, function: str, inputs: list[int]) -> tuple[int, ...]:
        chip = self.chips.get(handle.port)
        match function:
            case "ioctl_find_bars":
                if chip is None or chip.cfg(0x20) in (0x00, 0xFF):
                    raise OSError("STATUS_NOT_FOUND")
                handle.bars.add(chip.base & 0xFFF8)
                chip.ldn = 0xFE                       # it walks every device select
                return ()
            case "ioctl_superio_inb":
                if chip is None:
                    return (0xFF,)
                if not chip.in_config:
                    chip.key(handle.port, inputs[0])  # an index write is a key byte
                return (chip.cfg(inputs[0]),)
            case "ioctl_superio_outb":
                if chip is not None:
                    if chip.in_config:
                        chip.cfg_write(inputs[0], inputs[1])
                    else:
                        chip.key(handle.port, inputs[0])
                return ()
            case "ioctl_pio_outb":
                port, value = inputs
                if port == handle.port:
                    if chip is not None:
                        if chip.in_config:
                            chip.cfg_index = value
                        else:
                            chip.key(port, value)
                elif port == handle.port + 1:
                    if chip is not None and chip.in_config:
                        chip.cfg_write(chip.cfg_index, value)
                elif (ec := self._monitor(port)) is not None and port == ec.base + 5:
                    ec.ec_index = value
                else:
                    raise AssertionError(f"wrote 0x{value:02x} to port 0x{port:04x}")
                return ()
            case "ioctl_pio_inb":
                ec = self._monitor(inputs[0])
                assert ec is not None, f"read port 0x{inputs[0]:04x}"
                if inputs[0] == ec.base + 6:
                    return (ec.ec_read(),)
                return (ec.ec_index if ec.echoes else 0x00,)
        raise AssertionError(f"unexpected {function}")


def _ec(*, fans: dict[int, int] | None = None, temps: dict[int, int] | None = None,
        main: int = 0x70, wide: int = 0x37, fan_div: int = 0x00, pins: int = 0x00,
        alt_fan6: bool = False, vendor: int = 0x90, config: int = 0x11) -> dict[int, int]:
    """An environment controller's bank 0: every fan counting 0xFFFF (stopped)
    unless *fans* gives its count; *temps* by input number 0-5."""
    regs = {0x00: config, 0x58: vendor, 0x13: main, 0x0C: wide, 0x0B: fan_div, 0x77: pins}
    for i in range(6):
        count = (fans or {}).get(i, 0xFFFF)
        low, high = (sio._IT_FAN6_ALT if alt_fan6 and i == 5
                     else (sio._IT_FAN[i], sio._IT_FANX[i]))
        regs[low], regs[high] = count & 0xFF, count >> 8
    for n, value in (temps or {}).items():
        regs[0x29 + n] = value & 0xFF
    return regs


def _count(rpm: int) -> int:
    return 1_350_000 // (2 * rpm)


def test_an_ite_sample_reads_what_linux_reads() -> None:
    """IT8686E: 16-bit counts as 1,350,000 / (2 x count), 0xFFFF a stopped
    fan; signed whole degrees; TMPIN4-6 only where register 0x77 makes
    the pin a temperature (here only TMPIN4)."""
    chip = _IteChip(0x8686, ec=_ec(fans={0: _count(1500), 3: _count(750), 4: 0x0010},
                                   temps={0: 34, 1: -128, 2: 127, 3: 41, 4: 55}, pins=0b000110,
                                   wide=0x37))
    sample = _reader(_IteBoard({0x2E: chip})).sample()
    assert sample == {"chips": [{
        "slot": 0, "chip": "IT8686E", "fans": [1500, 0, 0, 750, None, 0],
        "temps": {"TMPIN1": 34, "TMPIN2": -128, "TMPIN3": 127, "TMPIN4": 41}}]}


@pytest.mark.parametrize(("count", "rpm"), [
    (0xFFFF, 0), (0x0000, None), (0x003F, None), (0x0040, 10546), (0x0546, 500)])
def test_a_tachometer_count_becomes_rpm(count: int, rpm: int | None) -> None:
    """Linux FAN16_FROM_REG, with LibreHardwareMonitor's floor of 0x40.

    MUTATION CHECK: drop the 0x40 floor -- 0x3f reads 10714."""
    assert sio._rpm(count & 0xFF, count >> 8) == rpm


def _gigabyte() -> tuple[_IteBoard, _IteChip, _IteChip]:
    """IT8689E at 0x2E behind its key; IT87952E at 0x4E left in config mode
    by the BIOS, as Gigabyte boards ship."""
    first = _IteChip(0x8689, ec=_ec(fans={0: _count(1250)}, temps={0: 30, 1: 45}))
    second = _IteChip(0x8695, base=_EC2, preentered=True,
                      ec=_ec(fans={0: _count(900)}, temps={0: 52, 1: 60, 2: -128}))
    return _IteBoard({0x2E: first, 0x4E: second}), first, second


def test_every_ite_write_is_one_the_plan_allows() -> None:
    """Keys and exit on 0x2E/0x2F, device 4's select, the monitor's index
    register -- and nothing else: no monitor data, no bank, no config
    register, nothing at all on 0x4E, no port outside the two chips'.

    MUTATION CHECK: exit a NOCONF chip in Ite.leave -- the IT87952E exits."""
    board, first, second = _gigabyte()
    reader = _reader(board)
    reader.sample()
    reader.sample()
    reads = {0x00, 0x06, 0x0B, 0x0C, 0x13, 0x58, 0x77, *range(0x29, 0x2F),
             *sio._IT_FAN, *sio._IT_FANX, *sio._IT_FAN6_ALT}
    allowed_pio = {(0x2E, v) for v in (0x87, 0x01, 0x55, 0xAA, 0x02)} | {(0x2F, 0x02)}
    allowed_pio |= {(base + 5, r) for base in (_EC1, _EC2) for r in reads}
    stray = [(f, i) for f, i in _writes(board)
             if not (f == "ioctl_superio_outb" and i == [0x07, 0x04])
             and not (f == "ioctl_pio_outb" and tuple(i) in allowed_pio)]
    assert not stray, stray
    assert (first.keyed, first.exits, first.in_config) == (1, 1, False)
    assert (second.keyed, second.exits, second.in_config) == (0, 0, True)
    assert first.config_writes == second.config_writes == []


def test_a_gigabyte_pair_is_read_from_both_slots() -> None:
    """Two modules, one per slot (selecting a slot forgets the other's
    ports); the IT87952E's fans are not read -- Gigabyte SmartFan keeps
    them in H2RAM -- but its inputs are, under keys of their own.

    MUTATION CHECK: read fans_in_h2ram chips' fans -- superio2:fan1 offered."""
    board, _, _ = _gigabyte()
    reader = _reader(board)
    fans, temps = sio.discover_superio(reader.sample)
    assert [(f.key, f.name, f.rpm()) for f in fans] == [("superio:fan1", "IT8689E Fan 1", 1250)]
    assert [(t.key, t.name, t.temp()) for t in temps] == [
        ("superio:tmpin1", "IT8689E TMPIN1", 30.0), ("superio:tmpin2", "IT8689E TMPIN2", 45.0),
        ("superio2:tmpin1", "IT87952E TMPIN1", 52.0), ("superio2:tmpin2", "IT87952E TMPIN2", 60.0)]
    assert board.opened == 2
    reader.close()
    assert board.closed == 2


def test_a_noconf_chip_entered_by_its_key_is_never_taken_out() -> None:
    """An IT8792E that did not answer before the key is keyed (as Linux and
    LHM do) -- and then left in config mode, never exited.

    MUTATION CHECK: drop the noconf test in Ite.leave -- exits == 1."""
    first = _IteChip(0x8686, ec=_ec(temps={0: 30}))
    second = _IteChip(0x8733, base=_EC2, ec=_ec(temps={0: 40}))
    sample = _reader(_IteBoard({0x2E: first, 0x4E: second})).sample()
    assert [c["chip"] for c in sample["chips"]] == ["IT8686E", "IT8792E"]
    assert (second.keyed, second.exits, second.in_config) == (1, 0, True)


def test_an_unknown_chip_answering_before_a_key_is_left_alone() -> None:
    """LHM: an IT8792E already entered and keyed again reports 0x8883; a chip
    that answers before any key with an id TRCC does not know gets no key.

    MUTATION CHECK: fall through to the keys when the pre-read id is unknown --
    0x87 is written on 0x4E."""
    first = _IteChip(0x8686, ec=_ec(temps={0: 30}))
    stray = _IteChip(0x8883, base=_EC2, preentered=True)
    board = _IteBoard({0x2E: first, 0x4E: stray})
    sample = _reader(board).sample()
    assert [c["chip"] for c in sample["chips"]] == ["IT8686E"]
    assert not [i for f, i in _writes(board) if f == "ioctl_pio_outb" and i[0] in (0x4E, 0x4F)]
    assert (stray.keyed, stray.exits) == (0, 0)


def test_a_second_chip_on_the_first_ones_address_is_dropped() -> None:
    """Linux it87: "Don't register second chip if its ISA address matches".

    MUTATION CHECK: drop the address comparison in _find -- two chips."""
    first = _IteChip(0x8686, ec=_ec(temps={0: 30}))
    echo = _IteChip(0x8733, base=_EC1, preentered=True)
    sample = _reader(_IteBoard({0x2E: first, 0x4E: echo})).sample()
    assert [c["chip"] for c in sample["chips"]] == ["IT8686E"]


def test_another_bank_reads_nothing_and_is_not_switched_back() -> None:
    """A banked chip left on bank 2 by another program: nothing this tick, no
    write to the bank register; bank 0 again, and it reads.

    MUTATION CHECK: drop the bank test in Ite.measure -- bank-2 values read."""
    chip = _IteChip(0x8686, ec=_ec(fans={0: _count(1000)}, temps={0: 33}), bank=2)
    chip.banks[2] = {0x0D: 0x11, 0x29: 99}
    now = [0.0]
    reader = _reader(_IteBoard({0x2E: chip}), now)
    assert reader.sample() == {"chips": [{
        "slot": 0, "chip": "IT8686E",
        "reason": "IT8686E's monitor is on bank 2 -- another program switched it; "
                  "TRCC reads bank 0 only and does not switch it back"}]}
    assert chip.ec[0x06] == 0x40
    chip.ec[0x06] = 0x00
    now[0] += 1
    assert reader.sample()["chips"][0]["temps"]["TMPIN1"] == 33


def test_an_unbanked_chip_ignores_register_0x06() -> None:
    """Bank select is FEAT_BANK_SEL's; an IT8728F's 0x06 is not a bank."""
    chip = _IteChip(0x8728, ec=_ec(temps={0: 33}), bank=3)
    assert _reader(_IteBoard({0x2E: chip})).sample()["chips"][0]["temps"]["TMPIN1"] == 33


def test_only_the_tachometers_the_bios_switched_on_are_read() -> None:
    """Fans 1-3 by register 0x13 bits 4-6, fans 4-5 by 0x0C bits 4-5, fan 6
    by 0x0C bit 2 -- or, on IT8665E, by 0x0B bit 3 with its count at
    0x93/0x94.  Nothing is switched on.

    MUTATION CHECK: ignore 0x13 in _fans_enabled -- fan 3 reads 500; take
    IT8665E's fan 6 enable from 0x0C or its count from 0x4C -- fan 6 None."""
    rpm500 = {i: _count(500) for i in range(6)}
    it8665 = _IteChip(0x8665, ec=_ec(fans=rpm500, main=0x30, wide=0x10, fan_div=0x08,
                                     alt_fan6=True))
    assert _reader(_IteBoard({0x2E: it8665})).sample()["chips"][0]["fans"] == [
        500, 500, None, 500, None, 500]
    it8628 = _IteChip(0x8628, ec=_ec(fans=rpm500, main=0x70, wide=0x30, fan_div=0x08))
    assert _reader(_IteBoard({0x2E: it8628})).sample()["chips"][0]["fans"] == [
        500, 500, 500, 500, 500, None]
    assert (it8665.ec[0x13], it8665.ec[0x0C], it8628.ec[0x0C]) == (0x30, 0x10, 0x30)


def test_a_fan_counting_in_8_bits_is_not_read() -> None:
    """IT8721F counts fans 1-3 in 16 bits only once 0x0C bits 0-2 are set --
    a write Linux makes and TRCC does not.

    MUTATION CHECK: drop the fan16_config test -- fan 2 reads 500."""
    chip = _IteChip(0x8721, ec=_ec(fans={i: _count(500) for i in range(5)},
                                   main=0x70, wide=0x35))
    assert _reader(_IteBoard({0x2E: chip})).sample()["chips"][0]["fans"] == [
        500, None, 500, 500, 500]


@pytest.mark.parametrize(("config", "vendor"), [(0x11, 0x7F), (0x91, 0x90)])
def test_a_monitor_that_does_not_answer_as_ite_reads_nothing(config: int, vendor: int) -> None:
    """Linux it87_probe: config bit 7 clear and register 0x58 = 0x90.

    MUTATION CHECK: drop the vendor test -- the 0x7F case reads."""
    chip = _IteChip(0x8628, ec=_ec(temps={0: 33}, config=config, vendor=vendor))
    entry = _reader(_IteBoard({0x2E: chip})).sample()["chips"][0]
    assert entry["reason"].startswith("IT8628E's monitor does not answer as an ITE one")


def test_a_register_moved_under_the_read_is_no_reading() -> None:
    """Another agent's index write between ours and the data read: that
    value is dropped.  IT8688E does not echo its index, so it is trusted.

    MUTATION CHECK: drop the echo check in _IteMonitor.read -- TMPIN1 reads."""
    moved = _IteChip(0x8628, ec=_ec(temps={0: 33, 1: 44}), moves=frozenset({0x29}))
    temps = _reader(_IteBoard({0x2E: moved})).sample()["chips"][0]["temps"]
    assert (temps["TMPIN1"], temps["TMPIN2"]) == (None, 44)
    quiet = _IteChip(0x8688, ec=_ec(temps={0: 33}), echoes=False)
    board = _IteBoard({0x2E: quiet})
    assert _reader(board).sample()["chips"][0]["temps"]["TMPIN1"] == 33
    assert ("ioctl_pio_inb", [_EC1 + 5]) not in board.calls


def test_a_switched_off_ite_monitor_reads_nothing() -> None:
    """Linux: "Device ... not activated, skipping".  Never switched on.

    MUTATION CHECK: drop the active test in Ite.identify -- a chip is read."""
    chip = _IteChip(0x8689, active=0)
    assert _reader(_IteBoard({0x2E: chip})).sample() == {
        "chips": [], "reason": "IT8689E's hardware monitor is switched off; "
                               "no Super I/O chip at 0x4e"}
    assert chip.ldn_regs[4][0x30] == 0 and chip.exits == 1


def test_an_empty_0x4e_gets_the_keys_and_no_exit() -> None:
    """Beside an ITE at 0x2E, 0x4E is keyed (Nuvoton's, then ITE's), and
    where nothing answers no exit follows -- nothing entered.

    MUTATION CHECK: drop the no-chip test in Ite.leave -- 0x02 on 0x4E/0x4F."""
    board = _IteBoard({0x2E: _IteChip(0x8628, ec=_ec(temps={0: 30}))})
    _reader(board).sample()
    on_4e = [tuple(i) for f, i in _writes(board) if f == "ioctl_pio_outb" and i[0] in (0x4E, 0x4F)]
    assert on_4e == [(0x4E, v) for v in (0x87, 0x87, 0xAA, 0x87, 0x01, 0x55, 0xAA)]


def test_a_second_slot_that_fails_costs_nothing_on_the_first() -> None:
    """LpcIO refusing a second module: the 0x2E chip is still read; with no
    chip found at all, the failure is raised so the next sample asks again.

    MUTATION CHECK: drop the per-slot OSError catch in _find -- sample raises."""
    board = _IteBoard({0x2E: _IteChip(0x8628, ec=_ec(temps={0: 30}))}, opens=1)
    assert [c["chip"] for c in _reader(board).sample()["chips"]] == ["IT8628E"]
    empty = _IteBoard({}, opens=1)
    with pytest.raises(OSError, match="no more modules"):
        _reader(empty).sample()


def test_a_chip_that_fails_to_read_costs_nothing_on_the_other() -> None:
    """MUTATION CHECK: drop the OSError catch in _measure -- sample raises."""
    board, first, second = _gigabyte()
    second.broken = True
    chips = _reader(board).sample()["chips"]
    assert chips[0]["temps"]["TMPIN1"] == 30
    assert chips[1] == {"slot": 1, "chip": "IT87952E",
                        "reason": "IT87952E could not be read: STATUS_IO_DEVICE_ERROR"}


def test_implausible_ite_inputs_are_not_offered() -> None:
    chip = _IteChip(0x8655, ec=_ec(temps={0: 0, 1: -128, 2: 127, 3: 126, 4: 125, 5: 1}))
    _, temps = sio.discover_superio(_reader(_IteBoard({0x2E: chip})).sample)
    assert [t.key for t in temps] == ["superio:tmpin5", "superio:tmpin6"]


# ── the service ──────────────────────────────────────────────────────────


def _broker(open_superio: Any = None) -> PrivilegedBroker:
    def nothing(*_: Any) -> Any:
        raise AssertionError("not this")

    return PrivilegedBroker(version="t", find_drive=nothing, open_scsi=nothing,
                            open_module=nothing, open_superio=open_superio)


def test_the_service_samples_with_its_own_reader_and_closes_it() -> None:
    chip = _FakeChip(monitor=_monitor({0: 1100}, {"SYSTIN": 33}))
    broker = _broker(lambda: _reader(chip))
    session = Session()
    reply, _ = broker.handle(session, {"op": "superio.sample"}, b"")
    assert reply["ok"] and reply["sample"]["chips"][0]["fans"][0] == 1100
    session.close()
    assert chip.calls[-1] == ("close", [])


def test_a_client_cannot_steer_the_super_io() -> None:
    """No arguments are read, and LpcIO is not a module a client may load."""
    chip = _FakeChip(monitor=_monitor({0: 1100}, {}))
    broker = _broker(lambda: _reader(chip))
    session = Session()
    reply, _ = broker.handle(session, {"op": "superio.sample", "port": 0x2E,
                                       "register": 0x28, "value": 0}, b"")
    assert reply["ok"] and chip.config[0x0B][0x28] == 0
    loaded, _ = broker.handle(session, {"op": "pawnio.load", "module": "LpcIO"}, b"")
    assert loaded == {"ok": False, "refused": True, "error": "'LpcIO' is not a module TRCC ships"}


def test_a_service_without_the_reader_refuses() -> None:
    reply, _ = _broker().handle(Session(), {"op": "superio.sample"}, b"")
    assert reply["refused"] is True


def test_windows_sensors_offer_the_board(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_build_windows_gpu_chains", "_build_windows_disks"):
        monkeypatch.setattr(win, name, lambda: [])
    chip = _FakeChip(monitor=_monitor({1: 1400}, {"CPUTIN": 45}))
    sensors = win.build_windows_sensors(superio=_reader(chip).sample)
    listed = {r.sensor_id for r in sensors.discover()}
    assert {"fan:superio:fan2:rpm", "board:superio:cputin:temp"} <= listed
    values = sensors.read_all()
    assert (values["fan:superio:fan2:rpm"], values["board:superio:cputin:temp"]) == (1400, 45.0)
