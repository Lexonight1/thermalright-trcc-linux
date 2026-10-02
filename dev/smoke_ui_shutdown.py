#!/usr/bin/env python3
"""Does each long-running UI actually DIE — and clean up — when told to?

Process lifecycle is the one thing a unit test cannot prove: the suite can
assert ``QApplication.quit()`` was called, but only a real subprocess shows
whether the interpreter exited, the metrics thread stopped, the panel was
blanked and ``/dev/sgN`` released.

SIGTERM — what systemd / the session manager sends at PC shutdown — at two
moments per face:

    start-up  — just after ``UserInterface.start`` takes the signals, while
                the App composes and the fleet coldplugs.  Each face used to
                install its handler only as its loop began, so a SIGTERM here
                died with the default action (rc 143) and no cleanup — measured
                on both skins 2026-10-01.
    running   — inside the face's own loop.  Without a handler the process
                dies BEFORE ``qapp.exec()`` returns, so cleanup never runs and
                the LCD is left lit showing its last frame (#143).

The UI-quit path (tray "Exit" / window close) is not driven here:
with ``quitOnLastWindowClosed`` False (both skins hide to tray), accepting the
close is not enough: the event loop must be told to stop or the process lives
on with the metrics thread still polling.

A skin PASSES only when the process is gone AND its log shows the teardown
markers.  Exiting without them is the #143 bug, not a pass.

Runs against the MOCK fleet — never touches real hardware.

Usage::
    PYTHONPATH=src python3 dev/smoke_ui_shutdown.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "dev" / ".trcc" / "trcc.latest.log"

BOOT_TIMEOUT_S = 30
GRACE_S = 15
# Teardown proof.  ``cleanup complete`` is unconditional — it means the
# ``finally`` ran at all.  ``SleepDevice`` (the panel going dark, #143) can
# only appear when a device was actually attached, so it is required ONLY when
# ``App.close`` reports one: a mock fleet that connected nothing would
# otherwise fail a skin that shut down perfectly.
ALWAYS = ("cleanup complete",)
IF_DEVICES = ("SleepDevice",)
_CLOSE_LINE = "close: devices="


@dataclass(frozen=True)
class Skin:
    name: str
    argv: list[str]
    # Proof THIS skin's UI is live: its Qt loop is running, which is also
    # when the quit handlers are installed.  "Any trcc.ui.<skin> line" was
    # used until 2026-09-29 and matched ~0.2 s into a ~3 s bring-up of the
    # mock fleet, so the SIGTERM landed before the handlers existed and both
    # skins "failed" on the harness's timing, not on their teardown.
    boot_marker: str
    env: dict[str, str] = field(default_factory=dict)


_LOOP = "_exec: entering the Qt event loop"
#: ``UserInterface.start`` owns SIGTERM from here -- before compose and the
#: coldplug, so a signal on this line lands in the start-up window.
_STARTUP = "_catch_stop_signals:"

SKINS = (
    Skin("gui", ["dev/mock_gui.py", "-v", "-platform", "offscreen"],
         boot_marker=_LOOP),
    Skin("qtgui", ["dev/mock.py", "--ui", "qtgui", "-v",
                   "-platform", "offscreen"],
         boot_marker=_LOOP),
    # Its own runtime dir: a real daemon on this machine would otherwise own
    # the socket and the mock one would refuse to start.
    Skin("daemon", ["dev/_mock_daemon.py"],
         boot_marker="IPC server listening on",
         env={"XDG_RUNTIME_DIR": tempfile.mkdtemp(prefix="trcc-smoke-")}),
)
PHASES = ("start-up", "running")


def _launch(skin: Skin) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONPATH": "src", "QT_QPA_PLATFORM": "offscreen",
           **skin.env}
    return subprocess.Popen(
        [sys.executable, *skin.argv],
        cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_boot(proc: subprocess.Popen[bytes], since: int,
                   marker: str, settle_s: float = 2) -> bool:
    """Wait for THIS run to log *marker*, then *settle_s* more.

    Reads only past ``since`` — ``trcc.latest.log`` does not truncate per run
    despite ``mode="w"``, so scanning the whole tail matches a PREVIOUS run's
    boot and reports a process ready that never started.
    """
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if marker in _log_since(since):
            time.sleep(settle_s)    # let the loops spin up
            return True
        time.sleep(0.05)
    return False


def _log_since(offset: int) -> str:
    """Log content written after ``offset``, tolerating truncation.

    The launched process truncates ``trcc.latest.log`` on startup (that is
    the point of a per-run log), so an offset captured BEFORE launch can sit
    past EOF.  Seeking there would read nothing and the harness would decide
    the app never booted.  A file smaller than the offset means it was
    truncated — read it whole.
    """
    if not LOG.exists():
        return ""
    try:
        if LOG.stat().st_size < offset:
            offset = 0
    except OSError:
        return ""
    with LOG.open(errors="replace") as fh:
        fh.seek(offset)
        return fh.read()


def check(skin: Skin, phase: str) -> str:
    print(f"\n── {skin.name} / {phase} ──────────────────────────────")
    start = LOG.stat().st_size if LOG.exists() else 0
    proc = _launch(skin)
    early = phase == "start-up"
    marker = _STARTUP if early else skin.boot_marker
    if not _wait_for_boot(proc, start, marker, settle_s=0 if early else 2):
        print(f"  SKIP  {skin.name} never logged {marker!r} within "
              f"{BOOT_TIMEOUT_S}s (rc={proc.poll()}) — environment, not a "
              f"shutdown verdict")
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        return "SKIP"

    offset = start if early else (LOG.stat().st_size if LOG.exists() else 0)
    print(f"  {marker!r} seen, pid={proc.pid} — sending SIGTERM")
    t0 = time.monotonic()
    proc.send_signal(signal.SIGTERM)

    try:
        rc = proc.wait(timeout=GRACE_S)
    except subprocess.TimeoutExpired:
        print(f"  FAIL  still alive {GRACE_S}s after SIGTERM — the process "
              f"never exits")
        proc.kill()
        proc.wait(timeout=5)
        return "FAIL"

    tail = _log_since(offset)
    print(f"  exited rc={rc} in {time.monotonic() - t0:.1f}s")
    if early and skin.boot_marker in tail:
        print("  SKIP  the signal landed after the loop started — this run "
              "proved the running case, not start-up")
        return "SKIP"

    required = list(ALWAYS)
    devices = _devices_at_close(tail)
    if devices:
        required += IF_DEVICES
    else:
        print(f"  note: {devices} device(s) attached — panel-blank check "
              f"not applicable")

    missing = [m for m in required if m not in tail]
    if missing:
        print(f"  FAIL  exited WITHOUT cleanup — missing {missing}.")
        print("        The panel is left lit and the transport held (#143).")
        return "FAIL"
    print(f"  PASS  clean shutdown — {', '.join(required)} all present")
    return "PASS"


def _devices_at_close(tail: str) -> int:
    """How many devices ``App.close`` saw, from its own log line."""
    for line in tail.splitlines():
        if _CLOSE_LINE in line:
            try:
                return int(line.rsplit(_CLOSE_LINE, 1)[1].split()[0])
            except (IndexError, ValueError):
                return 0
    return 0


def main() -> int:
    print("UI shutdown smoke — mock fleet, no hardware touched.")
    results = {f"{skin.name} / {phase}": check(skin, phase)
               for skin in SKINS for phase in PHASES}
    print("\n── summary ─────────────────────────────────────────")
    for name, verdict in results.items():
        print(f"  {verdict}  {name}")
    return 0 if "FAIL" not in results.values() else 1


if __name__ == "__main__":
    sys.exit(main())
