"""Cloud backgrounds resolve per ORIENTED resolution — through the command bus.

A non-square panel mounted portrait must pull its cloud background from the
oriented catalog (``web/480854``), not the native landscape (``web/854480``)
fit-squished into the portrait canvas.  The C# keys every ``Web\\{res}\\``
directory on ``directionB`` (``GetWebBackgroundImageDirectory``: 854480 ↔
480854) and re-runs the background loader on every rotation
(``buttonSelectBackgroundImage`` → ``ucThemeWeb1.CheakDirectionB``).

These drive the *commands* (``SetOrientation`` / ``LoadCloudTheme``) every UI
dispatches — no GUI — so the fix is proven universal: CLI, API, daemon and GUI
all inherit it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, LoadCloudTheme, SetOrientation
from trcc.services.media import MediaService

_KEY = "87ad:70db"
_VID, _PID = 0x87AD, 0x70DB
# Bulk panel scripted to 854×480 (pm=11) — non-square + rotatable.
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}


class _RecordingCatalog:
    """Stand-in for ``App.cloud_themes`` that records the resolution it's asked
    to materialise and returns a real (seeded) path under that resolution's
    cloud dir — so ``LoadCloudTheme`` persists a ``web/{res}/<id>`` background."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def materialise(self, theme_id: str, resolution: tuple[int, int]) -> Path:
        self.calls.append((theme_id, resolution))
        target = self._app.platform.paths().cloud_theme_dir(*resolution)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{theme_id}.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42")   # mp4 magic; content unused
        return path


@pytest.fixture
def connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[App, _RecordingCatalog, list[tuple[int, int] | None]]:
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(_VID, _PID)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.devices[_KEY].profile.resolution == (854, 480)

    recorder = _RecordingCatalog(app)
    app.cloud_themes = cast(Any, recorder)

    # PlayVideo (fired by LoadCloudTheme) must not run ffmpeg on the stub mp4;
    # record the decode size it's asked for so tests can assert it's oriented.
    decode_sizes: list[tuple[int, int] | None] = []

    def _fake_load(self, device_key, path, size=None, **kwargs):  # type: ignore[no-untyped-def]
        from trcc.core.models import RawFrame
        from trcc.services.media import Playback
        decode_sizes.append(size)
        w, h = size if size is not None else (854, 480)
        pb = Playback(frames=[RawFrame(data=b"\x00" * (w * h * 3),
                                       width=w, height=h)], fps=15)
        self._playbacks[device_key] = pb
        return pb

    monkeypatch.setattr(MediaService, "load_video", _fake_load)
    return app, recorder, decode_sizes


def test_loadcloudtheme_materialises_oriented_resolution(connected) -> None:
    """LoadCloudTheme pulls from the catalog at the device's CURRENT
    orientation — portrait at 90/270, landscape at 0/180."""
    app, recorder, _ = connected

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    app.dispatch(LoadCloudTheme(key=_KEY, theme_id="a003"))
    assert recorder.calls[-1] == ("a003", (480, 854)), (
        "portrait must materialise from web/480854, not native landscape"
    )

    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    app.dispatch(LoadCloudTheme(key=_KEY, theme_id="a003"))
    assert recorder.calls[-1] == ("a003", (854, 480))


def test_rotation_reapplies_cloud_background_oriented(connected) -> None:
    """Applying a cloud background at landscape then rotating to portrait must
    re-apply it from the oriented catalog (the C# CheakDirectionB on rotation),
    not leave the landscape background squished into the portrait canvas."""
    app, recorder, _ = connected

    # Apply at landscape → background_path lands under web/854480.
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    app.dispatch(LoadCloudTheme(key=_KEY, theme_id="a003"))
    assert "854480" in app.settings.for_device(_KEY).background_path

    # Rotate to portrait → the OrientationChanged observer re-applies oriented.
    recorder.calls.clear()
    app.dispatch(SetOrientation(key=_KEY, degrees=90))

    assert ("a003", (480, 854)) in recorder.calls, (
        "rotation must re-materialise the cloud background at web/480854"
    )
    assert "480854" in app.settings.for_device(_KEY).background_path


def test_cloud_background_decodes_at_oriented_canvas(connected) -> None:
    """The video decode size must be the ORIENTED canvas (480x854 at 90), not
    the native landscape — else the portrait video is squished into a landscape
    frame.  Guards the PlayVideo decode-size leg of the orientation fix."""
    app, _recorder, decode_sizes = connected

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    decode_sizes.clear()
    app.dispatch(LoadCloudTheme(key=_KEY, theme_id="a003"))
    assert decode_sizes[-1] == (480, 854), (
        f"portrait cloud video must decode at the portrait canvas, "
        f"got {decode_sizes[-1]}"
    )

    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    decode_sizes.clear()
    app.dispatch(LoadCloudTheme(key=_KEY, theme_id="a003"))
    assert decode_sizes[-1] == (854, 480)
