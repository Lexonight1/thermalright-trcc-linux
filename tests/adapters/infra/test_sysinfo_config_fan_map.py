"""Fan auto-map regression: spinning fans claim the visible slots (#145).

Label-less super-I/O boards (nct6xxx) expose every fan header whether or not a
fan is plugged in, and give no CPU/GPU labels.  The positional fallback used to
bind headers in raw id order, so the CPU/GPU rows landed on disconnected
headers reading 0 RPM while the real fans fell off the end — "CPU and other
fans not reporting" (#145).  The target audience runs hydronic Thermalright
coolers whose pump + radiator fans sit on arbitrary headers and always spin, so
the fallback must prefer live readings.
"""
from __future__ import annotations

from trcc.adapters.infra.sysinfo_config import SysInfoConfig
from trcc.core.models import SensorReading


class _FakeEnumerator:
    """Minimal enumerator exposing only ``discover()`` — auto_map's contract."""

    def __init__(self, readings: list[SensorReading]) -> None:
        self._readings = readings

    def discover(self) -> list[SensorReading]:
        return list(self._readings)


def _fan(idx: int, rpm: float) -> SensorReading:
    return SensorReading(
        sensor_id=f"fan:hwmon:nct6798:fan{idx}:rpm",
        category="fan", value=rpm, unit="RPM",
        label="",  # super-I/O leaves the label blank — the whole problem
    )


def _fan_panel(cfg: SysInfoConfig):
    return next(p for p in cfg.panels if p.category_id == 6)


def test_positional_fallback_binds_spinning_fans_to_visible_slots() -> None:
    # fan1/fan3/fan4/fan5 dead (disconnected headers), fan2 + fan6 spinning
    # (e.g. a hydronic pump + a radiator fan on arbitrary headers).
    enum = _FakeEnumerator([
        _fan(1, 0.0), _fan(2, 895.0), _fan(3, 0.0),
        _fan(4, 0.0), _fan(5, 0.0), _fan(6, 3125.0),
    ])
    cfg = SysInfoConfig()
    cfg.panels = SysInfoConfig.defaults()
    cfg.auto_map(enum)

    rows = _fan_panel(cfg).sensors
    # The two visible slots (CPUFAN, GPUFAN) must get the LIVE fans, in id
    # order among the spinning ones — never the dead fan1.
    assert rows[0].sensor_id == "fan:hwmon:nct6798:fan2:rpm"
    assert rows[1].sensor_id == "fan:hwmon:nct6798:fan6:rpm"
    # Dead headers only backfill the remaining slots.
    assert rows[2].sensor_id == "fan:hwmon:nct6798:fan1:rpm"
    assert rows[3].sensor_id == "fan:hwmon:nct6798:fan3:rpm"


def test_all_dead_fans_still_bind_positionally_no_crash() -> None:
    # Degenerate case: nothing spinning at map time — must not crash and
    # still bind in id order (a fan idle now may spin under load later).
    enum = _FakeEnumerator([_fan(1, 0.0), _fan(2, 0.0)])
    cfg = SysInfoConfig()
    cfg.panels = SysInfoConfig.defaults()
    cfg.auto_map(enum)

    rows = _fan_panel(cfg).sensors
    assert rows[0].sensor_id == "fan:hwmon:nct6798:fan1:rpm"
    assert rows[1].sensor_id == "fan:hwmon:nct6798:fan2:rpm"


def test_labelled_cpu_fan_still_wins_over_positional() -> None:
    # Boards that DO label their fans must keep label-first mapping: a fan
    # labelled "CPU Fan" binds to the CPUFAN slot even if it isn't fan1.
    labelled = SensorReading(
        sensor_id="fan:hwmon:it87:fan3:rpm", category="fan",
        value=1200.0, unit="RPM", label="CPU Fan",
    )
    enum = _FakeEnumerator([_fan(1, 800.0), labelled])
    cfg = SysInfoConfig()
    cfg.panels = SysInfoConfig.defaults()
    cfg.auto_map(enum)

    assert _fan_panel(cfg).sensors[0].sensor_id == "fan:hwmon:it87:fan3:rpm"
