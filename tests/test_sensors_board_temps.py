"""Motherboard / super-I/O temperatures — the sensors nothing claimed.

Every other temperature port is ROLE-typed: we know a ``coretemp`` reading is
the CPU and an ``nvme`` reading is a disk.  A board sensor has no role — it is
whatever the builder wired to that header — so it needs its own port and the
user has to be the one who picks it.

The gap was invisible because the chip was already half-read: ``hwmon.py``
opens an ``nct6xxx`` for its FANS and walked straight past a dozen
``tempN_input`` channels on the same node.  #259 asked for ``T_SENSOR1`` (an
``AUXTIN`` on ASUS boards) and #282 for a Fujitsu ``sch5636`` that our own
scanner never saw at all.
"""
from __future__ import annotations

from typing import Any

import pytest

from trcc.adapters.sensors import psutil_sources
from trcc.core.models import MIN_REFRESH_INTERVAL_S


class _Entry:
    """Shaped like ``psutil``'s ``shwtemp`` namedtuple, for the fields we read."""

    def __init__(self, label: str, current: float) -> None:
        self.label = label
        self.current = current


def _fake_chips(monkeypatch: pytest.MonkeyPatch, chips: dict[str, Any]) -> None:
    monkeypatch.setattr(
        psutil_sources.psutil, "sensors_temperatures", lambda: chips,
    )


def test_an_unconnected_header_reading_zero_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly 0.0 is the documented signature of a header the board never wired.

    This desk's ``nct6798`` publishes twelve inputs and four of them
    (``PCH_CHIP_TEMP``, ``PCH_CPU_TEMP``, ``PCH_MCH_TEMP``,
    ``PCH_CHIP_CPU_MAX_TEMP``) read 0.0.  lm-sensors users mask those with
    per-board ``ignore`` directives we cannot ship, so without this filter every
    such user is handed four dead sensors to choose between.
    """
    _fake_chips(monkeypatch, {"nct6798": [
        _Entry("SYSTIN", 23.0),
        _Entry("PCH_CHIP_TEMP", 0.0),
        _Entry("AUXTIN0", 28.0),
    ]})
    keys = [s.key for s in psutil_sources.discover_board_temps()]
    assert keys == ["nct6798_systin", "nct6798_auxtin0"]


def test_a_cold_probe_is_not_mistaken_for_an_unconnected_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filter is ``== 0.0``, never a plausibility RANGE.

    An external probe in a cold room reads a low number, and #259 is asking for
    exactly that sensor.  A "looks too low" heuristic would discard the feature
    while appearing to work everywhere it was tested.
    """
    _fake_chips(monkeypatch, {"nct6798": [_Entry("AUXTIN1", 4.0)]})
    assert [s.key for s in psutil_sources.discover_board_temps()] == [
        "nct6798_auxtin1"]


@pytest.mark.parametrize(
    "chip", ["coretemp", "k10temp", "amdgpu", "nvme", "spd5118", "jc42"])
def test_a_role_owned_chip_is_not_reported_twice(
    monkeypatch: pytest.MonkeyPatch, chip: str,
) -> None:
    """A DIMM must not appear as ``memory:temp`` AND as a nameless board sensor.

    These chips already have a typed port and a typed discovery; surfacing them
    here would duplicate every one of them under a second identity.
    """
    _fake_chips(monkeypatch, {chip: [_Entry("temp1", 42.0)]})
    assert psutil_sources.discover_board_temps() == []


@pytest.mark.parametrize("chip, entries, expected", [
    ("k10temp", [("Tctl", 61.0), ("Tccd1", 55.0), ("Tccd2", 57.0)],
     ["k10temp_tccd1", "k10temp_tccd2"]),
    ("coretemp", [("Package id 0", 48.0), ("Core 0", 45.0), ("Core 1", 47.0)],
     ["coretemp_core_0", "coretemp_core_1"]),
])
def test_a_cpu_offers_its_cores_but_not_its_package_twice(
    monkeypatch: pytest.MonkeyPatch, chip: str,
    entries: list[tuple[str, float]], expected: list[str],
) -> None:
    """#301: CPU chips were claimed whole, so per-CCD and per-core readings
    were read by nothing.  The first input IS ``cpu:temp`` and stays claimed."""
    _fake_chips(monkeypatch, {chip: [_Entry(label, t) for label, t in entries]})
    assert [s.key for s in psutil_sources.discover_board_temps()] == expected


def test_the_key_is_built_from_the_LABEL_the_user_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``T_SENSOR1`` is found by its label, not by its channel number.

    A bare ``temp7`` tells the user nothing, and two unlabelled chips would
    collide — so the chip name stays in the key as well.
    """
    _fake_chips(monkeypatch, {"nct6798": [
        _Entry("PECI Agent 0 Calibration", 26.0)]})
    source = psutil_sources.discover_board_temps()[0]
    assert source.key == "nct6798_peci_agent_0_calibration"
    assert source.name == "PECI Agent 0 Calibration (nct6798)"


def test_an_unlabelled_input_falls_back_to_its_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``acpitz`` publishes no label; it must still be addressable."""
    _fake_chips(monkeypatch, {"acpitz": [_Entry("", 27.8)]})
    assert [s.key for s in psutil_sources.discover_board_temps()] == [
        "acpitz_temp1"]


def test_psutil_refusing_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No sensors is a valid answer — a VM, a container, a locked-down kernel.

    The whole family must degrade to "none" rather than take the snapshot down
    with it.
    """
    def _boom() -> dict[str, Any]:
        raise OSError("no hwmon here")

    monkeypatch.setattr(
        psutil_sources.psutil, "sensors_temperatures", _boom)
    assert psutil_sources.discover_board_temps() == []


def test_one_poll_rescans_once_however_many_sensors_the_board_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sensors_temperatures()`` returns EVERY chip, so calling it per source
    makes the cost scale with the sensor count.

    Each source called it independently until this was fixed: 9 sources on the
    dev box meant 9 full rescans per poll (~2,500 file opens), and the metric
    poll went 27.7 ms -> 180.8 ms when board temperatures landed (49b8143c) --
    1.4% to 9.0% of a core.  That commit shipped 122 lines of tests and none of
    them asked how long a poll takes.  This is that question.
    """
    chips = {
        "nct6798": [
            _Entry(f"SYS_TEMP{i}", 30.0 + i) for i in range(1, 10)
        ],
    }
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return chips

    monkeypatch.setattr(psutil_sources.psutil, "sensors_temperatures", counting)

    sources = psutil_sources.discover_board_temps()
    assert len(sources) == 9, "fixture should build nine board sensors"

    calls["n"] = 0
    readings = [s.temp() for s in sources]

    assert calls["n"] == 1, (
        f"one poll over {len(sources)} board sensors rescanned "
        f"{calls['n']} time(s) — each source is calling "
        "psutil.sensors_temperatures() again, which returns every chip"
    )
    assert readings == [30.0 + i for i in range(1, 10)], (
        "sharing the scan must not change what each source reads"
    )


def test_board_temps_are_not_rescanned_on_every_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Motherboard temperatures do not move at 1 Hz, and reading them is the
    single most expensive thing a sensor sweep does.

    MEASURED on the dev box: ``psutil.sensors_temperatures()`` opens **260 of
    the sweep's 316 files** — it reads ``temp_input``, ``temp_max``,
    ``temp_crit``, ``temp_label`` and ``name`` for ALL 35 temperature sensors,
    to serve 9 board readings from one file each.  Every other source family
    (gpus, fans, disks, dram, spd) contributes ~0 opens.

    The scan is already shared, so a poll rescans once rather than nine times.
    This is the other half: it must not rescan on every POLL either.  At a 5 s
    TTL, 5 polls one second apart cost ONE scan instead of five — measured
    316 -> 99.3 opens per sweep, a 69% cut, with all 9 keys still present.

    Cadence belongs on the shared scan and nowhere else: ``chips()`` rescans
    whenever ANY source is due, so nine sources each holding their own
    schedule would drift apart and re-create the very rescan-per-poll this
    prevents.  One scan, one timestamp, lockstep by construction.
    """
    chips = {"nct6798": [_Entry(f"SYS_TEMP{i}", 30.0 + i) for i in range(1, 10)]}
    clock = {"t": 1000.0}
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return chips

    monkeypatch.setattr(psutil_sources.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(psutil_sources.psutil, "sensors_temperatures", counting)

    sources = psutil_sources.discover_board_temps()
    assert len(sources) == 9, "fixture should build nine board sensors"
    calls["n"] = 0

    polls, seen = 5, []
    for _ in range(polls):
        clock["t"] += MIN_REFRESH_INTERVAL_S          # one poll apart
        seen.append(frozenset(
            s.key for s in sources if s.temp() is not None))

    assert calls["n"] <= 2, (
        f"{polls} polls {MIN_REFRESH_INTERVAL_S}s apart rescanned every chip "
        f"on the machine {calls['n']} time(s).  psutil.sensors_temperatures() "
        "is ~260 file opens; the shared scan's TTL must span at least one "
        "poll interval so slow-moving board temperatures are not re-read at "
        "the metric refresh rate."
    )
    assert len(set(seen)) == 1 and len(seen[0]) == 9, (
        "a cached scan must still answer EVERY board sensor — a key that "
        "disappears between polls reads as 'sensor not present' downstream "
        f"and renders '--', not a stale value: {[len(s) for s in seen]}"
    )
