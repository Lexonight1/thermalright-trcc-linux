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
    app = App(fake_platform, renderer=_CliRenderer())
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
    """A reopen wedges the Warframe SE's firmware until a replug (#228), so
    a single-session panel's device, transport and sender stay exactly as
    they were through a wake.  MUTATION CHECK: drop the single-session
    ``continue`` in ``_on_system_resumed`` -- the device is rebuilt."""
    app = _app(fake_platform, pm=1)
    before = app.get(_LED_KEY)
    before.set_quirks(DeviceQuirks(single_session=True))
    sender = app.senders.get(_LED_KEY)

    app.events.publish(SystemResumed())

    assert (app.get(_LED_KEY) is before, before.is_connected,
            app.senders.get(_LED_KEY) is sender) == (True, True, True)


# ── Firmware 4.07: only the panel that volunteers the short reply keeps its
#    session (#228, #283, #323) ─────────────────────────────────────────────

_407_KEY = "0416:5302"
#: happyendingll's panel: silent to the probe, PM 52 to the ordinary handshake.
_PM52_407 = {"type": "lcd", "name": "4.07 PM 52", "vid": "0416", "pid": "5302",
             "pm": 52, "bcd": "0407"}
#: The Frozen Warframe SE: volunteers its 8-byte reply before any init packet.
_WARFRAME_SE = {"type": "lcd", "name": "Warframe SE", "vid": "0416",
                "pid": "5302", "pm": 58, "bcd": "0407",
                "reply": "da db dc dd 00 3a 00 00"}


def _app_on(tmp_path, monkeypatch, spec: dict) -> App:
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.core.commands import ConnectDevice

    from .mock_platform import MockPlatform

    monkeypatch.setattr("trcc.adapters.device.hid_lcd.time.sleep",
                        lambda *_: None)
    app = App(platform=MockPlatform([spec], tmp_path),
              send_scheduler=SyncSendScheduler(), renderer=_CliRenderer())
    assert app.dispatch(ConnectDevice(key=_407_KEY)).ok
    return app


def test_a_407_panel_that_answers_the_handshake_is_reopened_on_a_wake(
    tmp_path, monkeypatch,
) -> None:
    """Measured on real glass (#228 run A/B, 2026-10-23): this panel kept its
    session and stayed black after the wake; reopened, it showed the theme.
    MUTATION CHECK: drop the ``single_session`` narrowing in
    ``HidLcd._do_handshake`` -- the wake leaves the stale device in place."""
    app = _app_on(tmp_path, monkeypatch, _PM52_407)
    before = app.get(_407_KEY)
    assert before.quirks.single_session is False

    app.events.publish(SystemResumed())

    after = app.get(_407_KEY)
    assert (after is not before, before.is_connected, after.is_connected,
            app.senders[_407_KEY].device is after) == (True, False, True, True)


def test_the_warframe_se_keeps_its_session_on_a_wake(tmp_path, monkeypatch) -> None:
    app = _app_on(tmp_path, monkeypatch, _WARFRAME_SE)
    before = app.get(_407_KEY)
    assert before.quirks.single_session is True

    app.events.publish(SystemResumed())

    assert app.get(_407_KEY) is before and before.is_connected


def test_a_warframe_se_that_left_the_bus_is_announced_lost(
    tmp_path, monkeypatch,
) -> None:
    """#323's log: ENODEV on every write for 84 s, and ``note_lost`` answered
    ``attached=True lost=False`` -- the App never told anyone or watched for
    the panel's return.  MUTATION CHECK: drop the ``is_gone_error`` exit in
    ``_send_with_recovery`` -- nothing is announced."""
    import errno

    import pytest

    from trcc.core.errors import DeviceDisconnectedError
    from trcc.core.events import DeviceDisconnected, Event

    app = _app_on(tmp_path, monkeypatch, _WARFRAME_SE)
    seen: list[Event] = []
    app.events.subscribe(DeviceDisconnected, seen.append)

    def gone(*_args: object, **_kwargs: object) -> int:
        raise OSError(errno.ENODEV, "No such device")

    app.get(_407_KEY)._transport.write = gone
    with pytest.raises(DeviceDisconnectedError):
        app.send(_407_KEY, b"\x00" * 153600)

    assert seen == [DeviceDisconnected(key=_407_KEY)]
    assert not app.get(_407_KEY).is_connected
