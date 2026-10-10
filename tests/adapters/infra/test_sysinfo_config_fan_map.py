"""Which fan fills CPUFAN / GPUFAN / FAN1 / FAN2 -- one policy, one place (#145).

The dashboard's FAN rows ran their own label scan and spinning-order fill while
the LCD's fan slots ran a different one, so the two could show different fans.
The policy now lives in ``SensorEnumerator.fan_slots``: a pin from the
dashboard first, then a fan NAME keyword, then still-spinning fans in order,
never a GPU's own fan.  The dashboard's FAN rows bind to the slots themselves,
so they show exactly what the panel shows; rebinding a row to one fan pins it.

Label-less super-I/O boards (nct6xxx) expose every header whether or not a fan
is plugged in, so a 0-RPM header is an empty one: the spinning fans must claim
the visible slots (the original #145 bug).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.conftest import FakeCpu, FakeGpu, FakeMemory
from trcc.adapters.infra.sysinfo_config import SysInfoConfig
from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.core.models import SensorReading
from trcc.core.ports import FanSource


class _Fan(FanSource):
    def __init__(self, key: str, rpm: int, name: str = "", on_gpu: bool = False):
        self._key, self._rpm, self._name, self._on_gpu = key, rpm, name, on_gpu

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name or self._key

    @property
    def on_gpu(self) -> bool:
        return self._on_gpu

    def rpm(self) -> int | None:
        return self._rpm


def _slots(fans: list[_Fan], pins: dict[str, str] | None = None) -> dict[str, float]:
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
                        fans=fans)
    if pins is not None:
        s.set_fan_slot_pins(pins)
    r = s.read_all()
    return {k: r[k] for k in ("fan:cpu", "fan:gpu", "fan:ssd", "fan:sys2")}


def test_spinning_fans_claim_the_slots_before_dead_headers() -> None:
    """#145: an nct6798 with fan2 (pump) and fan6 (radiator) spinning, the
    other headers empty.  The live fans fill the slots, in order."""
    fans = [_Fan(f"nct6798:fan{i}", rpm) for i, rpm in
            ((1, 0), (2, 895), (3, 0), (4, 0), (5, 0), (6, 3125))]
    assert _slots(fans) == {"fan:cpu": 895, "fan:gpu": 1500.0,
                            "fan:ssd": 3125, "fan:sys2": 0.0}


def test_a_fan_named_for_a_slot_claims_it_first() -> None:
    """Boards that DO label their headers keep label-first mapping."""
    fans = [_Fan("it87:fan1", 800, "SYS_FAN1"), _Fan("it87:fan2", 1100, "NVME_M2 Fan"),
            _Fan("it87:fan3", 1200, "CPU Fan"), _Fan("it87:fan4", 3000, "AIO_PUMP")]
    assert _slots(fans) == {"fan:cpu": 1200, "fan:gpu": 1500.0,
                            "fan:ssd": 1100, "fan:sys2": 800}


def test_a_gpu_fan_never_fills_a_motherboard_slot() -> None:
    fans = [_Fan("amdgpu:fan1", 2000, "CPU-looking amdgpu fan", on_gpu=True),
            _Fan("nct:fan1", 900)]
    assert _slots(fans)["fan:cpu"] == 900


def test_a_pin_from_the_dashboard_wins_and_is_not_reused() -> None:
    """The C#'s FAN rows are rebindable: the user picks the pump for CPUFAN."""
    fans = [_Fan("nct:fan1", 790), _Fan("nct:fan6", 3082)]
    slots = _slots(fans, {"fan:cpu": "fan:nct:fan6:rpm"})
    assert (slots["fan:cpu"], slots["fan:ssd"], slots["fan:sys2"]) == (3082, 790, 0.0)


def test_a_pin_to_a_missing_fan_falls_back_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
                        fans=[_Fan("nct:fan1", 900)])
    s.set_fan_slot_pins({"fan:cpu": "fan:gone:fan9:rpm"})
    with caplog.at_level(logging.WARNING, logger="trcc.core.ports"):
        first = s.read_all()["fan:cpu"]
        s._last_poll = 0.0                       # force a second sweep
        s.read_all()
    assert first == 900
    assert len([r for r in caplog.records if "fan:gone:fan9:rpm" in r.message]) == 1


def test_the_dashboard_fan_rows_bind_to_the_panels_own_slots(tmp_path: Path) -> None:
    """auto_map binds each FAN row to its slot, which discover offers as
    "CPUFAN (auto)" -- so a fresh dashboard shows what the LCD shows."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
                        fans=[_Fan("nct:fan1", 900)])
    readings: list[SensorReading] = s.discover()
    cfg = SysInfoConfig(tmp_path / "system_config.json")
    cfg.panels = SysInfoConfig.defaults()
    cfg.auto_map(readings)

    fan_panel = next(p for p in cfg.panels if p.category_id == 6)
    assert [b.sensor_id for b in fan_panel.sensors] == [
        "fan:cpu", "fan:gpu", "fan:ssd", "fan:sys2"]
    assert {r.sensor_id: r.label for r in readings}["fan:cpu"] == "CPUFAN (auto)"


# ── #145: a dashboard saved before fan pins existed must not pin the LCD ──
#
# Before v9.10.0 the gui auto-mapped and SAVED positional guesses into the FAN
# rows (any motherboard fan, in order).  v9.10.6 made a bound FAN row pin the
# LCD's fan slot, so those old guesses started driving the panel: #145's
# GPUFAN showed a Corsair case fan.  Every file ever written says version 1,
# and a guess cannot be told from a choice, so a version-1 file loses its FAN
# rows once and is re-saved as version 2.

_REPORTER_ROWS = ["fan:hwmon:corsaircpro:fan1:rpm", "fan:hwmon:corsaircpro:fan2:rpm",
                  "fan:hwmon:corsaircpro:fan3:rpm", "fan:hwmon:nct6798:fan1:rpm"]


def _saved_dashboard(tmp_path: Path, version: int) -> Path:
    import json

    from trcc.core.models import FAN_PANEL_CATEGORY
    path = tmp_path / "system_config.json"
    path.write_text(json.dumps({"version": version, "panels": [
        {"category_id": 0, "name": "CPU", "sensors": [
            {"label": "TEMP", "sensor_id": "cpu:temp", "unit": "°C"}]},
        {"category_id": FAN_PANEL_CATEGORY, "name": "FAN", "sensors": [
            {"label": label, "sensor_id": sid, "unit": "RPM"}
            for label, sid in zip(("CPUFAN", "GPUFAN", "SSDFAN", "FAN2"),
                                  _REPORTER_ROWS, strict=True)]},
    ]}), encoding="utf-8")
    return path


def test_an_old_dashboard_pins_no_fan(tmp_path: Path) -> None:
    """MUTATION CHECK: skip the version check -> the four pins come back."""
    import json

    from trcc.core.commands._helpers import fan_slot_pins

    path = _saved_dashboard(tmp_path, version=1)

    panels = SysInfoConfig(path).load()

    assert fan_slot_pins(panels) == {}
    assert panels[0].sensors[0].sensor_id == "cpu:temp"     # only FAN rows
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_a_current_dashboard_keeps_its_fan_choices(tmp_path: Path) -> None:
    from trcc.core.commands._helpers import fan_slot_pins

    panels = SysInfoConfig(_saved_dashboard(tmp_path, version=2)).load()

    assert fan_slot_pins(panels) == dict(zip(
        ("fan:cpu", "fan:gpu", "fan:ssd", "fan:sys2"), _REPORTER_ROWS, strict=True))
