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
from trcc.core.models import DeviceQuirks

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


def test_resume_actually_REBUILDS_the_device(fake_platform: FakePlatform) -> None:
    """Resume must replace the stale device, not merely leave a live one alone.

    Every test above publishes the real event and then asserts the device
    ``is_connected`` — which was ALREADY true when the event was published, and
    stays true if nothing happens at all.  MEASURED 2026-09-10 by deleting
    ``self.events.subscribe(SystemResumed, self._on_system_resumed)`` from
    ``App.__init__`` and running the whole suite: **4830 passed.**  The entire
    wake-from-suspend recovery could be unsubscribed and this file would not
    notice, because it asserts a STATE that holds on both sides of the event
    rather than the TRANSITION the handler exists to perform.

    The transition is observable without simulating a dead USB node:
    ``_on_system_resumed`` disconnects the stale instance and dispatches
    ``ConnectDevice``, which attaches a NEW one.  So identity changes, and the
    old object is left disconnected — neither is true if the handler never ran.
    """
    app = _app(fake_platform, pm=1)
    before = app.get(_LED_KEY)
    assert before.is_connected
    fake_platform.bulk.read_script.append(_scripted_handshake(1))

    app.events.publish(SystemResumed())

    after = app.get(_LED_KEY)
    assert after is not before, (
        "resume must REBUILD the device — a still-connected object of the same "
        "identity means the handler never ran (or is unsubscribed)")
    assert not before.is_connected, "the stale instance must be released"
    assert after.is_connected


def test_a_single_session_panel_keeps_its_session_through_a_wake(
    fake_platform: FakePlatform,
) -> None:
    """#283: a 4.07 panel came back after a wake while nothing reopened it,
    and went black once the wake reopened it -- a reopen wedges that firmware
    (#228).  Its device, transport and sender stay exactly as they were.
    MUTATION CHECK: drop the single-session ``continue`` in
    ``_on_system_resumed`` -- the device is rebuilt."""
    app = _app(fake_platform, pm=1)
    before = app.get(_LED_KEY)
    before.set_quirks(DeviceQuirks(single_session=True))
    sender = app.senders.get(_LED_KEY)

    app.events.publish(SystemResumed())

    assert (app.get(_LED_KEY) is before, before.is_connected,
            app.senders.get(_LED_KEY) is sender) == (True, True, True)
