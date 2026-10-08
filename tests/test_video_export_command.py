"""ExportVideoClip / ProbeVideoDuration — video export as a bus capability.

Before these Commands, a ``.zt`` encode existed three times: the service,
a qtgui QThread around it, and a gui copy that hand-rolled both the ffmpeg
invocation and the container writer.  None of the three was reachable from
the CLI or the API, so two of the four UIs could not export a video at all.

The tests that matter here are about the SHAPE, not the pixels:

* the Command returns as soon as the clip is queued, because ffmpeg runs
  for minutes and the IPC dispatch timeout is 30 s;
* every guard answers at the call site rather than seconds later from a
  worker thread, where the caller has already returned;
* the outcome — success or failure — arrives on the EventBus under the
  token the Result handed back.

The encode itself is exercised end-to-end when ffmpeg is present
(``test_real_encode_round_trips``); everything else runs without it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from trcc.adapters.infra.video_export_runner import (
    SyncVideoExportRunner,
    ThreadVideoExportRunner,
)
from trcc.app import App
from trcc.core import toolchain
from trcc.core.commands import ExportVideoClip, ProbeVideoDuration
from trcc.core.events import VideoExportFinished, VideoExportProgress
from trcc.core.models import (
    CUTTER_FPS_CHOICES,
    ZT_MAGIC,
    ZT_MAX_DURATION_MS,
    FitMode,
    VideoExportRequest,
)
from trcc.core.registry import ALL_DEVICES

# A registry row is enough to know the canvas — that is what lets a user
# stage a video theme BEFORE plugging the cooler in.  Taken from
# ``ALL_DEVICES`` rather than hardcoded: the registry is the single source
# of truth for what a device is, and a literal here would be a claim that
# nothing checks.
(_VID, _PID), _PRODUCT = next(
    (ids, p) for ids, p in ALL_DEVICES.items()
    if p.native_resolution != (0, 0)
)
KEY = f"{_VID:04x}:{_PID:04x}"

needs_ffmpeg = pytest.mark.skipif(
    not (toolchain.present("ffmpeg") and toolchain.present("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture
def app(fake_platform) -> App:
    """An App whose exports run inline, so a test never waits on a thread."""
    a = App(fake_platform)
    a.video_export_runner = SyncVideoExportRunner(a.events, a.themes)
    return a


@pytest.fixture
def events(app: App) -> list:
    seen: list = []
    app.events.subscribe(VideoExportProgress, seen.append)
    app.events.subscribe(VideoExportFinished, seen.append)
    return seen


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A real 2 s test pattern, or a skip when ffmpeg is absent."""
    if not toolchain.present("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=2:size=320x240:rate=30",
         "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True, timeout=120,
    )
    return out


@pytest.fixture
def fake_clip(tmp_path: Path) -> Path:
    """A file with a video extension and no video in it.

    Enough for every guard that runs BEFORE the encoder is reached, which
    is all of them — so those tests need no ffmpeg.
    """
    p = tmp_path / "not-really.mp4"
    p.write_bytes(b"not a video")
    return p


# ── The submit contract ──────────────────────────────────────────────


def test_returns_a_live_token_without_waiting_for_the_encode(
    app: App, fake_clip: Path,
) -> None:
    """ok=True means QUEUED, not encoded — the only shape daemon mode allows.

    A Result that waited for ffmpeg could not cross the socket: the
    encoder allows itself 600 s and ``ipc._DEFAULT_TIMEOUT_S`` is 30.
    """
    recorded: list[tuple[str, VideoExportRequest]] = []

    class RecordingRunner:
        def submit(self, token: str, request: VideoExportRequest) -> None:
            recorded.append((token, request))

        def shutdown(self) -> None:
            pass

    app.video_export_runner = RecordingRunner()  # type: ignore[assignment]
    result = app.dispatch(ExportVideoClip(key=KEY, path=fake_clip, end_ms=500))

    assert result.ok is True
    assert result.token
    assert result.source == str(fake_clip)
    assert (result.target_w, result.target_h) == _PRODUCT.native_resolution
    assert len(recorded) == 1
    token, request = recorded[0]
    assert token == result.token
    assert request.source == fake_clip
    assert request.start_ms == 0
    assert request.end_ms == 500


def test_canvas_is_the_panels_native_size_never_the_oriented_one(
    app: App, fake_clip: Path,
) -> None:
    """A .zt is authored for the panel's own pixels.

    The firmware applies the mount rotation itself, so orienting here as
    well would encode the turn twice.
    """
    result = app.dispatch(
        ExportVideoClip(key=KEY, path=fake_clip, end_ms=500, rotation=90),
    )
    assert (result.target_w, result.target_h) == _PRODUCT.native_resolution


def test_every_token_is_distinct(app: App, fake_clip: Path) -> None:
    """Several clients share one daemon; a token must identify ONE export."""
    tokens = {
        app.dispatch(ExportVideoClip(key=KEY, path=fake_clip, end_ms=500)).token
        for _ in range(5)
    }
    assert len(tokens) == 5


# ── Guards answer at the call site ───────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"start_ms": 500, "end_ms": 400}, "Invalid clip range"),
        ({"end_ms": 400, "rotation": 45}, "Rotation must be one of"),
        ({"end_ms": ZT_MAX_DURATION_MS + 1}, "max is"),
    ],
)
def test_bad_arguments_fail_immediately_not_on_the_worker(
    app: App, events: list, fake_clip: Path,
    kwargs: dict, expected: str,
) -> None:
    """Refused before queueing, so the caller hears it while it is still there.

    Left to the runner these would surface only as a
    ``VideoExportFinished(ok=False)`` seconds later, from a thread the
    caller has no relationship with.
    """
    result = app.dispatch(ExportVideoClip(key=KEY, path=fake_clip, **kwargs))
    assert result.ok is False
    assert expected in result.message
    assert result.token == ""
    assert events == []


def test_unknown_device_says_what_to_do(app: App, fake_clip: Path) -> None:
    result = app.dispatch(
        ExportVideoClip(key="ffff:ffff", path=fake_clip, end_ms=400),
    )
    assert result.ok is False
    assert "Don't know the target resolution" in result.message
    assert "Connect the device first" in result.message


def test_missing_file_is_refused(app: App, tmp_path: Path) -> None:
    result = app.dispatch(
        ExportVideoClip(key=KEY, path=tmp_path / "nope.mp4", end_ms=400),
    )
    assert result.ok is False
    assert "Video file not found" in result.message


def test_a_still_image_is_refused_with_the_supported_list(
    app: App, tmp_path: Path,
) -> None:
    """The message names what IS accepted — a dead end otherwise."""
    still = tmp_path / "frame.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = app.dispatch(ExportVideoClip(key=KEY, path=still, end_ms=400))
    assert result.ok is False
    assert "Unsupported video extension" in result.message
    assert ".mp4" in result.message


# ── The outcome rides the bus ────────────────────────────────────────


def test_failure_is_published_not_raised(
    app: App, events: list, fake_clip: Path,
) -> None:
    """The encode happens on a worker; there is no caller left to raise at.

    ``fake_clip`` passes every Command-level guard and then fails inside
    ffmpeg, which is exactly the case that must not escape.
    """
    result = app.dispatch(ExportVideoClip(key=KEY, path=fake_clip, end_ms=500))
    assert result.ok is True   # queued fine

    finished = [e for e in events if isinstance(e, VideoExportFinished)]
    assert len(finished) == 1
    assert finished[0].ok is False
    assert finished[0].token == result.token
    assert finished[0].path == ""
    assert finished[0].message


@pytest.fixture
def private_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The system temp dir, private to the test, so a leftover is countable."""
    import tempfile
    private = tmp_path / "systmp"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    return private


@needs_ffmpeg
@pytest.mark.parametrize("fps", CUTTER_FPS_CHOICES)
def test_real_encode_round_trips(app: App, events: list, clip: Path,
                                 private_tmp: Path, fps: int) -> None:
    """Encode a real clip and read it back with the project's own decoder.

    Gating the round trip rather than the write half: the encoder and the
    ``ZtDecoder`` now share ``ZT_MAGIC`` and ``ZT_FRAME_INTERVAL_MS`` from
    ``core.models``, and only decoding what was encoded proves the two
    halves still agree -- at each of the trimmer's two frame rates, which
    the decoder must read back from the timestamps alone.
    """
    from trcc.services.media import ZtDecoder

    result = app.dispatch(
        ExportVideoClip(key=KEY, path=clip, start_ms=0, end_ms=1000, fps=fps),
    )
    assert result.ok is True

    finished = [e for e in events if isinstance(e, VideoExportFinished)]
    assert len(finished) == 1
    assert finished[0].ok is True, finished[0].message
    assert finished[0].token == result.token

    # Kept in the user's background library, not left in /tmp where every UI
    # had to decide what to do with it -- and the temp directory is gone.
    out = Path(finished[0].path)
    assert out.parent == app.platform.paths().user_background_dir(
        result.target_w, result.target_h)
    assert out.suffix == ".zt"
    assert list(private_tmp.iterdir()) == []
    assert out.read_bytes()[0] == ZT_MAGIC

    decoder = ZtDecoder(out, (result.target_w, result.target_h))
    frames = decoder.decode()
    # A 1 s clip holds ~fps frames: ffmpeg's -t rounding added 2 at both
    # rates (17 at 15, 26 at 24, measured).  The timestamps alone cannot
    # tell, since the decoder derives fps from them: this is what pins -r.
    assert fps <= len(frames) <= fps + 2
    assert decoder.fps == fps
    # Each frame's END time, as UCVideoCut writes it
    # (``(int)(1000.0 / originalImageHz * i)`` for i = 1..n) -- not its start.
    assert decoder.timestamps == [int(1000.0 / fps * i)
                                  for i in range(1, len(frames) + 1)]


def test_the_trimmer_defaults_to_the_csharps_15_fps() -> None:
    """``UCVideoCut.cs:120``: ``originalImageHz = 15``."""
    assert ExportVideoClip(key=KEY, path=Path("x.mp4")).fps == 15


def test_a_frame_rate_the_trimmer_does_not_offer_is_refused(
        app: App, clip: Path) -> None:
    result = app.dispatch(ExportVideoClip(key=KEY, path=clip, start_ms=0,
                                          end_ms=1000, fps=30))
    assert result.ok is False
    assert result.message == "Frame rate must be one of 15/24, got 30"


@needs_ffmpeg
def test_progress_runs_from_zero_to_a_hundred(
    app: App, events: list, clip: Path,
) -> None:
    """The bar the CLI and the API never had — and both GUIs kept private."""
    result = app.dispatch(ExportVideoClip(key=KEY, path=clip, end_ms=800))
    progress = [e for e in events if isinstance(e, VideoExportProgress)]

    assert len(progress) >= 2
    assert all(e.token == result.token for e in progress)
    assert progress[0].percent == 0
    assert progress[-1].percent == 100
    assert all(0 <= e.percent <= 100 for e in progress)
    # Monotonic — a bar that goes backwards is worse than none.
    assert [e.percent for e in progress] == sorted(e.percent for e in progress)
    assert all(e.message for e in progress)


# ── ProbeVideoDuration ───────────────────────────────────────────────


@needs_ffmpeg
def test_probe_reads_a_real_duration(app: App, clip: Path) -> None:
    result = app.dispatch(ProbeVideoDuration(path=clip))
    assert result.ok is True
    assert result.path == str(clip)
    assert 1900 <= result.duration_ms <= 2100


def test_probe_is_best_effort_on_a_file_it_cannot_read(
    app: App, fake_clip: Path,
) -> None:
    """Zero and ok=False, so a trimmer defaults its range instead of refusing."""
    result = app.dispatch(ProbeVideoDuration(path=fake_clip))
    assert result.ok is False
    assert result.duration_ms == 0
    assert result.message


def test_probe_on_a_missing_file_names_it(app: App, tmp_path: Path) -> None:
    result = app.dispatch(ProbeVideoDuration(path=tmp_path / "gone.mp4"))
    assert result.ok is False
    assert "Video file not found" in result.message


@needs_ffmpeg
def test_end_ms_defaults_to_the_whole_clip(app: App, clip: Path) -> None:
    """``end_ms=None`` probes rather than guessing — the trimmer's default."""
    recorded: list[VideoExportRequest] = []

    class RecordingRunner:
        def submit(self, token: str, request: VideoExportRequest) -> None:
            recorded.append(request)

        def shutdown(self) -> None:
            pass

    app.video_export_runner = RecordingRunner()  # type: ignore[assignment]
    app.dispatch(ExportVideoClip(key=KEY, path=clip))
    assert 1900 <= recorded[0].end_ms <= 2100


# ── The runner port ──────────────────────────────────────────────────


@needs_ffmpeg
def test_thread_runner_returns_before_the_encode_finishes(
    app: App, clip: Path,
) -> None:
    """Submitting must not block — that is the entire point of the port."""
    import time

    runner = ThreadVideoExportRunner(app.events, app.themes)
    seen: list = []
    app.events.subscribe(VideoExportFinished, seen.append)
    request = VideoExportRequest(
        source=clip, start_ms=0, end_ms=1000, target_w=320, target_h=240,
    )
    try:
        start = time.monotonic()
        runner.submit("tok", request)
        assert time.monotonic() - start < 0.5

        deadline = time.monotonic() + 120
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)
        assert seen and seen[0].ok is True, seen
        assert seen[0].token == "tok"
    finally:
        runner.shutdown()


def test_thread_runner_ignores_a_submission_after_shutdown(app: App) -> None:
    """Teardown races a UI closing mid-export; dropping it beats a crash."""
    runner = ThreadVideoExportRunner(app.events, app.themes)
    runner.shutdown()
    seen: list = []
    app.events.subscribe(VideoExportFinished, seen.append)
    runner.submit("tok", VideoExportRequest(
        source=Path("/nonexistent.mp4"), start_ms=0, end_ms=1,
        target_w=1, target_h=1,
    ))
    assert seen == []


def test_shutdown_is_safe_when_nothing_was_ever_submitted(app: App) -> None:
    """Most runs never export; the worker must not exist until one does."""
    runner = ThreadVideoExportRunner(app.events, app.themes)
    runner.shutdown()          # no worker was ever spawned
    runner.shutdown()          # and twice is fine


def test_app_owns_a_runner_and_shuts_it_down(fake_platform) -> None:
    """Wired at the composition root, torn down with everything else."""
    from trcc.core.ports import VideoExportRunner

    a = App(fake_platform)
    assert isinstance(a.video_export_runner, VideoExportRunner)
    a.close()   # must not raise


# =========================================================================
# fit_mode — the trimmer's W/H buttons (#291)
# =========================================================================


@pytest.mark.parametrize("mode", [None, FitMode.WIDTH, FitMode.HEIGHT,
                                  FitMode.STRETCH])
def test_the_fit_button_reaches_the_encoder(
    app: App, fake_clip: Path, mode: FitMode | None,
) -> None:
    """The W/H choice must cross every hop to the ffmpeg invocation.

    It crossed NONE of them.  ``ExportVideoClip`` had no ``fit_mode`` field,
    so the trimmer's two buttons recorded a flag that nothing downstream
    could read, and both letterboxed identically — reported by @ocarinal
    (#291) after the ``-s WxH`` stretch was already gone.

    MUTATION CHECK -- drop ``fit_mode=self.fit_mode`` from the
    ``VideoExportRequest`` construction and this fails for all three
    non-default arms while the ``None`` arm still passes, which is exactly
    how the bug presented.
    """
    recorded: list[VideoExportRequest] = []

    class RecordingRunner:
        def submit(self, token: str, request: VideoExportRequest) -> None:
            recorded.append(request)

        def shutdown(self) -> None:
            pass

    app.video_export_runner = RecordingRunner()  # type: ignore[assignment]
    result = app.dispatch(
        ExportVideoClip(key=KEY, path=fake_clip, end_ms=500, fit_mode=mode),
    )

    assert result.ok is True
    assert len(recorded) == 1
    assert recorded[0].fit_mode is mode


def test_an_untouched_export_still_takes_the_auto_arm(
    app: App, fake_clip: Path,
) -> None:
    """The default must stay auto, or every existing export silently changes.

    The panel's flag was a BOOL defaulting to True ("width"), so wiring it
    through unchanged would have switched every user's export to forced-width
    — a crop, on footage that used to letterbox.  ``None`` is the arm that
    means "the user never pressed a fit button".
    """
    recorded: list[VideoExportRequest] = []

    class RecordingRunner:
        def submit(self, token: str, request: VideoExportRequest) -> None:
            recorded.append(request)

        def shutdown(self) -> None:
            pass

    app.video_export_runner = RecordingRunner()  # type: ignore[assignment]
    app.dispatch(ExportVideoClip(key=KEY, path=fake_clip, end_ms=500))
    assert recorded[0].fit_mode is None


def test_an_unknown_fit_is_refused_before_queueing(
    app: App, events: list, fake_clip: Path,
) -> None:
    """Reachable over IPC, where the union coercion passes a bad string on.

    ``_coerce`` tries ``FitMode(raw)``, and on ``ValueError`` it falls back to
    the raw value rather than raising — so a daemon client can put a string
    here that the enum does not name.  Refuse it while the caller is still
    listening instead of handing ffmpeg a filter it cannot mean.
    """
    result = app.dispatch(
        ExportVideoClip(key=KEY, path=fake_clip, end_ms=500,
                        fit_mode="diagonal"),  # type: ignore[arg-type]
    )
    assert result.ok is False
    assert "Fit mode must be" in result.message
    assert "diagonal" in result.message
    assert result.token == ""
    assert events == []


@needs_ffmpeg
def test_the_same_clip_is_kept_once(app: App, events: list, clip: Path) -> None:
    """Content-addressed: two identical exports are one library file."""
    for _ in range(2):
        assert app.dispatch(
            ExportVideoClip(key=KEY, path=clip, start_ms=0, end_ms=1000)).ok
    paths = [e.path for e in events if isinstance(e, VideoExportFinished)]
    assert len(paths) == 2 and paths[0] == paths[1]
    assert len(list(Path(paths[0]).parent.iterdir())) == 1


def test_an_unexpected_failure_leaves_no_temp_directory(
    app: App, events: list, fake_clip: Path, private_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a worded ``VideoExportError`` cleaned up; anything else -- a raise
    from the progress callback, the frame reader -- left the directory."""
    from trcc.services.video_export import VideoExporter

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("frame reader fell over")

    monkeypatch.setattr(VideoExporter, "_run_ffmpeg", boom)

    assert app.dispatch(
        ExportVideoClip(key=KEY, path=fake_clip, start_ms=0, end_ms=1000)).ok

    finished = [e for e in events if isinstance(e, VideoExportFinished)]
    assert [f.ok for f in finished] == [False]
    assert list(private_tmp.iterdir()) == []



@pytest.mark.parametrize("data", [b"", b"GIF89a" + b"\0" * 32])
def test_a_file_that_is_not_a_zt_archive_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes,
) -> None:
    """The refusal's own f-string had an invalid format spec, so a bad .zt
    raised ValueError -- which escaped SaveTheme -> LoadTheme -> PlayVideo
    instead of failing the load (2026-10-07)."""
    from trcc.core.errors import ThemeError
    from trcc.services.media import ZtDecoder

    monkeypatch.setattr(toolchain, "present", lambda _tool: True)
    bad = tmp_path / "bad.zt"
    bad.write_bytes(data)

    with pytest.raises(ThemeError, match=re.escape(
            f"Not a Theme.zt archive (starts {data[:4]!r}): {bad}")):
        ZtDecoder(bad, (8, 8)).decode()

