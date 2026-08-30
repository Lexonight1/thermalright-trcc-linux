"""The slideshow driver — what makes a CLI/API slideshow actually rotate.

``ConfigureSlideshow`` persisted a slideshow that nothing advanced outside the
gui: the gui runs its own ``QTimer``, so a slideshow set up through the CLI or
REST was saved, reported back correctly by ``ConfigureSlideshow``, and **never
switched a theme**.  Nothing failed — every surface agreed it was enabled — so
the bug was invisible from the outside.  ``services/slideshow`` names the gap in
its own docstring and calls the driver "a separate piece of work [that] has not
been done".

The tests use ``SyncSendScheduler`` so the cadence is a ``tick()`` and not a
sleep.
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
    StartSlideshowDriver,
    StopSlideshowDriver,
)
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


def test_a_configured_slideshow_registers_a_driver(configured: App,
                                                   scheduler: SyncSendScheduler,
                                                   ) -> None:
    """This is the bug, in one assertion: configuring is not driving."""
    assert task_key(_KEY) not in scheduler._tasks, (
        "a driver existed before anything asked for one"
    )
    assert configured.dispatch(StartSlideshowDriver(key=_KEY)).ok
    assert task_key(_KEY) in scheduler._tasks


def test_it_refuses_when_no_slideshow_is_configured(app: App) -> None:
    """Driving nothing is a caller error worth reporting, not a silent no-op."""
    result = app.dispatch(StartSlideshowDriver(key=_KEY))
    assert not result.ok
    assert "configure" in result.message.lower()


def test_stop_is_idempotent(configured: App) -> None:
    """A client may stop a slideshow it never drove."""
    assert configured.dispatch(StopSlideshowDriver(key=_KEY)).ok
    assert configured.dispatch(StopSlideshowDriver(key=_KEY)).ok


def test_the_key_is_namespaced_away_from_the_device_sender(app: App) -> None:
    """Registering under the bare key would evict the device's own sender.

    ``ThreadSendScheduler.add`` is keyed by ``task.key`` and STOPS whatever it
    replaces, so a driver under ``0402:3922`` would kill that device's sender
    and its keepalives — the panel would go dark the moment a slideshow started.
    """
    assert task_key(_KEY) != _KEY
    assert task_key(_KEY).endswith(_KEY)


def test_stop_sender_drops_the_driver_too(configured: App,
                                          scheduler: SyncSendScheduler) -> None:
    """Or a disconnected device keeps rotating forever.

    The namespacing that protects the sender is exactly why this removal has to
    be explicit — ``stop_sender`` removing only the bare key would leave the
    driver running against a device that is gone.
    """
    assert configured.dispatch(StartSlideshowDriver(key=_KEY)).ok
    assert task_key(_KEY) in scheduler._tasks
    configured.stop_sender(_KEY)
    assert task_key(_KEY) not in scheduler._tasks


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
