"""The screencast driver — what made CLI / API / daemon capture anything.

``StartScreencast`` used to only publish ``ScreencastStarted``, and the GUI's
``ScreencastHandler`` was the sole subscriber that ran a timer.  Every other
client printed "Capturing on …" and captured nothing.  The Command now starts
the driver itself, and the gui's timer is gone.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar, TypedDict

import pytest

from trcc.adapters.infra.send_scheduler import (
    SyncSendScheduler,
    ThreadSendScheduler,
)
from trcc.app import App
from trcc.core.commands import (
    CaptureSourceFrame,
    ConnectDevice,
    SendScreencastFrame,
    StartScreencast,
    StopScreencast,
)
from trcc.core.events import Event, ScreencastStopped
from trcc.core.models import SCREENCAST_TICK_S, MediaPlayerBackground, PanelSource, RawFrame
from trcc.services.source_driver import TELL_AFTER_S, SourceDriver
from trcc.services.source_driver import task_key as _live_task_key

from .conftest import FakeMic, FakePlatform, FakeScreenCapture, _CliRenderer
from .mock_platform import MockPlatform
from .test_display_rotation import RecordingRenderer

_KEY = "0402:3922"


def task_key(device_key: str) -> str:
    """The cast's driver key -- what every test in this file is about."""
    return _live_task_key(device_key, PanelSource.SCREENCAST)


class _Region(TypedDict):
    x: int
    y: int
    w: int
    h: int


_REGION = _Region(x=10, y=20, w=64, h=48)


def _capture(app: App) -> FakeScreenCapture:
    """The fake desktop grab behind *app* -- every fixture here is a FakePlatform."""
    platform = app.platform
    assert isinstance(platform, FakePlatform)
    return platform.capture


def _stops(app: App) -> list[str]:
    """Every key a ``ScreencastStopped`` names from here on."""
    stopped: list[str] = []

    def on_stopped(e: Event) -> None:
        assert isinstance(e, ScreencastStopped)
        stopped.append(e.key)
    app.events.subscribe(ScreencastStopped, on_stopped)
    return stopped


@pytest.fixture
def scheduler() -> SyncSendScheduler:
    return SyncSendScheduler()


@pytest.fixture
def app(tmp_home: Path, scheduler: SyncSendScheduler) -> App:
    platform = FakePlatform(tmp_home)
    a = App(platform=platform, send_scheduler=scheduler,
            renderer=_CliRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100                      # FBL=100 → 320x320
    platform.scsi.read_script.append(bytes(resp))
    assert a.dispatch(ConnectDevice(key=_KEY)).ok
    return a


@pytest.fixture
def casting(app: App) -> App:
    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    return app


# ── the frame path ───────────────────────────────────────────────────


# The Phantom Spirit 120 Vision EVO: BULK, 480x480, and its handshake answers
# ``jpeg=True`` while the static registry row does NOT.  That disagreement is
# the whole point — on a device where the fallback happens to agree, omitting
# the profile is invisible, which is why this gate names a device where it is
# not.  Measured over the 9-LCD mock fleet, exactly two devices disagree:
# this one (jpeg) and 0416:5302 (320x240 rot=90 vs 240x320 rot=0).
_EVO = {"vid": "87ad", "pid": "70db", "pm": 4, "sub": 4,
        "name": "Phantom Spirit 120 Vision EVO"}
_EVO_KEY = "87ad:70db"


@pytest.fixture
def evo(tmp_home: Path, scheduler: SyncSendScheduler) -> App:
    """A connected EVO with a renderer that records which encoder ran."""
    a = App(platform=MockPlatform([_EVO], tmp_home), send_scheduler=scheduler,
            renderer=RecordingRenderer())
    assert a.dispatch(ConnectDevice(key=_EVO_KEY)).ok
    return a


def test_the_screencast_frame_is_encoded_for_the_PANEL_not_the_registry(
    evo: App,
) -> None:
    """``SendScreencastFrame`` must hand the encoder the LIVE profile.

    Six of the seven ``app.display.build_*`` call sites pass
    ``profile=device.profile``; this one did not, so ``_resolve_profile``
    fell back to the registry FBL / a synthesised RGB565 profile.  On this
    panel that is not cosmetic: the handshake says JPEG and the fallback says
    RGB565, so every captured frame went out as **460,800 bytes instead of
    6,927** — the "460KB/frame" @alan7383 reported in #271, reproduced here.

    Asserted as WHICH ENCODER RAN rather than as a byte count: the count is a
    consequence of the choice, and the choice is the bug.
    """
    device = evo.devices[_EVO_KEY]
    assert device.profile is not None and device.profile.jpeg is True, (
        "fixture no longer models a JPEG panel — this gate is then vacuous"
    )

    renderer = evo.renderer
    assert isinstance(renderer, RecordingRenderer)
    renderer.calls.clear()
    result = evo.dispatch(SendScreencastFrame(
        key=_EVO_KEY, frame=RawFrame(b"", 64, 64),
    ))
    assert result.ok is True, result.message

    ran = [name for name, _ in renderer.calls]
    assert "encode_jpeg" in ran, (
        "the panel handshook as JPEG and the frame was encoded some other "
        "way — the live profile is not reaching build_screencast_frame"
    )
    assert "encode_rgb565" not in ran, (
        "encoded RGB565 for a JPEG panel: that is the 66x oversized frame"
    )



def test_capture_grabs_the_region_the_session_declared(casting: App) -> None:
    """The region comes from ``screencast_region``, not from the caller.

    One home for "is a screencast running and over what" — the Command takes
    only a key, so a driver cannot drift from what StartScreencast persisted.
    """
    result = casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))

    assert result.ok is True, result.message
    assert _capture(casting).regions == [(10, 20, 64, 48)]


def test_a_tick_with_no_region_ends_the_session(
    casting: App, scheduler: SyncSendScheduler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another source clearing the region ends the cast properly, once.

    ``SetBackground`` / ``PlayVideo`` / ``SetMediaPlayer`` keep the display
    sources exclusive by clearing ``screencast_region`` in Settings.  The
    driver used to go on ticking with nothing to capture: a WARNING every tick,
    forever, and no ``ScreencastStopped`` for the UIs' buttons.

    MUTATION CHECK: drop the ``StopScreencast`` dispatch from the no-region
    branch and the task survives.
    """
    stopped = _stops(casting)
    casting.settings.set_screencast_region(_KEY, None)   # what a source switch does
    caplog.set_level(logging.WARNING)

    result = casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))
    scheduler.tick(1.0)

    assert result.ok is True, result.message
    assert result.message == f"screencast on {_KEY} ended"
    assert task_key(_KEY) not in scheduler._tasks
    assert stopped == [_KEY]
    assert _capture(casting).regions == []
    assert [r.getMessage() for r in caplog.records
            if "CaptureSourceFrame" in r.getMessage()] == []


def test_a_failing_grab_does_not_raise(casting: App, monkeypatch) -> None:
    """Capture depends on a desktop session that can vanish mid-cast.

    Screen locked, portal consent revoked, ``grim`` uninstalled — the frame is
    lost, the session is not.

    MUTATION CHECK: drop the try/except in the Command and this raises.
    """
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("portal session revoked")

    monkeypatch.setattr(_capture(casting), "grab_region", boom)

    result = casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))

    assert result.ok is False
    assert "portal session revoked" in result.message


def test_a_source_that_is_not_ready_yet_is_quiet(
    casting: App, monkeypatch, caplog,
) -> None:
    """The portal's consent seconds are a dropped frame, not an error.

    ``CaptureNotReady`` fires on every tick of a consent window -- seven a
    second for up to thirty seconds.  ``App.dispatch`` escalates every
    ``ok=False`` to WARNING regardless of level, so the Command reports a
    completed tick with no frame, and the only line is a DEBUG one.
    """
    from trcc.core.ports import CaptureNotReady

    def not_yet(*_a: object, **_k: object) -> None:
        raise CaptureNotReady("waiting for consent")

    monkeypatch.setattr(_capture(casting), "grab_region", not_yet)
    with caplog.at_level(logging.DEBUG, logger="trcc"):
        result = casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))

    assert result.ok is True, "a consent wait was reported as a failure"
    assert "waiting for consent" in result.message
    loud = [r for r in caplog.records
            if r.levelno >= logging.WARNING and "CaptureSourceFrame" in r.getMessage()]
    assert not loud, [r.getMessage() for r in loud]


# ── the cadence ──────────────────────────────────────────────────────


def test_the_driver_key_cannot_collide_with_the_device_sender(app: App) -> None:
    """THE trap this design turns on.

    ``ThreadSendScheduler.add`` evicts — and STOPS — any task already
    registered under the same key.  A driver registered under the bare device
    key would kill that device's ``DeviceSender`` and its keepalives, so the
    panel would go dark the moment a screencast started.

    MUTATION CHECK: return the bare device key from ``SourceDriver.key``
    and this fails.
    """
    driver = SourceDriver(app, _KEY, PanelSource.SCREENCAST)

    assert driver.key != _KEY
    assert driver.key == task_key(_KEY) == f"live:screencast:{_KEY}"


def test_driving_does_not_evict_the_device_sender(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """The same trap, proved through the scheduler rather than by inspection."""
    casting.start_sender(_KEY)
    assert _KEY in scheduler._tasks

    assert casting.dispatch(StartScreencast(key=_KEY, **_REGION)).ok

    assert _KEY in scheduler._tasks, "the screencast driver evicted the sender"
    assert task_key(_KEY) in scheduler._tasks


def test_a_cast_that_keeps_failing_tells_the_uis_once_and_carries_on(
    casting: App, scheduler: SyncSendScheduler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three seconds of failed grabs reach the UIs ONCE; the session stays
    (a locked screen must not end it); after it captures again, the next run
    of failures is told again.  It used to say nothing at all -- the shared
    Windows App's offscreen Qt failed every tick for 13 minutes (2026-10-20).

    MUTATION CHECK: publish on every failed tick -- 150 messages, not 1."""
    from trcc.core.events import ErrorOccurred
    FAILED_TICKS_BEFORE_TELLING = round(TELL_AFTER_S / SCREENCAST_TICK_S)   # 50

    told: list[ErrorOccurred] = []
    casting.events.subscribe(ErrorOccurred, told.append)
    stopped = _stops(casting)
    capture = _capture(casting)
    works = capture.grab_region

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("Qt returned a blank pixmap")

    monkeypatch.setattr(capture, "grab_region", boom)
    for tick in range(FAILED_TICKS_BEFORE_TELLING - 1):
        scheduler.tick(float(tick))
    assert told == []
    for tick in range(100):
        scheduler.tick(float(FAILED_TICKS_BEFORE_TELLING + tick))
    assert [(e.kind, e.key) for e in told] == [("screencast", _KEY)]
    assert "Qt returned a blank pixmap" in told[0].message
    assert stopped == [], "a failing capture must not end the session"

    monkeypatch.setattr(capture, "grab_region", works)
    scheduler.tick(1000.0)
    monkeypatch.setattr(capture, "grab_region", boom)
    for tick in range(FAILED_TICKS_BEFORE_TELLING):
        scheduler.tick(1001.0 + tick)
    assert len(told) == 2


@pytest.mark.parametrize(("work_s", "wait_s"), [(0.025, 0.035), (0.1, 0.0)])
def test_the_cast_runs_on_a_beat_not_a_rest_after_each_frame(
    casting: App, monkeypatch: pytest.MonkeyPatch, work_s: float, wait_s: float,
) -> None:
    """A 25 ms frame leaves 35 ms of the 60 ms beat; an overrun waits nothing.
    It waited the full 60 ms after every frame: 142 ms apart on the win11 VM,
    6.9 fps for 16.7 (2026-10-20).

    MUTATION CHECK: drop FIXED_BEAT from the driver -- it waits 60 ms."""
    from types import SimpleNamespace

    from trcc.services import _send_task

    clock = iter((100.0, 100.0 + work_s))
    monkeypatch.setattr(_send_task, "time", SimpleNamespace(perf_counter=lambda: next(clock)))

    wait = SourceDriver(casting, _KEY, PanelSource.SCREENCAST,
                        SCREENCAST_TICK_S).run_once(0.0)

    assert wait == pytest.approx(wait_s)


def test_a_task_that_rests_keeps_its_whole_rest(
    app: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FIXED_BEAT`` is opt-in: a poll's interval stays a rest, however long
    the turn took.

    MUTATION CHECK: default FIXED_BEAT to True -- this waits 0.3 s, not 0.5."""
    from types import SimpleNamespace

    from trcc.services import _send_task

    class _Rests(_send_task.BaseSendTask):
        KEY_PREFIX = "rests:"
        DEFAULT_INTERVAL_S = 0.5

        def _run(self, now: float) -> float:
            return self._interval

    clock = iter((100.0, 100.2))
    monkeypatch.setattr(_send_task, "time", SimpleNamespace(perf_counter=lambda: next(clock)))

    assert _Rests(app, _KEY).run_once(0.0) == 0.5


def test_a_cast_frame_is_queued_not_waited_for(
    casting: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-frame path hands the frame to the sender and returns, as
    RenderAndSend does.  It waited for the USB write -- on Windows four pipe
    round trips to TRCCService and four SCSI writes inside every turn
    (2026-10-20).

    MUTATION CHECK: send with the default ``wait=True`` -- this fails."""
    waits: list[bool] = []
    real = casting.send

    def spy(key: str, payload: object, *, wait: bool = True) -> bool:
        waits.append(wait)
        return real(key, payload, wait=wait)

    monkeypatch.setattr(casting, "send", spy)

    assert casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST)).ok
    assert waits == [False]


def test_each_tick_captures_one_frame(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    for tick in range(5):
        scheduler.tick(float(tick))

    assert len(_capture(casting).regions) == 5
    assert set(_capture(casting).regions) == {(10, 20, 64, 48)}


def test_stopping_the_screencast_stops_its_driver(
    casting: App, scheduler: SyncSendScheduler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One Command ends the screencast AND its driver, for every UI.

    The CLI's ``stop-screencast`` sent only StopScreencast; against a daemon the
    driver kept ticking at 16 Hz, every tick a WARNING in the log file.
    MUTATION CHECK: drop the task removal from StopScreencast → this fails.
    """
    scheduler.tick(0.0)
    caplog.set_level(logging.WARNING)

    assert casting.dispatch(StopScreencast(key=_KEY)).ok
    scheduler.tick(1.0)
    scheduler.tick(2.0)

    assert task_key(_KEY) not in scheduler._tasks
    assert len(_capture(casting).regions) == 1
    # The driver's symptom was THIS warning, every tick.  (A re-render after
    # the stop can time out waiting on the synchronous test scheduler; that
    # is this harness, not the driver.)
    assert [r.getMessage() for r in caplog.records
            if "CaptureSourceFrame" in r.getMessage()] == []


def test_disconnecting_stops_the_driver(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """Letting the panel go removes the driver; it is namespaced, so the
    removal has to be explicit (``App.stop_sources``).

    MUTATION CHECK: drop the ``remove(task_key(key))`` from ``stop_sources``
    and this fails.
    """
    assert task_key(_KEY) in scheduler._tasks

    casting.detach(_KEY)

    assert task_key(_KEY) not in scheduler._tasks


def test_disconnecting_stops_a_module_sources_driver(
    app: App, scheduler: SyncSendScheduler,
) -> None:
    """The module-source driver is let go the same way, and its source is
    closed.  ``stop_sources`` named every driver but this one, so a panel
    that left kept a ``source:`` task polling while it was away.

    MUTATION CHECK: drop the ``remove(source_task(key))`` from
    ``stop_sources`` and this fails.
    """
    from trcc.core.ports import CaptureNotReady, FrameSource
    from trcc.services.source_driver import task_key as live_key

    class _Still(FrameSource):
        name = "still"
        closed = False

        def latest(self) -> RawFrame:
            raise CaptureNotReady("nothing yet")

        def close(self) -> None:
            self.closed = True

    source = _Still()
    app.show_source("probe", _KEY, source, 30.0)
    assert live_key(_KEY, PanelSource.MODULE) in scheduler._tasks

    app.detach(_KEY)

    assert (live_key(_KEY, PanelSource.MODULE) in scheduler._tasks, source.closed) == (False, True)


def test_a_pushed_picture_is_not_painted_over_by_the_cast(casting: App) -> None:
    """A push holds the panel, and every implicit producer honours the hold
    (``_take_over_panel``) -- the cast too.  It had no check, so the next
    capture tick, a seventh of a second later, painted over the picture.

    MUTATION CHECK: drop the ``held`` check in ``CaptureSourceFrame``
    and the screen is grabbed again.
    """
    from trcc.core.commands import SendColor

    grabbed = len(_capture(casting).regions)
    assert casting.dispatch(SendColor(key=_KEY, r=255, g=0, b=0)).ok

    result = casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))

    assert (result.ok, result.message, len(_capture(casting).regions)) == (
        True, "held", grabbed)


def test_a_blink_keeps_the_cast(casting: App, scheduler: SyncSendScheduler) -> None:
    """A wake or a replug drops the WIRE; the cast keeps going.

    MUTATION CHECK: remove the driver in ``stop_sender`` again and every wake
    freezes the panel on its last frame while every window says "casting".
    """
    casting.stop_sender(_KEY)
    assert task_key(_KEY) in scheduler._tasks


def test_nothing_is_captured_for_a_panel_that_is_away(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """A lost panel costs no screen capture."""
    capture = _capture(casting)
    casting.devices[_KEY].disconnect()
    driver = scheduler._tasks[task_key(_KEY)]
    grabbed = len(capture.regions)

    for now in range(100, 110):
        driver.run_once(float(now))

    assert len(capture.regions) == grabbed


def test_starting_a_screencast_starts_its_driver(
    app: App, scheduler: SyncSendScheduler,
) -> None:
    """One Command, and it captures — for every face, and for ``LoadTheme``.

    The driver used to be a second Command each UI paired by hand, so a saved
    screencast theme (which dispatches only ``StartScreencast``) answered
    "screencast started" and captured nothing outside the gui.

    MUTATION CHECK: drop ``add_task`` from ``StartScreencast`` → no task.
    """
    assert app.dispatch(StartScreencast(key=_KEY, **_REGION)).ok
    assert task_key(_KEY) in scheduler._tasks

    scheduler.tick(0.0)
    assert _capture(app).regions == [(10, 20, 64, 48)]


def test_a_refused_screencast_starts_no_driver(
    app: App, scheduler: SyncSendScheduler,
) -> None:
    """A zero-area region is refused before anything is registered."""
    assert not app.dispatch(StartScreencast(key=_KEY, x=0, y=0, w=0, h=10)).ok
    assert task_key(_KEY) not in scheduler._tasks


def test_stopping_a_screencast_nobody_started_is_fine(app: App) -> None:
    """Idempotent: a script may stop defensively."""
    assert app.dispatch(StopScreencast(key=_KEY)).ok


def test_stopping_releases_the_capture_source(
    casting: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A portal stream stays open until ``stop``, streaming a screen nobody is
    showing.  Only the gui's own timer used to release it; the Command now
    does, for every face.

    MUTATION CHECK: drop the ``stop()`` call from ``StopScreencast``.
    """
    stops: list[int] = []
    monkeypatch.setattr(_capture(casting), "stop", lambda: stops.append(1))

    assert casting.dispatch(StopScreencast(key=_KEY)).ok

    assert stops == [1]


def test_stop_screencast_leaves_no_region_for_the_driver(casting: App) -> None:
    """After StopScreencast an in-flight driver tick grabs nothing.

    The region IS the session flag, so clearing it is what makes that tick
    harmless.
    """
    assert casting.dispatch(StopScreencast(key=_KEY)).ok

    casting.dispatch(CaptureSourceFrame(key=_KEY, kind=PanelSource.SCREENCAST))
    assert _capture(casting).regions == []


# ── one producer owns the panel ──────────────────────────────────────


def _render_observer(app: App):
    """The App's own ``_DeviceRenderObserver``, whichever bus slot it sits in."""
    from trcc.app import _DeviceRenderObserver
    for attr in vars(app).values():
        if isinstance(attr, _DeviceRenderObserver):
            return attr
    raise AssertionError("no _DeviceRenderObserver on the App")


def test_a_running_screencast_suppresses_the_reactive_theme_render(
    casting: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE blink, gated.

    Reported 2026-09-14: "it does capture the screen but it blinks the metrics
    mask".  ``_DeviceRenderObserver`` dispatched ``RenderAndSend`` on every
    ``SensorsUpdated`` with no screencast check, so a second producer wrote the
    same panel — a full theme frame — while the capture tick was writing screen
    frames at ``SCREENCAST_TICK_S`` (~7 fps).  Measured in the reporter's log:
    ~13 capture frames, then one theme frame, every 2 s.

    The two composites are genuinely different pictures (one's background is
    the captured region, the other's is the theme), so this is not a redundant
    frame — it is a visible flash.

    MUTATION CHECK: drop the ``screencast_region is not None`` guard in
    ``_DeviceRenderObserver._on_visual_change`` and this fails.
    """
    from trcc.core.events import SensorsUpdated

    # An ACTIVE THEME is required or this passes vacuously: the observer
    # skips a device with no theme BEFORE it reaches the screencast guard, so
    # without this the first assertion proves nothing.  (Caught by the
    # "resumes after stop" half below refusing to fire.)
    from trcc.core.models import Theme
    casting.active_themes[_KEY] = Theme(name="T", path=Path("/nonexistent"), resolution=(320, 320))

    rendered: list[str] = []
    observer = _render_observer(casting)
    monkeypatch.setattr(
        observer, "_RenderAndSend",
        lambda key: (rendered.append(key), _Noop())[1],
    )

    casting.events.publish(SensorsUpdated(readings={}))
    assert rendered == [], (
        "a theme render was dispatched while a screencast owned the panel — "
        "two producers, which is the blink"
    )

    # …and it resumes the moment the session ends.
    assert casting.dispatch(StopScreencast(key=_KEY)).ok
    rendered.clear()
    casting.events.publish(SensorsUpdated(readings={}))
    assert rendered == [_KEY], (
        "the theme render did not resume after the screencast stopped"
    )


class _Noop:
    """A Command the stubbed observer can dispatch harmlessly.

    ``LOG_LEVEL`` is not decoration: ``App.dispatch`` reads it to pick a
    logger, so without it the mutation check fails on an AttributeError
    instead of on the assertion it is meant to prove.
    """

    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    #: No capability: ``App.dispatch`` reads it for its gate, as it does
    #: ``LOG_LEVEL``.
    REQUIRES: ClassVar[None] = None

    def execute(self, app: App) -> None:
        return None


def test_the_metrics_actually_reach_the_screencast_pixels(tmp_home: Path) -> None:
    """BEHAVIOURAL, with the real renderer — the layer must change the bytes.

    The sibling gates in ``test_wire_frame_shape`` count ``composite`` CALLS,
    and that is structural: driving this by hand with a real ``QtRenderer``
    showed a frame whose composite call fired while the output stayed
    byte-identical, because an overlay with no elements paints nothing.  A
    call-count gate would have reported the blink fixed while the panel still
    showed a bare desktop.

    So this asserts what a user sees: same LENGTH (the geometry did not move)
    and different CONTENT (something was actually drawn).
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import OverlayElement, RawFrame, Theme

    platform = FakePlatform(tmp_home)
    app = App(platform=platform, renderer=QtRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 → 320x320
    platform.scsi.read_script.append(bytes(resp))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    info = app.get(_KEY).info
    frame = RawFrame(data=bytes([128, 128, 128]) * (320 * 320),
                     width=320, height=320)
    theme = Theme(name="T", path=tmp_home, resolution=(320, 320))
    app.settings.for_device(_KEY).overlay_enabled = True
    for i, y in enumerate((40, 90, 140)):
        app.settings.add_user_overlay_element(_KEY, OverlayElement(
            id=f"e{i}", type="text", text="CPU", x=40, y=y,
            size=36, color="#ff0000"))

    bare = app.display.build_screencast_frame(info=info, frame=frame, theme=None)
    drawn = app.display.build_screencast_frame(
        info=info, frame=frame, theme=theme, sensors={})

    assert len(bare) == len(drawn), (
        "compositing the overlay changed the frame SIZE — the geometry moved"
    )
    assert bare != drawn, (
        "the overlay composited but changed no pixel — the metrics are not "
        "reaching the panel, which is the reported defect"
    )


def test_the_preview_shows_the_same_picture_as_the_panel(tmp_home: Path) -> None:
    """The preview surface must be the COMPOSITED frame, not the raw grab.

    Reported 2026-09-14, immediately after the mask/metrics fix landed: "it
    does not show the metrics mask in preview just on the lcd screen".  The
    gui painted its preview directly from the captured image
    (``lcd_handler.on_screencast_frame``) while the wire frame went through
    the compositor, so the two diverged the moment the wire frame gained a
    layer.

    ``SendScreencastFrame`` now publishes ``FrameSent`` carrying the composited
    surface, the same way ``RenderAndSend`` does, so both faces read one
    picture.  Asserted on the SURFACE the event would carry, because that is
    the thing the gui paints.

    MUTATION CHECK: drop the ``_remember_preview`` call in
    ``build_screencast_frame`` and ``rendered_surface`` answers None — the
    preview would go blank rather than stale, which is why None is asserted
    against explicitly.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import OverlayElement, RawFrame, Theme

    platform = FakePlatform(tmp_home)
    app = App(platform=platform, renderer=QtRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100
    platform.scsi.read_script.append(bytes(resp))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    info = app.get(_KEY).info
    theme = Theme(name="T", path=tmp_home, resolution=(320, 320))
    app.active_themes[_KEY] = theme
    app.settings.for_device(_KEY).overlay_enabled = True
    app.settings.add_user_overlay_element(_KEY, OverlayElement(
        id="e0", type="text", text="CPU", x=40, y=40, size=36, color="#ff0000"))

    grab = RawFrame(data=bytes([128, 128, 128]) * (320 * 320),
                    width=320, height=320)
    app.display.build_screencast_frame(
        info=info, frame=grab, theme=theme, sensors={})

    preview = app.display.rendered_surface(_KEY)
    assert preview is not None, (
        "no preview surface was recorded — the gui would show a blank panel"
    )

    # The composited preview must differ from the flat grey grab: if it does
    # not, the preview is the raw capture again and the defect is back.
    bare = app.display.build_screencast_frame(info=info, frame=grab, theme=None)
    composited = app.display.build_screencast_frame(
        info=info, frame=grab, theme=theme, sensors={})
    assert bare != composited, (
        "the preview path is showing an uncomposited frame — the mask and "
        "metrics reach the panel but not the preview"
    )


# ── the cadence is ONE fact, and it comes from the C# ────────────────────

def test_the_screencast_cadence_matches_the_c_sharp_oracle() -> None:
    """~16.7 fps, because that is what the app being ported does.

    ``TRCC.CZTV/FormCZTV.Timer_event`` runs its screen-cast branch
    (``myMode == 16``) behind ``if (++TPXSCount >= 4)``, off the one timer
    interval in the whole 2.1.6 decompile -- ``m_timer.Interval = 15`` at
    ``TRCC/Form1.cs:502``.  Four ticks of 15 ms is 60 ms.

    This was 0.15 from the cutover until 2026-09-18: invented, never measured
    against the oracle, and 2.5x slower than the program it ports.
    """
    assert pytest.approx(0.06) == SCREENCAST_TICK_S, (
        f"{1 / SCREENCAST_TICK_S:.1f} fps — the C# casts 4 x 15 ms = 16.7")


def test_every_face_starts_at_the_same_cadence(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """One rate, the C#'s, and no field to vary it by UI.

    qtgui had a 1-30 fps slider and was the only face that could set
    ``StartScreencast.interval_s``; cli, api and gui always took the default,
    recorded as three ``scoped:`` exceptions.  The C# casts at one fixed rate,
    so the field went with the slider (2026-10-01): a cadence no Command can
    carry is a cadence no UI can make different.
    """
    import dataclasses

    assert "interval_s" not in {f.name for f in dataclasses.fields(StartScreencast)}
    driver = scheduler._tasks[task_key(_KEY)]
    assert isinstance(driver, SourceDriver)
    assert driver._interval == SCREENCAST_TICK_S


# ── one display source at a time ─────────────────────────────────────
#
# On the REAL threaded scheduler: case B has the driver end the session from
# its own thread, and ``ThreadSendScheduler.remove`` used to join the thread
# it was called from -- "cannot join current thread".


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def threaded(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    """A connected panel on the production scheduler, recording thread deaths."""
    import threading

    from trcc.adapters.render.qt import QtRenderer

    deaths: list[str] = []
    monkeypatch.setattr(threading, "excepthook",
                        lambda a: deaths.append(repr(a.exc_value)))
    platform = FakePlatform(tmp_home)
    a = App(platform=platform, renderer=QtRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100
    platform.scsi.read_script.append(bytes(resp))
    assert a.dispatch(ConnectDevice(key=_KEY)).ok
    stopped = _stops(a)
    yield a, stopped, deaths
    a.close()


def _driving(app: App) -> bool:
    scheduler = app._send_scheduler
    assert isinstance(scheduler, ThreadSendScheduler)
    return task_key(_KEY) in scheduler._threads


def test_a_saved_screencast_theme_captures_without_the_gui(
    threaded, tmp_home: Path,
) -> None:
    """Case C: ``LoadTheme`` of a screencast theme answered "screencast
    started" and captured nothing in qtgui, the CLI, the API and the daemon.
    """
    from trcc.core.commands import LoadTheme, SaveTheme

    from .conftest import renderable_theme

    app, _stopped, deaths = threaded
    plain = renderable_theme(tmp_home / "themes", "Plain")
    assert app.dispatch(LoadTheme(key=_KEY, path=plain)).ok
    assert app.dispatch(StartScreencast(key=_KEY, x=5, y=6, w=64, h=48)).ok
    saved = app.dispatch(SaveTheme(key=_KEY, name="castme"))
    assert saved.ok, saved.message
    assert app.dispatch(StopScreencast(key=_KEY)).ok
    grabs = len(app.platform.capture.regions)

    loaded = app.dispatch(LoadTheme(key=_KEY, path=Path(saved.message.split(" at ", 1)[1])))

    assert loaded.ok, loaded.message
    assert _driving(app), "the theme started a session nothing drives"
    assert _wait_for(lambda: len(app.platform.capture.regions) > grabs), \
        "no frame was captured"
    assert app.platform.capture.regions[-1] == (5, 6, 64, 48)
    assert deaths == []


def test_loading_a_plain_theme_ends_the_screencast(threaded, tmp_home: Path) -> None:
    """Case A: the capture ran on behind the theme just picked.

    MUTATION CHECK: drop the StopScreencast branch from ``LoadTheme``.
    """
    from trcc.core.commands import LoadTheme

    from .conftest import renderable_theme

    app, stopped, deaths = threaded
    plain = renderable_theme(tmp_home / "themes", "Plain")
    assert app.dispatch(LoadTheme(key=_KEY, path=plain)).ok
    assert app.dispatch(StartScreencast(key=_KEY, **_REGION)).ok
    assert _driving(app)

    assert app.dispatch(LoadTheme(key=_KEY, path=plain)).ok

    assert not _driving(app)
    assert app.settings.for_device(_KEY).screencast_region is None
    assert stopped == [_KEY]
    assert deaths == []


def test_a_background_ends_the_screencast_from_the_driver_thread(
    threaded, tmp_home: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Case B: ``SetBackground`` cleared the region and left the driver
    ticking, a WARNING every tick, with no stop event for the buttons.

    MUTATION CHECK: drop the own-thread guard in ``_TaskThread.stop`` and the
    driver thread dies on "cannot join current thread".
    """
    from PySide6.QtGui import QColor, QImage

    from trcc.core.commands import SetBackground

    from .conftest import show_a_theme

    app, stopped, deaths = threaded
    show_a_theme(app, _KEY)
    image = tmp_home / "bg.png"
    img = QImage(320, 320, QImage.Format.Format_RGB888)
    img.fill(QColor(200, 0, 0))
    assert img.save(str(image))
    assert app.dispatch(StartScreencast(key=_KEY, **_REGION)).ok
    caplog.set_level(logging.WARNING)

    assert app.dispatch(SetBackground(key=_KEY, path=image)).ok

    assert _wait_for(lambda: stopped == [_KEY]), "the session never ended"
    assert _wait_for(lambda: not _driving(app))
    assert deaths == []
    assert [r.getMessage() for r in caplog.records
            if "CaptureSourceFrame" in r.getMessage()] == []


def test_a_broken_capture_warns_once_not_every_tick(
    casting: App, scheduler: SyncSendScheduler, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gui's timer warned once; the core path warned twice per tick.

    MUTATION CHECK: make ``App.dispatch`` use ``log.warning`` for every
    failure again → five warnings.
    """
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("grim is not installed")

    monkeypatch.setattr(_capture(casting), "grab_region", boom)
    caplog.set_level(logging.WARNING)

    for tick in range(5):
        scheduler.tick(float(tick))

    loud = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and "grim is not installed" in r.getMessage()]
    assert len(loud) == 1, loud


# ── the microphone ───────────────────────────────────────────────────
#
# One microphone, many panels.  Both lifecycle Commands used to carry HALF the
# rule and neither carried the whole one: ``StartScreencast`` only ever
# STARTED (``if self.audio and not app.audio.running``) and ``StopScreencast``
# only ever stopped.  Re-issuing a live session with ``audio=False`` — which
# is how every face turns the bars off mid-cast — therefore persisted the new
# flag and left the microphone open, and the spectrum gate is
# ``app.audio.running`` rather than that flag, so the bars kept drawing.
#
# MUTATION CHECK: make ``_sync_audio`` start-only again (drop its ``elif``)
# and ``test_audio_off_mid_cast_releases_the_microphone`` must fail on the
# stop count, not on ``running`` — a fake that never stops still reads
# ``running=True``, so the counts are what prove nothing released it.


@pytest.fixture
def mic(app: App, monkeypatch: pytest.MonkeyPatch) -> FakeMic:
    """Replace ``App.audio`` — a plain attribute (``app.py:216``).

    Assigned HERE and not on ``_audio``: a probe that wrote ``app._audio``
    silently measured the real ``AudioCapture`` instead and its reading
    depended on whether the box running it had a microphone.
    """
    fake = FakeMic()
    monkeypatch.setattr(app, "audio", fake)
    return fake


def test_audio_off_mid_cast_releases_the_microphone(
    app: App, mic: FakeMic,
) -> None:
    """THE regression — driven, with counts rather than a flag."""
    assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    assert (mic.running, mic.starts, mic.stops) == (True, 1, 0)

    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    assert (mic.running, mic.starts, mic.stops) == (False, 1, 1), (
        "re-issuing with audio=False left the microphone open — the spectrum "
        "gate is app.audio.running, so the bars keep drawing after the user "
        "turned them off"
    )
    region = app.settings.for_device(_KEY).screencast_region
    assert region is not None
    assert region[4] is False


def test_audio_on_mid_cast_opens_the_microphone(
    app: App, mic: FakeMic,
) -> None:
    """And the other direction, which is how the bars are turned ON."""
    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    assert (mic.running, mic.starts) == (False, 0)

    assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    assert (mic.running, mic.starts, mic.stops) == (True, 1, 0)


def test_re_issuing_the_same_flag_does_not_churn_the_microphone(
    app: App, mic: FakeMic,
) -> None:
    """Idempotent: the rule compares demand to state, it does not toggle."""
    for _ in range(3):
        assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    assert (mic.running, mic.starts, mic.stops) == (True, 1, 0)


def test_stopping_one_cast_keeps_the_microphone_for_another(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two panels, one microphone — the reason demand is asked per FLEET.

    ``_sync_audio`` reads every device's persisted flag, so turning the
    second device's audio off must not silence the first one's bars.  The
    one-device tests above pass equally well against a rule that asks only
    about the device in hand; this is the one that does not.
    """
    other = "87ad:70db"
    app = App(
        platform=MockPlatform(
            [{"vid": "0402", "pid": "3922", "fbl": 100},
             {"vid": "87ad", "pid": "70db", "fbl": 100}],
            tmp_home,
        ),
        send_scheduler=SyncSendScheduler(), renderer=_CliRenderer(),
    )
    app.attach(0x0402, 0x3922)
    app.attach(0x87AD, 0x70DB)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.dispatch(ConnectDevice(key=other)).ok
    mic = FakeMic()
    monkeypatch.setattr(app, "audio", mic)

    assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    assert app.dispatch(StartScreencast(key=other, audio=True, **_REGION)).ok
    assert (mic.running, mic.starts, mic.stops) == (True, 1, 0)

    assert app.dispatch(StartScreencast(key=other, audio=False, **_REGION)).ok
    assert (mic.running, mic.stops) == (True, 0), (
        "turning the second panel's audio off silenced the first panel's bars"
    )

    assert app.dispatch(StopScreencast(key=_KEY)).ok
    assert (mic.running, mic.stops) == (False, 1)


def test_a_failed_return_keeps_the_cast(casting: App,
                                        scheduler: SyncSendScheduler,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost panel's return can fail its first handshake (it is not answering
    yet).  That retry must drop only the half-made device.

    MUTATION CHECK: ``app.detach`` on a failed handshake again and the cast is
    gone before the panel is back -- it froze on its last frame instead.
    """
    from trcc.adapters.device.scsi_lcd import ScsiLcd
    from trcc.core.errors import HandshakeError

    from .conftest import show_a_theme

    def not_answering(self: ScsiLcd) -> object:
        raise HandshakeError("no reply yet")

    show_a_theme(casting, _KEY)
    casting.devices[_KEY].disconnect()             # the panel went away
    monkeypatch.setattr(ScsiLcd, "_do_handshake", not_answering)

    result = casting.dispatch(ConnectDevice(key=_KEY))

    assert not result.ok, "precondition: the handshake failed"
    assert _KEY in casting.active_themes
    assert task_key(_KEY) in scheduler._tasks


# ── the overlay is drawn when it changes, not every frame ────────────────────


def _cast_frame(app: App, sensors: dict[str, float]) -> bytes:
    from trcc.core.models import RawFrame

    from .conftest import show_a_theme

    if _KEY not in app.active_themes:
        show_a_theme(app, _KEY)
    device = app.devices[_KEY]
    grab = RawFrame(data=bytes([30, 60, 90]) * (64 * 48), width=64, height=48)
    return app.display.build_screencast_frame(
        info=device.info, frame=grab, theme=app.active_themes[_KEY],
        sensors=sensors, profile=device.profile)


def test_a_cast_draws_its_overlay_only_when_it_changes(
    casting: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~16 cast frames a second; the readings move every couple of seconds.

    MUTATION CHECK: build the overlay unconditionally in
    ``build_screencast_frame`` again and five frames draw it five times.
    """
    from trcc.services.display import DisplayService

    drawn: list[dict[str, float]] = []
    real = DisplayService._build_overlay

    def counting(self, info, theme, sensors, size, clock):
        drawn.append(dict(sensors))
        return real(self, info, theme, sensors, size, clock)

    monkeypatch.setattr(DisplayService, "_build_overlay", counting)
    monkeypatch.setattr("trcc.services.display.compute_clock",
                        lambda **_: {"time": "12:00", "date": "2026-10-07"})

    for _ in range(5):
        _cast_frame(casting, {"cpu:temp": 41.0})
    assert len(drawn) == 1
    _cast_frame(casting, {"cpu:temp": 42.0})
    assert len(drawn) == 2, "a new reading must be drawn"


def test_a_reused_overlay_sends_the_same_picture(
    casting: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse is only a saving if the panel cannot tell the difference."""
    monkeypatch.setattr("trcc.services.display.compute_clock",
                        lambda **_: {"time": "12:00", "date": "2026-10-07"})

    drawn_fresh = _cast_frame(casting, {"cpu:temp": 41.0})
    reused = _cast_frame(casting, {"cpu:temp": 41.0})

    assert reused == drawn_fresh


# ── turning a grab into a surface ────────────────────────────────────────────


def test_a_grab_becomes_a_surface_that_owns_its_pixels() -> None:
    """The surface must not share the grab's buffer: a capture reuses it.

    MUTATION CHECK: convert to a format the input already has (no new buffer)
    and changing the grab afterwards changes the surface.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import RawFrame

    grab = bytearray(bytes([10, 20, 30]) * (8 * 4))
    surface = QtRenderer().from_raw_rgb24(RawFrame(data=grab, width=8, height=4))
    grab[:] = bytes(len(grab))                    # the capture reuses its buffer

    r, g, b, a = surface.pixelColor(3, 2).getRgb()
    assert (r, g, b, a) == (10, 20, 30, 255)


def test_a_cast_frame_writes_nothing_to_the_log(
    casting: App, caplog: pytest.LogCaptureFixture,
) -> None:
    """Grab -> surface -> resize ran on the plain logger, ~16 times a second.

    caplog hears every thread, and the App's preview backfill starts on its
    own after the data install -- its lines landed in the window on CI 3.13
    (2026-10-27) and failed this.  They are not the cast's, so they are left
    out.  MUTATION CHECK: log ``DisplayService.build_screencast_frame`` on the
    plain logger and the frames below reach the file.  (This App draws with
    ``_CliRenderer``, so a ``QtRenderer`` line never runs here -- that is
    ``test_the_qt_renderer_writes_nothing_per_cast_frame``'s; this docstring
    named it until 2026-10-27, and the claim could not fail.)
    """
    import logging

    from trcc.app import PREVIEW_BACKFILL_THREAD

    _cast_frame(casting, {"cpu:temp": 41.0})
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        for _ in range(10):
            _cast_frame(casting, {"cpu:temp": 41.0})

    assert [f"{r.name}: {r.getMessage()}" for r in caplog.records
            if not r.name.startswith("trcc.frame")
            and r.threadName != PREVIEW_BACKFILL_THREAD] == []


def test_a_single_send_still_says_its_brightness_and_angle(
    casting: App, caplog: pytest.LogCaptureFixture,
) -> None:
    """Said once per change: a one-shot send is diagnosed from the file.

    MUTATION CHECK: send these to the per-frame family unconditionally and
    a colour push leaves no trace of the angle it went out at.
    """
    import logging

    from trcc.core.commands import SetBrightness

    def said() -> list[str]:
        return [r.getMessage() for r in caplog.records
                if r.name == "trcc.services.display"
                and ("brightness=" in r.getMessage() or "→ wire" in r.getMessage())]

    info = casting.devices[_KEY].info
    profile = casting.devices[_KEY].profile
    with caplog.at_level(logging.DEBUG):
        casting.display.build_solid_color_frame(info=info, color=(1, 2, 3),
                                                profile=profile)
        first = said()
        caplog.clear()
        casting.display.build_solid_color_frame(info=info, color=(1, 2, 3),
                                                profile=profile)
        repeat = said()
        casting.dispatch(SetBrightness(key=_KEY, percent=50))
        caplog.clear()
        casting.display.build_solid_color_frame(info=info, color=(1, 2, 3),
                                                profile=profile)
        changed = said()

    assert len(first) == 2 and repeat == []
    assert len(changed) == 1 and "brightness=" in changed[0]


def test_the_qt_renderer_writes_nothing_per_cast_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cast fixture draws with ``_CliRenderer``; this is the Qt one the
    app runs, through the two calls every cast frame makes.

    MUTATION CHECK: log ``from_raw_rgb24`` or ``resize`` on the plain logger
    again and these ten frames reach the file.
    """
    import logging

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import RawFrame

    renderer = QtRenderer()
    grab = RawFrame(data=bytes([30, 60, 90]) * (64 * 48), width=64, height=48)
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        for _ in range(10):
            renderer.resize(renderer.from_raw_rgb24(grab), 320, 320)

    assert [f"{r.name}: {r.getMessage()}" for r in caplog.records
            if not r.name.startswith("trcc.frame")] == []


def test_stopping_one_cast_keeps_the_capture_for_another(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two panels, one capture source.  Stopping the first panel's cast
    stopped the source under the second (on Wayland, its portal session).

    MUTATION CHECK: stop the capture unconditionally and the first stop
    already releases it.
    """
    other = "87ad:70db"
    app = App(
        platform=MockPlatform(
            [{"vid": "0402", "pid": "3922", "fbl": 100},
             {"vid": "87ad", "pid": "70db", "fbl": 100}],
            tmp_home,
        ),
        send_scheduler=SyncSendScheduler(), renderer=_CliRenderer(),
    )
    app.attach(0x0402, 0x3922)
    app.attach(0x87AD, 0x70DB)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.dispatch(ConnectDevice(key=other)).ok
    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    assert app.dispatch(StartScreencast(key=other, audio=False, **_REGION)).ok
    stops: list[int] = []
    monkeypatch.setattr(_capture(app), "stop", lambda: stops.append(1))

    assert app.dispatch(StopScreencast(key=_KEY)).ok
    assert stops == [], "the other panel is still casting"

    assert app.dispatch(StopScreencast(key=other)).ok
    assert stops == [1]


def test_letting_the_panel_go_releases_the_mic_and_the_capture(
    app: App, mic: FakeMic, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detach stops the RUNNING parts of a cast, as it does a stream's and a
    module picture's: the microphone and -- the last panel casting -- the
    capture session.  The choice stays saved, so the panel's return resumes it.
    ``stop_sources`` removed the cast's driver and released neither: the mic
    and a PipeWire portal session stayed open with nothing casting.

    MUTATION CHECK: drop the release from ``stop_sources`` and this fails.
    """
    stops: list[int] = []
    monkeypatch.setattr(_capture(app), "stop", lambda: stops.append(1))
    assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    assert (mic.running, mic.starts) == (True, 1)

    app.detach(_KEY)

    assert (mic.running, mic.stops, len(stops)) == (False, 1, 1)
    assert app.settings.for_device(_KEY).screencast_region is not None


# ── the cast as a live picture (RegionSource) ─────────────────────────────


def test_the_cast_grabs_the_region_saved_now() -> None:
    """Read on every grab, so moving the region moves a running cast."""
    from trcc.services.region_source import RegionSource

    capture = FakeScreenCapture()
    region: list[tuple[int, int, int, int, bool]] = [(1, 2, 30, 40, False)]
    source = RegionSource(capture, lambda: region[0])
    source.latest()
    region[0] = (5, 6, 70, 80, False)
    frame = source.latest()
    assert (capture.regions, (frame.width, frame.height)) == (
        [(1, 2, 30, 40), (5, 6, 70, 80)], (70, 80))


def test_a_grab_that_errors_is_a_failed_tick_not_the_end() -> None:
    """The desktop session can blink: a grab error is ``CaptureFailed`` -- the
    cast lives on -- while ``CaptureNotReady`` (a consent window) passes
    through as it is."""
    from trcc.core.ports import CaptureFailed, CaptureNotReady
    from trcc.services.region_source import RegionSource

    class _Broken(FakeScreenCapture):
        error: BaseException = RuntimeError("portal hiccup")

        def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
            raise self.error

    capture = _Broken()
    source = RegionSource(capture, lambda: (0, 0, 8, 8, False))
    with pytest.raises(CaptureFailed, match="portal hiccup"):
        source.latest()
    capture.error = CaptureNotReady("asking for consent")
    with pytest.raises(CaptureNotReady):
        source.latest()


def test_a_push_does_not_end_the_cast_s_live_picture(casting: App) -> None:
    """``StopVideo`` -- which a push, a background change and more dispatch --
    never ended a cast, and must not now that the cast sits in the live slot.

    MUTATION CHECK: let ``MediaService.unload`` close a cast's picture and
    the push leaves the cast with nothing in the slot."""
    from trcc.core.commands import SendColor
    from trcc.core.models import PanelSource

    assert casting.dispatch(SendColor(key=_KEY, r=255, g=0, b=0)).ok
    assert casting.media.live_kind(_KEY) is PanelSource.SCREENCAST


# ── one driver for every kind of live picture ─────────────────────────────


def test_a_stream_taking_over_still_ends_the_cast_properly(
    casting: App, scheduler: SyncSendScheduler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each kind's driver has its own key, so the cast's is still there to see
    the stream take the panel -- and ends the cast the way the UIs follow,
    with ``ScreencastStopped``.  One key per panel would have let the stream's
    driver replace it, and the cast buttons would say "casting" for ever.

    MUTATION CHECK: give ``SourceDriver`` one key per panel and the cast is
    never stopped."""
    import trcc.core.toolchain as toolchain
    from tests.test_media_stream import _FakeReader
    from trcc.core.commands import SetMediaPlayer
    from trcc.services.media import MediaService

    monkeypatch.setattr(MediaService, "stream_reader", _FakeReader)
    monkeypatch.setattr(toolchain, "present", lambda tool: True)
    heard: list[Event] = []
    casting.events.subscribe(ScreencastStopped, heard.append)

    assert casting.dispatch(SetMediaPlayer(key=_KEY, uri="https://example.test/a.m3u8")).ok
    for now in range(3):
        scheduler.tick(float(now))

    assert [type(e).__name__ for e in heard] == ["ScreencastStopped"]
    assert casting.media.live_kind(_KEY) is PanelSource.MEDIA


def test_a_stream_whose_sends_keep_failing_tells_every_ui_once(app: App) -> None:
    """His rule, for every kind (2026-11-04): failed ticks are counted, every
    UI is told ONCE after about three seconds, and the stream lives on.  Only
    the cast used to say anything; a stream failed silently for ever."""
    from trcc.core.events import ErrorOccurred
    from trcc.core.models import STREAM_TICK_S
    from trcc.core.ports import CaptureFailed, FrameSource

    class _Failing(FrameSource):
        name = "https://example.test/a.m3u8"

        def latest(self) -> RawFrame:
            raise CaptureFailed("the encoder dropped the frame")

        def close(self) -> None:
            pass

    told: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, told.append)
    app.media.open_live(_KEY, PanelSource.MEDIA, _Failing())
    app.settings.set_background(_KEY, MediaPlayerBackground(_Failing.name))
    driver = SourceDriver(app, _KEY, PanelSource.MEDIA, STREAM_TICK_S)

    for now in range(round(TELL_AFTER_S / STREAM_TICK_S) + 20):
        driver.run_once(float(now))

    assert [(e.kind, e.key) for e in told] == [("video", _KEY)]
    assert "the encoder dropped the frame" in told[0].message
    assert app.media.live_kind(_KEY) is PanelSource.MEDIA


def _reconnect(app: App) -> None:
    """Let the panel go and connect it again, as an unplug and replug do, in a
    SESSION: the connect's own ``DeviceConnected`` primes the blank panel
    (``App._prime`` -> ``RestoreDeviceState``), as in a running App.  Only the
    flag is switched on -- ``start_session`` would start the hotplug and
    metrics threads too."""
    app._session = True
    app.detach(_KEY)
    assert _KEY not in app.active_themes, "detach left the panel not blank"
    resp = bytearray(0xE100)
    resp[0] = 100                      # FBL=100 → 320x320
    platform = app.platform
    assert isinstance(platform, FakePlatform)
    platform.scsi.read_script.append(bytes(resp))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert _KEY in app.active_themes, "the connect restored nothing"


def test_a_cast_the_user_started_resumes_when_the_panel_comes_back(
    app: App, mic: FakeMic, tmp_home: Path, scheduler: SyncSendScheduler,
) -> None:
    """A cast is the panel's display source, like a media player or a module's
    picture, and those come back on a reconnect or a restart -- so does a cast
    (his decision, 2026-11-04), with its region and its microphone.

    It was ended twice over: the restore's ``LoadTheme`` of the saved theme
    ran ``StopScreencast`` (that theme carries no cast), which DELETED the
    saved region -- and ``RestoreDeviceState`` had no cast branch to resume
    it from anyway.

    MUTATION CHECK: drop either half -- the ``picked`` guard on the
    ``StopScreencast`` in ``LoadTheme``, or the cast branch in
    ``RestoreDeviceState`` -- and this fails.
    """
    from trcc.core.commands import LoadTheme

    from .conftest import renderable_theme

    plain = renderable_theme(tmp_home / "themes", "Plain")
    assert app.dispatch(LoadTheme(key=_KEY, path=plain)).ok
    assert app.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok

    _reconnect(app)

    s = app.settings.for_device(_KEY)
    assert s.screencast_region == (10, 20, 64, 48, True)
    assert app.media.live_kind(_KEY) is PanelSource.SCREENCAST
    assert task_key(_KEY) in scheduler._tasks
    assert mic.running is True


def test_a_cast_over_a_video_theme_resumes_without_the_video_taking_over(
    app: App, mic: FakeMic, tmp_home: Path,
) -> None:
    """The theme restored under the cast has a video of its own: the restore
    must not play it.  Its ``PlayVideo`` set ``background_path``, and the
    one-source rule cleared the cast's region -- the media-player theme's bug
    (``b088bb50``), one branch down.  (A theme's own video chooses nothing
    now; the early return still keeps the cast's resume its own.)

    MUTATION CHECK: drop the early return for a cast that stays in
    ``LoadTheme`` and the region is cleared.
    """
    from trcc.core.commands import LoadTheme

    from .conftest import renderable_theme
    from .test_theme_persistence import _clip, _needs_ffmpeg

    if _needs_ffmpeg.args[0]:
        pytest.skip(_needs_ffmpeg.kwargs["reason"])
    vid = renderable_theme(tmp_home / "themes", "Vid")
    _clip(vid / "Theme.mp4")
    assert app.dispatch(LoadTheme(key=_KEY, path=vid)).ok
    assert app.dispatch(StartScreencast(key=_KEY, **_REGION)).ok

    _reconnect(app)

    from trcc.core.models import ThemeBackground

    s = app.settings.for_device(_KEY)
    # The theme's own background stays the choice UNDER the cast -- what the
    # cast's off restores (2026-11-04) -- and its video does not take the panel.
    assert (s.screencast_region, s.background) == (
        (10, 20, 64, 48, False), ThemeBackground())
    assert s.display_source is PanelSource.SCREENCAST
    assert app.media.live_kind(_KEY) is PanelSource.SCREENCAST


def test_a_cast_theme_comes_back_with_ONE_cast(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A theme saved while casting starts its own cast when it loads; the
    restore after it must not start a second one over it (a second
    ``ScreencastStarted``, the video stopped again, the capture reopened).

    MUTATION CHECK: drop the "already running" check from
    ``RestoreDeviceState`` and two starts are counted.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import LoadTheme, SaveTheme
    from trcc.core.events import ScreencastStarted

    from .conftest import renderable_theme

    # The real renderer: a save encodes the theme's thumbnail.
    platform = FakePlatform(tmp_home)
    app = App(platform=platform, send_scheduler=SyncSendScheduler(),
              renderer=QtRenderer())
    monkeypatch.setattr(app, "audio", FakeMic())
    resp = bytearray(0xE100)
    resp[0] = 100                      # FBL=100 → 320x320
    platform.scsi.read_script.append(bytes(resp))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    plain = renderable_theme(tmp_home / "themes", "Plain")
    assert app.dispatch(LoadTheme(key=_KEY, path=plain)).ok
    assert app.dispatch(StartScreencast(key=_KEY, **_REGION)).ok
    saved = app.dispatch(SaveTheme(key=_KEY, name="castme"))
    assert saved.ok, saved.message
    assert app.dispatch(LoadTheme(
        key=_KEY, path=Path(saved.message.split(" at ", 1)[1]))).ok
    starts: list[str] = []
    app.events.subscribe(ScreencastStarted, lambda e: starts.append(e.key))

    _reconnect(app)

    assert starts == [_KEY]
    assert app.media.live_kind(_KEY) is PanelSource.SCREENCAST


def test_a_cast_the_user_started_resumes_after_a_restart(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restart half of the same decision: the App closes, a new one reads
    ``trcc.json`` and restores -- and the cast is there again, mic and all."""
    from trcc.core.commands import LoadTheme, RestoreDeviceState

    from .conftest import renderable_theme

    def started() -> tuple[App, FakeMic, SyncSendScheduler]:
        platform = FakePlatform(tmp_home)
        scheduler = SyncSendScheduler()
        a = App(platform=platform, send_scheduler=scheduler,
                renderer=_CliRenderer())
        fake = FakeMic()
        monkeypatch.setattr(a, "audio", fake)
        resp = bytearray(0xE100)
        resp[0] = 100                  # FBL=100 → 320x320
        platform.scsi.read_script.append(bytes(resp))
        assert a.dispatch(ConnectDevice(key=_KEY)).ok
        return a, fake, scheduler

    first, _, _ = started()
    plain = renderable_theme(tmp_home / "themes", "Plain")
    assert first.dispatch(LoadTheme(key=_KEY, path=plain)).ok
    assert first.dispatch(StartScreencast(key=_KEY, audio=True, **_REGION)).ok
    first.close()

    app, mic, scheduler = started()
    restored = app.dispatch(RestoreDeviceState(key=_KEY))

    assert restored.ok, restored.message
    assert app.settings.for_device(_KEY).screencast_region == (10, 20, 64, 48,
                                                               True)
    assert task_key(_KEY) in scheduler._tasks
    assert mic.running is True


# ── what the panel shows after a cast (2026-11-04) ───────────────────


def _recording(app: App, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The names of the Commands dispatched on *app* from here on."""
    sent: list[str] = []
    real = app.dispatch

    def recording(cmd: Any) -> Any:
        sent.append(type(cmd).__name__)
        return real(cmd)
    monkeypatch.setattr(app, "dispatch", recording)
    return sent


def test_the_picked_background_stays_under_the_cast_and_comes_back(
        tmp_home: Path, scheduler: SyncSendScheduler, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Reported on real glass: pick a background, cast, turn the cast off --
    and the THEME's background came back.  The cast erased the pick; the App
    already restores a saved state at start, and the cast off never used it.
    Now the pick stays chosen under the cast and the off is that restore.
    MUTATION CHECK: erase the other sources again in set_screencast_region."""
    from PySide6.QtGui import QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import LoadTheme, SetBackground
    from trcc.core.models import PanelSource

    from .conftest import renderable_theme

    platform = FakePlatform(tmp_home)      # the real renderer: real pixels
    app = App(platform=platform, send_scheduler=scheduler, renderer=QtRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100                          # FBL=100 -> 320x320
    platform.scsi.read_script.append(bytes(resp))
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    theme = renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme2")
    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok
    picked = tmp_path / "picked.png"
    image = QImage(320, 320, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    assert image.save(str(picked))
    assert app.dispatch(SetBackground(key=_KEY, path=picked)).ok
    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    s = app.settings.for_device(_KEY)
    assert (s.display_source, s.chosen_file) == (PanelSource.SCREENCAST, str(picked))

    sent = _recording(app, monkeypatch)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="trcc"):
        assert app.dispatch(StopScreencast(key=_KEY)).ok
    assert "RestoreDeviceState" in sent
    # The restore replayed a picked IMAGE through PlayVideo, which refused it.
    assert [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and "failed" in r.getMessage()] == []
    s = app.settings.for_device(_KEY)
    assert (s.display_source, s.chosen_file) == (PanelSource.BACKGROUND, str(picked))
    # The picture the panel gets: the picked green, not the theme's blue.
    surface = app.display.build_preview_surface(
        info=app.devices[_KEY].info, theme=app.active_themes[_KEY], sensors={})
    centre = surface.pixelColor(160, 160)
    assert (centre.red(), centre.green(), centre.blue()) == (0, 255, 0)


def test_stopping_with_no_cast_restores_nothing(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """A defensive stop must not reload the panel.  MUTATION CHECK: restore
    whether or not a cast was running."""
    sent = _recording(app, monkeypatch)
    assert app.dispatch(StopScreencast(key=_KEY)).ok
    assert sent == ["StopScreencast"]


def test_a_source_that_takes_the_panel_never_flashes_the_old_state(
        casting: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """Superseded -- another source chose the panel -- the cast ends and
    nothing old comes back first.  MUTATION CHECK: make ``_end_cast``
    dispatch StopScreencast."""
    from trcc.core.commands.device import _LIVE_KINDS

    sent = _recording(casting, monkeypatch)
    stopped: list[Event] = []
    casting.events.subscribe(ScreencastStopped, stopped.append)
    _LIVE_KINDS[PanelSource.SCREENCAST].superseded(casting, _KEY)
    assert sent == []
    assert casting.settings.for_device(_KEY).screencast_region is None
    assert len(stopped) == 1


def test_a_capture_gone_for_good_restores_the_panel(
        casting: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """As the user's off: what the panel showed before comes back."""
    from trcc.core.commands.device import _LIVE_KINDS

    sent = _recording(casting, monkeypatch)
    _LIVE_KINDS[PanelSource.SCREENCAST].failed(casting, _KEY)
    assert sent[:2] == ["StopScreencast", "RestoreDeviceState"]
