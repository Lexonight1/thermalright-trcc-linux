#!/usr/bin/env python3
"""Prove the Qt windows collect garbage on the GUI thread, and own no QObject off it.

Two hazards, one drive of the REAL window on the mock fleet:

* **A collection on a background thread.**  When it frees a widget, PySide runs
  the widget's C++ destructor on that thread with the GIL released -- the CI
  hang.  ``trcc.ui.qapp.collect_on_gui_thread`` turns automatic gc off and
  collects from a GUI-thread timer; a ``gc.callbacks`` hook here counts every
  collection that freed something, by the thread it ran on.
* **A QObject living on a background thread.**  The collector cannot help an
  object a thread OWNS.  Every 2 s a GUI-thread timer walks ``gc.get_objects()``
  and names each live QObject whose thread affinity is not the GUI thread.

A run is only evidence if the instrument ran, so it also reports how many
collections ran on the GUI thread and how many scans happened; zero of either
voids the verdict.  ``--selftest`` proves it can fail: the collector stubbed out
must show off-thread collections, and a QTimer planted on a thread must be named.

    PYTHONPATH=src python3.12 dev/tools/gc_threads.py                  # gui, 30 s
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --ui qtgui
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --alloc          # + a garbage-making thread
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --control        # collector stubbed out
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --plant          # QTimer made on a thread
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --perf           # + perf instructions:u
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --selftest       # the answers it must give

Every drive runs sandboxed: a throwaway HOME and XDG dirs, ``xvfb-run`` and
``dbus-run-session`` (memory: reference_drive_windows_on_xvfb), so it never
opens a window on the desktop, reaches the user's running App, or leaks a tray
icon.  The device fleet is scripted (``dev/_mock_bootstrap``): no USB is touched.

Exit 0 = clean (or every selftest answer right), 1 = a hazard found (or a
selftest answer wrong), 2 = the drive produced no verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARK = "GC_THREADS "
SCAN_MS = 2000
PLANT_AFTER_S = 5.0
UI_ENTRY = {
    "gui": ("dev/mock_gui.py", ["--all"]),
    "qtgui": ("dev/mock.py", ["--ui", "qtgui"]),
}


@dataclass
class Verdict:
    """What one drive saw, parsed from the child's result line."""

    ui: str
    flags: list[str]
    gc_enabled: bool
    on_gui: int
    off_gui: dict[str, int]
    foreign: dict[str, int]
    scans: int
    instructions: int | None = None

    @property
    def alive(self) -> bool:
        """The instrument ran: the GUI thread collected and the scan fired."""
        return self.on_gui > 0 and self.scans > 0

    @property
    def clean(self) -> bool:
        return not self.gc_enabled and not self.off_gui and not self.foreign

    def lines(self) -> list[str]:
        flags = " ".join(self.flags) or "(none)"
        out = [
            f"{self.ui} {flags}",
            f"  automatic gc on at exit : {self.gc_enabled}",
            f"  collections on GUI      : {self.on_gui}",
            f"  collections off GUI     : {sum(self.off_gui.values())} {self.off_gui or ''}",
            f"  QObjects off GUI thread : {self.foreign or 'none'} (over {self.scans} scans)",
        ]
        if self.instructions is not None:
            out.append(f"  instructions:u          : {self.instructions:,}")
        return out


# ─── Child: runs inside the sandbox, beside the real window ──────────────────

@dataclass
class _Probe:
    """Counts collections by thread and QObjects by thread affinity."""

    control: bool
    on_gui: int = 0
    off_gui: dict[str, int] = field(default_factory=dict)
    foreign: dict[str, int] = field(default_factory=dict)
    scans: int = 0
    armed: bool = False
    _keep: list[object] = field(default_factory=list)

    def note(self, phase: str, info: dict[str, int]) -> None:
        """``gc.callbacks`` hook: one count per collection that freed something.

        Only from the moment the window's collector is set up: before it there
        is no widget for a stray collection to destroy, and the App's threads
        do not exist yet.
        """
        import threading
        if not self.armed or phase != "stop" or not info["collected"]:
            return
        thread = threading.current_thread()
        if thread is threading.main_thread():
            self.on_gui += 1
        else:
            self.off_gui[thread.name] = self.off_gui.get(thread.name, 0) + 1

    def scan(self) -> None:
        """On the GUI thread: name every live QObject owned by another thread."""
        import gc

        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if qapp is None:
            return
        gui = qapp.thread()
        self.scans += 1
        for obj in gc.get_objects():
            if not isinstance(obj, QObject):
                continue
            try:
                owner = obj.thread()
            except RuntimeError:        # C++ side already deleted
                continue
            if owner != gui:
                key = f"{type(obj).__module__}.{type(obj).__qualname__}"
                self.foreign[key] = self.foreign.get(key, 0) + 1

    def hook_collector(self) -> None:
        """Start the scan where the collector starts; stub the collector under --control."""
        import trcc.ui.qapp as qapp_module
        real = qapp_module.collect_on_gui_thread

        def wrapped(qapp):
            from PySide6.QtCore import QTimer
            timer = QTimer(qapp)
            timer.setInterval(SCAN_MS)
            timer.timeout.connect(self.scan)
            timer.start()
            self.armed = True
            return None if self.control else real(qapp)

        qapp_module.collect_on_gui_thread = wrapped

    def plant(self) -> None:
        """Make a QTimer on a plain thread -- the object the scan must name."""
        from PySide6.QtCore import QTimer
        self._keep.append(QTimer())

    def report(self, ui: str, flags: list[str]) -> None:
        import gc
        print(MARK + json.dumps({
            "ui": ui, "flags": flags, "gc_enabled": gc.isenabled(),
            "on_gui": self.on_gui, "off_gui": self.off_gui,
            "foreign": self.foreign, "scans": self.scans,
        }), file=sys.stderr, flush=True)


def _make_garbage() -> None:
    """A thread allocating cyclic garbage all run long: it crosses gen 0 often."""
    import time

    class Node:
        def __init__(self) -> None:
            self.me = self

    while True:
        for _ in range(500):
            Node()
        time.sleep(0.1)


def _child(args: argparse.Namespace, flags: list[str]) -> None:
    import atexit
    import gc
    import runpy
    import signal
    import threading

    probe = _Probe(control=args.control)
    gc.callbacks.append(probe.note)
    probe.hook_collector()
    if args.alloc:
        threading.Thread(target=_make_garbage, name="alloc", daemon=True).start()
    if args.plant:
        threading.Timer(PLANT_AFTER_S, probe.plant).start()
    # SIGTERM, not a kill: the UI's own stop handler closes the window and the
    # App, so the exit hook below runs and prints the result.
    threading.Timer(args.seconds,
                    lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    atexit.register(probe.report, args.ui, flags)
    script, argv = UI_ENTRY[args.ui]
    sys.argv = [str(REPO / script), *argv]
    runpy.run_path(sys.argv[0], run_name="__main__")


# ─── Parent: builds the sandbox, runs the child, reads its verdict ───────────

def _instructions(perf_out: Path) -> int:
    """Sum ``instructions:u`` across perf's lines (hybrid CPUs print one per core type)."""
    total = 0
    for line in perf_out.read_text(encoding="utf-8").splitlines():
        value, _, rest = line.partition(",")
        if "instructions" in rest and value.isdigit():
            total += int(value)
    return total


def drive(ui: str, seconds: int, flags: list[str], perf: bool) -> Verdict:
    """Run one sandboxed drive of *ui* with *flags*; return what it saw."""
    with tempfile.TemporaryDirectory(prefix="gc-threads-") as tmp:
        home = Path(tmp)
        runtime = home / "run"
        runtime.mkdir(mode=0o700)
        env = {k: v for k, v in os.environ.items()
               if k not in ("WAYLAND_DISPLAY", "QT_QPA_PLATFORM")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_RUNTIME_DIR=str(runtime),
                   XDG_DATA_HOME=str(home / ".local/share"),
                   XDG_CACHE_HOME=str(home / ".cache"),
                   PYTHONPATH=str(REPO / "src"))
        child = [sys.executable, str(Path(__file__).resolve()), "--child",
                 "--ui", ui, "--seconds", str(seconds), *flags]
        perf_out = home / "perf.csv"
        if perf:
            child = ["perf", "stat", "-x,", "-e", "instructions:u",
                     "-o", str(perf_out), "--", *child]
        cmd = ["timeout", "-s", "KILL", str(seconds + 90),
               "xvfb-run", "-a", "dbus-run-session", "--", *child]
        proc = subprocess.run(cmd, env=env, cwd=REPO, capture_output=True,
                              text=True, check=False)
        # xvfb-run folds the client's stderr into stdout, so read both.
        output = (proc.stdout + proc.stderr).splitlines()
        result = next((line for line in output if line.startswith(MARK)), None)
        if result is None:
            tail = "\n".join(output[-25:])
            print(f"no verdict from {ui} {flags} (rc={proc.returncode}):\n{tail}",
                  file=sys.stderr)
            raise SystemExit(2)
        verdict = Verdict(**json.loads(result[len(MARK):]))
        if perf:
            verdict.instructions = _instructions(perf_out)
        return verdict


SELFTEST: tuple[tuple[str, list[str], Callable[[Verdict], bool]], ...] = (
    ("collector on, garbage thread -> clean", ["--alloc"],
     lambda v: v.clean),
    ("collector stubbed, garbage thread -> off-GUI collections", ["--control", "--alloc"],
     lambda v: v.gc_enabled and bool(v.off_gui)),
    ("QTimer planted on a thread -> named", ["--plant"],
     lambda v: "PySide6.QtCore.QTimer" in v.foreign),
)


def _selftest(ui: str, seconds: int) -> int:
    wrong = 0
    for name, flags, expected in SELFTEST:
        verdict = drive(ui, seconds, flags, perf=False)
        ok = verdict.alive and expected(verdict)
        wrong += not ok
        print(f"{'OK   ' if ok else 'WRONG'} {name}")
        print("\n".join(verdict.lines()))
    print(f"selftest: {len(SELFTEST) - wrong}/{len(SELFTEST)} answers right")
    return 1 if wrong else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ui", choices=sorted(UI_ENTRY), default="gui")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--control", action="store_true",
                        help="stub the collector out: automatic gc stays on")
    parser.add_argument("--alloc", action="store_true",
                        help="add a thread making cyclic garbage")
    parser.add_argument("--plant", action="store_true",
                        help="create a QTimer on a plain thread")
    parser.add_argument("--perf", action="store_true",
                        help="count instructions:u with perf stat")
    parser.add_argument("--selftest", action="store_true",
                        help="run the controls; each must give its known answer")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    flags = [f"--{name}" for name in ("control", "alloc", "plant")
             if getattr(args, name)]
    if args.child:
        _child(args, flags)
        return 0
    if args.selftest:
        return _selftest(args.ui, args.seconds)
    verdict = drive(args.ui, args.seconds, flags, args.perf)
    print("\n".join(verdict.lines()))
    if not verdict.alive:
        print("VOID: the instrument did not run (no GUI-thread collection or no scan)")
        return 2
    print("CLEAN" if verdict.clean else "HAZARD")
    return 0 if verdict.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
