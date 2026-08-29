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

    for _ in range(20):        # warm-up: caches filled, so they are not measured
        tick()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(frames):
        tick()
    pr.disable()

    out: dict[tuple[str, int, str], float] = {}
    for (path, lineno, name), (_, calls, *_rest) in pstats.Stats(pr).stats.items():
        if "/src/trcc/" in path:
            out[(path.split("/src/trcc/")[1], lineno, name)] = calls / frames
    log.info("frame_profile: %d function(s) over %d frames", len(out), frames)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument("--hot", action="store_true",
                    help="only functions at >=0.5 calls/frame — the ones that "
                         "must log through core.logs.per_frame")
    args = ap.parse_args()

    rows = profile(args.frames, args.video)
    picked = {k: v for k, v in rows.items() if not args.hot or v >= 0.5}
    print(f"# calls/frame · {len(picked)} function(s) · "
          f"{'HOT only' if args.hot else 'all'} · "
          f"{'video' if args.video else 'static theme'}")
    for (rel, lineno, name), per in sorted(picked.items(), key=lambda kv: -kv[1]):
        print(f"{per:9.3f}  {rel}:{lineno} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
