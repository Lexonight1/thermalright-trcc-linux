"""Which functions actually run per frame — measured on real hardware.

The logging burn-down has to know, for each function, whether it is on the frame
path: one at 12 calls/frame needs ``core.logs.per_frame`` so its record is never
constructed, while a plain ``log.debug`` there is the shape that made logging
82-90%% of the CPU regression since v9.9.2.

**Guessing this does not work, and was tried twice.**  The first heuristic asked
whether a module already imports ``per_frame`` — which only finds hot code
someone already noticed was hot, and missed ``adapters/device/transport.py``
whose ``write`` fires once per 16 KiB chunk of every frame.  The second attempt
cross-referenced a profile keyed by ``(file, function)``, which collapsed all 25
``execute`` methods in ``core/commands/device.py`` onto one measurement and
over-reported the hot set by 2.2x.  Frame-path membership is a property of the
FUNCTION, identified by file AND line.

Run::

    PYTHONPATH=src python3.12 dev/tools/frame_profile.py            # static theme
    PYTHONPATH=src python3.12 dev/tools/frame_profile.py --video X  # advancing video
    PYTHONPATH=src python3.12 dev/tools/frame_profile.py --hot      # just the hot set

Needs a real device.  A workload only proves what it EXERCISES: a function absent
from the output is unobserved on this path, NOT proven cold — the GUI, the other
wires and the LED effects each need their own run before anything is called cold.

**The panel updates far more slowly than the loop renders, and holds the last
frame once the run exits.**  Neither is a bug.  Ticks are driven flat out with
no pacing while ``DeviceSender`` is asynchronous and supersedes whatever frame
is still pending, so only a fraction reach USB — measured 2026-08-30 on an
advancing video, **200 frames built, 4 raw writes (2%)**.  The render is what is
being measured and all of it is real: ``build_frame`` returned 30 distinct
payloads over 30 advancing cursors.  Do not "fix" this by pacing the loop —
that makes the run length depend on the video's fps, the trap
``glass_bench._run_gui_arm`` documents.
"""
from __future__ import annotations

import argparse
import cProfile
import logging
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trcc._boot import trcc
from trcc.adapters.infra.logging import configure_logging
from trcc.core.commands import (
    ConnectDevice,
    DiscoverDevices,
    LoadTheme,
    PlayVideo,
    RenderAndSend,
)

log = logging.getLogger(__name__)

#: Frames rendered before the profiler is enabled, so filled caches are not
#: what gets measured.  Shared by both arms so they exclude the same prefix.
WARMUP = 20


def profile(frames: int, video: Path | None) -> dict[tuple[str, int, str], float]:
    """Calls-per-frame for every ``src/trcc`` function, keyed (file, line, name)."""
    app = trcc()
    configure_logging(app.platform.paths().log_file(), level=logging.DEBUG,
                      stderr_level=logging.CRITICAL, per_frame=False)
    keys = [d.key for d in getattr(app.dispatch(DiscoverDevices()), "devices", [])]
    if not keys:
        raise SystemExit("no device attached — this profiler needs real hardware")
    key = keys[0]
    if not app.dispatch(ConnectDevice(key=key)).ok:
        raise SystemExit("connect failed")
    theme = app.platform.paths().data_dir() / "theme320320" / "Theme1"
    if not app.dispatch(LoadTheme(key=key, path=theme)).ok:
        raise SystemExit("theme load failed")

    playback = None
    if video is not None:
        if not app.dispatch(PlayVideo(key=key, path=video)).ok:
            raise SystemExit("play failed")
        playback = app.media.playback(key)

    def tick() -> None:
        if playback is not None:
            playback.advance()
        app.dispatch(RenderAndSend(key=key))

    for _ in range(WARMUP):    # warm-up: caches filled, so they are not measured
        tick()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(frames):
        tick()
    pr.disable()

    return _calls_per_frame(pr, frames)


def _calls_per_frame(pr: cProfile.Profile,
                     frames: int) -> dict[tuple[str, int, str], float]:
    """Reduce a profile to calls-per-frame for every ``src/trcc`` function.

    Keyed (file, LINE, name) — never (file, name).  Collapsing on the name
    merged all 25 ``execute`` methods in ``core/commands/device.py`` onto one
    row and over-reported the hot set by 2.2x.
    """
    out: dict[tuple[str, int, str], float] = {}
    for (path, lineno, name), (_, calls, *_rest) in pstats.Stats(pr).stats.items():
        if "/src/trcc/" in path:
            out[(path.split("/src/trcc/")[1], lineno, name)] = calls / frames
    log.info("frame_profile: %d function(s) over %d frames", len(out), frames)
    return out


def profile_gui(frames: int) -> dict[tuple[str, int, str], float]:
    """Calls-per-frame inside the REAL shipping GUI.

    The headless :func:`profile` measures the render path.  This adds what that
    path never touches — live widgets, the preview render, the sensor loop, the
    Qt event loop — and the burn-down needs it because ``ui`` is the largest
    silent area (684 functions) and NONE of it appears in a headless run.

    Driven, not awaited, for the reason ``glass_bench._run_gui_arm`` records: a
    run whose length depends on whatever background the user last saved is not
    a measurement.  A zero-interval timer drives ``RenderAndSend`` while every
    GUI observer, preview update and repaint still runs.

    Cannot go through ``trcc()`` — that builds a ``QtRenderer``, which creates a
    ``QGuiApplication``, and ``run()`` then refuses to make a ``QApplication``.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from trcc.adapters.system import current_platform
    from trcc.services.display import DisplayService
    from trcc.ui.gui import run

    platform = current_platform()
    configure_logging(platform.paths().log_file(), level=logging.DEBUG,
                      stderr_level=logging.CRITICAL, per_frame=False)

    pr = cProfile.Profile()
    counter = {"frames": 0}
    real_build = DisplayService.build_frame

    def counting_build(self, *a, **kw):
        counter["frames"] += 1
        return real_build(self, *a, **kw)

    DisplayService.build_frame = counting_build   # type: ignore[method-assign]
    state: dict = {}

    def on_ready(window) -> None:
        app = getattr(window, "_app", None)
        if app is None:
            raise SystemExit("no App on the window — unknown tree layout")
        keys = list(getattr(app, "devices", {}))
        if not keys:
            raise SystemExit("GUI came up with no attached device")
        key = keys[0]

        def pump() -> None:
            # Warm-up is excluded the same way the headless arm excludes it:
            # caches fill first, so they are not what gets measured.
            if counter["frames"] == WARMUP:
                pr.enable()
            if counter["frames"] >= WARMUP + frames:
                pr.disable()
                timer.stop()
                qapp = QApplication.instance()
                if qapp is not None:
                    qapp.quit()
                return
            app.dispatch(RenderAndSend(key=key))

        timer = QTimer()
        timer.setInterval(0)
        timer.timeout.connect(pump)
        timer.start()
        state["timer"] = timer          # keep it alive past this scope

    run(platform, single_instance=False, ipc=False, force_exit=False,
        on_ready=on_ready)
    DisplayService.build_frame = real_build      # type: ignore[method-assign]
    return _calls_per_frame(pr, frames)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument("--hot", action="store_true",
                    help="only functions at >=0.5 calls/frame — the ones that "
                         "must log through core.logs.per_frame")
    ap.add_argument("--gui", action="store_true",
                    help="drive the REAL GUI, so ui/ appears at all")
    args = ap.parse_args()

    if args.gui and args.video is not None:
        raise SystemExit("--gui drives whatever the GUI has loaded; "
                         "--video is for the headless arm")
    rows = profile_gui(args.frames) if args.gui else profile(args.frames,
                                                             args.video)
    picked = {k: v for k, v in rows.items() if not args.hot or v >= 0.5}
    workload = "GUI" if args.gui else ("video" if args.video else "static theme")
    print(f"# calls/frame · {len(picked)} function(s) · "
          f"{'HOT only' if args.hot else 'all'} · {workload}")
    for (rel, lineno, name), per in sorted(picked.items(), key=lambda kv: -kv[1]):
        print(f"{per:9.3f}  {rel}:{lineno} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
