#!/usr/bin/env python3
"""Instructions-per-frame on REAL hardware, measured differentially.

Answers "did this build get more expensive per rendered frame?" for two trees,
on the device, with the work counted independently.

    # one arm, current tree
    PYTHONPATH=src python3.12 dev/tools/glass_bench.py

    # compare against an older tree checked out in a worktree
    git worktree add --detach /tmp/v992 v9.9.2
    PYTHONPATH=src python3.12 dev/tools/glass_bench.py --against /tmp/v992/src

    # the workload the CPU hunt used
    PYTHONPATH=src python3.12 dev/tools/glass_bench.py --video ~/.trcc/data/web/320320/a008.mp4

NEVER measure CPU% to compare builds on a scaling box.  ``ps %cpu``, ``utime``
and ``task-clock`` measure TIME ON CPU, and `intel_pstate`/`powersave` moves the
clock with the load: silencing logging once measured 24%% fewer instructions and
36%% MORE time on CPU.  See memory ``project_cpu_regression_is_logging``.

This file exists because its predecessor did not.  The 2026-08-26 harness lived
in a session scratchpad and was gone by 2026-08-27; rebuilding it cost most of a
session, and rebuilding it re-introduced three instrument bugs that are now
comments in this file rather than lessons to relearn:

1.  **``DeviceSender._raw_write`` is the WRONG work counter.**  The sender is
    asynchronous and coalesces -- a tight loop logs "submit superseded a pending
    frame" and 1 tick in 30 reaches the wire.  It counts what the DEVICE
    accepted.  ``DisplayService.build_frame`` is the CPU work, once per tick.
2.  **Keep perf's output away from the harness's.**  A wrapper doing
    ``2>&1 >/dev/null`` swallows the work-parity line, and a measurement whose
    work count you cannot see is not a measurement.  ``perf -o FILE`` here.
3.  **A differential needs a span that dominates startup.**  At N=200 vs 400 the
    measured span was ~2 G against ~9 G of fixed startup, and three replicates of
    ONE build read 6.25 / 11.32 / 12.81 M/frame (+-34%%) -- useless for a 9%%
    effect.  At 1000 vs 3000 it is +-3%%.  An ffmpeg child inside the fixed part
    is the main variance source, so the static workload is the quieter one.

Hybrid P/E-core boxes report ``instructions:u`` per PMU (``cpu_atom`` /
``cpu_core``); both are summed here.  Validate with ``--selftest``, which checks
a synthetic workload scales linearly before you trust any app number.
"""
from __future__ import annotations

import argparse
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

_RESULT = re.compile(r"RESULT requested=(\d+) sent=(\d+)")

#: Performance cores, on a hybrid (P/E) Intel box.  Empty elsewhere.
_PCORES = Path("/sys/devices/cpu_core/cpus")


def _pinned(cmd: list[str]) -> list[str]:
    """Pin *cmd* to the performance cores, if this box has two core types.

    THIS IS NOT A TUNING KNOB -- without it the numbers are worthless.  On a
    hybrid box perf exposes one PMU per core type and counts each only while the
    process is on that type, then SCALES the estimate: a run that spent 5%% of
    its time on E-cores reports ``cpu_atom (5.15%%)`` / ``cpu_core (94.85%%)``,
    and summing two scaled partials across a migrating process is unstable.
    Measured on this box with a synthetic loop that is exactly linear:

        unpinned   two identical doublings disagreed by  44%%
        pinned     two identical runs disagreed by      0.19%%

    ``taskset`` keeps the process on one PMU, so nothing is multiplexed.
    """
    if not _PCORES.exists():
        return cmd
    return ["taskset", "-c", _PCORES.read_text().strip(), *cmd]


def _perf_instructions(cmd: list[str], env_src: str | None) -> tuple[int, int, int]:
    """Run *cmd* under perf; return (instructions, requested, sent).

    ``requested``/``sent`` come from the child's own stderr and MUST be equal --
    a build that renders fewer frames "wins" by doing less work.
    """
    import os

    env = dict(os.environ)
    if env_src:
        env["PYTHONPATH"] = env_src
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    with tempfile.NamedTemporaryFile("r", suffix=".perf") as out:
        proc = subprocess.run(
            ["perf", "stat", "-x,", "-e", "instructions:u", "-o", out.name,
             "--", *_pinned(cmd)],
            env=env, capture_output=True, text=True, check=False,
        )
        text = Path(out.name).read_text()

    total = 0
    for line in text.splitlines():
        if "instructions" not in line:
            continue
        digits = re.sub(r"[^0-9]", "", line.split(",")[0])
        if digits:
            total += int(digits)

    m = _RESULT.search(proc.stderr)
    if m is None:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit("harness produced no RESULT line — see stderr above")
    return total, int(m.group(1)), int(m.group(2))


def _slope(src: str | None, lo: int, hi: int, video: Path | None,
           reps: int, label: str) -> float:
    """Median instructions/frame across *reps* differential pairs."""
    inner = [sys.executable, str(Path(__file__).resolve()), "--run", "0"]
    slopes: list[float] = []
    for rep in range(1, reps + 1):
        pair: list[int] = []
        for n in (lo, hi):
            cmd = list(inner)
            cmd[cmd.index("--run") + 1] = str(n)
            if video is not None:
                cmd += ["--video", str(video)]
            instr, requested, sent = _perf_instructions(cmd, src)
            if requested != sent:
                raise SystemExit(
                    f"{label}: WORK PARITY BROKEN at N={n} "
                    f"(requested={requested} sent={sent}) — measurement void")
            pair.append(instr)
        per_frame = (pair[1] - pair[0]) / (hi - lo)
        slopes.append(per_frame)
        print(f"  {label} rep{rep}: {per_frame / 1e6:7.3f} M/frame")
    return statistics.median(slopes)


def _selftest() -> None:
    """A known answer from OUTSIDE the app, measured the SAME way as the app.

    A ratio test at small sizes does not work here: CPython startup is ~250 M
    instructions, which swamps a synthetic loop, and the first version of this
    selftest duly reported 3.44x and 1.85x for two identical doublings.  So it
    uses the benchmark's own differential instead -- equal increments for equal
    added work, with startup cancelled.  If these two disagree by much, do not
    trust any number this tool prints.
    """
    import os

    def instructions(n: int) -> int:
        cmd = [sys.executable, "-c", f"sum(range({n}))"]
        with tempfile.NamedTemporaryFile("r", suffix=".perf") as out:
            subprocess.run(
                ["perf", "stat", "-x,", "-e", "instructions:u", "-o", out.name,
                 "--", *_pinned(cmd)],
                env=dict(os.environ), capture_output=True, text=True, check=False,
            )
            text = Path(out.name).read_text()
        return sum(
            int(re.sub(r"[^0-9]", "", ln.split(",")[0]) or 0)
            for ln in text.splitlines() if "instructions" in ln
        )

    unit = 20_000_000
    i1, i2, i3 = (instructions(unit * k) for k in (1, 2, 3))
    inc1, inc2 = i2 - i1, i3 - i2
    print("selftest — equal work must cost equal instructions (differential):")
    print(f"  n={unit:,}    total={i1:,}")
    print(f"  n={unit * 2:,}    total={i2:,}   increment={inc1:,}")
    print(f"  n={unit * 3:,}    total={i3:,}   increment={inc2:,}")
    skew = abs(inc1 - inc2) / max(inc1, inc2) * 100
    verdict = "OK" if skew < 10 else "TOO NOISY — do not trust app numbers"
    print(f"  increments differ by {skew:.1f}%  → {verdict}")


def _run_arm(n: int, video: Path | None) -> None:
    """Inner mode: build *n* frames on the real device and report the count."""
    import inspect
    import logging
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from trcc._boot import trcc
    from trcc.adapters.infra.logging import configure_logging
    from trcc.core.commands import (
        ConnectDevice,
        DiscoverDevices,
        LoadTheme,
        PlayVideo,
        RenderAndSend,
    )
    from trcc.services.display import DisplayService

    # See docstring note 1: build_frame, never DeviceSender._raw_write.
    counter = {"frames": 0}
    real_build = DisplayService.build_frame

    def counting_build(self, *a, **kw):
        counter["frames"] += 1
        return real_build(self, *a, **kw)

    DisplayService.build_frame = counting_build   # type: ignore[method-assign]

    # ``trcc()`` auto-detects the platform: ``current_platform`` moved between
    # releases, so importing it by path would measure the import, not the drift.
    app = trcc()
    platform = app.platform

    # Configure logging exactly as the shipping app does at default verbosity
    # (ui/cli/main:_root) — measuring it unconfigured understates its cost, and
    # logging cost is the thing this benchmark was built to track.  ``per_frame``
    # landed in 19ab3ad0; its ABSENCE in an older tree is the old behaviour.
    kwargs = {}
    if "per_frame" in inspect.signature(configure_logging).parameters:
        kwargs["per_frame"] = False
    configure_logging(platform.paths().log_file(), level=logging.DEBUG,
                      stderr_level=logging.WARNING, **kwargs)

    disc = app.dispatch(DiscoverDevices())
    keys = [d.key for d in getattr(disc, "devices", [])]
    if not keys:
        raise SystemExit("no device attached — this benchmark needs real hardware")
    key = keys[0]

    conn = app.dispatch(ConnectDevice(key=key))
    if not conn.ok:
        raise SystemExit(f"connect failed: {conn.message}")

    theme = platform.paths().data_dir() / "theme320320" / "Theme1"
    load = app.dispatch(LoadTheme(key=key, path=theme))
    if not load.ok:
        raise SystemExit(f"theme load failed: {load.message}")

    # A STATIC theme makes every frame after the first a BgMaskCache hit, so it
    # measures the cache rather than the render path on any tree that has one.
    # An advancing video presents a different frame per tick.  The advance is
    # driven through ``Playback`` directly, not ``TickDisplay``, because
    # TickDisplay does not exist in older trees — both arms run one loop.
    playback = None
    if video is not None:
        play = app.dispatch(PlayVideo(key=key, path=video))
        if not play.ok:
            raise SystemExit(f"play failed: {play.message}")
        playback = app.media.playback(key)
        if playback is None:
            raise SystemExit("no playback after PlayVideo")

    def tick() -> None:
        if playback is not None:
            playback.advance()
        app.dispatch(RenderAndSend(key=key))

    for _ in range(20):        # warm-up; identical at both sizes, so it cancels
        tick()

    start = counter["frames"]
    for _ in range(n):
        tick()

    print(f"RESULT requested={n} sent={counter['frames'] - start}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, help=argparse.SUPPRESS)   # inner mode
    ap.add_argument("--against", metavar="SRC",
                    help="a second tree's src/ to compare against (a worktree)")
    ap.add_argument("--video", type=Path, help="advancing-video workload")
    ap.add_argument("--lo", type=int, default=1000, help="small frame count")
    ap.add_argument("--hi", type=int, default=3000, help="large frame count")
    ap.add_argument("--reps", type=int, default=3, help="replicates per arm")
    ap.add_argument("--selftest", action="store_true",
                    help="validate perf counting against a known answer, then exit")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if args.run is not None:
        _run_arm(args.run, args.video)
        return

    workload = f"advancing video ({args.video.name})" if args.video else "static theme"
    print(f"workload: {workload}   span: {args.lo} → {args.hi} frames   "
          f"reps: {args.reps}")

    here = _slope(None, args.lo, args.hi, args.video, args.reps, "this tree")
    print(f"\nthis tree : {here / 1e6:7.3f} M instructions/frame (median)")

    if args.against:
        there = _slope(args.against, args.lo, args.hi, args.video, args.reps,
                       "--against")
        print(f"--against : {there / 1e6:7.3f} M instructions/frame (median)")
        faster = "cheaper" if here < there else "MORE EXPENSIVE"
        print(f"\nthis tree is {max(here, there) / min(here, there):.1f}x "
              f"{faster} per frame")


if __name__ == "__main__":
    main()
