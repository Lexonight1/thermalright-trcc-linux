"""How many log records does ONE rendered frame write?  Measured, at default -v.

The burn-down's ratchet counts SILENT functions.  This measures the opposite
defect, which nothing counted: a function on the frame path that logs through
the ORDINARY logger instead of ``core.logs.per_frame`` writes a record EVERY
frame.  The file floor is DEBUG at every rung by design -- ``trcc report`` is
the whole diagnosis for hardware we do not own -- so those records are written
even with no ``-v`` at all.  Cost is paid, and the one-shot lines a report is
actually read for get scrolled out of the 1 MB tail.

That shape was 82-90%% of the CPU regression since v9.9.2.  It is also easy to
re-create by accident while ADDING coverage, which is how it came back.

Run at the verbosity a user runs::

    PYTHONPATH=src python3.12 dev/tools/record_rate.py            # static theme
    PYTHONPATH=src python3.12 dev/tools/record_rate.py --video X  # advancing video

Needs a real device.  The answer should be **0.00 records/frame**: everything
per-frame belongs to the ``trcc.frame`` family, which is silent by default.
Any non-zero row names a call site to move onto ``per_frame(__name__)``.

Reads the file the app actually writes, not a captured handler -- the question
is what a reporter's log contains, and only the file can answer that.

The panel updates far more slowly than the loop renders and holds the last frame
after the run exits -- same flat-out drive as ``frame_profile``, where 200 built
frames produced 4 raw writes.  Neither is a bug.
"""
from __future__ import annotations

import argparse
import collections
import logging
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from _gui_drive import drive_gui
from trcc._boot import trcc
from trcc.adapters.infra.logging import configure_logging
from trcc.core.commands import (
    ConnectDevice,
    DiscoverDevices,
    LoadTheme,
    PlayVideo,
    RenderAndSend,
)
from trcc.core.logs import levels_for

log = logging.getLogger(__name__)

#: ``2026-08-30T11:55:17 DEBUG   trcc.services.display:Foo.bar:329: msg``
_RECORD = re.compile(r"^\S+ (\w+)\s+(\S+?):(\S+?):(\d+):")

WARMUP = 20


def _open_log() -> Path:
    """Configure logging at DEFAULT verbosity into a dedicated file, and return it.

    Shared by both drivers so neither can measure under a different ladder than
    the other.
    """
    ladder = levels_for(0)                      # what a user runs: no -v at all
    # A DEDICATED file, not the app's own.  Sharing it meant starting from a
    # byte offset into a log that prior runs had already grown near the 1 MB
    # cap -- the rotation then fired mid-measurement, the offset pointed past
    # the end of the new file, and the tool reported a confident 0.00
    # records/frame for a video run that was in fact the noisiest.  The cap is
    # also lifted here, and rotation is asserted against below.
    tmp = tempfile.mkdtemp(prefix="trcc-record-rate-")
    log_file = Path(tmp) / "trcc.log"
    # ``level`` is the ROOT level and therefore the FILE's; ``stderr_level`` is
    # the terminal's.  Passing the terminal level as ``level`` sets the root to
    # WARNING, suppresses every DEBUG record everywhere, and makes this tool
    # report a confident 0.00 no matter what the code does -- the same false
    # negative, by a second route.  Both are spelled out because this tool's
    # answer is only worth anything if it cannot fake a pass.
    configure_logging(log_file, level=ladder.file,
                      stderr_level=ladder.terminal,
                      per_frame=ladder.per_frame,
                      max_bytes=1_000_000_000, latest_max_bytes=1_000_000_000)
    return log_file


def _tally(log_file: Path, before: int, frames: int,
           *, end: int | None = None) -> tuple[int, collections.Counter]:
    """Count records in ``[before, end)``, keyed by call site.

    ``end`` bounds the window explicitly for callers whose run keeps logging
    after the measured frames stop (the GUI's teardown); the headless arm has
    nothing after its last frame and reads to EOF.
    """
    after = log_file.stat().st_size if log_file.exists() else 0
    if after < before or any(log_file.with_suffix(f".log.{i}").exists()
                             for i in range(1, 6)):
        raise SystemExit(
            "the log ROTATED mid-measurement — every count below the mark is "
            "lost and a 0.00 here would be a false pass, not a clean result")

    stop = after if end is None else end
    by_site: collections.Counter[str] = collections.Counter()
    with log_file.open("r", errors="replace") as fh:
        fh.seek(before)
        while fh.tell() < stop:
            line = fh.readline()
            if not line:
                break
            m = _RECORD.match(line)
            if m is not None:
                level, module, func, lineno = m.groups()
                by_site[f"{module}:{func}:{lineno} [{level}]"] += 1
    return frames, by_site


def measure_gui(frames: int) -> tuple[int, collections.Counter]:
    """Records per frame inside the REAL GUI, at DEFAULT verbosity.

    The headless arm cannot answer this: ``ui/`` never executes without a
    window, and it is the largest silent area in the tree.  The GUI bring-up is
    ``_gui_drive.drive_gui``, shared with ``frame_profile --gui``.
    """
    log_file = _open_log()
    span = {"start": 0, "end": 0}

    def _size() -> int:
        for handler in logging.getLogger().handlers:
            handler.flush()
        return log_file.stat().st_size if log_file.exists() else 0

    def on_mark() -> None:
        span["start"] = _size()

    def on_done() -> None:
        # Closed BEFORE the window tears down.  ``run()`` does not return until
        # shutdown finishes, and shutdown is loud — the tray, the metrics loop,
        # the hotplug monitor, the sender, the disconnect.  Measured once
        # without this: 32 one-shot teardown records landed in the window and
        # reported 0.16 records/frame for a GUI that is actually at 0.00.
        span["end"] = _size()

    rendered = drive_gui(frames=frames, on_mark=on_mark, on_done=on_done)
    # Divide by what was actually rendered: the Qt event loop can process a
    # tick or two after quit(), and a denominator you assumed is one that lies.
    return _tally(log_file, span["start"], rendered or frames,
                  end=span["end"])


def measure(frames: int, video: Path | None) -> tuple[int, collections.Counter]:
    """Render *frames* at DEFAULT verbosity; return (frames, records by site)."""
    app = trcc()
    log_file = _open_log()

    keys = [d.key for d in getattr(app.dispatch(DiscoverDevices()), "devices", [])]
    if not keys:
        raise SystemExit("no device attached — this needs real hardware")
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

    for _ in range(WARMUP):    # connect + first-frame lines are one-shot, not
        tick()                 # per-frame, so they must not be counted

    # A byte offset, not a line count: the file is appended to.
    before = log_file.stat().st_size if log_file.exists() else 0
    for _ in range(frames):
        tick()

    return _tally(log_file, before, frames)


#: Ordinary-logger sites a sensor tick is ALLOWED to write, as ``module:func``.
#: Not a count: a count target is machine-dependent, because a box with a
#: failing sensor legitimately emits ``_read``'s first-failure warning.  Line
#: numbers are excluded deliberately -- they move, the decision does not.
SENSOR_ALLOWED = {
    # The payload: the one line of the tick that carries resolved values, and
    # exactly what a `trcc report` is read for.  It STAYS on the ordinary
    # logger; everything else on the tick moved to the frame family.
    "trcc.core.ports:BaselineSensors.snapshot",
}


def measure_sensors(ticks: int) -> tuple[int, collections.Counter]:
    """Records per SENSOR TICK, at default verbosity.  Needs no device.

    The render arms cannot see this path at all: ``--gui`` drives frames with a
    zero-interval timer, so 200 of them finish INSIDE one 2-second sensor tick
    and the tick's records never land in the measured window.  Measured on real
    hardware, ``--gui`` reports 0.03 records/frame and ZERO sensor records while
    the sensor path was writing 39 records per tick -- 73% of a real log.

    ``read_all`` is cache-gated, so a loop over ``snapshot()`` polls ONCE and
    reports a confident 1/N.  ``_interval_s = 0`` defeats that; without it this
    tool measures nothing and says so cheerfully.
    """
    from trcc.adapters.system import current_platform

    log_file = _open_log()
    sensors = current_platform().sensors()
    sensors._interval_s = 0
    for _ in range(WARMUP):                     # discovery lines are one-shot
        sensors.snapshot()
    for handler in logging.getLogger().handlers:
        handler.flush()
    before = log_file.stat().st_size if log_file.exists() else 0
    for _ in range(ticks):
        sensors.snapshot()
    for handler in logging.getLogger().handlers:
        handler.flush()
    return _tally(log_file, before, ticks)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument("--gui", action="store_true",
                    help="measure inside the REAL GUI, so ui/ counts at all")
    ap.add_argument("--sensors", action="store_true",
                    help="measure the SENSOR TICK, which the frame arms cannot "
                         "see; needs no device")
    args = ap.parse_args()

    if args.gui and args.video is not None:
        raise SystemExit("--gui measures whatever the GUI has loaded; "
                         "--video is for the headless arm")
    if args.sensors and (args.gui or args.video is not None):
        raise SystemExit("--sensors measures the sensor tick, not a render "
                         "workload; run it on its own")

    if args.sensors:
        units, by_site = measure_sensors(args.frames)
        unit, workload = "tick", "sensor tick"
        # Site-based, not count-based: the payload line is SUPPOSED to be here.
        # ``module:func:lineno [LEVEL]`` -> ``module:func``; the line number
        # moves whenever the file does, the decision does not.
        offenders = {
            site: n for site, n in by_site.items()
            if ":".join(site.split(":")[:2]) not in SENSOR_ALLOWED
        }
        target = f"only {len(SENSOR_ALLOWED)} allowed site(s)"
    else:
        units, by_site = (measure_gui(args.frames) if args.gui
                          else measure(args.frames, args.video))
        unit = "frame"
        workload = "GUI" if args.gui else ("video" if args.video else "static theme")
        offenders = dict(by_site)
        target = "0.00"

    total = sum(by_site.values())
    print(f"# {total} record(s) over {units} {unit}s · {workload} · default -v")
    print(f"# {total / units:.2f} records/{unit}  (target: {target})")
    for site, n in by_site.most_common():
        mark = " " if site not in offenders else "!"
        print(f"{mark}{n / units:9.3f}  {site}")
    if offenders:
        print(f"# {len(offenders)} site(s) marked ! are not allowed on this path")
    return 0 if not offenders else 1


if __name__ == "__main__":
    raise SystemExit(main())
