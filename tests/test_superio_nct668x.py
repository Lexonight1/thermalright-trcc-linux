"""Board fans and temperatures from a Nuvoton NCT6683D / NCT6686D / NCT6687D.

No real chip is reachable here, so a fake stands in.  It behaves as the
hardware and PawnIO's ``LpcIO`` module do:

* config space only answers after ``0x87 0x87``;
* a monitor port is refused until ``find_bars`` has seen its BAR, as in
  LpcIO's whitelist;
* the EC window at base+4/5/6 is paged.  The page port reads ``0xFF`` while
  the EC is free and reads another page while something else holds it.  The
  data port only reads.

Register facts are Linux ``nct6683``'s; the handshake is LibreHardwareMonitor's.

The test that matters most is the WRITE list.  Every byte the reader puts on the
bus is one the plan allows, and a busy EC is never forced.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.sensors import _nct668x as ec
from trcc.adapters.sensors import _superio as sio
from trcc.adapters.sensors import windows as win
from trcc.adapters.system._service_broker import PrivilegedBroker, Session

_BASE = 0xA20
_PAGE, _INDEX, _DATA = _BASE + 4, _BASE + 5, _BASE + 6
_OTHER_PAGE = 0x06          # what the page port reads while another accessor holds it


class _FakeEc:
    """An NCT668x behind PawnIO's LpcIO, recording every call."""

    def __init__(self, *, chip_id: int = 0xD592, slot: int = 0, base: int = _BASE,
                 active: int = 1, customer: int = 0x201, hwm_cfg: int = 0x80,
                 fans: dict[int, int] | None = None,
                 temps: dict[int, tuple[int, int]] | None = None, busy: int = 0) -> None:
        self.slot_ids = {slot: chip_id}
        self.base = base
        self.config = {0x0B: {0x60: base >> 8, 0x61: base & 0xFF, 0x30: active}}
        self.ec = {0x602: customer >> 8, 0x603: customer & 0xFF, 0x180: hwm_cfg,
                   0x604: 20, 0x605: 5, 0x606: 7, 0x608: 1, 0x609: 0}
        for i, rpm in (fans or {}).items():
            self.ec[ec.FANIN_CFG + i] = 0xA0                 # present, 2 pulses
            self.ec[ec.FAN_RPM + 2 * i], self.ec[ec.FAN_RPM + 2 * i + 1] = rpm >> 8, rpm & 0xFF
        for slot_, (source, word) in (temps or {}).items():
            self.ec[ec.MON_CFG + slot_] = source
            self.ec[ec.MON + 2 * slot_], self.ec[ec.MON + 2 * slot_ + 1] = word >> 8, word & 0xFF
        self.busy = busy
        self.calls: list[tuple[str, list[int]]] = []
        self.ec_reads: list[int] = []
        self.slot: int | None = None
        self.port = 0
        self.entered = 0
        self.device = 0
        self.bars: set[int] = set()
        self.page = 0xFF
        self.index = 0

    @property
    def chip_id(self) -> int:
        return self.slot_ids.get(self.slot if self.slot is not None else -1, 0xFFFF)

    def _cfg_read(self, register: int) -> int:
        if self.entered < 2 or self.chip_id == 0xFFFF:
            return 0xFF                     # no chip, or not in config mode
        if register in (0x20, 0x21):
            return (self.chip_id >> 8) if register == 0x20 else self.chip_id & 0xFF
        if register == 0x07:
            return self.device
        return self.config.get(self.device, {}).get(register, 0xFF)

    def _allowed(self, port: int) -> bool:
        return (port in (self.port, self.port + 1, 0x25C, 0x25D)
                or (port & 0xFFF8) in self.bars)

    def execute(self, function: str, inputs: list[int], outputs: int) -> tuple[int, ...]:
        self.calls.append((function, list(inputs)))
        match function:
            case "ioctl_select_slot":
                self.slot, self.port, self.bars = inputs[0], sio._SLOTS[inputs[0]], set()
                return ()
            case "ioctl_find_bars":
                if self._cfg_read(0x20) in (0x00, 0xFF):
                    raise OSError("STATUS_NOT_FOUND")
                if self.base >= 0x100:                       # check_and_add_bar
                    self.bars.add(self.base & 0xFFF8)
                self.device = 0xFE                           # it walks every select
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
                return self._outb(*inputs)
            case "ioctl_pio_inb":
                return (self._inb(inputs[0]),)
        raise AssertionError(f"unexpected {function}")

    def _outb(self, port: int, value: int) -> tuple[int, ...]:
        if not self._allowed(port):
            raise OSError("STATUS_ACCESS_DENIED")
        if port == self.port:
            # Nuvoton's key and exit, and ITE's key bytes: the reader tries
            # ITE's key on a port where no Nuvoton answered.
            if value not in (0x87, 0xAA, 0x01, 0x55):
                raise AssertionError(f"wrote 0x{value:02x} to the config index port")
            self.entered = self.entered + 1 if value == 0x87 else 0
        elif port == self.base + 4:
            self.page = value
        elif port == self.base + 5:
            self.index = value
        else:
            raise AssertionError(f"wrote 0x{value:02x} to port 0x{port:04x} "
                                 f"(EC register 0x{self.page:02x}{self.index:02x})")
        return ()

    def _inb(self, port: int) -> int:
        if not self._allowed(port):
            raise OSError("STATUS_ACCESS_DENIED")
        if port == self.base + 4:
            if self.busy:
                self.busy -= 1
                return _OTHER_PAGE
            return self.page
        if port == self.base + 6:
            assert self.page != 0xFF, "read the data port with no page selected"
            register = (self.page << 8) | self.index
            self.ec_reads.append(register)
            return self.ec.get(register, 0)
        raise AssertionError(f"read port 0x{port:04x}")

    def close(self) -> None:
        self.calls.append(("close", []))


class _Time:
    """A clock that only moves when the reader sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps = 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


class _Bus:
    def __init__(self) -> None:
        self.held = 0

    @contextmanager
    def __call__(self) -> Any:
        self.held += 1
        yield


@pytest.fixture(autouse=True)
def _ec_time_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_reader`` stands a fake clock in for ``_nct668x.time``; this puts the
    real one back after each test."""
    monkeypatch.setattr(ec, "time", ec.time)


def _reader(chip: _FakeEc, time: _Time | None = None) -> sio.SuperIoReader:
    time = time or _Time()
    # The EC monitor looks ``time`` up when it is made, during the first sample.
    ec.__dict__["time"] = SimpleNamespace(monotonic=time.clock, sleep=time.sleep)
    return sio.SuperIoReader(lambda: chip, _Bus(), clock=time.clock)


def _one(sample: dict[str, Any]) -> dict[str, Any]:
    """The one chip's entry, as these tests were written against: a chip that
    cannot be read is ``{"chip": None, "reason": ...}``."""
    chips = sample["chips"]
    if not chips:
        return {"chip": None, "reason": sample["reason"]}
    (entry,) = chips
    if "reason" in entry:
        return {"chip": None, "reason": entry["reason"]}
    return {k: v for k, v in entry.items() if k != "slot"}


def _word(celsius: float) -> int:
    """The monitor word for *celsius*: whole degrees high, half in bit 7."""
    raw = int(celsius * 2) << 7
    return raw & 0xFFFF


# ── detection ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("chip_id", "name"), [
    (0xC732, "NCT6683D"), (0xD441, "NCT6686D"), (0xD592, "NCT6687D"),
    (0xD59B, "NCT6687D")], ids=["0xc732", "0xd441", "0xd592", "0xd59b"])
def test_a_sample_reads_the_fans_and_temperatures_linux_reads(chip_id: int, name: str) -> None:
    """Chip IDs masked 0xFFF0, so any revision nibble -- 0xD59B is one LHM does
    not list; RPM is the register itself; a temperature is the high byte plus
    bit 7 of the low byte as a half degree.

    MUTATION CHECK: mask 0xFFF8 -- 0xD59B is not found."""
    chip = _FakeEc(chip_id=chip_id, fans={0: 1250, 1: 2100, 11: 870},
                   temps={0: (0x0B, _word(34.5)), 1: (0x0C, _word(31.0))})
    sample = _one(_reader(chip).sample())
    assert sample == {"chip": name,
                      "fans": [1250, 2100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 870, 0, 0, 0, 0],
                      "temps": {"Thermistor 0": 34.5, "Thermistor 1": 31.0}}


def test_the_chip_may_sit_at_0x4e() -> None:
    chip = _FakeEc(slot=1, fans={0: 900})
    assert _one(_reader(chip).sample())["chip"] == "NCT6687D"


def test_a_switched_off_monitor_reads_nothing() -> None:
    """Linux forces CR30 on ("Data may be unusable"); TRCC reads nothing."""
    chip = _FakeEc(active=0, fans={0: 900})
    assert _one(_reader(chip).sample()) == {
        "chip": None, "reason": "NCT6687D's hardware monitor is switched off"}
    assert chip.config[0x0B][0x30] == 0
    assert not any(f in ("ioctl_pio_inb", "ioctl_pio_outb") and i[0] >= 0x100
                   for f, i in chip.calls)


def test_an_unconfigured_base_reads_nothing() -> None:
    chip = _FakeEc(base=0)
    assert _one(_reader(chip).sample())["reason"] == "NCT6687D's hardware monitor is switched off"


def test_the_ec_is_read_only_after_config_mode_is_left() -> None:
    """Linux reads the EC in probe, after superio_exit."""
    chip = _FakeEc(fans={0: 900})
    _one(_reader(chip).sample())
    exits = [n for n, (f, i) in enumerate(chip.calls)
             if f == "ioctl_pio_outb" and i == [0x2E, 0xAA]]
    first_ec = next(n for n, (f, i) in enumerate(chip.calls)
                    if f.startswith("ioctl_pio") and i[0] >= _BASE)
    assert exits and exits[-1] < first_ec


# ── the write list ───────────────────────────────────────────────────────


def _ec_registers() -> set[int]:
    """Every EC register the plan reads."""
    return ({0x602, 0x603, 0x180, 0x604, 0x605, 0x606, 0x608, 0x609}
            | {ec.FANIN_CFG + i for i in range(ec.FAN_COUNT)}
            | {ec.MON_CFG + i for i in range(ec.MON_COUNT)}
            | {ec.FAN_RPM + i for i in range(2 * ec.FAN_COUNT)}
            | {ec.MON + i for i in range(2 * ec.MON_COUNT)})


def test_every_write_is_one_the_plan_allows() -> None:
    """Config enter/exit, the monitor's device select, the page port (0xFF or
    a page the plan reads), the index port -- nothing else.  The data port is
    never written (the fake raises), and neither is 0x180 nor CR30.

    MUTATION CHECK: start the monitor (write 0x180) -- the fake raises."""
    chip = _FakeEc(fans={0: 900, 5: 1200}, temps={0: (0x0B, _word(30)), 3: (0x20, _word(45))})
    reader = _reader(chip)
    _one(reader.sample())
    _one(reader.sample())
    registers = _ec_registers()
    allowed_pio = ({(0x2E, 0x87), (0x2E, 0xAA), (_PAGE, 0xFF)}
                   | {(_PAGE, r >> 8) for r in registers}
                   | {(_INDEX, r & 0xFF) for r in registers})
    writes = [(f, i) for f, i in chip.calls if f in ("ioctl_pio_outb", "ioctl_superio_outb")]
    stray = [(f, i) for f, i in writes
             if not (f == "ioctl_superio_outb" and i == [0x07, 0x0B])
             and not (f == "ioctl_pio_outb" and tuple(i) in allowed_pio)]
    assert not stray, stray
    assert set(chip.ec_reads) <= registers
    assert chip.ec[0x180] == 0x80 and chip.config[0x0B][0x30] == 1


def test_each_ec_byte_follows_the_handshake_and_frees_the_page_port() -> None:
    """inb page (free) -> 0xFF -> page -> index -> inb data -> 0xFF.

    MUTATION CHECK: drop the release write -- the pattern and the final page fail."""
    chip = _FakeEc(fans={0: 900})
    _one(_reader(chip).sample())
    ec_calls = [(f, i) for f, i in chip.calls
                if f.startswith("ioctl_pio") and i[0] in (_PAGE, _INDEX, _DATA)]
    assert len(ec_calls) % 6 == 0 and ec_calls
    for n in range(0, len(ec_calls), 6):
        (f0, i0), (f1, i1), (f2, i2), (f3, i3), (f4, i4), (f5, i5) = ec_calls[n:n + 6]
        assert (f0, i0) == ("ioctl_pio_inb", [_PAGE])
        assert (f1, i1) == ("ioctl_pio_outb", [_PAGE, 0xFF])
        assert f2 == "ioctl_pio_outb" and i2[0] == _PAGE and i2[1] != 0xFF
        assert f3 == "ioctl_pio_outb" and i3[0] == _INDEX
        assert (f4, i4) == ("ioctl_pio_inb", [_DATA])
        assert (f5, i5) == ("ioctl_pio_outb", [_PAGE, 0xFF])
    assert chip.page == 0xFF


# ── the EC handshake ─────────────────────────────────────────────────────


def test_a_busy_ec_is_waited_for() -> None:
    chip = _FakeEc(fans={0: 900}, busy=3)
    time = _Time()
    assert _one(_reader(chip, time).sample())["fans"][0] == 900
    assert time.sleeps == 3


def test_an_ec_that_stays_busy_is_skipped_never_forced() -> None:
    """LHM forces the page port after 500 ms; TRCC skips this sample.  The
    port is never written while another accessor holds it, and the busy
    sample is not kept as a refusal: the next one tries again.

    MUTATION CHECK: write 0xFF on timeout instead of raising -- it fails."""
    chip = _FakeEc(fans={0: 900}, busy=10**6)
    time = _Time()
    reader = _reader(chip, time)
    busy = _one(reader.sample())
    assert busy["chip"] is None and "does not force it" in busy["reason"]
    assert not any(f == "ioctl_pio_outb" and i[0] in (_PAGE, _INDEX) for f, i in chip.calls)
    time.now += 1.0
    assert "fans" not in sio.SuperIoBoard(reader.sample, clock=time.clock).chip(0)
    chip.busy = 0
    time.now += 1.0
    assert _one(reader.sample())["fans"][0] == 900


# ── the customer-ID gate ─────────────────────────────────────────────────


def test_a_customer_linux_does_not_list_is_not_read() -> None:
    """nct6683_probe returns -ENODEV for an unlisted customer ID unless
    'force' is set; TRCC reads the ID and nothing more.

    MUTATION CHECK: drop the gate -- the fans are read."""
    chip = _FakeEc(customer=0x1234, fans={0: 900})
    reader = _reader(chip)
    assert _one(reader.sample()) == {
        "chip": None, "reason": "NCT6687D's customer ID 0x1234 is not one Linux's "
                                "nct6683 reads without 'force' -- TRCC reads only those"}
    assert chip.ec_reads == [0x602, 0x603]
    reads = len(chip.calls)
    reader._at = -1e9                       # past the rate limit: the refusal is kept
    _one(reader.sample())
    assert len(chip.calls) == reads


@pytest.mark.parametrize("customer", sorted(ec.CUSTOMERS))
def test_every_customer_linux_lists_is_read(customer: int) -> None:
    chip = _FakeEc(customer=customer, fans={1: 1500})
    assert _one(_reader(chip).sample())["fans"][1] == 1500


def test_a_monitor_the_bios_did_not_start_is_not_started() -> None:
    """Linux and LHM set 0x180 bit 7; TRCC reads nothing instead."""
    chip = _FakeEc(hwm_cfg=0x00, fans={0: 900})
    assert _one(_reader(chip).sample()) == {
        "chip": None,
        "reason": "NCT6687D's hardware monitor is not started (TRCC does not start it)"}
    assert chip.ec[0x180] == 0x00
    assert chip.ec_reads == [0x602, 0x603, 0x180]


# ── conversion and layout ────────────────────────────────────────────────


@pytest.mark.parametrize(("word", "celsius"), [
    (0x2280, 34.5), (0x2200, 34.0), (0x0080, 0.5), (0x7D00, 125.0),
    (0x8000, -128.0), (0xFF00, -1.0), (0xFF80, -0.5), (0xFFFF, 0.0)])
def test_a_monitor_word_converts_as_linux_show_temp16(word: int, celsius: float) -> None:
    """(s16 / 128) * 500 millidegrees, truncated toward zero as C does."""
    assert ec.celsius(word) == celsius


def test_temperature_slots_follow_linux_source_mapping() -> None:
    """MON_CFG & 0x7F: below 0x60 with a label is a temperature; a voltage,
    a disabled slot and a reserved source are not.  A repeated source is
    numbered.

    MUTATION CHECK: take sources up to 0x7F -- the voltage slot appears."""
    chip = _FakeEc(temps={0: (0x01, _word(40)), 1: (0x61, 0x0123), 2: (0x00, 0),
                          3: (0x19, _word(50)), 4: (0xA0, _word(55)), 5: (0x01, _word(41))})
    assert _one(_reader(chip).sample())["temps"] == {"Local": 40.0, "PECI 0.0": 55.0,
                                               "Local #2": 41.0}


def test_a_fan_with_no_header_reads_zero_and_is_never_read() -> None:
    """FANIN_CFG bit 7 clear: Linux makes no fanN_input for it.

    MUTATION CHECK: read every fan -- 0x150 is read."""
    chip = _FakeEc(fans={0: 900})
    chip.ec[0x150], chip.ec[0x151] = 0x04, 0xB0              # a value, but no header
    assert _one(_reader(chip).sample())["fans"][8] == 0
    assert 0x150 not in chip.ec_reads


def test_only_spinning_fans_and_plausible_temperatures_are_offered() -> None:
    """A fan at 0 or 0xFFFF is not offered; nor is a temperature at or below
    0, or above 125."""
    chip = _FakeEc(fans={0: 1250, 2: 0, 3: 0xFFFF, 9: 640},
                   temps={0: (0x0B, _word(34.5)), 1: (0x0C, 0x8000), 2: (0x0D, 0),
                          3: (0x0E, _word(126)), 4: (0x0F, _word(125))})
    fans, temps = sio.discover_superio(_reader(chip).sample)
    assert [(f.key, f.name, f.rpm()) for f in fans] == [
        ("superio:fan1", "NCT6687D Fan 1", 1250), ("superio:fan10", "NCT6687D Fan 10", 640)]
    assert [(t.key, t.name, t.temp()) for t in temps] == [
        ("superio:thermistor_0", "NCT6687D Thermistor 0", 34.5),
        ("superio:thermistor_4", "NCT6687D Thermistor 4", 125.0)]


def test_key_of_keeps_the_nct679x_keys() -> None:
    assert [sio.key_of(x) for x in ("SYSTIN", "AUXTIN3", "PECI 0.1", "Diode 0 (curr)",
                                    "Local #2")] == [
        "systin", "auxtin3", "peci_0_1", "diode_0_curr", "local_2"]


# ── the service and the Windows sensors ──────────────────────────────────


def test_the_service_samples_it_with_no_arguments() -> None:
    def nothing(*_: Any) -> Any:
        raise AssertionError("not this")

    chip = _FakeEc(fans={0: 1100})
    broker = PrivilegedBroker(version="t", find_drive=nothing, open_scsi=nothing,
                              open_module=nothing, open_superio=lambda: _reader(chip))
    reply, _ = broker.handle(Session(), {"op": "superio.sample", "register": 0x180,
                                         "value": 0x80}, b"")
    assert reply["ok"] and reply["sample"]["chips"][0]["fans"][0] == 1100
    assert chip.ec[0x180] == 0x80


def test_windows_sensors_offer_the_board(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_build_windows_gpu_chains", "_build_windows_disks"):
        monkeypatch.setattr(win, name, lambda: [])
    chip = _FakeEc(fans={11: 1400}, temps={2: (0x09, _word(45.5))})
    sensors = win.build_windows_sensors(superio=_reader(chip).sample)
    listed = {r.sensor_id for r in sensors.discover()}
    assert {"fan:superio:fan12:rpm", "board:superio:thermistor_15:temp"} <= listed
    values = sensors.read_all()
    assert (values["fan:superio:fan12:rpm"],
            values["board:superio:thermistor_15:temp"]) == (1400, 45.5)
