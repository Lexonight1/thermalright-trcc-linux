"""What a gui overlay-grid tile shows for a hardware element (#301).

The C# refreshes every tile about once a second from the live readings
(``UCXiTongXianShi.UCXiTongXianShiTimer``, FormCZTV.cs:2853): label2 the
number, label3 the unit -- the unit always, whatever the element's unit switch
says.  Ours never fed the tiles at all, so a picked sensor showed ``--``.

A tile reads its text from the same pure function the panel draws with
(``services.overlay.metric_text``), so the two cannot disagree: a tile with
its own copy would have shown °F numbers under a °C glyph, and "0RPM" for a
GPU fan whose driver gives only a duty percent.
"""
from __future__ import annotations

from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.ui.presentation.overlay_serialization import tile_reading


def _hardware(main: int = 0, sub: int = 1, *, mode_sub: int = 1,
              metric: str = "") -> OverlayElementConfig:
    return OverlayElementConfig(mode=OverlayMode.HARDWARE, main_count=main,
                                sub_count=sub, mode_sub=mode_sub, metric=metric)


def test_a_tile_shows_the_live_number_and_unit() -> None:
    assert tile_reading(_hardware(), {"cpu:temp": 52.4}, "C") == ("52", "°C")


def test_fahrenheit_says_fahrenheit() -> None:
    """The reading is already converted upstream; only the glyph follows."""
    assert tile_reading(_hardware(), {"cpu:temp": 126.0}, "F") == ("126", "°F")


def test_the_unit_shows_whatever_the_unit_switch_says() -> None:
    """C# label3 is the unit in mode 0, regardless of myModeSub."""
    assert tile_reading(_hardware(mode_sub=0), {"cpu:temp": 52.0}, "C") == ("52", "°C")


def test_a_duty_only_gpu_fan_reads_as_a_percent() -> None:
    cfg = _hardware(metric="fan:gpu")

    assert tile_reading(cfg, {"fan:gpu:percent": 44.0}, "C") == ("44", "%")


def test_a_picked_sensor_shows_its_value() -> None:
    """The #301 case: a sensor added from the picker, by its id."""
    cfg = _hardware(metric="board:nct6798_systin:temp")

    assert tile_reading(cfg, {"board:nct6798_systin:temp": 38.0}, "C") == ("38", "°C")


def test_no_reading_shows_nothing_new() -> None:
    assert tile_reading(_hardware(), {}, "C") is None


def test_a_clock_tile_has_no_reading() -> None:
    cfg = OverlayElementConfig(mode=OverlayMode.TIME)

    assert tile_reading(cfg, {"cpu:temp": 52.0}, "C") is None
