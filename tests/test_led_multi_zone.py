"""A multi-zone LED (PA120/LF10) behaves as the Windows app's FormLED does.

FormLED, for styles 2 and 7: every control writes the global value AND the
selected zones (colour :2057, on/off :2124, brightness :2279, mode :2447); the
device is driven by the zones alone (:10958, ``SendHidVal``); a first run
starts with select-all on (:1966).

Measured before this: a fresh PA120 shown red sent 107/255 (its zones' 65 %
times the global 65 %) and reached 1 zone of 4; at 100 % it sent 165; the off
switch darkened every zone; the preview stayed lit after "off".
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.device.led import _COLOR_SCALE
from trcc.app import App
from trcc.core.commands import (
    LedSnapshot,
    RenderLed,
    SetLedBrightness,
    SetLedColor,
    SetLedMode,
    SetLedZoneSync,
    SetLedZoneSyncZones,
    ToggleLed,
)
from trcc.core.events import FrameSent
from trcc.core.led_models import LED_STYLES, LEDMode
from trcc.core.models import LedStyle
from trcc.services.led_effects import LEDEffectEngine, apply_brightness
from trcc.services.led_segment import get_display

from .conftest import FakePlatform
from .test_render_led import _LED_KEY, _attach_and_connect, _decode_body

_PA120, _AX120 = 16, 1


def _connected(platform: FakePlatform, pm: int) -> App:
    """Connected and rendered once.  In the running App the LED loop that
    ``App.start_session`` starts renders every connected device, and that
    first frame (or a first edit) is what creates a multi-zone device's zones."""
    app = App(platform)
    _attach_and_connect(app, platform, pm=pm)
    assert app.dispatch(RenderLed(key=_LED_KEY)).ok
    return app


def _render(app: App, platform: FakePlatform, style: LedStyle) -> tuple[list, list]:
    """(what the preview shows, what the wire received) for one frame."""
    shown: list[Any] = []
    app.events.subscribe(FrameSent, lambda e: shown.append(list(e.display_colors)))
    platform.bulk.writes.clear()
    assert app.dispatch(RenderLed(key=_LED_KEY)).ok
    return shown[-1], _decode_body(platform.bulk.writes, LED_STYLES[style].led_count)


def _peak(colors: list) -> int:
    return max(max(c) for c in colors)


def _only_zone(app: App, zone: int) -> None:
    """Select-all off, one zone selected — a user's pick on the panel."""
    zones = tuple(i == zone for i in range(4))
    assert app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False)).ok
    assert app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=zones)).ok


def test_a_fresh_pa120_reaches_every_zone_at_the_csharp_brightness(
    fake_platform: FakePlatform,
) -> None:
    app = _connected(fake_platform, _PA120)

    app.dispatch(SetLedColor(key=_LED_KEY, color=(0, 0, 255)))

    snap = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert snap.zone_sync is True
    assert [z.color for z in snap.zones] == [(0, 0, 255)] * 4
    preview, wire = _render(app, fake_platform, LedStyle.PA120)
    assert _peak(preview) == 165                        # 65 %, not 65 % x 65 %
    assert _peak(wire) == int(165 * _COLOR_SCALE)


def test_a_page_style_device_has_no_zones_to_select(
    fake_platform: FakePlatform,
) -> None:
    app = _connected(fake_platform, _AX120)

    app.dispatch(SetLedColor(key=_LED_KEY, color=(0, 0, 255)))

    snap = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert (snap.zones, snap.zone_sync, snap.color) == ((), False, (0, 0, 255))


# control: (the Command, its global field, its zone field, the value it sets)
_CONTROLS: dict[str, tuple[Callable[[], Any], Callable[[Any], Any],
                           Callable[[Any], Any], Any]] = {
    "colour": (lambda: SetLedColor(key=_LED_KEY, color=(1, 2, 3)),
               lambda s: s.color, lambda z: z.color, (1, 2, 3)),
    # Not RAINBOW: that is the starting mode now (FormLED's ``myLedMode = 4``).
    "mode": (lambda: SetLedMode(key=_LED_KEY, mode=LEDMode.BREATHING),
             lambda s: s.mode, lambda z: z.mode, "BREATHING"),
    "brightness": (lambda: SetLedBrightness(key=_LED_KEY, percent=30),
                   lambda s: s.brightness, lambda z: z.brightness, 30),
    "on/off": (lambda: ToggleLed(key=_LED_KEY, on=False),
               lambda s: s.global_on, lambda z: z.on, False),
}


@pytest.mark.parametrize("control", list(_CONTROLS))
def test_a_control_writes_the_global_value_and_the_selected_zones(
    fake_platform: FakePlatform, control: str,
) -> None:
    send, global_of, zone_of, value = _CONTROLS[control]
    app = _connected(fake_platform, _PA120)
    _only_zone(app, 1)
    before = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert zone_of(before.zones[1]) != value

    assert app.dispatch(send()).ok

    after = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert global_of(after) == value
    assert [zone_of(z) == value for z in after.zones] == [False, True, False, False]


def test_with_no_zone_selected_an_edit_reaches_the_first(
    fake_platform: FakePlatform,
) -> None:
    """FormLED cannot deselect its last zone; select-all off from the CLI,
    with no zone picked, can.  The edit still lands somewhere."""
    app = _connected(fake_platform, _PA120)
    assert app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False)).ok

    app.dispatch(SetLedColor(key=_LED_KEY, color=(1, 2, 3)))

    zones = app.dispatch(LedSnapshot(key=_LED_KEY)).zones
    assert [z.color == (1, 2, 3) for z in zones] == [True, False, False, False]


@pytest.mark.parametrize("percent, peak", [(100, 255), (50, 127)])
def test_the_slider_sets_the_brightness_the_device_shows(
    fake_platform: FakePlatform, percent: int, peak: int,
) -> None:
    app = _connected(fake_platform, _PA120)

    app.dispatch(SetLedBrightness(key=_LED_KEY, percent=percent))

    preview, wire = _render(app, fake_platform, LedStyle.PA120)
    assert _peak(preview) == peak
    assert _peak(wire) == int(peak * _COLOR_SCALE)


def test_off_darkens_only_the_selected_zone(fake_platform: FakePlatform) -> None:
    app = _connected(fake_platform, _PA120)
    display = get_display(LedStyle.PA120)
    assert display is not None and display.zone_led_map is not None
    _only_zone(app, 1)

    app.dispatch(ToggleLed(key=_LED_KEY, on=False))

    preview, wire = _render(app, fake_platform, LedStyle.PA120)
    zone_1 = set(display.zone_led_map[1])
    assert all(preview[i] == (0, 0, 0) for i in zone_1)
    assert any(c != (0, 0, 0) for i, c in enumerate(preview) if i not in zone_1)
    assert _peak(wire) > 0


def test_a_page_style_off_darkens_the_device_and_the_preview(
    fake_platform: FakePlatform,
) -> None:
    """The preview drew every LED lit after "off" while the device was dark:
    it applied the segment mask and not the switch."""
    app = _connected(fake_platform, _AX120)

    app.dispatch(ToggleLed(key=_LED_KEY, on=False))

    preview, wire = _render(app, fake_platform, LedStyle.AX120)
    assert _peak(preview) == 0
    assert _peak(wire) == 0


# ── The upgrade: a saved device keeps looking the same ────────────────────


def _load(tmp_path: Path, schema: int, led: dict[str, Any]) -> Any:
    from tests.mock_platform import MockPlatform
    from trcc.services.settings import Settings

    platform = MockPlatform([], tmp_path)
    (tmp_path / "trcc.json").write_text(json.dumps(
        {"schema": schema, "led_devices": {_LED_KEY: led}}), encoding="utf-8")
    return Settings(platform.paths()).for_led(_LED_KEY)


_ZONES = [{"brightness": 65, "on": True}, {"brightness": 100, "on": True}]


def test_an_upgrade_folds_the_global_values_into_the_zones(tmp_path: Path) -> None:
    led = _load(tmp_path, 2, {"brightness": 50, "global_on": False, "zones": _ZONES})

    assert [(z.brightness, z.on) for z in led.zones] == [(32, False), (50, False)]


def test_a_current_config_and_a_device_without_zones_are_read_as_they_are(
    tmp_path: Path,
) -> None:
    current = _load(tmp_path, 3, {"brightness": 50, "zones": _ZONES})
    assert [z.brightness for z in current.zones] == [65, 100]

    page_style = _load(tmp_path, 2, {"brightness": 50, "global_on": False})
    assert (page_style.brightness, page_style.global_on, page_style.zones) == (
        50, False, [])


def test_an_upgrade_folds_once(tmp_path: Path) -> None:
    """The next save records the new schema; folding again on every start
    would halve a user's brightness at each launch."""
    from tests.mock_platform import MockPlatform
    from trcc.services.settings import Settings

    _load(tmp_path, 2, {"brightness": 50, "zones": _ZONES})
    paths = MockPlatform([], tmp_path).paths()
    Settings(paths).set_language("de")                 # any change saves

    assert [z.brightness for z in Settings(paths).for_led(_LED_KEY).zones] == [32, 50]


def test_the_folded_brightness_draws_what_the_old_render_drew() -> None:
    """Old: zone brightness, then the global on top.  New: the folded zone
    alone.  A whole percent cannot always hold the product, so a colour may
    move by 2 of 255 at most — measured over every pair; truncating instead
    of rounding reached 3."""
    import logging

    from trcc.services.settings import _migrate_led

    def folded(zone: int, glob: int) -> int:
        data = {"brightness": glob, "zones": [{"brightness": zone}]}
        return _migrate_led(data, 2, _LED_KEY)["zones"][0]["brightness"]

    logging.disable(logging.INFO)
    try:
        worst = max(
            abs(apply_brightness(LEDEffectEngine._scale_brightness([(v, 0, 0)], zone), glob)[0][0]
                - LEDEffectEngine._scale_brightness([(v, 0, 0)], folded(zone, glob))[0][0])
            for zone in range(101) for glob in range(101) for v in (255, 128, 1))
    finally:
        logging.disable(logging.NOTSET)
    assert worst <= 2
