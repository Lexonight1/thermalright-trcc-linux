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
  and names each QObject whose thread affinity is not the GUI thread and that
  was there at the scan before too.  A thread may use a QObject of its own --
  the renderer's ``QBuffer`` lives one ``encode_png`` call -- and freeing it by
  refcount on that thread is sound; only one that LIVES there is the hazard.
  Those passing ones are counted apart, as ``transient``.

Both windows get the same three panels -- a SCSI LCD, a HID LCD and an LED --
so their senders, renders and previews run.  A run is only evidence if the
instrument ran AND the panels were there, so it also reports collections on
the GUI thread, scans, and the most panels the App held; zero of any voids the
verdict.  (An earlier version drove ``--all``, which connects nothing at boot:
every count it gave was of an empty window.)  ``--selftest`` proves it can fail: the collector stubbed out
must show off-thread collections, and a QTimer planted on a thread must be named.

    PYTHONPATH=src python3.12 dev/tools/gc_threads.py                  # gui, 30 s
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --ui qtgui
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --alloc          # + a garbage-making thread
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --control        # collector stubbed out
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --plant          # QTimer made on a thread
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --perf           # + perf instructions:u
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --selftest       # the answers it must give
    PYTHONPATH=src python3.12 dev/tools/gc_threads.py --ui daemon      # the App with no window

``--ui daemon`` runs the real ``run_daemon`` on the same fleet and drives it
through a real ``AppProxy`` over its socket -- an event stream open, the panels
connected, a theme, a video, a preview every half second -- while a side thread
scans (it has no Qt loop).  The daemon keeps automatic gc, so it is safe only
if it holds no Python-subclass QObject at all, on any thread: that is its
verdict.  It runs in bubblewrap with an empty ``/dev``, so nothing in it can
reach a USB device even if something went wrong; the verdict is void unless
``/dev/bus/usb`` really is hidden.

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
from typing import Any
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARK = "GC_THREADS "
SCAN_MS = 2000
PLANT_AFTER_S = 5.0
UI_ENTRY = {
    "gui": ("dev/mock_gui.py", []),
    "qtgui": ("dev/mock.py", ["--ui", "qtgui"]),
}
#: Connected at boot in either window, through the harness's devices.json path.
FLEET = [
    {"type": "lcd", "name": "SCSI LCD", "vid": "0402", "pid": "3922",
     "pm": 32, "fbl": 100, "resolution": "320x320"},
    {"type": "lcd", "name": "HID LCD", "vid": "0416", "pid": "5302",
     "pm": 5, "fbl": 50, "resolution": "240x320"},
    {"type": "led", "name": "LED", "vid": "0416", "pid": "8001", "pm": 16},
]


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
    panels: int
    transient: dict[str, int] = field(default_factory=dict)
    py_qobjects: dict[str, int] = field(default_factory=dict)
    client: dict[str, list[int]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    usb_hidden: bool = False
    instructions: int | None = None

    @property
    def alive(self) -> bool:
        """The instrument ran with every panel connected: the scan fired, the
        App held every panel, and -- in a window -- the GUI thread collected;
        the daemon instead needs its USB hidden."""
        if self.ui == "daemon":
            return self.scans > 0 and self.panels == len(FLEET) and self.usb_hidden
        return self.on_gui > 0 and self.scans > 0 and self.panels == len(FLEET)

    @property
    def clean(self) -> bool:
        if self.ui == "daemon":
            # Automatic gc stays on: safe only with no Python QObject to free.
            return not self.foreign and not self.py_qobjects
        return not self.gc_enabled and not self.off_gui and not self.foreign

    def lines(self) -> list[str]:
        flags = " ".join(self.flags) or "(none)"
        out = [
            f"{self.ui} {flags}",
            f"  automatic gc on at exit : {self.gc_enabled}",
            f"  collections on GUI      : {self.on_gui}",
            f"  collections off GUI     : {sum(self.off_gui.values())} {self.off_gui or ''}",
            f"  QObjects off GUI thread : {self.foreign or 'none'} (over {self.scans} scans)",
            f"    passing, not living   : {self.transient or 'none'}",
            f"  panels connected        : {self.panels} of {len(FLEET)}",
        ]
        if self.ui == "daemon":
            out += [
                f"  Python QObjects (any)   : {self.py_qobjects or 'none'}",
                f"  client Commands ok/sent : {self.client}",
                f"  first refusal each      : {self.refused or 'none'}",
                f"  /dev/bus/usb hidden     : {self.usb_hidden}",
            ]
        if self.instructions is not None:
            out.append(f"  instructions:u          : {self.instructions:,}")
        return out


# ─── Child: runs inside the sandbox, beside the real window ──────────────────

@dataclass
class _Probe:
    """Counts collections by thread and QObjects by thread affinity."""

    control: bool
    daemon: bool = False
    on_gui: int = 0
    off_gui: dict[str, int] = field(default_factory=dict)
    foreign: dict[str, int] = field(default_factory=dict)
    scans: int = 0
    panels: int = 0
    armed: bool = False
    transient: dict[str, int] = field(default_factory=dict)
    py_qobjects: dict[str, int] = field(default_factory=dict)
    client: dict[str, list[int]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    _last_off_gui: set[int] = field(default_factory=set)
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
        """On the GUI thread: name every live QObject owned by another thread,
        and note how many panels the App holds."""
        import gc

        from PySide6.QtCore import QCoreApplication, QObject

        from trcc.app import App
        qapp = QCoreApplication.instance()
        if qapp is None:
            return
        gui = qapp.thread()
        self.scans += 1
        off_gui: set[int] = set()
        for obj in gc.get_objects():
            if isinstance(obj, App):
                self.panels = max(self.panels, len(obj.devices))
            if not isinstance(obj, QObject):
                continue
            try:
                owner = obj.thread()
            except RuntimeError:        # C++ side already deleted
                continue
            key = f"{type(obj).__module__}.{type(obj).__qualname__}"
            if owner != gui:
                off_gui.add(id(obj))
                seen = self.foreign if id(obj) in self._last_off_gui else self.transient
                seen[key] = seen.get(key, 0) + 1
            if self.daemon and not type(obj).__module__.startswith("PySide6"):
                self.py_qobjects[key] = self.py_qobjects.get(key, 0) + 1
        self._last_off_gui = off_gui

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
        """Make a Python-subclass QTimer on a plain thread -- the object the
        scan must name, as off-thread and, in the daemon, as a Python QObject."""
        from PySide6.QtCore import QTimer

        class Planted(QTimer):
            pass

        self._keep.append(Planted())

    def sent(self, result: object, command: object) -> None:
        """Tally one client Command: [ok, sent] by Command name."""
        name = type(command).__name__
        tally = self.client.setdefault(name, [0, 0])
        ok = bool(getattr(result, "ok", False))
        tally[0] += ok
        tally[1] += 1
        if not ok:
            self.refused.setdefault(name, str(getattr(result, "message", result)))

    def report(self, ui: str, flags: list[str]) -> None:
        import gc
        print(MARK + json.dumps({
            "ui": ui, "flags": flags, "gc_enabled": gc.isenabled(),
            "on_gui": self.on_gui, "off_gui": self.off_gui,
            "foreign": self.foreign, "transient": self.transient,
            "scans": self.scans,
            "panels": self.panels, "py_qobjects": self.py_qobjects,
            "client": self.client, "refused": self.refused,
            "usb_hidden": not Path("/dev/bus/usb").exists(),
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


def _drive_daemon(probe: _Probe, platform: Any, seconds: int) -> None:
    """The client: what a window does to the App, over the real socket --
    then SIGTERM, which the daemon's own stop handler answers."""
    import signal
    import time

    from tests.conftest import renderable_theme
    from trcc import ipc
    from trcc.core.commands import BuildPreview, ConnectDevice, LoadTheme, PlayVideo
    from trcc.core.events import FrameSent
    from trcc.proxy import AppProxy

    def send(command: Any) -> None:
        probe.sent(proxy.dispatch(command), command)

    deadline = time.monotonic() + seconds
    try:
        if not ipc.wait_for_daemon(timeout=30):
            print("gc_threads: the daemon never answered", file=sys.stderr)
            return
        proxy = AppProxy()
        proxy.events.subscribe(FrameSent, lambda _event: None)
        keys = [f"{spec['vid']}:{spec['pid']}" for spec in FLEET]
        for key in keys:
            send(ConnectDevice(key=key))
        paths = platform.paths()
        send(LoadTheme(key=keys[0],
                       path=renderable_theme(paths.theme_dir(320, 320), "Theme1")))
        clip = Path(os.environ["HOME"]) / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f",
                        "lavfi", "-i", "testsrc=size=320x320:rate=15:duration=4",
                        "-y", str(clip)], check=True)
        send(PlayVideo(key=keys[0], path=clip))     # on the panel with a theme
        while time.monotonic() < deadline:
            for key in keys[:2]:
                send(BuildPreview(key=key, encode="png"))
            time.sleep(0.5)
        proxy.close()
    finally:
        os.kill(os.getpid(), signal.SIGTERM)


def _child_daemon(args: argparse.Namespace, flags: list[str]) -> None:
    """The real ``run_daemon`` on the fleet, a client thread and a scan thread."""
    import atexit
    import gc
    import threading
    import time

    sys.path.insert(0, str(REPO))           # tests.* -- the scripted platform
    from tests.mock_platform import MockPlatform
    from trcc.daemon import run_daemon

    probe = _Probe(control=False, daemon=True, armed=True)
    gc.callbacks.append(probe.note)
    platform = MockPlatform(FLEET, Path(os.environ["HOME"]) / "trcc")

    def scan_forever() -> None:
        while True:
            time.sleep(SCAN_MS / 1000)
            probe.scan()

    threading.Thread(target=scan_forever, name="gc-threads-scan", daemon=True).start()
    threading.Thread(target=_drive_daemon, args=(probe, platform, args.seconds),
                     name="gc-threads-client", daemon=True).start()
    if args.plant:
        threading.Timer(PLANT_AFTER_S, probe.plant).start()
    atexit.register(probe.report, args.ui, flags)
    run_daemon(platform=platform)


def _child(args: argparse.Namespace, flags: list[str]) -> None:
    import atexit
    import gc
    import runpy
    import signal
    import threading

    if args.ui == "daemon":
        _child_daemon(args, flags)
        return

    # The harness reads its boot fleet from DEVICES_JSON when it is called;
    # point it at FLEET, so a local dev/devices.json changes nothing here.
    sys.path.insert(0, str(REPO / "dev"))
    import _mock_bootstrap
    fleet = Path(os.environ["HOME"]) / "devices.json"
    fleet.write_text(json.dumps(FLEET), encoding="utf-8")
    _mock_bootstrap.DEVICES_JSON = fleet
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
        if ui == "daemon":
            # No display, and no /dev beyond the basics: nothing can open USB.
            wrap = ["bwrap", "--dev-bind", "/", "/", "--dev", "/dev",
                    "--proc", "/proc", "--die-with-parent", "--"]
        else:
            wrap = ["xvfb-run", "-a", "dbus-run-session", "--"]
        cmd = ["timeout", "-s", "KILL", str(seconds + 90), *wrap, *child]
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
     lambda v: any(key.endswith(".Planted") for key in v.foreign)),
)

DAEMON_SELFTEST: tuple[tuple[str, list[str], Callable[[Verdict], bool]], ...] = (
    ("daemon -> no Python QObject, none off the main thread", [],
     lambda v: v.clean),
    ("Python QTimer planted on a thread -> named twice", ["--plant"],
     lambda v: any(key.endswith(".Planted") for key in v.foreign)
     and any(key.endswith(".Planted") for key in v.py_qobjects)),
)


def _selftest(ui: str, seconds: int) -> int:
    wrong = 0
    table = DAEMON_SELFTEST if ui == "daemon" else SELFTEST
    for name, flags, expected in table:
        verdict = drive(ui, seconds, flags, perf=False)
        ok = verdict.alive and expected(verdict)
        wrong += not ok
        print(f"{'OK   ' if ok else 'WRONG'} {name}")
        print("\n".join(verdict.lines()))
    print(f"selftest: {len(table) - wrong}/{len(table)} answers right")
    return 1 if wrong else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ui", choices=sorted([*UI_ENTRY, "daemon"]), default="gui")
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
        print("VOID: the instrument did not run (no GUI-thread collection, "
              "no scan, or not every panel connected)")
        return 2
    print("CLEAN" if verdict.clean else "HAZARD")
    return 0 if verdict.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
