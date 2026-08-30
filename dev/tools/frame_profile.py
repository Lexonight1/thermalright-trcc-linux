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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from _gui_drive import WARMUP, drive_gui
from trcc._boot import trcc
from trcc.adapters.infra.logging import configure_logging
from trcc.adapters.system import current_platform
from trcc.core.commands import (
    ConnectDevice,
    DiscoverDevices,
    LoadTheme,
    PlayVideo,
    RenderAndSend,
)

log = logging.getLogger(__name__)


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
    silent area (681 functions) and NONE of it appears in a headless run.

    The GUI bring-up and the driven pump live in :mod:`_gui_drive`, shared with
    ``record_rate.py --gui``; the only part specific to this tool is starting
    the profiler at the warm-up boundary.
    """
    platform = current_platform()
    configure_logging(platform.paths().log_file(), level=logging.DEBUG,
                      stderr_level=logging.CRITICAL, per_frame=False)

    pr = cProfile.Profile()
    # Stopped via on_done, BEFORE the window tears down — ``run()`` does not
    # return until shutdown completes, and profiling that attributes the tray
    # close, the metrics loop, the hotplug monitor and the disconnect to the
    # frame path.
    rendered = drive_gui(frames=frames, warmup=WARMUP,
                         on_mark=pr.enable, on_done=pr.disable)
    # Divide by what was actually rendered, not what was asked for: the event
    # loop can process a tick or two after quit().
    return _calls_per_frame(pr, rendered or frames)


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
