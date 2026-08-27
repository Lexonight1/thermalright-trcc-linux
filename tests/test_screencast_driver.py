"""The screencast driver — what made CLI / API / daemon capture anything.

``StartScreencast`` only publishes ``ScreencastStarted``, and the GUI's
``ScreencastHandler`` was the sole subscriber that ran a timer.  Every other
client printed "Capturing on …" and captured nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import (
    CaptureScreencastFrame,
    ConnectDevice,
    StartScreencast,
    StartScreencastDriver,
    StopScreencast,
    StopScreencastDriver,
)
from trcc.services.screencast_driver import ScreencastDriver, task_key

from .conftest import FakePlatform, _CliRenderer

_KEY = "0402:3922"
_REGION = dict(x=10, y=20, w=64, h=48)


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
def casting(app: App) -> App:
    assert app.dispatch(StartScreencast(key=_KEY, audio=False, **_REGION)).ok
    return app


# ── the frame path ───────────────────────────────────────────────────


def test_capture_grabs_the_region_the_session_declared(casting: App) -> None:
    """The region comes from ``screencast_region``, not from the caller.

    One home for "is a screencast running and over what" — the Command takes
    only a key, so a driver cannot drift from what StartScreencast persisted.
    """
    result = casting.dispatch(CaptureScreencastFrame(key=_KEY))

    assert result.ok is True, result.message
    assert casting.platform.capture.regions == [(10, 20, 64, 48)]


def test_capture_without_a_session_is_a_skip_not_a_crash(app: App) -> None:
    """A driver keeps dispatching briefly after the session is stopped.

    That race is normal, so "no region" must be an ok=False report rather than
    an exception that would kill the scheduler thread.
    """
    result = app.dispatch(CaptureScreencastFrame(key=_KEY))

    assert result.ok is False
    assert "no screencast session" in result.message
    assert app.platform.capture.regions == []


def test_a_failing_grab_does_not_raise(casting: App, monkeypatch) -> None:
    """Capture depends on a desktop session that can vanish mid-cast.

    Screen locked, portal consent revoked, ``grim`` uninstalled — the frame is
    lost, the session is not.

    MUTATION CHECK: drop the try/except in the Command and this raises.
    """
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("portal session revoked")

    monkeypatch.setattr(casting.platform.capture, "grab_region", boom)

    result = casting.dispatch(CaptureScreencastFrame(key=_KEY))

    assert result.ok is False
    assert "portal session revoked" in result.message


# ── the cadence ──────────────────────────────────────────────────────


def test_the_driver_key_cannot_collide_with_the_device_sender(app: App) -> None:
    """THE trap this design turns on.

    ``ThreadSendScheduler.add`` evicts — and STOPS — any task already
    registered under the same key.  A driver registered under the bare device
    key would kill that device's ``DeviceSender`` and its keepalives, so the
    panel would go dark the moment a screencast started.

    MUTATION CHECK: return the bare device key from ``ScreencastDriver.key``
    and this fails.
    """
    driver = ScreencastDriver(app, _KEY)

    assert driver.key != _KEY
    assert driver.key == task_key(_KEY) == f"screencast:{_KEY}"


def test_driving_does_not_evict_the_device_sender(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """The same trap, proved through the scheduler rather than by inspection."""
    casting.start_sender(_KEY)
    assert _KEY in scheduler._tasks

    assert casting.dispatch(StartScreencastDriver(key=_KEY)).ok

    assert _KEY in scheduler._tasks, "the screencast driver evicted the sender"
    assert task_key(_KEY) in scheduler._tasks


def test_each_tick_captures_one_frame(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    assert casting.dispatch(StartScreencastDriver(key=_KEY)).ok

    for tick in range(5):
        scheduler.tick(float(tick))

    assert len(casting.platform.capture.regions) == 5
    assert set(casting.platform.capture.regions) == {(10, 20, 64, 48)}


def test_stopping_the_driver_stops_the_capture(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    assert casting.dispatch(StartScreencastDriver(key=_KEY)).ok
    scheduler.tick(0.0)
    assert casting.dispatch(StopScreencastDriver(key=_KEY)).ok

    scheduler.tick(1.0)
    scheduler.tick(2.0)

    assert len(casting.platform.capture.regions) == 1


def test_disconnecting_stops_the_driver(
    casting: App, scheduler: SyncSendScheduler,
) -> None:
    """``App.stop_sender`` removes the BARE key; the driver is namespaced.

    Without an explicit removal the driver outlives its device and keeps
    capturing for something no longer attached.

    MUTATION CHECK: drop the ``remove(task_key(key))`` from ``stop_sender``
    and this fails.
    """
    casting.start_sender(_KEY)
    assert casting.dispatch(StartScreencastDriver(key=_KEY)).ok
    assert task_key(_KEY) in scheduler._tasks

    casting.stop_sender(_KEY)

    assert task_key(_KEY) not in scheduler._tasks


def test_driver_refuses_a_device_with_no_session(app: App) -> None:
    """Driving nothing is a mistake worth naming, not a silent no-op."""
    result = app.dispatch(StartScreencastDriver(key=_KEY))

    assert result.ok is False
    assert "no screencast session" in result.message
    assert task_key(_KEY) not in app._send_scheduler._tasks   # type: ignore[attr-defined]


def test_stopping_a_driver_that_never_ran_is_fine(app: App) -> None:
    """A client may stop a session it did not drive."""
    assert app.dispatch(StopScreencastDriver(key=_KEY)).ok


def test_stop_screencast_leaves_no_region_for_the_driver(casting: App) -> None:
    """After StopScreencast the frame Command must decline.

    The region IS the session flag, so clearing it is what makes an in-flight
    driver tick harmless.
    """
    assert casting.dispatch(StopScreencast(key=_KEY)).ok

    assert casting.dispatch(CaptureScreencastFrame(key=_KEY)).ok is False
