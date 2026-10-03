"""An LED's zone/page selection is ONE fact, the mask, as FormLED keeps it.

FormLED holds ``LunBo1..4`` and nothing else: with the carousel off a zone
click selects exactly that page (:2812) and the cooler shows the first in the
mask (:3540); switching the carousel off keeps the lowest selected page
(``buttonLB_Click`` :2600), except on PA120/LF10.  We kept a second field,
``selected_zone``; each Command wrote one of the two, and measured on an AX120
the cooler showed page 2 while the mask said page 1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trcc.app import App
from trcc.core.commands import (
    LedSnapshot,
    RenderLed,
    SelectZone,
    SetLedColor,
    SetLedZoneSync,
    SetLedZoneSyncZones,
)
from trcc.core.led_models import LedRuntimeState
from trcc.core.models import LedStyle
from trcc.services.led_segment import get_display

from .conftest import FakePlatform
from .test_render_led import _LED_KEY, _attach_and_connect

_AX120, _PA120 = 1, 16


def _connected(platform: FakePlatform, pm: int) -> App:
    app = App(platform)
    _attach_and_connect(app, platform, pm=pm)
    assert app.dispatch(RenderLed(key=_LED_KEY)).ok
    return app


def _page_shown(app: App) -> int:
    """The metric page an AX120 renders now, carousel off."""
    display = get_display(LedStyle.AX120)
    assert display is not None and display.phase_count > 1
    return RenderLed(key=_LED_KEY)._metric_page(
        app, display, LedStyle.AX120, app.settings.for_led(_LED_KEY),
        LedRuntimeState())


def _mask(app: App) -> tuple[bool, ...]:
    return app.dispatch(LedSnapshot(key=_LED_KEY)).zone_sync_zones


def test_selecting_a_page_selects_exactly_that_page(fake_platform: FakePlatform) -> None:
    app = _connected(fake_platform, _AX120)

    assert app.dispatch(SelectZone(key=_LED_KEY, zone=2)).ok

    assert (_mask(app), _page_shown(app)) == ((False, False, True), 2)
    assert app.dispatch(LedSnapshot(key=_LED_KEY)).selected_zone == 2


def test_a_mask_set_with_the_carousel_off_is_the_page_shown(
    fake_platform: FakePlatform,
) -> None:
    """The CLI / API ``zone-sync-zones`` used to change the mask and leave the
    cooler on the page a separate field held."""
    app = _connected(fake_platform, _AX120)
    app.dispatch(SelectZone(key=_LED_KEY, zone=2))

    app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=(False, True, False, False)))

    assert _page_shown(app) == 1


def test_switching_the_carousel_off_keeps_the_lowest_page(
    fake_platform: FakePlatform,
) -> None:
    app = _connected(fake_platform, _AX120)
    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=True))
    app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=(False, True, True, False)))

    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False))

    assert (_mask(app), _page_shown(app)) == ((False, True, False, False), 1)


def test_select_all_off_on_a_pa120_keeps_the_zones_picked(
    fake_platform: FakePlatform,
) -> None:
    """On PA120/LF10 the flag means "every zone", not a carousel; FormLED
    leaves ``LunBo`` alone when it goes off."""
    app = _connected(fake_platform, _PA120)
    app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=(False, True, True, False)))

    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False))

    assert _mask(app) == (False, True, True, False)


def _fresh(platform: FakePlatform, pm: int) -> App:
    """Connected, not yet rendered: a multi-zone cooler has no zones yet —
    the state for the seconds after connecting, when nothing animates it."""
    app = App(platform)
    _attach_and_connect(app, platform, pm=pm)
    assert app.dispatch(LedSnapshot(key=_LED_KEY)).zones == ()
    return app


def test_selecting_a_pa120_zone_makes_an_edit_reach_only_it(
    fake_platform: FakePlatform,
) -> None:
    """Select-all is on for a new cooler; with it on an edit reaches every
    zone, so selecting one zone turns it off."""
    app = _fresh(fake_platform, _PA120)

    app.dispatch(SelectZone(key=_LED_KEY, zone=3))
    app.dispatch(SetLedColor(key=_LED_KEY, color=(1, 2, 3)))

    snap = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert snap.zone_sync is False
    assert [z.color == (1, 2, 3) for z in snap.zones] == [False, False, False, True]


def test_select_all_turned_off_before_the_zones_exist_stays_off(
    fake_platform: FakePlatform,
) -> None:
    """Creating the zones turns select-all on; it happened on the first edit,
    AFTER the user had turned it off."""
    app = _fresh(fake_platform, _PA120)

    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False))
    app.dispatch(SetLedColor(key=_LED_KEY, color=(1, 2, 3)))

    snap = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert snap.zone_sync is False
    assert [z.color == (1, 2, 3) for z in snap.zones] == [True, False, False, False]


def test_a_mask_set_before_the_zones_exist_is_kept(fake_platform: FakePlatform) -> None:
    app = _fresh(fake_platform, _PA120)

    app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=(False, True, False, False)))

    assert _mask(app) == (False, True, False, False)
    assert len(app.dispatch(LedSnapshot(key=_LED_KEY)).zones) == 4


def test_a_negative_zone_is_refused(fake_platform: FakePlatform) -> None:
    app = _connected(fake_platform, _AX120)

    assert not app.dispatch(SelectZone(key=_LED_KEY, zone=-1)).ok


# ── The upgrade: a saved cooler keeps showing its page ────────────────────


def _load(tmp_path: Path, led: dict[str, Any]) -> Any:
    from tests.mock_platform import MockPlatform
    from trcc.services.settings import Settings

    platform = MockPlatform([], tmp_path)
    (tmp_path / "trcc.json").write_text(json.dumps(
        {"schema": 3, "led_devices": {_LED_KEY: led}}), encoding="utf-8")
    return Settings(platform.paths()).for_led(_LED_KEY)


def test_an_upgrade_turns_the_stored_page_into_the_mask(tmp_path: Path) -> None:
    led = _load(tmp_path, {"selected_zone": 2, "zone_sync_zones": [True, False, False, False]})

    assert (led.zone_sync_zones, led.selected_zone) == ([False, False, True, False], 2)


@pytest.mark.parametrize("saved", [
    {"selected_zone": 2, "zone_sync": True, "zone_sync_zones": [True, True, False, False]},
    {"selected_zone": 2, "zones": [{"brightness": 65}], "zone_sync_zones": [True, True]},
], ids=["carousel on: the mask already rotates", "multi-zone: never read it"])
def test_an_upgrade_leaves_a_mask_that_already_says_it(
    tmp_path: Path, saved: dict[str, Any],
) -> None:
    assert _load(tmp_path, saved).zone_sync_zones == saved["zone_sync_zones"]


# ── The gui panel shows the page the cooler shows ─────────────────────────


def _gui_led_panel(tmp_path: Path, qtbot: Any, mask: tuple[bool, ...]) -> tuple[Any, Any, App]:
    """The real window on a page-style cooler whose saved mask is *mask*."""
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import ConnectDevice
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([{"type": "led", "vid": "0416", "pid": "8001", "pm": _AX120}],
                           tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_LED_KEY)).ok
    app.dispatch(SetLedZoneSyncZones(key=_LED_KEY, zones=mask))
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: _LED_KEY in win._handlers)
    win._activate_device(_LED_KEY)
    return win, win.uc_led_control, app


def test_a_page_click_on_the_gui_panel_shows_that_page(
    tmp_path: Path, qtbot: Any,
) -> None:
    """With a saved carousel mask of 1+3, clicking page 2 lit pages 1 and 3:
    the panel reloaded the mask, which the click had not changed."""
    win, panel, app = _gui_led_panel(tmp_path, qtbot, (True, False, True, False))

    panel._zone_buttons[1].click()

    qtbot.waitUntil(lambda: app.dispatch(LedSnapshot(key=_LED_KEY)).selected_zone == 1)
    assert [b.isChecked() for b in panel._zone_buttons] == [False, True, False, False]
    win.close()


def test_the_gui_carousel_switch_leaves_the_mask_to_the_app(
    tmp_path: Path, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which page survives the carousel going off is the App's decision
    (``SetLedZoneSync``); the panel used to decide it and send the mask."""
    from trcc.core.commands._base import Query

    win, panel, app = _gui_led_panel(tmp_path, qtbot, (True, False, True, False))
    sent: list[str] = []
    dispatch = app.dispatch
    monkeypatch.setattr(app, "dispatch", lambda cmd: (
        sent.append(type(cmd).__name__) if not isinstance(cmd, Query) else None,
        dispatch(cmd))[1])

    panel._carousel_btn.click()
    panel._carousel_btn.click()

    # RenderLed is the App's own re-render, dispatched inside the change.
    assert [c for c in sent if c != "RenderLed"] == ["SetLedZoneSync", "SetLedZoneSync"]
    win.close()


def test_select_all_on_a_pa120_gives_every_zone_the_current_mode(
    fake_platform: FakePlatform,
) -> None:
    """FormLED, on turning select-all on (styles 2 and 7 only):
    ``myLedMode1..4 = myLedMode`` (:2578-2595) -- the mode the buttons show,
    and nothing else.  We set only the flag, so the zones kept their own
    modes until the next mode click reached them.

    MUTATION CHECK -- MEASURED 2026-10-02: drop the copy in SetLedZoneSync →
    fails.
    """
    from trcc.core.commands import SetLedMode
    from trcc.core.led_models import LEDMode

    app = _connected(fake_platform, _PA120)
    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=False))
    app.dispatch(SelectZone(key=_LED_KEY, zone=1))
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.BREATHING))
    app.dispatch(SetLedColor(key=_LED_KEY, color=(9, 9, 9)))
    app.dispatch(SelectZone(key=_LED_KEY, zone=0))
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.COLORFUL))
    before = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert [z.mode for z in before.zones][:2] == ["COLORFUL", "BREATHING"]

    assert app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=True)).ok

    after = app.dispatch(LedSnapshot(key=_LED_KEY))
    assert {z.mode for z in after.zones} == {"COLORFUL"}
    assert after.zones[1].color == (9, 9, 9), "only the mode is copied"


def test_a_new_cooler_starts_in_rainbow_on_every_zone(
    fake_platform: FakePlatform,
) -> None:
    """FormLED starts in rainbow (``myLedMode = 4``, :27) and its first run
    copies that into the zones (:1966 → :2578).  We started in STATIC.

    MUTATION CHECK -- MEASURED 2026-10-02: STATIC as the default again →
    fails.
    """
    app = _fresh(fake_platform, _PA120)
    assert app.dispatch(LedSnapshot(key=_LED_KEY)).mode == "RAINBOW"

    app.dispatch(RenderLed(key=_LED_KEY))                    # creates the zones

    zones = app.dispatch(LedSnapshot(key=_LED_KEY)).zones
    assert zones and {z.mode for z in zones} == {"RAINBOW"}


def test_zones_created_later_take_the_device_s_saved_mode(
    fake_platform: FakePlatform,
) -> None:
    """A saved mode wins over the default, zones included: a cooler whose
    zones do not exist yet gets them in the mode the user already chose.

    MUTATION CHECK -- MEASURED 2026-10-02: create zones with the zone default
    instead of the device's mode → fails.
    """
    from trcc.core.led_models import LEDMode

    app = _fresh(fake_platform, _PA120)
    app.settings.set_led_mode(_LED_KEY, LEDMode.TEMP_LINKED)

    app.dispatch(RenderLed(key=_LED_KEY))

    zones = app.dispatch(LedSnapshot(key=_LED_KEY)).zones
    assert zones and {z.mode for z in zones} == {"TEMP_LINKED"}
