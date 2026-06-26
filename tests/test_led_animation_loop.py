"""LedAnimationLoop — fast tick that animates LED effect modes.

Without it, breathing/colour-cycle/rainbow/carousel only advance on the ~2 s
sensor broadcast and look frozen.  These tests pin: which devices the loop
selects to animate, and that a tick re-renders them (a wire write).
"""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import SetLedMode, SetLedZoneSync
from trcc.core.led_models import LEDMode

from .conftest import FakePlatform, _CliRenderer
from .test_render_led import _LED_KEY, _attach_and_connect


def _app(fake_platform: FakePlatform, pm: int) -> App:
    app = App(fake_platform, renderer=_CliRenderer())  # type: ignore[arg-type]
    _attach_and_connect(app, fake_platform, pm=pm)
    return app


def test_static_led_is_not_animated(fake_platform: FakePlatform) -> None:
    app = _app(fake_platform, pm=1)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.STATIC))
    assert app.led_animation_loop.animating_keys() == []


def test_breathing_led_is_animated(fake_platform: FakePlatform) -> None:
    app = _app(fake_platform, pm=1)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.BREATHING))
    assert app.led_animation_loop.animating_keys() == [_LED_KEY]


def test_carousel_animates_even_when_static(fake_platform: FakePlatform) -> None:
    app = _app(fake_platform, pm=1)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.STATIC))
    app.dispatch(SetLedZoneSync(key=_LED_KEY, enabled=True))
    assert app.led_animation_loop.animating_keys() == [_LED_KEY]


def test_tick_re_renders_animating_led(fake_platform: FakePlatform) -> None:
    """One loop tick must push a fresh frame for an animating device."""
    app = _app(fake_platform, pm=1)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.RAINBOW))
    fake_platform.bulk.writes.clear()
    app.led_animation_loop.tick()
    assert fake_platform.bulk.writes, "animation tick should render a frame"


def test_multi_zone_per_zone_effect_is_animated(fake_platform: FakePlatform) -> None:
    """#193: a multi-zone device (PA120) with an effect set on its zones — and
    "select all" (zone_sync) OFF — must still be ticked.

    On a multi-zone style ``SetLedMode`` writes each selected ZONE's mode and
    leaves the device-level ``mode`` at STATIC, so gating ``animating_keys`` on
    ``s.mode`` alone left the device unticked → breathing/colour-cycle/rainbow
    looked frozen while a solid colour worked.  The loop must see the zone modes.
    """
    app = _app(fake_platform, pm=16)   # PA120 — multi-zone (per-zone modes)
    app.dispatch(SetLedMode(key=_LED_KEY, mode=LEDMode.RAINBOW))
    s = app.settings.for_led(_LED_KEY)
    assert s.mode is LEDMode.STATIC, "device-level mode stays STATIC on a zone style"
    assert not s.zone_sync, "zone_sync (select-all) is off by default"
    assert any(z.mode is LEDMode.RAINBOW for z in s.zones), "a zone carries the effect"
    assert app.led_animation_loop.animating_keys() == [_LED_KEY]
