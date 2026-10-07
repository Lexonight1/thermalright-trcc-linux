"""A Command names the capability it needs, and the App refuses it for a
device that provably lacks it -- before it runs, so nothing is written.

Measured on a mock AX120 before the gate: about 20 LCD-only Commands answered
ok=True on an LED cooler and wrote LCD settings under its key (LoadTheme put
it in ``active_themes``; SetBackground with a video wrote the path and THEN
failed).  Declared per Command, not "is it an LCD", so a device of another
kind (TR-VISION) is admitted by exactly what it can do.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import trcc.core.commands as C
from trcc.app import App
from trcc.core.commands._base import Command, result_type
from trcc.core.models import Capability

from .mock_platform import MockPlatform

_LED = "0416:8001"
_LCD = "0402:3922"


def _commands(key: str, tmp: Path) -> list[Command[Any]]:
    """Every Command that declares REQUIRES, built for *key*."""
    img = tmp / "bg.png"
    clip = tmp / "clip.mp4"
    return [
        C.SetBrightness(key=key, percent=40),
        C.SetOrientation(key=key, degrees=90),
        C.SetFitMode(key=key, mode="stretch"),
        C.EnableOverlay(key=key, enabled=True),
        C.SetSplitMode(key=key, mode=1),
        C.SetGameMode(key=key, enabled=True),
        C.SetMaskPosition(key=key, x=1, y=2),
        C.SetMaskVisible(key=key, visible=False),
        C.SetBackgroundMode(key=key, mode="color"),
        C.SetOverlayBackground(key=key, color=(1, 2, 3)),
        C.AddOverlayElement(key=key, element_id="e1"),
        C.UpdateOverlayElement(key=key, element_id="e1", x=5),
        C.DeleteOverlayElement(key=key, element_id="e1"),
        C.SetOverlayConfig(key=key),
        C.ApplyMask(key=key, path=tmp / "mask"),
        C.UploadCustomMask(key=key, source=img),
        C.SetBackground(key=key, path=img),
        C.SetMediaPlayer(key=key, uri=str(clip)),
        C.StopVideo(key=key),
        C.LoadTheme(key=key, path=tmp / "theme"),
        C.LoadImage(key=key, path=img),
        C.LoadVideo(key=key, path=clip),
        C.LoadCloudTheme(key=key, theme_id="a001"),
        C.SaveTheme(key=key, name="x"),
        C.RestoreDeviceState(key=key),
        C.ConfigureSlideshow(key=key, themes=("a",), interval_s=5.0),
        C.SetSlideshow(key=key, enabled=True),
        C.ExportCurrentTheme(key=key, archive_path=tmp / "out.tr"),
    ]


def _gated() -> set[str]:
    return {cls.__name__ for cls in C.__dict__.values()
            if isinstance(cls, type) and issubclass(cls, Command)
            and cls.REQUIRES is not None}


def test_the_table_below_is_every_gated_command(tmp_path: Path) -> None:
    """The parametrized checks cover exactly what declares REQUIRES."""
    assert {type(c).__name__ for c in _commands("k", tmp_path)} == _gated()


@pytest.fixture
def app(tmp_path: Path) -> App:
    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 3},
                            {"vid": "0402", "pid": "3922", "fbl": 100}],
                           tmp_path / "root"))
    for vid, pid in ((0x0416, 0x8001), (0x0402, 0x3922)):
        app.attach(vid, pid)
    return app


@pytest.mark.parametrize("index", range(27))
def test_an_led_is_refused_and_nothing_is_written(app: App, tmp_path: Path,
                                                  index: int) -> None:
    """MUTATION CHECK: drop the check in ``App.dispatch`` and these write."""
    cmd = _commands(_LED, tmp_path)[index]

    result = app.dispatch(cmd)

    assert result.ok is False
    assert f"has no {cmd.REQUIRES.value} capability" in result.message
    assert isinstance(result, result_type(type(cmd)))
    assert _LED not in app.settings._devices, "LCD settings written for an LED"
    assert _LED not in app.active_themes


@pytest.mark.parametrize("index", range(27))
def test_an_lcd_passes_the_gate(app: App, tmp_path: Path, index: int) -> None:
    """Whatever an LCD's answer is, it is not the capability refusal."""
    cmd = _commands(_LCD, tmp_path)[index]

    result = app.dispatch(cmd)

    assert "capability" not in getattr(result, "message", "")


def test_an_led_keeps_no_panel_brightness() -> None:
    """BRIGHTNESS is the panel's; an LED's brightness has its own Commands."""
    from trcc.core.models import CAPABILITIES_BY_KIND, Kind
    assert Capability.BRIGHTNESS not in CAPABILITIES_BY_KIND[Kind.LED]
    assert {Capability.THEME, Capability.BACKGROUND} <= CAPABILITIES_BY_KIND[Kind.LCD]
