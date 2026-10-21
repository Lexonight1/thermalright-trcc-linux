"""W4: motherboard fans and inputs on Windows, from the Nuvoton Super I/O.

Nothing here can reach a real chip -- the win11 VM has none -- so a fake chip
stands in, built to behave as the hardware and PawnIO's ``LpcIO`` module do:
config space per logical device behind ``0x87 0x87``, a banked monitor behind
base+5/base+6, and LpcIO's port whitelist (a monitor port is refused until
``find_bars`` has seen it).  Register facts are Linux ``nct6775``'s.

The test that matters most is the WRITE list: every byte the reader puts on
the bus is one the plan allows, and the I/O-space lock is never touched.
"""
from __future__ import annotations

from typing import Any

import pytest

from trcc.adapters.sensors import _superio as sio
from trcc.adapters.sensors import windows as win
from trcc.adapters.system._service_broker import PrivilegedBroker, Session

_BASE = 0x290


class _FakeChip:
    """A Nuvoton chip behind PawnIO's LpcIO, recording every call."""

    def __init__(self, *, slot_ids: dict[int, int] | None = None, base: int = _BASE,
                 active: int = 1, lock: int = 0, monitor: dict[int, int] | None = None,
                 already_entered: frozenset[int] = frozenset()) -> None:
        self.slot_ids = slot_ids if slot_ids is not None else {0: 0xD42B}
        #: Slots whose chip is in config mode from power-on (Gigabyte's IT8792E).
        self.already_entered = set(already_entered)
        #: Slots a config-mode key was ever written to -- the Gigabyte hazard.
        self.keyed: list[tuple[int, int]] = []
        self.config = {0x0B: {0x60: base >> 8, 0x61: base & 0xFF, 0x30: active, 0x28: lock}}
        self.monitor = monitor or {}
        self.calls: list[tuple[str, list[int]]] = []
        self.slot: int | None = None
        self.port = 0
        self.entered = 0
        self.index = 0
        self.device = 0
        self.bars: set[int] = set()
        self.base = base
        self.hwm_index = 0
        self.bank = 0

    @property
    def chip_id(self) -> int:
        """What CR20/21 answer: 0xFFFF unless the chip is in config mode.  Only
        a Nuvoton enters on 0x87 0x87 (an ITE's key is 0x87 0x01 0x55 0x55)."""
        real = self.slot_ids.get(self.slot if self.slot is not None else -1, 0xFFFF)
        nuvoton = (real & sio._ID_MASK) in sio.CHIPS
        in_config = self.slot in self.already_entered or (nuvoton and self.entered >= 2)
        return real if in_config else 0xFFFF

    def _cfg_read(self, register: int) -> int:
        if register == 0x20:
            return self.chip_id >> 8
        if register == 0x21:
            return self.chip_id & 0xFF
        if register == 0x07:
            return self.device
        return self.config.get(self.device, {}).get(register, 0xFF)

    def _allowed(self, port: int) -> bool:
        return port in (self.port, self.port + 1) or (port & 0xFFF8) in self.bars

    def execute(self, function: str, inputs: list[int], outputs: int) -> tuple[int, ...]:
        self.calls.append((function, list(inputs)))
        match function:
            case "ioctl_select_slot":
                self.slot, self.port, self.bars = inputs[0], sio._SLOTS[inputs[0]], set()
                self.entered = 0
                return ()
            case "ioctl_find_bars":
                if self.chip_id in (0, 0xFFFF):
                    raise OSError("STATUS_NOT_FOUND")
                self.bars.add(self.base & 0xFFF8)
                self.device = 0xFE                   # it walks every device select
                return ()
            case "ioctl_superio_inb":
                return (self._cfg_read(inputs[0]),)
            case "ioctl_superio_outb":
                if inputs[0] == 0x07:
                    self.device = inputs[1]
                else:
                    self.config.setdefault(self.device, {})[inputs[0]] = inputs[1]
                return ()
            case "ioctl_pio_outb":
                port, value = inputs
                if not self._allowed(port):
                    raise OSError("STATUS_ACCESS_DENIED")
                if port == self.port:
                    self.keyed.append((port, value))
                    self.entered = self.entered + 1 if value == 0x87 else 0
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
                if not self._allowed(inputs[0]):
                    raise OSError("STATUS_ACCESS_DENIED")
                return (self.monitor.get((self.bank << 8) | self.hwm_index, 0),)
        raise AssertionError(f"unexpected {function}")

    def close(self) -> None:
        self.calls.append(("close", []))


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
        from contextlib import contextmanager

        @contextmanager
        def held() -> Any:
            self.held += 1
            yield
        return held()


def _reader(chip: _FakeChip, now: list[float] | None = None) -> sio.SuperIoReader:
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return sio.SuperIoReader(chip, _Bus(), clock=clock)


def test_a_sample_reads_the_fans_and_inputs_linux_reads() -> None:
    """NCT6798D (0xD42B masked to 0xD428); RPM is the register itself;
    inputs are one signed byte of whole degrees."""
    chip = _FakeChip(monitor=_monitor({0: 1250, 1: 0, 6: 812},
                                      {"SYSTIN": 34, "CPUTIN": 41, "AUXTIN0": -128}))
    sample = _reader(chip).sample()
    assert sample["chip"] == "NCT6798D"
    assert sample["fans"] == [1250, 0, 0, 0, 0, 0, 812]
    assert sample["temps"] == {"SYSTIN": 34, "CPUTIN": 41, "AUXTIN0": -128,
                               "AUXTIN1": 0, "AUXTIN2": 0, "AUXTIN3": 0}


def test_every_write_is_one_the_plan_allows() -> None:
    """Config enter/exit on the index port, the monitor's device select, the
    bank and index registers -- and nothing else.  Never config 0x28.

    MUTATION CHECK: clear the lock (superio_outb 0x28) in _identify -- listed."""
    chip = _FakeChip(monitor=_monitor({0: 900}, {"SYSTIN": 30}))
    reader = _reader(chip)
    reader.sample()
    reader.sample()
    writes = [(f, i) for f, i in chip.calls if f in ("ioctl_pio_outb", "ioctl_superio_outb")]
    allowed_pio = {(0x2E, 0x87), (0x2E, 0xAA), (_BASE + 5, 0x4E), (_BASE + 6, 4), (_BASE + 6, 0)}
    allowed_index = {r & 0xFF for fan in sio.FAN_REGISTERS for r in (fan, fan + 1)} | {
        r & 0xFF for r in sio.TEMP_REGISTERS.values()}
    stray = [(f, i) for f, i in writes
             if not (f == "ioctl_superio_outb" and i == [0x07, 0x0B])
             and not (f == "ioctl_pio_outb" and tuple(i) in allowed_pio)
             and not (f == "ioctl_pio_outb" and i[0] == _BASE + 5 and i[1] in allowed_index)]
    assert not stray, stray
    assert chip.bank == 0, "bank 0 was not selected again"


def test_a_locked_monitor_is_reported_and_left_locked() -> None:
    """LHM and Linux clear 0x28 bit 4; TRCC reads nothing instead."""
    chip = _FakeChip(lock=0x10, monitor=_monitor({0: 900}, {}))
    sample = _reader(chip).sample()
    assert sample == {"chip": None, "reason": "NCT6798D's hardware monitor is locked "
                                              "by the BIOS (TRCC does not clear it)"}
    assert chip.config[0x0B][0x28] == 0x10
    assert not any(f == "ioctl_pio_inb" for f, _ in chip.calls)


def test_a_switched_off_monitor_reads_nothing() -> None:
    chip = _FakeChip(active=0)
    assert _reader(chip).sample() == {
        "chip": None, "reason": "NCT6798D's hardware monitor is switched off"}
    assert chip.config[0x0B][0x30] == 0                # never forced on


def test_the_chip_may_sit_at_0x4e() -> None:
    chip = _FakeChip(slot_ids={0: 0xFFFF, 1: 0xC803})
    assert _reader(chip).sample()["chip"] == "NCT6791D"


def test_a_known_chip_that_cannot_be_read_ends_the_search() -> None:
    """The second slot's "no chip" must not replace the first slot's reason.

    MUTATION CHECK: continue to the next slot -- the reason names 0x4e."""
    chip = _FakeChip(slot_ids={0: 0xD42B, 1: 0xFFFF}, lock=0x10)
    assert "locked by the BIOS" in _reader(chip).sample()["reason"]
    assert ("ioctl_select_slot", [1]) not in chip.calls


def test_a_chip_already_in_config_mode_is_never_entered_or_exited() -> None:
    """Gigabyte: an ITE IT8686E at 0x2E (not keyed by Nuvoton's 0x87 0x87) and
    an IT8792E at 0x4E left in config mode from power-on.  Linux it87.c:
    "entering and/or exiting configuration mode is what causes the crash".

    MUTATION CHECK: drop the read-before-enter step -- 0x4E is keyed."""
    chip = _FakeChip(slot_ids={0: 0x8686, 1: 0x8733}, already_entered=frozenset({1}))
    sample = _reader(chip).sample()
    assert sample == {"chip": None, "reason": "chip 0x8733 at 0x4e is already in config mode "
                                              "and is not one TRCC reads -- left alone"}
    assert [k for k in chip.keyed if k[0] == 0x4E] == []
    assert not any(f == "ioctl_find_bars" for f, _ in chip.calls)


def test_a_nuvoton_already_in_config_mode_is_read_as_found() -> None:
    """Entered by the BIOS or another tool: read without writing a key or 0xAA."""
    chip = _FakeChip(already_entered=frozenset({0}), monitor=_monitor({0: 700}, {}))
    assert _reader(chip).sample()["fans"][0] == 700
    assert chip.keyed == []


def test_another_vendors_chip_is_not_read() -> None:
    chip = _FakeChip(slot_ids={0: 0x8628, 1: 0xFFFF})          # an ITE IT8628E
    sample = _reader(chip).sample()
    assert sample["chip"] is None
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
    assert reply["ok"] and reply["sample"]["fans"][0] == 1100
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
