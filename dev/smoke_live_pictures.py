#!/usr/bin/env python3
"""Every live picture -- a screencast, a web stream, a module's visual --
driven through a REAL daemon, as a remote window sees it.

  TEST 1  a cast starts: the panel's source is the cast, and its frames
          reach the PREVIEW (``FrameSent`` with a picture, the window's feed)
  TEST 2  a web stream takes over: the cast is stopped (``ScreencastStopped``),
          the source is the stream, its frames reach the preview
  TEST 3  a module's visual takes over the same way
  TEST 4  the panel is replugged (``ResetDevice``: disconnect, connect,
          restore): the visual comes back and draws again
  TEST 5  a picked theme ends it: the source is the background again
  TEST 6  a cast survives the App restarting: a NEW daemon reads
          ``trcc.json`` and its restore resumes the cast -- the snapshot says
          so, and the new daemon's own log shows ``StartScreencast`` running.
          The snapshot alone cannot fail (the region stays saved whether or
          not the cast restarts); the log is the half that can.

"Reaches the preview" is a RATE, not a count: the theme alone is redrawn on
the sensor tick, so three frames in ten seconds arrive with nothing live at
all -- and the snapshot's source is the SAVED choice, true whether or not
anything draws.  Measured 2026-11-04: cast 16, stream 12, visual 15 preview
frames/s; the theme alone 0.3 (TEST 5, the control); the line is 4.

MUTATION CHECKS (each measured 2026-11-04):
  * one driver key per panel (a stream's driver replaces the cast's) -> TEST 2
    fails: the cast is never stopped
  * no module branch in ``RestoreDeviceState`` -> TEST 4 fails, 0.7 frames/s
    (it PASSED while the check was a count of three)
  * no screencast branch in ``RestoreDeviceState`` -> TEST 6 fails

Safe on any machine: the daemon is ``dev/_mock_daemon.py`` (the real
``run_daemon`` on the scripted mock fleet: a fake screen capture, so no
portal, and ``ScriptedSound``, so a visual never hears the microphone); the
client is a raw ``AppProxy`` (it never starts an App, so never a real daemon on
real USB); ``XDG_RUNTIME_DIR`` is a throwaway (never a running TRCC's socket).
The cast is ``audio=False`` -- cast audio would be the daemon's real mic.  The
stream is a clip this script makes and serves on 127.0.0.1.  The theme is
written here, so a fresh checkout with no ``dev/.trcc`` catalog drives it;
``dev/.trcc/trcc.json`` is put back as it was, or removed if there was none.
"""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_DEV = Path(__file__).resolve().parent
_REPO = _DEV.parent
sys.path[:0] = [str(_REPO / "src"), str(_REPO), str(_DEV)]

_KEY = "0416:5302"                      # the mock fleet's 240x240 SCSI panel
_REGION = (0, 0, 64, 48)
#: A live picture's preview rate must reach this; the theme alone, redrawn
#: on the sensor tick, stays under it (TEST 5 is the control).  Measured
#: 2026-11-04 before it was chosen -- see the commit.
_LIVE_FPS = 4.0
_WINDOW_S = 3.0
_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: object = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        _FAILURES.append(label)


def _wait(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _theme(root: Path) -> Path:
    """A minimal 240x240 theme the real renderer draws."""
    from PySide6.QtGui import QColor, QImage

    theme = root / "LivePictures"
    theme.mkdir(parents=True)
    (theme / "trcc.json").write_text(
        '{"name": "LivePictures", "width": 240, "height": 240, "elements": []}',
        encoding="utf-8")
    img = QImage(240, 240, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 64, 128))
    assert img.save(str(theme / "00.png"))
    return theme


def _clip(folder: Path) -> Path | None:
    """A 30 s clip -- streamed in real time (``-re``), so it outlasts the run.
    Its index first (``faststart``): this script's server answers no range
    requests, and an mp4 indexed at its end cannot be read without one."""
    if shutil.which("ffmpeg") is None:
        return None
    clip = folder / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=duration=30:size=240x240:rate=15",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip)],
                   check=True, timeout=120)
    return clip


def _serve(folder: Path) -> http.server.ThreadingHTTPServer:
    """*folder* over http on 127.0.0.1, quietly -- the stream's web source."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _log_since(log: Path, mark: int) -> str:
    """The log written after *mark* -- all of it, if it rotated meanwhile."""
    with log.open(encoding="utf-8", errors="replace") as f:
        if log.stat().st_size >= mark:
            f.seek(mark)
        return f.read()


def main() -> int:
    runtime = Path(tempfile.mkdtemp(prefix="trcc-live-pictures-"))
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("TRCC_DAEMON", None)        # the daemon process IS the daemon
    env.pop("WAYLAND_DISPLAY", None)

    from _mock_bootstrap import DEV_TRCC

    from trcc import ipc
    from trcc.core.commands import (
        LcdSnapshot,
        LoadTheme,
        ResetDevice,
        SetMediaPlayer,
        StartScreencast,
        StopScreencast,
    )
    from trcc.core.events import FrameSent, ScreencastStopped
    from trcc.daemon import kill_daemon
    from trcc.modules.visualizer.core import PlayVisual
    from trcc.proxy import AppProxy

    conf, log = DEV_TRCC / "trcc.json", DEV_TRCC / "trcc.log"
    kept = runtime / "trcc.json.kept"
    if conf.exists():
        shutil.copy2(conf, kept)

    def start() -> subprocess.Popen[str]:
        d = subprocess.Popen([sys.executable, str(_DEV / "_mock_daemon.py")],
                             env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True)
        if not ipc.wait_for_daemon(timeout=20.0):
            d.kill()                    # kill BEFORE reading (smoke_daemon_gui)
            print(f"daemon did not come up:\n{d.communicate()[1][-2000:]}")
            raise SystemExit(1)
        return d

    def stop(d: subprocess.Popen[str]) -> None:
        if not kill_daemon(timeout=10.0):
            d.terminate()
        try:
            d.wait(timeout=10)
        except subprocess.TimeoutExpired:
            d.kill()

    daemon, server = None, None
    try:
        theme = _theme(runtime / "themes")
        clip = _clip(runtime)
        daemon = start()
        proxy = AppProxy()
        pictures: list[int] = []        # one per preview frame with a picture
        stopped: list[str] = []
        proxy.events.subscribe(FrameSent, lambda e: pictures.append(1) if (
            e.key == _KEY and (e.image or e.shared_seq)) else None)
        proxy.events.subscribe(ScreencastStopped, lambda e: stopped.append(e.key))

        def source() -> str:
            return proxy.dispatch(LcdSnapshot(key=_KEY)).display_source

        def rate() -> float:
            """Preview frames a second, over a fixed window."""
            seen = len(pictures)
            time.sleep(_WINDOW_S)
            return (len(pictures) - seen) / _WINDOW_S

        def previewed(label: str, kind: str) -> None:
            """A LIVE picture reaches the preview -- at a live rate, which a
            theme re-rendered on the sensor tick does not reach -- and after
            it the panel's source is still *kind*."""
            fps = rate()
            print(f"        {label}: {fps:.1f} preview frames/s")
            _check(fps >= _LIVE_FPS, f"{label}: its frames reach the preview live",
                   f"{fps:.1f}/s < {_LIVE_FPS}")
            _check(source() == kind, f"{label}: still the source after its frames",
                   source())

        assert proxy.dispatch(LoadTheme(key=_KEY, path=theme)).ok

        # ── TEST 1: a cast ───────────────────────────────────────────────
        cast = proxy.dispatch(StartScreencast(key=_KEY, x=_REGION[0], y=_REGION[1],
                                              w=_REGION[2], h=_REGION[3]))
        _check(cast.ok and source() == "screencast", "TEST 1: the cast is the source",
               cast.message)
        previewed("TEST 1: the cast", "screencast")

        # ── TEST 2: a web stream takes over ──────────────────────────────
        if clip is None:
            print("  SKIP  TEST 2: no ffmpeg on PATH, so no stream to serve")
        else:
            server = _serve(clip.parent)
            url = f"http://127.0.0.1:{server.server_address[1]}/{clip.name}"
            played = proxy.dispatch(SetMediaPlayer(key=_KEY, uri=url))
            _check(played.ok, "TEST 2: SetMediaPlayer", played.message)
            _check(_wait(lambda: _KEY in stopped), "TEST 2: the cast was stopped",
                   stopped)
            _check(source() == "media", "TEST 2: the stream is the source", source())
            previewed("TEST 2: the stream", "media")

        # ── TEST 3: a module's visual takes over ─────────────────────────
        visual = proxy.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
        _check(visual.ok, "TEST 3: PlayVisual", visual.message)
        _check(_wait(lambda: source() == "module"), "TEST 3: the visual is the source",
               source())
        previewed("TEST 3: the visual", "module")

        # ── TEST 4: replug ───────────────────────────────────────────────
        reset = proxy.dispatch(ResetDevice(key=_KEY))
        _check(reset.ok, "TEST 4: ResetDevice", reset.message)
        _check(_wait(lambda: source() == "module"),
               "TEST 4: the visual is the source again", source())
        previewed("TEST 4: the visual, after the replug", "module")

        # ── TEST 5: a picked theme ends it ───────────────────────────────
        assert proxy.dispatch(LoadTheme(key=_KEY, path=theme)).ok
        _check(source() == "background", "TEST 5: a picked theme is the source again",
               source())
        # The control for every rate above: nothing live, only the theme.
        idle = rate()
        print(f"        TEST 5: the theme alone: {idle:.1f} preview frames/s")
        _check(idle < _LIVE_FPS, "TEST 5: nothing live draws any more",
               f"{idle:.1f}/s")

        # ── TEST 6: a cast survives the App restarting ───────────────────
        cast = proxy.dispatch(StartScreencast(key=_KEY, x=_REGION[0], y=_REGION[1],
                                              w=_REGION[2], h=_REGION[3]))
        _check(cast.ok, "TEST 6: StartScreencast", cast.message)
        proxy.close()
        stop(daemon)
        mark = log.stat().st_size if log.exists() else 0

        daemon = start()                # the restart: a new App, from trcc.json
        proxy = AppProxy()
        new = ""

        def resumed() -> bool:
            nonlocal new
            new = _log_since(log, mark) if log.exists() else ""
            return "StartScreencast ok" in new

        _wait(resumed)                  # the prime runs during start-up
        after = proxy.dispatch(LcdSnapshot(key=_KEY))
        _check(after.display_source == "screencast",
               "TEST 6: after the restart the panel's source is the cast",
               after.display_source)
        _check(tuple(after.screencast_region or ()) == (*_REGION, False),
               "TEST 6: the same region", after.screencast_region)
        _check("resuming its screencast" in new,
               "TEST 6: RestoreDeviceState resumed it (daemon log)")
        _check("StartScreencast ok" in new,
               "TEST 6: StartScreencast ran in the new daemon (daemon log)")
        proxy.dispatch(StopScreencast(key=_KEY))
        proxy.close()
    finally:
        if server is not None:
            server.shutdown()
        if daemon is not None and daemon.poll() is None:
            stop(daemon)
        if kept.exists():
            shutil.copy2(kept, conf)
        else:
            conf.unlink(missing_ok=True)
        shutil.rmtree(runtime, ignore_errors=True)

    if _FAILURES:
        print(f"\nFAIL: {len(_FAILURES)} assertion(s) failed: {_FAILURES}")
        return 1
    print("\nPASS: every live picture reaches the panel, hands over, survives a "
          "replug -- and a cast survives a restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
