"""The slideshow driver — what makes a CLI/API slideshow actually rotate.

``ConfigureSlideshow`` persisted a slideshow that nothing advanced outside the
gui: the gui runs its own ``QTimer``, so a slideshow set up through the CLI or
REST was saved, reported back correctly by ``ConfigureSlideshow``, and **never
switched a theme**.  Nothing failed — every surface agreed it was enabled — so
the bug was invisible from the outside.  ``services/slideshow`` names the gap in
its own docstring and calls the driver "a separate piece of work [that] has not
been done".

The driver then became a second Command every UI paired by hand, and the gui
never did -- it rotated from its own timer.  ``SetSlideshow`` owns it now: on
means rotating, in every UI and when the App starts.

Most tests use ``SyncSendScheduler`` so the cadence is a ``tick()`` and not a
sleep; the three that drive a rotation run on the production scheduler.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import (
    ConfigureSlideshow,
    ConnectDevice,
    SetSlideshow,
)
from trcc.core.events import SlideshowChanged
from trcc.services.slideshow_driver import SlideshowDriver, task_key

from .conftest import FakePlatform, _CliRenderer

_KEY = "0402:3922"


@pytest.fixture
def scheduler() -> SyncSendScheduler:
    return SyncSendScheduler()


@pytest.fixture
def app(tmp_home: Path, scheduler: SyncSendScheduler) -> App:
    a = App(platform=FakePlatform(tmp_home), send_scheduler=scheduler,
            renderer=_CliRenderer())          # type: ignore[arg-type]
    resp = bytearray(0xE100)
    resp[0] = 100                      # FBL=100 → 320x320
    a.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert a.dispatch(ConnectDevice(key=_KEY)).ok
    return a


@pytest.fixture
def configured(app: App) -> App:
    """A slideshow persisted exactly as the CLI or REST would leave it."""
    assert app.dispatch(ConfigureSlideshow(
        key=_KEY, themes=("Theme1", "Theme2", "Theme3"), interval_s=1.0,
    )).ok
    assert app.dispatch(SetSlideshow(key=_KEY, enabled=True)).ok
    return app


def test_switching_the_slideshow_on_is_what_rotates_it(
    app: App, scheduler: SyncSendScheduler,
) -> None:
    """On means rotating, off means not — one switch, not two.

    MUTATION CHECK: drop ``_drive_slideshow`` from ``SetSlideshow``.
    """
    assert app.dispatch(ConfigureSlideshow(
        key=_KEY, themes=("Theme1",), interval_s=1.0)).ok
    assert task_key(_KEY) not in scheduler._tasks, "configuring is not switching on"

    assert app.dispatch(SetSlideshow(key=_KEY, enabled=True)).ok
    assert task_key(_KEY) in scheduler._tasks

    assert app.dispatch(SetSlideshow(key=_KEY, enabled=False)).ok
    assert task_key(_KEY) not in scheduler._tasks
    assert app.dispatch(SetSlideshow(key=_KEY, enabled=False)).ok, "idempotent"


def test_every_ui_hears_the_slideshow_change(app: App) -> None:
    """Configure and switch both publish the saved state, for every UI's
    controls to follow.

    MUTATION CHECK: drop ``_publish_slideshow`` from ``SetSlideshow``.
    """
    heard: list[SlideshowChanged] = []
    app.events.subscribe(SlideshowChanged, heard.append)

    app.dispatch(ConfigureSlideshow(key=_KEY, themes=("A", "B"), interval_s=5.0))
    app.dispatch(SetSlideshow(key=_KEY, enabled=True))

    assert heard == [
        SlideshowChanged(key=_KEY, enabled=False, interval_s=5.0, themes=("A", "B")),
        SlideshowChanged(key=_KEY, enabled=True, interval_s=5.0, themes=("A", "B")),
    ]


def test_the_key_is_namespaced_away_from_the_device_sender(app: App) -> None:
    """Registering under the bare key would evict the device's own sender.

    ``ThreadSendScheduler.add`` is keyed by ``task.key`` and STOPS whatever it
    replaces, so a driver under ``0402:3922`` would kill that device's sender
    and its keepalives — the panel would go dark the moment a slideshow started.
    """
    assert task_key(_KEY) != _KEY
    assert task_key(_KEY).endswith(_KEY)


def test_letting_the_panel_go_drops_the_driver(configured: App,
                                               scheduler: SyncSendScheduler) -> None:
    """Or a disconnected device keeps rotating forever.

    The namespacing that protects the sender is exactly why this removal has to
    be explicit — removing only the bare key would leave the driver running
    against a device that is gone.
    """
    assert task_key(_KEY) in scheduler._tasks
    configured.detach(_KEY)
    assert task_key(_KEY) not in scheduler._tasks


def test_a_blink_keeps_the_driver(configured: App,
                                  scheduler: SyncSendScheduler) -> None:
    """A wake or a replug drops the WIRE; the slideshow keeps going.

    MUTATION CHECK: remove the driver in ``stop_sender`` again and every wake
    ends the slideshow for good, as it did until 2026-10-06.
    """
    configured.stop_sender(_KEY)
    assert task_key(_KEY) in scheduler._tasks


def test_a_driver_waits_while_its_panel_is_away(configured: App,
                                                scheduler: SyncSendScheduler) -> None:
    """No theme is switched for a panel that is not there.

    MUTATION CHECK: drop the panel check in ``BaseSendTask.run_once`` and the
    driver rotates -- and connects first, behind the reconnect watcher's back.
    """
    configured.devices[_KEY].disconnect()
    driver = scheduler._tasks[task_key(_KEY)]
    asked: list[str] = []
    real_dispatch = configured.dispatch

    def spy(cmd):  # type: ignore[no-untyped-def]
        asked.append(type(cmd).__name__)
        return real_dispatch(cmd)

    configured.dispatch = spy                  # type: ignore[method-assign]
    for now in range(100, 110):
        driver.run_once(float(now))

    assert asked == []


def test_run_once_returns_the_poll_interval_when_not_due(configured: App) -> None:
    """Due-ness belongs to the service; the driver only polls.

    ``AdvanceSlideshow`` reads the persisted interval and decides. The driver
    must not second-guess it, or two places would own when a slideshow rotates.
    """
    driver = SlideshowDriver(configured, _KEY, interval_s=0.25)
    assert driver.run_once(now=0.0) == pytest.approx(0.25)


def test_a_missing_theme_costs_one_turn_not_the_driver(configured: App) -> None:
    """A slideshow naming a deleted theme must keep going.

    Stopping forever because one entry vanished is worse than skipping it: the
    themes are user data and can be renamed between two ticks.
    """
    configured.dispatch(ConfigureSlideshow(
        key=_KEY, themes=("no-such-theme-at-all",), interval_s=0.0,
    ))
    driver = SlideshowDriver(configured, _KEY, interval_s=0.25)
    assert driver.run_once(now=0.0) == pytest.approx(0.25)
    assert driver.run_once(now=1.0) == pytest.approx(0.25)


# ── on the production scheduler: rotations, resume, one source ──────────


_SPEC = [{"vid": "0402", "pid": "3922", "fbl": 100}]


def _wait_for(predicate, timeout: float = 4.0) -> bool:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _live(tmp_home: Path, *names: str, video: bool = False) -> App:
    """A scanning mock panel with real themes; ``video`` gives each a Theme.mp4."""
    from trcc.adapters.render.qt import QtRenderer

    from .conftest import renderable_theme
    from .mock_platform import MockPlatform

    a = App(MockPlatform(_SPEC, tmp_home), renderer=QtRenderer())
    root = a.platform.paths().user_content_dir() / "data" / "theme320320"
    for name in names:
        theme = renderable_theme(root, name)
        if video:
            (theme / "Theme.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return a


def _showing(app: App) -> str | None:
    theme = app.active_themes.get(_KEY)
    return theme.name if theme else None


def _driving(app: App) -> bool:
    return task_key(_KEY) in app._send_scheduler._threads   # type: ignore[attr-defined]


def test_a_saved_slideshow_resumes_when_the_app_starts(tmp_home: Path) -> None:
    """D2: enabled and saved, it never rotated after a restart but in the gui.

    MUTATION CHECK: drop ``_drive_slideshow`` from ``RestoreDeviceState``.
    """
    first = _live(tmp_home, "SlideA", "SlideB")
    first.dispatch(ConfigureSlideshow(key=_KEY, themes=("SlideA", "SlideB"),
                                      interval_s=1.0))
    first.dispatch(SetSlideshow(key=_KEY, enabled=True))
    first.close()

    app = _live(tmp_home)
    try:
        app.start_session()
        seen: set[str | None] = set()
        assert _wait_for(lambda: seen.add(_showing(app)) or {"SlideA", "SlideB"} <= seen), seen
        assert _driving(app)
    finally:
        app.close()


def test_a_theme_picked_by_hand_ends_the_slideshow(tmp_home: Path) -> None:
    """D1: the rotation replaced the user's pick within a second.  In the C#
    a theme cannot be picked while it runs, and every other mode ends it.

    MUTATION CHECK: drop the panel check from ``SlideshowDriver.run_once``.
    """
    from trcc.core.commands import LoadTheme

    app = _live(tmp_home, "SlideA", "SlideB", "Manual")
    heard: list[bool] = []
    app.events.subscribe(SlideshowChanged, lambda e: heard.append(e.enabled))
    try:
        app.start_session()
        app.dispatch(ConfigureSlideshow(key=_KEY, themes=("SlideA", "SlideB"),
                                        interval_s=1.0))
        app.dispatch(SetSlideshow(key=_KEY, enabled=True))
        assert _wait_for(lambda: _showing(app) in ("SlideA", "SlideB"))
        manual = app.platform.paths().user_content_dir() / "data" / "theme320320" / "Manual"
        assert app.dispatch(LoadTheme(key=_KEY, path=manual)).ok

        assert _wait_for(lambda: not app.settings.for_device(_KEY).slideshow_enabled)
        assert _wait_for(lambda: not _driving(app))
        # WAIT for the event: SetSlideshow saves "off" and removes the driver
        # BEFORE it publishes, so both waits above can pass in that gap.  Read
        # at once, this failed ~2 runs in 16 under load with ``[False, True]``
        # -- the event not yet published, not a missing one (a 0.3 s sleep
        # before the publish made it fail every time).
        assert _wait_for(lambda: heard[-1] is False), heard
        import time
        time.sleep(1.5)
        assert _showing(app) == "Manual", "the rotation replaced the user's pick"
    finally:
        app.close()


def test_a_video_theme_does_not_end_its_own_slideshow(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bundled video sets ``background_path`` as part of the slideshow's OWN
    load.  Judged as "another source", the slideshow would switch itself off
    after the first video theme.
    """
    from trcc.services.media import MediaService, Playback

    from .test_video_playback import _encoded_frame

    def fake_load(self, device_key, path, size, **kwargs):   # no ffmpeg here
        w, h = size if size is not None else (320, 320)
        playback = Playback(frames=[_encoded_frame(0xFF000000, w, h)] * 2,
                            fps=kwargs.get("fps", 15))
        self._playbacks[device_key] = playback
        return playback
    monkeypatch.setattr(MediaService, "load_video", fake_load)

    app = _live(tmp_home, "VidA", "VidB", video=True)
    try:
        app.start_session()
        app.dispatch(ConfigureSlideshow(key=_KEY, themes=("VidA", "VidB"),
                                        interval_s=1.0))
        app.dispatch(SetSlideshow(key=_KEY, enabled=True))
        order: list[str | None] = []

        def cycled() -> bool:
            now = _showing(app)
            if not order or order[-1] != now:
                order.append(now)
            return any(order[i:i + 3] in (["VidA", "VidB", "VidA"],
                                          ["VidB", "VidA", "VidB"])
                       for i in range(len(order)))

        # A FULL cycle: judging its own video as foreign ends it one tick
        # after a load, so a second rotation never comes.
        assert _wait_for(cycled, timeout=6.0), order
        bg = app.settings.for_device(_KEY).background_path
        assert bg and bg.endswith("Theme.mp4"), (
            f"background_path={bg!r}: no video was set, so this proves nothing")
        assert app.settings.for_device(_KEY).slideshow_enabled
        assert _driving(app)
    finally:
        app.close()


def test_a_pick_landing_during_a_rotation_still_ends_the_slideshow(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race a real daemon hit: a pick from another UI finished while the
    rotation's own load was running, the driver then read the LIVE panel,
    recorded the user's theme as its own, and rotated straight over it.

    Made deterministic: the pick lands the instant the rotation's load returns.

    MUTATION CHECK: record ``self._panel()`` after the load instead of the
    load's own ``theme_path``.
    """
    import threading

    from trcc.core.commands import LoadTheme

    app = _live(tmp_home, "SlideA", "SlideB", "Manual")
    manual = app.platform.paths().user_content_dir() / "data" / "theme320320" / "Manual"
    real = app.dispatch
    picked = threading.Event()

    def dispatch(cmd):
        result = real(cmd)
        on_driver = threading.current_thread().name.startswith("trcc-send-slideshow")
        if (isinstance(cmd, LoadTheme) and on_driver and cmd.path != manual
                and not picked.is_set()):
            picked.set()
            assert real(LoadTheme(key=_KEY, path=manual)).ok   # the other UI
        return result

    monkeypatch.setattr(app, "dispatch", dispatch)
    try:
        app.start_session()
        app.dispatch(ConfigureSlideshow(key=_KEY, themes=("SlideA", "SlideB"),
                                        interval_s=1.0))
        app.dispatch(SetSlideshow(key=_KEY, enabled=True))
        assert _wait_for(picked.is_set), "the rotation never loaded"

        assert _wait_for(lambda: not app.settings.for_device(_KEY).slideshow_enabled), (
            "the pick that landed during a rotation was taken for the slideshow's own")
        import time
        time.sleep(1.5)
        assert _showing(app) == "Manual"
    finally:
        app.close()


def test_a_turned_panel_rotates_the_themes_its_browser_lists(tmp_home: Path) -> None:
    """The #136 portrait fallback: a panel turned to portrait with no portrait
    themes on disk LISTS the landscape ones.  The driver searched the portrait
    folders only, so a slideshow picked from that list never rotated -- the
    gui's own timer had resolved names against its list and hid it.

    MUTATION CHECK: drop the fallback candidates from ``_search_theme_by_name``.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import ResolveThemeDirectories

    from .conftest import renderable_theme
    from .mock_platform import MockPlatform

    key = "87ad:70db"                                   # 854x480, bulk
    app = App(MockPlatform([{"type": "lcd", "vid": "87ad", "pid": "70db",
                             "pm": 11}], tmp_home), renderer=QtRenderer())
    landscape = app.platform.paths().theme_dir(854, 480)
    for name in ("LandA", "LandB"):
        renderable_theme(landscape, name)
    app.settings.set_orientation(key, 90)
    try:
        app.start_session()
        dirs = app.dispatch(ResolveThemeDirectories(key=key))
        assert dirs.portrait_fallback and Path(dirs.theme_dir) == landscape, (
            "fixture: this must be the fallback case, or the test proves nothing")

        app.dispatch(ConfigureSlideshow(key=key, themes=("LandA", "LandB"),
                                        interval_s=1.0))
        app.dispatch(SetSlideshow(key=key, enabled=True))
        seen: set[str | None] = set()

        def both() -> bool:
            theme = app.active_themes.get(key)
            seen.add(theme.name if theme else None)
            return {"LandA", "LandB"} <= seen
        assert _wait_for(both), seen
    finally:
        app.close()
