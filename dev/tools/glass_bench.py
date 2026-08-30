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
4.  **A cold ``--against`` tree reads FALSELY CHEAP.**  With no ``__pycache__``
    its first run compiles the whole tree; that run is ``lo``, so the
    differential subtracts a cost only ``i_lo`` paid and the slope comes out
    low.  Measured 2026-08-30: the second arm's rep1 read 1.192 / 1.387 / 1.203
    M/frame against ~1.85 once warm -- enough to report "1.2x MORE EXPENSIVE"
    for a change that added nothing to the frame path.  Two identical trees
    reproduced it and swapping the arms moved the "regression" to the other
    tree, which is what proved it was position and not code.
    ``_warm_bytecode`` compiles both arms before either is measured.

Hybrid P/E-core boxes report ``instructions:u`` per PMU (``cpu_atom`` /
``cpu_core``); both are summed here.  Validate with ``--selftest``, which checks
a synthetic workload scales linearly before you trust any app number.
"""
from __future__ import annotations

import argparse
import compileall
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

_RESULT = re.compile(r"RESULT requested=(\d+) sent=(\d+)")

#: Repo root -- ``dev/tools/glass_bench.py`` is two levels down.
_REPO = Path(__file__).resolve().parents[2]

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


def _warm_bytecode(src: str | None) -> None:
    """Compile the tree to ``.pyc`` before it is measured -- instrument bug #4.

    A fresh worktree has no ``__pycache__``, so its first run compiles the tree.
    That run is the ``lo`` one and the differential subtracts it, hiding a cost
    only ``i_lo`` paid and dragging the slope down.

    ``src=None`` means the ambient tree (the ``--against``-less arm), and it is
    warmed too rather than skipped: warming only ONE arm just moves the bias to
    the other one, and the ambient tree is stale exactly when it matters -- a
    ``git checkout`` that rewrites ``.py`` files invalidates its ``.pyc``.
    """
    tree = Path(src) if src else _REPO / "src"
    print(f"  warming bytecode: {tree}")
    compileall.compile_dir(str(tree), quiet=2, force=False)


def _slope(src: str | None, lo: int, hi: int, video: Path | None,
           reps: int, label: str, gui: bool = False) -> float:
    """Median instructions/frame across *reps* differential pairs."""
    _warm_bytecode(src)
    inner = [sys.executable, str(Path(__file__).resolve()), "--run", "0"]
    slopes: list[float] = []
    for rep in range(1, reps + 1):
        pair: list[tuple[int, int]] = []
        for n in (lo, hi):
            cmd = list(inner)
            cmd[cmd.index("--run") + 1] = str(n)
            if video is not None:
                cmd += ["--video", str(video)]
            if gui:
                cmd += ["--gui"]
            instr, requested, sent = _perf_instructions(cmd, src)
            # Divide by OBSERVED work, never requested.  The GUI arm cannot stop
            # mid-tick -- the event loop processes a build or two after quit() --
            # so `sent` overshoots by a little and no tolerance would be exactly
            # right.  Using the count the run actually did is not a workaround;
            # it is the more correct denominator, and it also means a build that
            # quietly renders FEWER frames can no longer look cheap.
            #
            # The guard stays for gross failures, which is what it caught before:
            # DeviceSender coalescing let 1 tick in 30 reach the wire.
            if sent == 0 or not (0.5 * n <= sent <= 1.5 * n):
                raise SystemExit(
                    f"{label}: WORK BROKEN at N={n} "
                    f"(requested={requested} sent={sent}) — measurement void")
            pair.append((instr, sent))
        (i_lo, n_lo), (i_hi, n_hi) = pair
        if n_hi == n_lo:
            raise SystemExit(f"{label}: identical frame counts — no span to measure")
        per_frame = (i_hi - i_lo) / (n_hi - n_lo)
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


def _host_platform():
    """The host Platform, built WITHOUT an App or a renderer.

    The GUI arm cannot go through ``trcc()``: that builds a ``QtRenderer``,
    which creates a ``QGuiApplication``, and ``run()`` then dies with
    "destroy the QGuiApplication singleton before creating a new QApplication".

    The accessor moved in the two-registry refactor -- ``PlatformFactory.
    current()`` in v9.9.2, ``current_platform()`` at HEAD -- so try both rather
    than import one by path, which would measure the import rather than the
    drift.
    """
    import importlib

    system = importlib.import_module("trcc.adapters.system")
    # Resolved by NAME, not by import statement: the v9.9.2 symbol does not
    # exist at HEAD, so a static `from ... import PlatformFactory` is a type
    # error on the very tree this tool ships in.
    modern = getattr(system, "current_platform", None)
    if modern is not None:
        return modern()
    legacy = getattr(system, "PlatformFactory", None)
    if legacy is None:
        raise SystemExit("no platform accessor found — unknown tree layout")
    return legacy.current()


def _logging_kwargs(configure_logging) -> dict:
    """``per_frame=`` landed in 19ab3ad0 and is absent in v9.9.2.

    Pass it only where supported -- its absence IS the old behaviour under
    measurement, so this must not be "fixed" by forcing the flag on.
    """
    import inspect

    if "per_frame" in inspect.signature(configure_logging).parameters:
        return {"per_frame": False}
    return {}


def _run_gui_arm(n: int) -> None:
    """Inner mode, GUI: compose *n* frames inside the REAL shipping GUI.

    The headless arm measures the render path.  This one adds what that path
    never touches -- live widgets, the preview render, the sensor loop, the Qt
    event loop -- which is the only place a regression can hide once the render
    path measures clean.

    ``run(platform, ...)`` is the ONE shared GUI composition root and its
    signature is byte-identical in v9.9.2 and HEAD, so both arms enter through
    the same door.  Production seams are off: no single-instance lock (it would
    collide with a real install), no IPC socket, and ``force_exit=False`` so
    teardown is a normal return and therefore identical at both sizes.

    **Renders are DRIVEN, not awaited.**  The first version simply let the GUI
    tick and counted -- which made the run's duration depend on whatever state
    happened to be persisted: with a video background it composed at ~12 fps
    (120 frames in 10 s), and with none it fell back to the 2 s metrics tick,
    where 900 frames is half an hour.  A benchmark whose length depends on the
    user's saved settings is not a benchmark.  A zero-interval timer drives
    RenderAndSend instead, so the frame count is the independent variable in
    both arms while every GUI observer, preview update and repaint still runs.
    """
    import logging
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from trcc.adapters.infra.logging import configure_logging
    from trcc.core.commands import RenderAndSend
    from trcc.services.display import DisplayService
    from trcc.ui.gui import run

    platform = _host_platform()
    kwargs = _logging_kwargs(configure_logging)
    configure_logging(platform.paths().log_file(), level=logging.DEBUG,
                      stderr_level=logging.WARNING, **kwargs)

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
            if counter["frames"] >= n:
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
    print(f"RESULT requested={n} sent={counter['frames']}", file=sys.stderr)


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
    ap.add_argument("--gui", action="store_true",
                    help="measure the REAL GUI shell, not just the render path")
    ap.add_argument("--selftest", action="store_true",
                    help="validate perf counting against a known answer, then exit")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if args.run is not None:
        if args.gui:
            _run_gui_arm(args.run)
        else:
            _run_arm(args.run, args.video)
        return

    workload = f"advancing video ({args.video.name})" if args.video else "static theme"
    workload += " · REAL GUI" if args.gui else " · headless"
    print(f"workload: {workload}   span: {args.lo} → {args.hi} frames   "
          f"reps: {args.reps}")

    here = _slope(None, args.lo, args.hi, args.video, args.reps, "this tree",
                  args.gui)
    print(f"\nthis tree : {here / 1e6:7.3f} M instructions/frame (median)")

    if args.against:
        there = _slope(args.against, args.lo, args.hi, args.video, args.reps,
                       "--against", args.gui)
        print(f"--against : {there / 1e6:7.3f} M instructions/frame (median)")
        faster = "cheaper" if here < there else "MORE EXPENSIVE"
        print(f"\nthis tree is {max(here, there) / min(here, there):.1f}x "
              f"{faster} per frame")


if __name__ == "__main__":
    main()
