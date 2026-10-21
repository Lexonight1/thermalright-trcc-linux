"""Audio visuals: the registry, TRCC Pipes, and the Commands every UI uses.

A visual is a key in ``VISUALS``; ``PlayVisual`` runs it on the App's frame
source chain (reader -> ``SourceDriver`` -> ``CaptureSourceFrame`` ->
``SendScreencastFrame``), exactly as a web stream runs.  The stand-in
platform hears a scripted beat and scripts MilkDrop, so no test reaches the
host's sound card; TRCC Pipes draws on the host's own OpenGL, as the App
would -- Mesa's software one in CI.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeVar, cast

import numpy as np
import pytest
from PySide6.QtGui import QImage

import trcc.modules.visualizer.app as visualizer_app
from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform, ScriptedSound
from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.system import host_platform_class
from trcc.app import App
from trcc.core.commands import (
    CaptureSourceFrame,
    ClearModuleSource,
    ConnectDevice,
    LcdSnapshot,
    LoadTheme,
    RestoreDeviceState,
    SaveTheme,
    SetMediaPlayer,
)
from trcc.core.events import BackgroundChanged, ErrorOccurred, Event
from trcc.core.models import SOUND_RATE, RawFrame
from trcc.core.ports import CaptureNotReady
from trcc.modules.visualizer.adapters import pipes as pipes_module
from trcc.modules.visualizer.adapters import projectm as projectm_module
from trcc.modules.visualizer.adapters._base import VISUALS, catalog
from trcc.modules.visualizer.core.commands import (
    ListVisuals,
    PlayVisual,
    SetVisualCenter,
    SetVisualPicture,
    SetVisualSettings,
    VisualTile,
)
from trcc.modules.visualizer.core.models import VISUAL_FPS, VisualSpec
from trcc.services.source_driver import task_key

_E = TypeVar("_E", bound=Event)

_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}


# ── The registry ─────────────────────────────────────────────────────

#: TRCC Pipes as this host draws it: bound to the host's OpenGL context,
#: as the App's platform binds it.
TrccPipes = cast("type[pipes_module.TrccPipes]",
                 VISUALS["trcc-pipes"].on_surface(host_platform_class().GL_SURFACE))


def _hear(app: App, event_type: type[_E]) -> list[_E]:
    """Every *event_type* the App publishes from here on."""
    heard: list[_E] = []

    def on_event(e: Event) -> None:
        assert isinstance(e, event_type)
        heard.append(e)
    app.events.subscribe(event_type, on_event)
    return heard


def test_every_visual_registers_by_the_key_in_its_class_line() -> None:
    """The registry keeps the order modules load; the gallery's order is
    the rank (test_cavalier checks Pipes first)."""
    assert {"trcc-pipes", "milkdrop"} <= set(VISUALS)
    assert all(VISUALS[k].INFO.kind == k for k in VISUALS)


class _FakeProjectM:
    """libprojectM that starts (or not) without OpenGL."""

    def __init__(self, starts: bool) -> None:
        self.starts, self.destroyed = starts, 0

    def projectm_create(self) -> int:
        return 7 if self.starts else 0

    def projectm_destroy(self, handle: int) -> None:
        self.destroyed += 1


class _FakeSurface:
    closed = 0

    def __init__(self, width: int, height: int, core: bool = False) -> None:
        pass

    def close(self) -> None:
        _FakeSurface.closed += 1


@pytest.mark.parametrize(("installed", "loads", "starts", "offered"), [
    (False, True, True, False),      # no libprojectM 4
    (True, False, True, False),      # installed, but no OpenGL to draw with
    (True, True, False, False),      # e.g. a GLES build in a GL core context
    (True, True, True, True),
])
def test_milkdrop_is_offered_only_where_projectm_can_draw(
        monkeypatch: pytest.MonkeyPatch, installed: bool, loads: bool,
        starts: bool, offered: bool) -> None:
    """MUTATION CHECK: return ``find_library(...) is not None`` again."""
    lib = _FakeProjectM(starts)

    def load() -> _FakeProjectM:
        if not loads:
            raise OSError("OpenGL (EGL) is not available")
        return lib

    monkeypatch.setattr(projectm_module.MilkdropVisual, "_draws", None)
    monkeypatch.setattr(projectm_module.ctypes.util, "find_library",
                        lambda name: "libprojectM-4.so.4" if installed else None)
    monkeypatch.setattr(projectm_module, "load_projectm", load)
    monkeypatch.setattr(projectm_module, "EglSurface", _FakeSurface)
    kinds = [info.kind for info in catalog()
             if not info.kind.startswith("cavalier-")]
    assert kinds == (["trcc-pipes", "trcc-spikes", "karmaviz", "milkdrop"]
                     if offered else ["trcc-pipes", "trcc-spikes", "karmaviz"])
    assert lib.destroyed == (1 if offered else 0)


# ── TRCC Pipes ───────────────────────────────────────────────────────

def _pipes(center: Path | None = None, center_fire: float = 0.0,
           **settings: float) -> pipes_module.TrccPipes:
    engine = TrccPipes()
    engine.open(VisualSpec("trcc-pipes", 160, 160, center=center,
                           center_fire=center_fire, settings=settings))
    return engine


@pytest.mark.parametrize("size", [8192, 9000, 16384])
def test_pipes_bands_take_exactly_their_fft_bins(size: int) -> None:
    """Each band is the bins at or above its lower edge and below its upper
    -- worked out once per FFT size, the same bins the per-frame scan took.
    MUTATION CHECK: take one bin past each band's upper edge.  (Not
    ``side="right"``: no edge lands on a bin's frequency, so that changes
    nothing.)"""
    engine = TrccPipes()
    hz = np.fft.rfftfreq(size, 1.0 / SOUND_RATE)
    edges = pipes_module.band_edges()
    expected = [np.flatnonzero((hz >= low) & (hz < high)).tolist()
                for low, high in pairwise(edges)]
    assert [list(range(low, high)) for low, high
            in engine._band_bins(size)] == expected


def _lit(frame: RawFrame) -> int:
    pixels = np.frombuffer(frame.data, dtype=np.uint8).reshape(-1, 3)
    return int((pixels.max(axis=1) > 40).sum())


def _beat_pcm(seconds: float) -> list[bytes]:
    sound = ScriptedSound()
    sound.open()
    per = SOUND_RATE // VISUAL_FPS
    chunks = []
    for _ in range(int(seconds * VISUAL_FPS)):
        chunks.append(_no_sleep_read(sound, per))
    return chunks


def _no_sleep_read(sound: ScriptedSound, frames: int) -> bytes:
    """The scripted beat without its real-time pacing."""
    import tests.mock_platform as mp
    real = mp.time.sleep
    mp.time.sleep = lambda s: None
    try:
        return sound.read(frames)
    finally:
        mp.time.sleep = real


def test_pipes_reach_out_with_the_music_and_draw_back_in_silence() -> None:
    """Each pipe reaches along its path as far as its band is loud; in
    silence they pull back to their roots at the centre.

    MUTATION CHECK: make ``_follow_the_music`` ignore the bands."""
    engine = _pipes(center_size=0.0)
    reached = 0
    for chunk in _beat_pcm(2.0):
        engine.feed(chunk)
        reached = max(reached, _lit(engine.render()))
    for _ in range(90):
        engine.feed(b"\x00" * (SOUND_RATE // VISUAL_FPS) * 8)
        silent = engine.render()
    assert reached > 3 * max(_lit(silent), 1)
    assert all(pipe.reach < 0.05 for pipe in engine._pipes)


def test_the_centre_picture_sits_in_the_middle_and_can_burn(
        tmp_path: Path) -> None:
    picture = tmp_path / "white.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    image.save(str(picture))
    plain = _pipes(picture, center_size=0.4)
    frame = plain.render()
    middle = (80 * 160 + 80) * 3
    assert sum(frame.data[middle:middle + 3]) > 60
    def above_the_picture(engine: pipes_module.TrccPipes) -> np.ndarray:
        for _ in range(20):
            frame = engine.render()
        return np.frombuffer(frame.data, dtype=np.uint8).reshape(
            160, 160, 3)[20:50, 60:100].astype(int)

    cold = above_the_picture(_pipes(picture, center_size=0.4, growth=0.3))
    hot = above_the_picture(_pipes(picture, center_fire=1.0, center_size=0.4,
                                   growth=0.3))
    flames = (hot[..., 0] - hot[..., 2]) - (cold[..., 0] - cold[..., 2])
    assert flames.mean() > 20                               # red rose above it
    assert not _pipes(center_fire=1.0)._fire                # no picture, no fire


# ── The Commands, on the App ─────────────────────────────────────────

class _FakeReader(visualizer_app.VisualReader):
    """What ``VisualReader`` promises, scripted -- no thread, no sound.  Its
    announcing (``_announce``) is the real reader's."""

    def __init__(self, spec: VisualSpec, sound: Any, engine: Any,
                 thread: Any = None, on_hearing: Any = None) -> None:
        self.spec, self.name, self.closed = spec, spec.kind, False
        self.fail, self.ready, self.heard = "", True, True
        self.announced, self._on_hearing = True, on_hearing

    @property
    def hearing(self) -> bool:
        """What the test says it hears."""
        return self.heard

    def _frame(self) -> RawFrame:
        if self.fail:
            raise OSError(self.fail)
        if not self.ready:
            raise CaptureNotReady("starting")
        return RawFrame(data=b"\x40" * (self.spec.width * self.spec.height * 3),
                        width=self.spec.width, height=self.spec.height)

    def close(self) -> None:
        self.closed = True

    def adjust(self, settings: Any) -> None:
        self.adjusted = dict(settings)


@pytest.fixture
def tasks() -> list[str]:
    """The key of every task the App was asked to run, in order."""
    return []


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        tasks: list[str]) -> Iterator[App]:
    monkeypatch.setattr(visualizer_app, "VisualReader", _FakeReader)
    monkeypatch.setattr(App, "add_task", lambda self, task: tasks.append(task.key))
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    yield app
    app.close()


def _reader(app: App) -> Any:
    return app.media.source(_KEY)


def _chosen(app: App) -> tuple[str | None, dict[str, Any]]:
    """The panel's background module, and the visualizer's own choice."""
    s = app.settings.for_device(_KEY)
    return s.module_source, s.module_settings.get("visualizer", {})


def _wire(app: App) -> list[Any]:
    return app.devices[_KEY]._transport.sent


def _wire_grows(app: App, past: int, timeout: float = 5.0) -> bool:
    """Whether the panel's wire passes *past* writes within *timeout* -- the
    per-frame send is fire-and-forget, so the frame lands on the sender's thread."""
    import time
    deadline = time.monotonic() + timeout
    while len(_wire(app)) <= past:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _preset(app: App, name: str = "Fractal/one.milk") -> str:
    path = app.platform.paths().data_dir() / "visuals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[preset00]\n")
    return name


#: KarmaViz's presets ship with TRCC: whatever the user's visuals folder holds.
_KARMAVIZ_PRESETS = tuple(sorted(
    p.name for p in (Path(__file__).resolve().parents[1]
                     / "src/trcc/modules/visualizer/assets/karmaviz/presets"
                     ).glob("*.json")))


def test_the_gallery_lists_every_visual_and_the_preset_files(app: App) -> None:
    _preset(app)
    listed = app.dispatch(ListVisuals())
    kinds = [v.kind for v in listed.visuals]
    assert (kinds[0], kinds[-1], len(kinds)) == ("trcc-pipes", "milkdrop", 15)
    assert [a.name for a in listed.visuals[0].adjustments] == [
        "growth", "thickness", "center_size"]       # Fire is the picture's
    assert [(v.kind, v.presets) for v in listed.visuals if v.presets] == [
        ("karmaviz", _KARMAVIZ_PRESETS), ("milkdrop", ("Fractal/one.milk",))]


def test_each_visual_lists_only_its_own_presets(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """The presets were ONE flat list, and the gallery gave every visual that
    draws presets all of it: harmless while MilkDrop was the only one, wrong
    the moment a second arrives -- each would show the other's tiles."""
    from trcc.modules.visualizer.core.models import VisualInfo

    _preset(app)
    root = cast("visualizer_app.VisualizerModule", app.modules.root("visualizer"))
    real_catalog, real_class = root.catalog, root.visual

    class Second:
        """A second visual that draws presets -- only what ListVisuals asks."""

        @classmethod
        def problem(cls) -> str:
            return ""

        @classmethod
        def presets(cls, folder: Path) -> tuple[str, ...]:
            del folder
            return ("built-in/one.json", "built-in/two.json")

    second = VisualInfo("second", "Second", "Test", uses_presets=True)
    monkeypatch.setattr(root, "catalog", lambda: (*real_catalog(), second))
    monkeypatch.setattr(root, "visual",
                        lambda kind: Second if kind == "second" else real_class(kind))

    listed = app.dispatch(ListVisuals())

    assert [(v.kind, v.presets) for v in listed.visuals if v.uses_presets] == [
        ("karmaviz", _KARMAVIZ_PRESETS),
        ("milkdrop", ("Fractal/one.milk",)),
        ("second", ("built-in/one.json", "built-in/two.json"))]
    assert all(v.presets == () for v in listed.visuals if not v.uses_presets)


def test_a_visual_plays_as_the_panel_s_background(app: App, tasks: list[str]) -> None:
    result = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                                     settings={"fire": 1.0}))
    assert (result.ok, result.playing) == (True, True)
    spec = _reader(app).spec
    profile = app.devices[_KEY].profile
    assert profile is not None
    assert (spec.width, spec.height) == profile.resolution
    assert dict(spec.settings) == {"fire": 1.0}
    assert _chosen(app) == ("visualizer", {"visual": "trcc-pipes", "preset": "",
                                           "settings": {"fire": 1.0}})
    snapshot = app.dispatch(LcdSnapshot(key=_KEY))
    assert (snapshot.display_source, snapshot.module_title) == (
        "module", "Audio Visualizer")
    assert tasks == [task_key(_KEY)]
    observer = app._render_observer
    assert observer._cadence_owner(_KEY) == "module"


@pytest.mark.parametrize(("visual", "preset", "problem"), [
    ("disco", "", "no audio visual 'disco' on this computer"),
    ("milkdrop", "", "no preset '' in the visuals' folder"),
    ("milkdrop", "../../escape.milk", "no preset '../../escape.milk' in the "
                                      "visuals' folder"),
])
def test_a_visual_that_cannot_play_changes_nothing(
        app: App, visual: str, preset: str, problem: str) -> None:
    """MUTATION CHECK: drop the is_under check in ``_preset_path``."""
    escape = app.platform.paths().data_dir().parent / "escape.milk"
    escape.parent.mkdir(parents=True, exist_ok=True)
    escape.write_text("[preset00]\n")
    result = app.dispatch(PlayVisual(key=_KEY, visual=visual, preset=preset))
    assert (result.ok, result.message) == (False, problem)
    assert _reader(app) is None
    assert app.settings.for_device(_KEY).module_source is None


def test_a_milkdrop_preset_plays_from_the_visuals_folder(app: App) -> None:
    name = _preset(app)
    assert app.dispatch(PlayVisual(key=_KEY, visual="milkdrop", preset=name)).ok
    assert _reader(app).spec.preset == app.platform.paths().data_dir() / "visuals" / name


def test_a_tick_puts_the_visual_s_picture_on_the_panel(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    sent = len(_wire(app))
    result = app.dispatch(CaptureSourceFrame(key=_KEY))
    assert result.ok, result.message
    assert _wire_grows(app, sent), "no picture reached the wire"


def test_another_source_ends_the_visual_within_a_tick(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    reader = _reader(app)
    app.settings.set_media_player_uri(_KEY, "https://example.test/a.m3u8")
    assert app.settings.for_device(_KEY).module_source is None       # exclusive
    app.dispatch(CaptureSourceFrame(key=_KEY))
    assert reader.closed and _reader(app) is None


def test_a_failed_visual_ends_and_says_why(app: App) -> None:
    heard = _hear(app, ErrorOccurred)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    _reader(app).fail = "the sound stopped"
    assert not app.dispatch(CaptureSourceFrame(key=_KEY)).ok
    assert [e.message for e in heard] == ["the sound stopped"]
    assert _reader(app) is None
    assert app.settings.for_device(_KEY).module_source is None


def test_starting_up_is_not_a_failure(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    _reader(app).ready = False
    assert app.dispatch(CaptureSourceFrame(key=_KEY)).ok
    assert _reader(app) is not None


def test_stopping_gives_the_panel_back_to_its_theme(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    reader = _reader(app)
    assert app.dispatch(ClearModuleSource(key=_KEY, module="visualizer")).ok
    assert reader.closed and _reader(app) is None
    assert app.dispatch(LcdSnapshot(key=_KEY)).display_source == "background"


def test_a_restart_resumes_the_visual_with_its_settings(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"growth": 2.0}))
    app.media.close_source(_KEY)                 # what a restart leaves
    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert dict(_reader(app).spec.settings) == {"growth": 2.0}


def test_a_media_player_replaces_the_visual(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    app.dispatch(SetMediaPlayer(key=_KEY, uri="https://example.test/a.m3u8"))
    assert app.settings.for_device(_KEY).module_source is None


def test_the_centre_picture_is_the_user_s(app: App, tmp_path: Path) -> None:
    missing = app.dispatch(SetVisualCenter(path=str(tmp_path / "none.png")))
    assert (missing.ok, missing.message) == (
        False, f"no picture at {tmp_path / 'none.png'}")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"png")
    assert not app.dispatch(SetVisualCenter(path=str(junk))).ok
    picture = tmp_path / "bob.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))
    assert app.dispatch(SetVisualCenter(path=str(picture))).center == str(picture)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    assert _reader(app).spec.center == picture
    assert app.dispatch(SetVisualCenter(path="")).center == ""


def test_the_api_plays_lists_and_confines_the_centre_picture(
        app: App, tmp_path_factory: pytest.TempPathFactory) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    client = loopback_client(build_app(app))
    played = client.post(f"/devices/{_KEY}/display/visual",
                         json={"visual": "trcc-pipes", "sliders": {"fire": 1.0}})
    assert (played.status_code, played.json()["playing"]) == (200, True)
    assert dict(_reader(app).spec.settings) == {"fire": 1.0}
    kinds = [v["kind"] for v in client.get("/display/visuals").json()["visuals"]]
    assert kinds[0] == "trcc-pipes"

    stranger = tmp_path_factory.mktemp("not_trcc") / "real.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(stranger))
    outside = client.post("/display/visual-center", json={"path": str(stranger)})
    assert outside.status_code == 400            # a real picture, not TRCC's
    picture = app.platform.paths().user_content_dir() / "bob.png"
    picture.parent.mkdir(parents=True, exist_ok=True)
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))
    inside = client.post("/display/visual-center", json={"path": str(picture)})
    assert (inside.status_code, inside.json()["center"]) == (200, str(picture.resolve()))

    stopped = client.post(f"/devices/{_KEY}/display/visual", json={"visual": ""})
    assert stopped.status_code == 200
    assert app.settings.for_device(_KEY).module_source is None



def test_the_gallery_lists_trcc_pipes_first_and_one_tile_per_preset() -> None:
    """Both windows build their tiles here, so they cannot list differently."""
    from trcc.modules.visualizer.core.models import VisualInfo
    from trcc.modules.visualizer.core.results import VisualsResult
    from trcc.modules.visualizer.ui.presentation.visual_gallery import (
        VisualItem,
        gallery_items,
    )

    listed = VisualsResult(ok=True, visuals=(
        VisualInfo("trcc-pipes", "TRCC Pipes", "TRCC"),
        VisualInfo("milkdrop", "MilkDrop", "MilkDrop", uses_presets=True,
                   presets=("geiss/Swirl.milk", "Flexi - Rain.milk")),
        VisualInfo("second", "Second", "Test", uses_presets=True,
                   presets=("built-in/one.json",))))
    assert gallery_items(listed) == [
        VisualItem("trcc-pipes", "", "TRCC Pipes"),
        VisualItem("milkdrop", "geiss/Swirl.milk", "Swirl"),
        VisualItem("milkdrop", "Flexi - Rain.milk", "Flexi - Rain"),
        VisualItem("second", "built-in/one.json", "one")]


def test_a_tile_is_what_the_visual_draws_for_noise(app: App) -> None:
    tile = app.dispatch(VisualTile(visual="trcc-pipes", size=64))
    assert (tile.ok, tile.media_type) == (True, "image/png")
    image = QImage.fromData(tile.image)
    assert (image.width(), image.height()) == (64, 64)
    lit = {image.pixel(x, y) for x in range(0, 64, 4) for y in range(0, 64, 4)}
    assert len(lit) > 8                                # pipes, not a blank tile


def test_a_tile_is_refused_for_an_unknown_visual_or_size(app: App) -> None:
    assert app.dispatch(VisualTile(visual="nope")).message == (
        "no audio visual 'nope' on this computer")
    assert app.dispatch(VisualTile(visual="trcc-pipes", size=4096)).message == (
        "a tile is 16 to 512 pixels a side")


def test_the_api_serves_a_tile_as_png(app: App) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    client = loopback_client(build_app(app))
    tile = client.get("/display/visual-tile", params={"visual": "trcc-pipes",
                                                      "size": 32})
    assert (tile.status_code, tile.headers["content-type"]) == (200, "image/png")
    assert client.get("/display/visual-tile",
                      params={"visual": "nope"}).status_code == 404


def test_a_computer_that_cannot_hear_says_so_up_front(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows/macOS/BSD before their sound inputs: the gallery says why on
    every tile, and a click is refused -- out loud -- before anything
    changes.  MUTATION CHECK: drop the sound check in PlayVisual."""
    monkeypatch.setattr(MockPlatform, "SOUND_INPUT", None)
    why = (f"TRCC cannot hear this computer's sound on "
           f"{app.platform.distro_name()} yet")
    assert {v.problem for v in app.dispatch(ListVisuals()).visuals} == {why}
    heard = _hear(app, ErrorOccurred)

    played = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))

    assert (played.ok, played.message) == (False, why)
    assert [e.message for e in heard] == [why]
    assert app.settings.for_device(_KEY).module_source is None
    assert app.dispatch(VisualTile(visual="trcc-pipes", size=32)).ok


@pytest.mark.parametrize(("pulse", "programs", "tool"), [
    (True, {"ffmpeg", "pw-record"}, "ffmpeg"),       # nearly every desktop
    (False, {"ffmpeg", "pw-record"}, "pw-record"),   # PipeWire, no Pulse layer
    (True, {"pw-record"}, "pw-record"),              # no ffmpeg
    (False, {"ffmpeg"}, ""),                         # ALSA only
])
def test_linux_listens_with_what_the_system_has(
        monkeypatch: pytest.MonkeyPatch, pulse: bool, programs: set[str],
        tool: str) -> None:
    from trcc.adapters.visual import sound

    monkeypatch.setattr(sound, "pulse_listens", lambda: pulse)
    monkeypatch.setattr(sound.toolchain, "present",
                        lambda name: name in programs)
    monkeypatch.setattr(sound.toolchain, "executable", lambda name: name)
    assert sound.MonitorSoundInput.tool() == tool
    if tool:
        command = sound.MonitorSoundInput().command()
        assert command[0] == tool
        assert ("{ stream.capture.sink=true }" in command) is (tool == "pw-record")
        assert sound.MonitorSoundInput.problem() == ""
    else:
        assert sound.MonitorSoundInput.problem() == (
            "TRCC hears the music through ffmpeg with PulseAudio (or "
            "PipeWire's PulseAudio layer), or through PipeWire's pw-record "
            "-- neither is here")


def test_a_pulse_server_is_found_the_way_libpulse_finds_it(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trcc.adapters.visual import sound

    monkeypatch.delenv("PULSE_SERVER", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert not sound.pulse_listens()
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / "native").touch()
    assert sound.pulse_listens()
    (tmp_path / "pulse" / "native").unlink()
    monkeypatch.setenv("PULSE_SERVER", "tcp:localhost")
    assert sound.pulse_listens()


def test_the_gallery_carries_why_a_visual_cannot_play() -> None:
    from trcc.modules.visualizer.core.models import VisualInfo
    from trcc.modules.visualizer.core.results import VisualsResult
    from trcc.modules.visualizer.ui.presentation.visual_gallery import gallery_items

    listed = VisualsResult(ok=True, visuals=(
        VisualInfo("trcc-pipes", "TRCC Pipes", "TRCC", problem="no sound"),))
    assert [i.problem for i in gallery_items(listed)] == ["no sound"]



# ── live sliders, from every UI ──────────────────────────────────────

def test_sliders_change_the_playing_visual_live(app: App) -> None:
    """Merged with what was set, kept in range, saved, handed to the reader
    -- which keeps playing -- and announced so every UI follows."""
    from trcc.core.events import BackgroundChanged

    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"growth": 2.0}))
    reader = _reader(app)
    heard = _hear(app, BackgroundChanged)

    moved = app.dispatch(SetVisualSettings(key=_KEY, settings={"growth": 9.0}))

    assert moved.ok and moved.playing
    assert reader.adjusted == {"growth": 3.0}                    # clamped
    assert _reader(app) is reader                                # not restarted
    assert _chosen(app)[1]["settings"] == reader.adjusted
    assert [e.key for e in heard] == [_KEY]
    listed = app.dispatch(ListVisuals(key=_KEY))
    assert (listed.playing, listed.sliders) == ("trcc-pipes", reader.adjusted)


def test_a_slider_the_visual_lacks_is_refused_out_loud(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    heard = _hear(app, ErrorOccurred)

    refused = app.dispatch(SetVisualSettings(key=_KEY, settings={"warp": 1.0}))

    assert (refused.ok, refused.message) == (
        False, "TRCC Pipes has no slider warp")
    assert [e.message for e in heard] == [refused.message]
    assert _chosen(app)[1]["settings"] == {}


def test_sliders_need_a_playing_visual(app: App) -> None:
    refused = app.dispatch(SetVisualSettings(key=_KEY, settings={"fire": 1.0}))
    assert (refused.ok, refused.message) == (
        False, f"no audio visual plays on {_KEY}")


def test_a_new_centre_picture_shows_at_once(app: App, tmp_path: Path) -> None:
    """The playing visual starts again with it, sliders kept."""
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"fire": 0.5}))
    before = _reader(app)
    picture = tmp_path / "bob.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))

    assert app.dispatch(SetVisualCenter(path=str(picture))).ok

    after = _reader(app)
    assert after is not before and before.closed
    assert (after.spec.center, dict(after.spec.settings)) == (picture,
                                                               {"fire": 0.5})


def test_the_reader_hands_sliders_to_its_engine_between_pictures() -> None:
    """On the engine's own thread, before the next picture -- never while
    it draws.  The real VisualReader, a scripted beat, a recording engine."""
    import threading

    from trcc.modules.visualizer.services.visual_stream import VisualReader

    seen: list[tuple[str, object]] = []
    adjusted = threading.Event()

    class Recording(TrccPipes):
        def adjust(self, settings: Any) -> None:
            seen.append((threading.current_thread().name, dict(settings)))
            super().adjust(settings)
            if seen[-1][1] == {"fire": 1.0}:
                adjusted.set()

    reader = VisualReader(VisualSpec("trcc-pipes", 64, 64), ScriptedSound,
                          Recording)
    try:
        reader.adjust({"fire": 1.0})
        assert adjusted.wait(5)
    finally:
        reader.close()
    assert seen[-1] == ("trcc-visual", {"fire": 1.0})
    assert dict(reader.spec.settings) == {"fire": 1.0}


# ── hearing nothing ─────────────────────────────────────────────────

def test_the_uis_hear_when_the_visual_stops_and_starts_hearing(
        app: App) -> None:
    """On the change only -- never a line per tick.  MUTATION CHECK: drop
    the ``!= reader.announced`` guard and this hears every tick."""
    from trcc.modules.visualizer.core.events import VisualHearingChanged

    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    reader = _reader(app)
    heard = _hear(app, VisualHearingChanged)

    app.dispatch(CaptureSourceFrame(key=_KEY))
    reader.heard = False
    for _ in range(3):
        app.dispatch(CaptureSourceFrame(key=_KEY))
    assert not app.dispatch(ListVisuals(key=_KEY)).hearing
    reader.heard = True
    app.dispatch(CaptureSourceFrame(key=_KEY))

    assert [(e.key, e.hearing) for e in heard] == [(_KEY, False), (_KEY, True)]
    assert app.dispatch(ListVisuals(key=_KEY)).hearing


class _Silence(ScriptedSound):
    """Paced like the real input, and nothing playing."""

    def read(self, frames: int) -> bytes:
        super().read(frames)
        return bytes(frames * 4 * 2)


@pytest.mark.parametrize(("sound", "hearing"), [(_Silence, False),
                                                (ScriptedSound, True)])
def test_the_reader_knows_silence_from_sound(
        monkeypatch: pytest.MonkeyPatch, sound: type, hearing: bool) -> None:
    """The real reader: silence is how a blocked capture looks on every OS.

    Waited for, not slept on: the reader is a thread drawing on OpenGL, and
    under a busy machine (the suite's other GL tests) a fixed 0.6 s let a
    late read look like 0.3 s of silence -- 2 failures in 3 runs.  A reader
    that never hears, or never notices silence, still fails at the deadline.
    """
    from trcc.modules.visualizer.services import visual_stream
    from trcc.modules.visualizer.services.visual_stream import VisualReader

    monkeypatch.setattr(visual_stream, "_SILENCE_S", 0.3)
    reader = VisualReader(VisualSpec("trcc-pipes", 32, 32), sound, TrccPipes)
    try:
        time.sleep(0.6)
        deadline = time.monotonic() + 5.0
        while reader.hearing is not hearing and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reader.hearing is hearing
    finally:
        reader.close()


class _FailsWhenClosed(ScriptedSound):
    """A sound whose waiting read fails once it is closed under it -- what
    Windows' loopback does when the App exits mid-read."""

    def read(self, frames: int) -> bytes:
        while not self.closed:
            time.sleep(0.01)
        raise OSError("Windows stopped sharing its sound: null pointer")


def test_closing_the_reader_mid_read_is_a_stop_not_a_failure(
        caplog: pytest.LogCaptureFixture) -> None:
    """A read that fails because the reader is closing is the stop itself:
    no error, no WARNING in the user's report.  MUTATION CHECK: drop the
    ``_stop`` test in ``_listen_and_draw``'s ``except OSError``."""
    from trcc.modules.visualizer.services.visual_stream import VisualReader

    reader = VisualReader(VisualSpec("trcc-pipes", 32, 32), _FailsWhenClosed,
                          TrccPipes)
    time.sleep(0.2)
    with caplog.at_level("WARNING",
                         logger="trcc.modules.visualizer.services.visual_stream"):
        reader.close()
    assert reader._error == ""
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# ── Windows: WASAPI loopback through soundcard ───────────────────────

class _FakeRecorder:
    """soundcard's recorder: frames x channels float32, as it returns."""

    def __init__(self, fail: str = "") -> None:
        self.fail, self.entered, self.exited = fail, False, False

    def __enter__(self) -> _FakeRecorder:
        if self.fail == "enter":
            raise RuntimeError("Error 0x88890008")
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited = True

    def record(self, numframes: int) -> np.ndarray:
        if self.fail == "record":
            raise RuntimeError("Error 0x88890004")
        left = np.arange(numframes, dtype=np.float32)
        return np.stack([left, -left], axis=1)


def _fake_soundcard(monkeypatch: pytest.MonkeyPatch,
                    recorder: _FakeRecorder) -> list[tuple[str, bool]]:
    import sys
    import types

    asked: list[tuple[str, bool]] = []
    speaker = types.SimpleNamespace(id="{0.0.0.00000000}.{speakers}",
                                    name="Speakers")

    def get_microphone(id: str, include_loopback: bool = False) -> object:
        asked.append((id, include_loopback))
        return types.SimpleNamespace(recorder=lambda samplerate, channels:
                                     recorder)

    module = types.ModuleType("soundcard")
    monkeypatch.setattr(module, "default_speaker", lambda: speaker, raising=False)
    monkeypatch.setattr(module, "get_microphone", get_microphone, raising=False)
    monkeypatch.setitem(sys.modules, "soundcard", module)
    from trcc.adapters.visual import loopback
    monkeypatch.setattr(loopback.importlib.util, "find_spec",
                        lambda name: object())
    return asked


def test_windows_hears_the_default_output_by_loopback(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.visual.loopback import LoopbackSoundInput

    recorder = _FakeRecorder()
    asked = _fake_soundcard(monkeypatch, recorder)
    sound = LoopbackSoundInput()
    sound.open()
    pcm = sound.read(3)
    sound.close()

    assert asked == [("{0.0.0.00000000}.{speakers}", True)]     # by id, loopback
    assert np.frombuffer(pcm, "<f4").tolist() == [0, -0, 1, -1, 2, -2]
    assert recorder.entered and recorder.exited


@pytest.mark.parametrize(("fail", "message"), [
    ("enter", "Windows would not share its sound: Error 0x88890008"),
    ("record", "Windows stopped sharing its sound: Error 0x88890004"),
])
def test_a_windows_sound_failure_is_an_oserror_the_reader_reports(
        monkeypatch: pytest.MonkeyPatch, fail: str, message: str) -> None:
    """The reader catches OSError -- soundcard raises RuntimeError."""
    from trcc.adapters.visual.loopback import LoopbackSoundInput

    _fake_soundcard(monkeypatch, _FakeRecorder(fail))
    sound = LoopbackSoundInput()
    with pytest.raises(OSError, match=message):
        sound.open()
        sound.read(3)


def test_windows_without_soundcard_says_so(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.visual import loopback

    monkeypatch.setattr(loopback.importlib.util, "find_spec", lambda name: None)
    assert loopback.LoopbackSoundInput.problem() == (
        "soundcard is not installed -- it is what hears the music for an "
        "audio visual (pip install soundcard)")


def test_windows_is_a_platform_that_hears() -> None:
    from trcc.adapters.system.windows import WindowsPlatform
    from trcc.adapters.visual.loopback import LoopbackSoundInput

    assert WindowsPlatform.SOUND_INPUT is LoopbackSoundInput


# ── macOS: a Core Audio tap through catap ────────────────────────────

def test_a_mac_buffer_becomes_our_format() -> None:
    from trcc.adapters.visual.coreaudio import to_ours

    stereo48 = np.ones((480, 2), dtype="<f4") * 0.5
    ours = to_ours(stereo48.tobytes(), 48000.0, 2, 32, True)
    assert ours.shape == (441, 2) and ours.dtype == np.float32
    assert np.allclose(ours, 0.5)

    mono16 = np.array([16384, -16384], dtype="<i2")
    assert to_ours(mono16.tobytes(), 44100.0, 1, 16, False).tolist() == [
        [0.5, 0.5], [-0.5, -0.5]]

    with pytest.raises(OSError, match="24-bit integer"):
        to_ours(b"\x00" * 6, 44100.0, 2, 24, False)


def _fake_catap(monkeypatch: pytest.MonkeyPatch,
                buffers: list[Any]) -> Any:
    import sys
    import types

    from trcc.adapters.visual import coreaudio

    class Session:
        def __init__(self, on_buffer: Any) -> None:
            self.on_buffer, self.stopped = on_buffer, False

        def start(self) -> None:
            for buffer in buffers:
                self.on_buffer(buffer)

        def stop(self) -> None:
            self.stopped = True

    made: list[Session] = []

    def record_system_audio(on_buffer: Any) -> Session:
        made.append(Session(on_buffer))
        return made[-1]

    module = types.ModuleType("catap")
    monkeypatch.setattr(module, "record_system_audio", record_system_audio,
                        raising=False)
    monkeypatch.setitem(sys.modules, "catap", module)
    monkeypatch.setattr(coreaudio.platform, "mac_ver", lambda: ("15.1", "", ""))
    monkeypatch.setattr(coreaudio.importlib.util, "find_spec",
                        lambda name: object())
    return made


def test_a_mac_hears_the_tap_and_stays_paced_when_it_stops(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    from trcc.adapters.visual.coreaudio import TapSoundInput

    fmt = types.SimpleNamespace(sample_rate=44100.0, num_channels=2,
                                bits_per_sample=32, is_float=True)
    left = np.arange(4, dtype="<f4")
    data = np.stack([left, -left], axis=1).tobytes()
    made = _fake_catap(monkeypatch, [types.SimpleNamespace(
        data=data, frame_count=4, format=fmt)])
    sound = TapSoundInput()
    sound.open()

    assert np.frombuffer(sound.read(4), "<f4").tolist() == [
        0, -0, 1, -1, 2, -2, 3, -3]
    assert sound.read(4) == bytes(32)             # nothing more: silence, paced
    sound.close()
    assert made[0].stopped


@pytest.mark.parametrize(("release", "found", "problem"), [
    ("13.6", True, "hearing what a Mac plays needs macOS 14.2 or later "
                   "(this is 13.6)"),
    ("14.1", True, "hearing what a Mac plays needs macOS 14.2 or later "
                   "(this is 14.1)"),
    ("14.2", False, "catap is not installed -- it is what hears the music for "
                    "an audio visual (pip install catap; Python 3.11 or later)"),
    ("26.0", True, ""),
])
def test_a_mac_says_what_it_needs(monkeypatch: pytest.MonkeyPatch,
                                  release: str, found: bool,
                                  problem: str) -> None:
    from trcc.adapters.visual import coreaudio

    monkeypatch.setattr(coreaudio.platform, "mac_ver", lambda: (release, "", ""))
    monkeypatch.setattr(coreaudio.importlib.util, "find_spec",
                        lambda name: object() if found else None)
    assert coreaudio.TapSoundInput.problem() == problem


def test_the_mac_is_a_platform_that_hears() -> None:
    from trcc.adapters.system.macos import MacOSPlatform
    from trcc.adapters.visual.coreaudio import TapSoundInput

    assert MacOSPlatform.SOUND_INPUT is TapSoundInput


def test_pipes_show_no_picture_until_the_user_picks_one() -> None:
    """The user's call: no TRCC logo in the middle -- empty until a picture
    is chosen."""
    engine = TrccPipes()
    engine.open(VisualSpec("trcc-pipes", 64, 64))
    assert engine._shown is None


def test_the_reader_thread_enters_the_platform_s_worker_context() -> None:
    """Platform.worker_thread_context: every worker touching OS APIs enters
    it -- on Windows the COM apartment its sound input needs.  MUTATION
    CHECK: call _listen_and_draw without the context."""
    import threading
    from contextlib import contextmanager

    from trcc.modules.visualizer.services.visual_stream import VisualReader

    entered: list[str] = []
    done = threading.Event()

    @contextmanager
    def apartment():
        entered.append(threading.current_thread().name)
        yield
        done.set()

    reader = VisualReader(VisualSpec("trcc-pipes", 32, 32), ScriptedSound,
                          TrccPipes, apartment)
    time.sleep(0.2)
    reader.close()
    assert done.wait(5)
    assert entered == ["trcc-visual"]



# ── the picture: on, off, and its fire ───────────────────────────────

def test_the_picture_switches_off_and_on_and_is_kept(app: App,
                                                     tmp_path: Path) -> None:
    """The user's button: off hides it, on brings the SAME picture back --
    the App keeps it; the playing visual starts again with each change."""
    picture = tmp_path / "bob.png"
    QImage(8, 8, QImage.Format.Format_ARGB32).save(str(picture))
    app.dispatch(SetVisualCenter(path=str(picture)))
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    assert _reader(app).spec.center == picture

    off = app.dispatch(SetVisualPicture(shown=False))
    assert (off.ok, off.center, off.center_shown) == (True, str(picture), False)
    assert _reader(app).spec.center is None

    app.dispatch(SetVisualPicture(shown=True))
    assert _reader(app).spec.center == picture


def test_the_fire_belongs_to_the_picture(app: App, tmp_path: Path) -> None:
    """Its fire reaches the visual only while the picture is on, and is
    kept while it is off.  MUTATION CHECK: drop the shown guard in
    Settings.visual_picture."""
    picture = tmp_path / "bob.png"
    QImage(8, 8, QImage.Format.Format_ARGB32).save(str(picture))
    app.dispatch(SetVisualCenter(path=str(picture)))
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    app.dispatch(SetVisualPicture(fire=0.7))
    assert _reader(app).spec.center_fire == pytest.approx(0.7)

    off = app.dispatch(SetVisualPicture(shown=False))
    assert _reader(app).spec.center_fire == 0.0
    assert off.center_fire == pytest.approx(0.7)            # kept
    assert app.dispatch(SetVisualPicture(fire=4.0)).center_fire == 1.0


def test_the_api_switches_the_picture(app: App, tmp_path: Path) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    client = loopback_client(build_app(app))
    answer = client.post("/display/visual-picture", json={"shown": False,
                                                          "fire": 0.5})
    assert answer.status_code == 200
    assert (answer.json()["center_shown"], answer.json()["center_fire"]) == (
        False, 0.5)
    assert client.post("/display/visual-picture",
                       json={"fire": 2.0}).status_code == 422


# ── A visual is a theme's background ─────────────────────────────────

def _theme(root: Path, name: str) -> Path:
    """A plain theme folder: a picture background, no visual."""
    folder = root / "themes" / name
    folder.mkdir(parents=True)
    (folder / "trcc.json").write_text(json.dumps(
        {"name": name, "width": 320, "height": 320, "elements": []}))
    picture = QImage(320, 320, QImage.Format.Format_RGB888)
    picture.fill(0x336699)
    picture.save(str(folder / "00.png"))
    return folder


def test_a_saved_theme_keeps_its_visual_and_plays_it_when_picked(
        app: App, tmp_path: Path) -> None:
    """The visual IS the saved theme's background: ``background`` names its
    JSON -- which visual, its preset, its sliders.  Picked again, it plays,
    the mask and metrics on top as over any background."""
    assert app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Start"))).ok
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes", settings={"growth": 2.0}))
    saved = app.dispatch(SaveTheme(key=_KEY, name="Viz"))
    manifest = json.loads((Path(saved.theme_path) / "trcc.json").read_text())
    ref = manifest["background"]
    assert (ref.split("/")[0], "visual" in manifest) == ("visual_background", False)
    stored = app.platform.paths().user_data_dir() / ref / "config.json"
    assert json.loads(stored.read_text()) == {
        "visual": "trcc-pipes", "preset": "", "settings": {"growth": 2.0}}
    assert _reader(app).spec.kind == "trcc-pipes"           # saving kept it on

    app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Plain")))
    assert _reader(app) is None
    assert app.dispatch(LoadTheme(key=_KEY, path=Path(saved.theme_path))).ok
    assert (_reader(app).spec.kind, dict(_reader(app).spec.settings)) == (
        "trcc-pipes", {"growth": 2.0})
    assert app.dispatch(LcdSnapshot(key=_KEY)).display_source == "module"


def test_picking_a_theme_ends_the_visual_and_a_restart_keeps_it_ended(
        app: App, tmp_path: Path) -> None:
    """The panel shows what was picked last.  MUTATION CHECK: drop the
    ``set_visual(None)`` in LoadTheme -- the visual came back at restart."""
    changes = _hear(app, BackgroundChanged)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    changes.clear()
    app.dispatch(LoadTheme(key=_KEY, path=_theme(tmp_path, "Plain")))
    assert app.settings.for_device(_KEY).module_source is None
    assert _KEY in [e.key for e in changes]                 # every UI hears
    app.media.close_source(_KEY)                            # what a restart leaves
    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert _reader(app) is None


def _visual_theme(app: App, root: Path, payload: object) -> Path:
    """A theme whose background is a visual JSON holding *payload*."""
    folder = _theme(root, "Visual")
    stored = app.platform.paths().user_data_dir() / "visual_background" / "x"
    stored.mkdir(parents=True)
    (stored / "config.json").write_text(json.dumps(payload))
    manifest = json.loads((folder / "trcc.json").read_text())
    manifest["background"] = "visual_background/x"
    (folder / "trcc.json").write_text(json.dumps(manifest))
    (folder / "00.png").unlink()
    return folder


def test_a_visual_this_computer_cannot_play_leaves_the_theme_without_it(
        app: App, tmp_path: Path) -> None:
    folder = _visual_theme(app, tmp_path, {"visual": "no-such-visual"})
    assert app.dispatch(LoadTheme(key=_KEY, path=folder)).ok
    assert _reader(app) is None


def test_an_unplayable_visual_theme_does_not_keep_the_last_visual(
        app: App, tmp_path: Path) -> None:
    """Pipes plays; a theme whose visual cannot play here is picked.  The
    panel shows that theme -- and the device's choice must say so too: it
    kept naming Pipes, so the status still read "visual" and the next
    reconnect brought Pipes back over the new theme (seen by the maintainer).

    MUTATION CHECK: drop the ``set_visual(None)`` on the refused branch of
    LoadTheme -- Pipes is still the device's visual."""
    changes = _hear(app, BackgroundChanged)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    changes.clear()
    folder = _visual_theme(app, tmp_path, {"visual": "no-such-visual"})
    assert app.dispatch(LoadTheme(key=_KEY, path=folder)).ok
    assert app.settings.for_device(_KEY).module_source is None
    assert app.dispatch(LcdSnapshot(key=_KEY)).display_source != "module"
    assert _KEY in [e.key for e in changes]                 # every UI hears
    app.media.close_source(_KEY)                            # what a restart leaves
    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert _reader(app) is None


@pytest.mark.parametrize(("payload", "expected"), [
    ({"visual": "milkdrop", "preset": "a/b.milk", "settings": {"x": 1}},
     ("milkdrop", "a/b.milk", {"x": 1.0})),
    ({"visual": "trcc-pipes"}, ("trcc-pipes", "", {})),
    ({"visual": ""}, None),
    ({"preset": "a.milk"}, None),
    ({"visual": "trcc-pipes", "settings": {"x": "loud"}}, None),
    ("trcc-pipes", None),
])
def test_a_visual_background_is_read_or_refused(
        app: App, tmp_path: Path, payload: object, expected: object) -> None:
    folder = _visual_theme(app, tmp_path, payload)
    theme = app.themes.load(folder)
    found = app.themes.module_background(theme)
    read = visualizer_app._choice(found[2], _KEY) if found is not None else None
    assert (read, app.themes.background_path(theme)) == (
        expected, None)                                     # drawn, never a file
