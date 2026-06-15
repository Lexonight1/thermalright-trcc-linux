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
