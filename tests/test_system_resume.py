"""Resume-from-suspend reconnect (#189).

After the machine sleeps, the USB transport a device opened beforehand is stale
on wake — writes silently no-op, so the panel stays blank until a manual
restart.  ``App`` subscribes to ``SystemResumed`` (published by the logind
PrepareForSleep listener) and rebuilds each attached device like a replug: stop
the old sender, release the stale transport, ``ConnectDevice`` to re-handshake +
start a fresh sender.  These pin that the device comes back AND can send again,
without dropping its active theme / LED state.
"""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import RenderLed
from trcc.core.events import SystemResumed

from .conftest import FakePlatform, _CliRenderer
from .test_render_led import _LED_KEY, _attach_and_connect, _scripted_handshake


def _app(fake_platform: FakePlatform, pm: int) -> App:
    app = App(fake_platform, renderer=_CliRenderer())  # type: ignore[arg-type]
    _attach_and_connect(app, fake_platform, pm=pm)
    return app


def test_resume_reconnects_the_device(fake_platform: FakePlatform) -> None:
    app = _app(fake_platform, pm=1)
    assert app.get(_LED_KEY).is_connected
    # The handler stops the old sender + releases the transport, then
    # ConnectDevice re-handshakes — feed the reconnect's handshake reply.
    fake_platform.bulk.read_script.append(_scripted_handshake(1))

    app.events.publish(SystemResumed())

    assert app.get(_LED_KEY).is_connected, "device must reconnect after resume"


def test_resume_restores_the_wire_so_renders_send_again(
    fake_platform: FakePlatform,
) -> None:
    """The bug: after wake the device is 'connected' but nothing reaches it.
    A render after resume must actually write to the (reopened) wire."""
    app = _app(fake_platform, pm=1)
    fake_platform.bulk.read_script.append(_scripted_handshake(1))

    app.events.publish(SystemResumed())

    fake_platform.bulk.writes.clear()
    app.dispatch(RenderLed(key=_LED_KEY))
    assert fake_platform.bulk.writes, "a render after resume must reach the wire"


def test_resume_keeps_led_runtime_not_a_blank_detach(
    fake_platform: FakePlatform,
) -> None:
    """Resume must NOT ``detach`` — that would drop the device's LED runtime /
    active theme and the panel would come back blank.  The key stays attached
    throughout (rebuilt in place), so its render state survives."""
    app = _app(fake_platform, pm=1)
    fake_platform.bulk.read_script.append(_scripted_handshake(1))

    app.events.publish(SystemResumed())

    # Still the same attached key (rebuilt), connected and renderable.
    assert _LED_KEY in app.devices
    assert app.get(_LED_KEY).is_connected
