"""Game mode on a panel: a busy CPU takes it, a quiet one gives it back.

The App half of the C#'s once-a-second check (FormCZTV.cs:2853-2913): the row
it reads, the frame it draws, the panel it gives back, and the task that turns
it.  The counting itself is ``test_game_mode.py``.

Ticks are dispatched by hand -- the cadence is ``GameModeTask``'s, tested
here for registration only -- and the CPU reading is set on
``app.last_raw_readings``, where the metrics loop leaves it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.infra.send_scheduler import ThreadSendScheduler
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    LoadTheme,
    SetGameMode,
    TickGameMode,
)
from trcc.core.events import FrameSent, GameModeEngaged
from trcc.core.ports import SendTask
from trcc.services.game_mode import HOLD_COUNT
from trcc.services.game_mode_driver import task_key

from .conftest import FakePlatform

_KEY = "0402:3922"
_RED = 0xC81E1E


class _Scheduler(ThreadSendScheduler):
    """The production scheduler, except that game-mode tasks are only
    recorded: the device's sender must run for a send to complete, and a
    live game task would tick alongside the test's own ticks."""

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict[str, SendTask] = {}

    def add(self, task: SendTask) -> None:
        if task.key.startswith("game:"):
            self._tasks[task.key] = task
        else:
            super().add(task)

    def remove(self, key: str) -> None:
        self._tasks.pop(key, None)
        super().remove(key)


@pytest.fixture
def scheduler() -> _Scheduler:
    return _Scheduler()


@pytest.fixture
def app(tmp_home: Path, scheduler: _Scheduler) -> App:
    """A 320x320 panel showing a red theme with one text element."""
    from PySide6.QtGui import QImage

    a = App(platform=FakePlatform(tmp_home), send_scheduler=scheduler,
            renderer=QtRenderer())
    resp = bytearray(0xE100)
    resp[0] = 100                      # FBL=100 -> 320x320
    a.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert a.dispatch(ConnectDevice(key=_KEY)).ok
    theme = a.platform.paths().theme_dir(320, 320) / "Red"
    theme.mkdir(parents=True)
    image = QImage(320, 320, QImage.Format.Format_RGB888)  # program art is pre-scaled
    image.fill(_RED)
    image.save(str(theme / "00.png"))
    (theme / "trcc.json").write_text(json.dumps({"name": "Red", "elements": [
        {"id": "t", "type": "text", "text": "GAME", "x": 10, "y": 10,
         "size": 30, "color": "#ffffff"},
    ]}), encoding="utf-8")
    assert a.dispatch(LoadTheme(key=_KEY, path=theme)).ok
    return a


def _cpu(app: App, percent: float) -> None:
    app.last_raw_readings = {"cpu:usage": percent}


def _ticks(app: App, n: int) -> list[str]:
    return [app.dispatch(TickGameMode(key=_KEY)).verdict for _ in range(n)]


def _corner(app: App) -> tuple[int, int, int]:
    """The bottom-right pixel of what was last sent -- clear of the text."""
    surface = app.display.rendered_surface(_KEY)
    colour = surface.pixelColor(surface.width() - 4, surface.height() - 4)
    return colour.red(), colour.green(), colour.blue()


def _sends(app: App) -> list[int]:
    sent: list[int] = []
    app.events.subscribe(FrameSent, lambda e: sent.append(e.bytes_sent))
    return sent


def _heard(app: App) -> list[bool]:
    heard: list[bool] = []
    app.events.subscribe(GameModeEngaged, lambda e: heard.append(e.engaged))
    return heard


def _engage(app: App) -> None:
    assert app.dispatch(SetGameMode(key=_KEY, enabled=True, threshold=75)).ok
    _cpu(app, 90)
    assert _ticks(app, HOLD_COUNT + 1)[-1] == "engage"


def test_a_busy_cpu_takes_the_panel_and_draws_the_overlay_on_black(
    app: App,
) -> None:
    """Eleven readings over the threshold engage, drawing nothing; from the
    next one on, the panel shows the overlay on black."""
    assert _corner(app)[0] > 150                     # the red theme
    heard, sent = _heard(app), _sends(app)
    assert app.dispatch(SetGameMode(key=_KEY, enabled=True, threshold=75)).ok
    _cpu(app, 90)

    verdicts = _ticks(app, HOLD_COUNT + 2)

    assert verdicts == ["idle"] * HOLD_COUNT + ["engage", "hold"]
    assert heard == [True]
    assert len(sent) == 1, "only the hold sends; engaging draws nothing"
    assert _corner(app) == (0, 0, 0)


def test_the_game_frame_keeps_the_theme_overlay(app: App) -> None:
    """Background and mask go; the text stays (FormCZTV.cs:2880-2881)."""
    _engage(app)
    _ticks(app, 1)

    surface = app.display.rendered_surface(_KEY)
    lit = sum(1 for y in range(10, 50) for x in range(10, 120)
              if surface.pixelColor(x, y).lightness() > 128)
    assert lit > 50, "the white GAME text is drawn on the black"


def test_a_quiet_cpu_gives_the_panel_back(app: App) -> None:
    """Eleven readings at or below release, and the theme is rendered back:
    a static theme has no producer that would redraw it otherwise."""
    _engage(app)
    _ticks(app, 1)
    heard = _heard(app)
    _cpu(app, 75)

    verdicts = _ticks(app, HOLD_COUNT + 1)

    assert verdicts == ["hold"] * HOLD_COUNT + ["release"]
    assert heard == [False]
    assert _corner(app)[0] > 150


def test_switching_game_mode_off_while_engaged_gives_the_panel_back(
    app: App, scheduler: _Scheduler,
) -> None:
    _engage(app)
    _ticks(app, 1)
    heard = _heard(app)

    assert app.dispatch(SetGameMode(key=_KEY, enabled=False)).ok

    assert heard == [False]
    assert _corner(app)[0] > 150
    assert task_key(_KEY) not in scheduler._tasks


@pytest.mark.parametrize("percent", [75.0, 75.9])
def test_a_reading_is_truncated_as_the_c_sharp_prints_it(
    app: App, percent: float,
) -> None:
    """The C# shows load as ``(int)value + "%"`` (UCSystemInfo.cs:871-873),
    so 75.9 is 75 -- not above a threshold of 75."""
    assert app.dispatch(SetGameMode(key=_KEY, enabled=True, threshold=75)).ok
    _cpu(app, percent)

    assert set(_ticks(app, 30)) == {"idle"}


def test_a_row_the_c_sharp_cannot_parse_never_engages(app: App) -> None:
    """Bound to a temperature, the C#'s text is "90℃": ``Convert.ToInt32``
    throws and the reading is 0."""
    assert app.dispatch(SetGameMode(key=_KEY, enabled=True, threshold=75)).ok
    row = app.sysinfo.panels[0].sensors[1]
    row.sensor_id, row.unit = "cpu:temp", "°C"
    app.last_raw_readings = {"cpu:temp": 90.0}

    results = [app.dispatch(TickGameMode(key=_KEY)) for _ in range(30)]

    assert {r.verdict for r in results} == {"idle"}
    assert {r.reading for r in results} == {0}


def test_switching_on_binds_an_unbound_row_and_watches_it(
    app: App, scheduler: _Scheduler,
) -> None:
    """A fresh install has no dashboard saved: switching game mode on binds
    the row the way ``GetSensorDashboard`` does, so it reads CPU usage."""
    assert not app.sysinfo.panels

    assert app.dispatch(SetGameMode(key=_KEY, enabled=True)).ok

    assert app.sysinfo.panels[0].sensors[1].sensor_id == "cpu:usage"
    assert task_key(_KEY) in scheduler._tasks
    _cpu(app, 42)
    assert app.dispatch(TickGameMode(key=_KEY)).reading == 42


def test_letting_the_panel_go_stops_watching_it(
    app: App, scheduler: _Scheduler,
) -> None:
    _engage(app)
    assert task_key(_KEY) in scheduler._tasks

    app.stop_sources(_KEY)

    assert task_key(_KEY) not in scheduler._tasks
    assert _KEY not in app.game_gates


def test_the_task_turns_the_tick(app: App, scheduler: _Scheduler) -> None:
    """Registered under its own key, so the device's sender survives it."""
    assert app.dispatch(SetGameMode(key=_KEY, enabled=True, threshold=75)).ok
    _cpu(app, 90)

    task = scheduler._tasks[task_key(_KEY)]
    for _ in range(HOLD_COUNT + 1):
        task.run_once(0.0)

    assert app.game_gates[_KEY].engaged
    assert task_key(_KEY) != _KEY


def test_a_saved_game_mode_runs_again_when_the_panel_is_restored(
    app: App, scheduler: _Scheduler,
) -> None:
    """Connect, restore and rotation all enter a folder through one function;
    a folder saved with game mode on starts watching again, as the C# reads
    ``isGame`` back from ``Theme.dc`` (FormCZTV.cs:1674-1675)."""
    from trcc.core.commands import RestoreDeviceState

    app.settings.set_game_mode(_KEY, enabled=True)
    assert task_key(_KEY) not in scheduler._tasks

    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok

    assert task_key(_KEY) in scheduler._tasks
