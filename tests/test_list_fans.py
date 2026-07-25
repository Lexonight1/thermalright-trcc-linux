"""ListFans — enumerate discovered fans with live readings.

The read-only diagnostic behind the fan reading (#145/#207): Linux has no
reliable ``fanN_label``, so ``snapshot()`` fills the CPU/SSD/SYS2 slots from
the device's fans automatically and the GPU slot follows the picked GPU.  This
command surfaces the raw fan list every UI (cli/api) shares, for debugging
"which fans does my box even expose".
"""
from __future__ import annotations

from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.app import App
from trcc.core.commands.system import ListFans

from .conftest import FakeCpu, FakeMemory, FakePlatform
from .test_sensors import FakeFan


def _with_fans(platform: FakePlatform, *fans: FakeFan) -> FakePlatform:
    """Seed the platform's enumerator with a known fan set before boot."""
    platform._sensors = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=list(fans),
    )
    return platform


def test_list_fans_returns_discovered_fans_with_rpm(fake_platform) -> None:
    _with_fans(fake_platform,
               FakeFan("hwmon:nct6798:fan1", 1200, name="CPU Fan"),
               FakeFan("hwmon:nct6798:fan2", 800, name="Sys Fan"))
    result = App(fake_platform).dispatch(ListFans())

    assert result.ok
    assert [(f.key, f.rpm) for f in result.fans] == [
        ("hwmon:nct6798:fan1", 1200),
        ("hwmon:nct6798:fan2", 800),
    ]
    assert result.fans[0].name == "CPU Fan"


def test_list_fans_empty_when_no_fans(fake_platform) -> None:
    _with_fans(fake_platform)
    result = App(fake_platform).dispatch(ListFans())

    assert result.ok
    assert result.fans == []
    assert "0 fan" in result.message
