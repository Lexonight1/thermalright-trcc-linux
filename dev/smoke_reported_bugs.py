#!/usr/bin/env python3
"""Per-reported-bug repro smoke — runs each open issue's exact failure path.

Where ``smoke_device_matrix`` proves the generic protocol lifecycle works,
this harness proves (or fails to prove) that each *specific* bug a reporter
filed still happens against the current code.  Each row is one (OS, Device,
Protocol, Action) tuple; the runner replays the reporter's failure mode and
reports REPRODUCED (still broken) or NOT-REPRODUCED (looks fixed).

Output legend per row:
    REPRODUCED       — the same exception still fires; reporter's bug is real
                       on current code.  Fix needed.
    NOT-REPRODUCED   — current code handles the case; the reply can confidently
                       point them at the latest release with the upgrade command.
    ERROR-DIFFERENT  — code raises but with a different message than reported;
                       triage manually — could be a related bug or env issue.
    SKIP             — environment-dependent (e.g. Bazzite numpy missing) or
                       hardware-only.  Note in the reply rather than nudge.

Usage::
    PYTHONPATH=src python3 dev/smoke_reported_bugs.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest.mock import patch

# Headless — no Qt event loop needed
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class ReproResult:
    status: str   # "REPRODUCED" / "NOT-REPRODUCED" / "ERROR-DIFFERENT" / "SKIP"
    detail: str


def _ok(detail: str) -> ReproResult:
    return ReproResult("NOT-REPRODUCED", detail)


def _bug(detail: str) -> ReproResult:
    return ReproResult("REPRODUCED", detail)


def _skip(detail: str) -> ReproResult:
    return ReproResult("SKIP", detail)


def _diff(detail: str) -> ReproResult:
    return ReproResult("ERROR-DIFFERENT", detail)


# ── Repro: #187/#188/#191 — frozen __main__ crash (typer.Exit escape) ────────

def repro_187_188_191_frozen_systemexit() -> ReproResult:
    """PyInstaller-frozen builds died with "Failed to execute script __main__"
    (#187/#188) and TRCC never appeared (#191): the ``gui``/``qtgui`` CLI
    wrappers raised ``typer.Exit``, which is meaningful only inside typer's
    runner — but those commands are ALSO direct entry points (the frozen
    ``trcc-gui.exe`` __main__ calls them outside typer), so it escaped unhandled
    and crashed the exe.  Fixed: both wrappers ``raise SystemExit(launch(...))``.

    Headless: patch ``launch`` to a no-op returning 0 and call the wrappers —
    they must raise ``SystemExit`` (``typer.Exit`` does NOT subclass SystemExit,
    so it's a clean discriminator).  No Qt boot, no frozen build needed.
    """
    import trcc.ui.gui as gui_mod
    import trcc.ui.qtgui as qtgui_mod
    from trcc.ui.cli.main import gui, qtgui

    def _noop_launch(*_a, **_k) -> int:
        return 0

    def _assert_systemexit(mod: object, thunk: Callable[[], object],
                           label: str) -> ReproResult | None:
        with patch.object(mod, "launch", _noop_launch):
            try:
                thunk()
            except SystemExit:
                return None
            except BaseException as e:
                return _bug(f"`trcc {label}` raised {type(e).__name__}, not SystemExit "
                            "— frozen build would crash")
            else:
                return _diff(f"`trcc {label}` did not raise SystemExit at all")

    def _run_gui() -> None:
        gui(resume=True)

    def _run_qtgui() -> None:
        qtgui()

    for res in (_assert_systemexit(gui_mod, _run_gui, "gui"),
                _assert_systemexit(qtgui_mod, _run_qtgui, "qtgui")):
        if res is not None:
            return res
    return _ok("gui + qtgui raise SystemExit(launch()) — frozen __main__ exits cleanly")


# ── Repro: #194 Tortillas-IT — no CPU power on AMD (RAPL package domain) ──────

def repro_194_rapl_faked_tree() -> ReproResult:
    """No CPU power on AMD (#194): CPU package power comes from the powercap
    RAPL ``energy_uj`` counter; Zen2+ registers its package domain under
    ``/sys/class/powercap/intel-rapl:*`` exactly like Intel, so the existing
    globber reads it (reporter confirmed working after upgrade).

    Headless: point ``_POWERCAP_ROOT`` at a faked ``intel-rapl:0`` package tree
    with a readable ``energy_uj`` and confirm ``_RaplCpuPower`` discovers it and
    returns watts on the second (delta) read.
    """
    import tempfile

    from trcc.adapters.sensors import hwmon

    with tempfile.TemporaryDirectory() as d:
        domain = Path(d) / "intel-rapl:0"
        domain.mkdir()
        (domain / "name").write_text("package-0\n")
        energy = domain / "energy_uj"
        energy.write_text("1000000\n")
        with patch.object(hwmon, "_POWERCAP_ROOT", Path(d)):
            rapl = hwmon._RaplCpuPower()
            if not rapl._paths:
                return _bug("_RaplCpuPower found no package domain in a readable "
                            "powercap tree — AMD/Intel RAPL discovery broken")
            first = rapl.read()          # first read seeds the baseline → None
            energy.write_text("2000000\n")
            second = rapl.read()         # delta → watts
    if first is not None:
        return _diff(f"first RAPL read should seed (None), got {first!r}")
    if not isinstance(second, float):
        return _bug(f"second RAPL read returned {second!r}, not watts — power path broken")
    return _ok("RAPL package power reads from the powercap tree (Zen2+ = Intel path)")


# ── Repro: #216 — Arch package pulled nvidia-utils on AMD ─────────────────────

def repro_216_arch_optdepend() -> ReproResult:
    """The Arch package listed NVIDIA GPU support as a hard dependency, dragging
    ``nvidia-utils`` (EGL/driver stack) onto AMD-only machines.  Fixed: it's now
    an ``optdepend`` (python-nvidia-ml-py), and the NVML import is guarded.

    Static spec check: the Arch package spec in release.yml must declare
    nvidia-ml-py as an ``optdepend`` and never as a hard ``depend``.
    """
    spec = (_REPO / ".github" / "workflows" / "release.yml").read_text()
    if "optdepend = python-nvidia-ml-py" not in spec:
        return _diff("release.yml no longer declares the nvidia-ml-py optdepend "
                     "line — verify the Arch spec by hand")
    for line in spec.splitlines():
        s = line.strip()
        if "nvidia" in s.lower() and s.startswith(("depend =", "depends=", "depends =")):
            return _bug(f"Arch spec has a HARD nvidia dependency: {s!r}")
    return _ok("Arch spec: nvidia-ml-py is optdepend, no hard nvidia depend")


# ── Repro 5: #142 Civilgrain — Bazzite numpy missing ─────────────────────────

def repro_142_bazzite_numpy() -> ReproResult:
    """Reporter on Bazzite hit ``ModuleNotFoundError: No module named 'numpy'``.
    This is environmental, not a code bug — Bazzite's immutable rootfs makes
    pip-installed deps unreachable from the system Python the GUI uses.
    The fix is the bundled RPM (already pointed at in the reply); no code
    change can resolve it.
    """
    return _skip("environment-dependent — Bazzite immutable rootfs, RPM bundles deps")


# ── Repro: #171 aaron-siegel — `trcc color` LCD index out of range ───────────

def repro_171_color_key_lookup() -> ReproResult:
    """`trcc test` worked but `trcc color 00ff00` failed with "LCD index 0 out
    of range (have 0)": the pre-cutover CLI used an index-based ``_get`` that
    indexed an empty LCD list because ``color`` never triggered connect while
    ``test`` did.  The cutover routed every wire command through key-based
    lookup + ``EnsureConnected``, so the asymmetry is gone.

    Headless: the legacy index module must be gone, and ``SendColor`` on an App
    with zero devices must return a graceful key-based miss — never an
    index/out-of-range error.
    """
    import tempfile

    from PySide6.QtWidgets import QApplication

    from tests.mock_platform import MockPlatform
    from trcc._boot import trcc
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import SendColor

    try:
        __import__("trcc.core._device_commands")
        return _bug("legacy index-based trcc.core._device_commands still importable")
    except ModuleNotFoundError:
        pass

    _ = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as d:
        app = trcc(platform=MockPlatform([], Path(d)), renderer=QtRenderer())
        res = app.dispatch(SendColor(key="0402:3922", r=0, g=255, b=0))
    msg = res.message
    if "index" in msg.lower() or "out of range" in msg.lower():
        return _bug(f"index-based error still fires: {msg!r}")
    if res.ok or "not attached" not in msg.lower():
        return _diff(f"unexpected result: ok={res.ok} msg={msg!r}")
    return _ok(f"key-based miss: {msg!r} (color/test share EnsureConnected, no index math)")


# ── Repro 8: #201 em73es — `trcc gui --resume` flag missing ──────────────────

def repro_201_gui_resume_flag() -> ReproResult:
    """v9.7.9: ``trcc gui --resume`` → "No such option: --resume"; autostart
    could no longer start hidden in the tray.  The flag was restored on
    ``main.gui`` → ``start_hidden`` → ``launch(start_hidden=...)``.

    Headless via Typer's help surface (no Qt boot): ``--resume`` must be a
    registered option on ``trcc gui``.  (The actual login-time tray-hiding is
    GUI-runtime and out of scope; the reported missing-flag is fully checkable.)
    """
    from typer.testing import CliRunner

    from trcc.ui.cli.main import app as cli

    # Report the EVIDENCE, not just the verdict.  This probe answered "absent"
    # on a CI runner and "present" on the dev box, deterministically, and a
    # verdict carrying no context cost a session of guessing at the cause.
    # ``--decorated`` is the sibling option declared immediately after
    # ``--resume`` on the same command: if it renders and ``--resume`` does
    # not, the fault is in how this one multi-alias option is displayed; if
    # neither renders, the options table is absent and the help never built.
    result = CliRunner().invoke(cli, ["gui", "--help"])
    out = result.output
    seen = (f"exit={result.exit_code} len={len(out)} "
            f"--decorated={'--decorated' in out} "
            f"exc={type(result.exception).__name__ if result.exception else 'none'}")
    if "No such option" in out:
        return _bug(f"`trcc gui --help` reports no --resume option [{seen}]")
    if "--resume" not in out:
        return _bug(f"--resume absent from `trcc gui` options [{seen}]")
    return _ok(f"`trcc gui --resume` registered [{seen}]")


# ── Repro 9: #162 TuxLux40 — TRCC_DAEMON=1 fork-bomb ─────────────────────────

def repro_162_daemon_flag_strip() -> ReproResult:
    """``TRCC_DAEMON=1`` in the environment fork-bombed: ``ensure_daemon``
    spawned ``trcc daemon``, the child inherited the flag, proxied to itself,
    and spawned again.  Fixed at two layers — ``ensure_daemon`` strips the flag
    from the child env, and ``run_daemon`` pops it before building the App.

    Headless: no real fork — assert the spawned child env lacks the flag AND
    ``run_daemon`` pops it before ``_build_local_app`` runs.
    """
    import trcc.daemon as dm

    captured: dict = {}

    def _popen_spy(cmd, **kw):  # type: ignore[no-untyped-def]
        captured["env"] = kw.get("env", {})
        return object()

    prev = os.environ.get("TRCC_DAEMON")
    os.environ["TRCC_DAEMON"] = "1"
    try:
        with patch.object(dm.ipc, "daemon_running", return_value=False), \
             patch.object(dm.ipc, "wait_for_daemon", return_value=True), \
             patch.object(dm.subprocess, "Popen", _popen_spy):
            dm.ensure_daemon(timeout=0.1)
        if "TRCC_DAEMON" in captured.get("env", {}):
            return _bug("ensure_daemon spawns child WITH TRCC_DAEMON — fork-bomb path open")

        seen: dict = {}

        def _build_spy(**kw):  # type: ignore[no-untyped-def]
            seen["flag"] = os.environ.get("TRCC_DAEMON")
            raise RuntimeError("stop before serve")

        with patch("trcc._boot._build_local_app", _build_spy):
            try:
                dm.run_daemon(platform=None, renderer=None)
            except RuntimeError:
                pass
        if seen.get("flag") is not None:
            return _bug(f"run_daemon builds App with TRCC_DAEMON={seen['flag']!r} still set")
    finally:
        if prev is None:
            os.environ.pop("TRCC_DAEMON", None)
        else:
            os.environ["TRCC_DAEMON"] = prev
    return _ok("both guards hold: child env stripped + run_daemon pops flag before build")


# ── Repro 10: #148 TuxLux40 — trccd never starts the metrics loop ────────────

def repro_148_daemon_metrics_loop() -> ReproResult:
    """trccd ran, device discovered, socket up — but the display stayed blank
    because ``run_daemon`` never started the metrics tick.  Fixed: ``run_daemon``
    calls ``app.metrics_loop.start()`` during bring-up.

    Headless: drive ``run_daemon`` with a stubbed App + non-blocking IPC server
    and assert the metrics loop was started.  (Actual pixels reaching a physical
    panel can't be observed headlessly; the "did the daemon start the tick" bug
    is fully checkable.)
    """
    import trcc.daemon as dm

    class _Loop:
        def __init__(self) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

    class _App:
        def __init__(self) -> None:
            self.metrics_loop = _Loop()
            self.led_animation_loop = _Loop()

        def start_hotplug(self) -> None: ...
        def close(self) -> None: ...

    class _Srv:
        def __init__(self, app: object) -> None: ...
        def start(self) -> None: ...
        def serve_forever(self) -> None: ...
        def shutdown(self) -> None: ...

    fake = _App()
    with patch("trcc._boot._build_local_app", return_value=fake), \
         patch.object(dm.ipc, "IPCServer", _Srv), \
         patch.object(dm, "_install_signal_handlers", lambda s: None):
        dm.run_daemon(platform=None, renderer=None)
    if not fake.metrics_loop.started:
        return _bug("run_daemon never called app.metrics_loop.start() — display stays blank")
    return _ok("run_daemon starts metrics_loop (+ led_animation_loop) during bring-up")


# ── Repro 11: #166 raragundi — linux.py imports fcntl on Windows ──────────────

def repro_166_linux_import_no_fcntl() -> ReproResult:
    """Windows: launching any command crashed with ``ModuleNotFoundError:
    fcntl`` because ``adapters/system/__init__`` side-imports ``linux.py`` to
    register the platform, and ``linux.py`` imported ``fcntl`` at module top.
    Fixed: ``fcntl`` is imported lazily inside the two SCSI ioctl methods, so
    ``linux.py`` imports cleanly where ``fcntl`` is absent.

    Headless (simulates Windows): block ``import fcntl``, then import
    ``linux.py`` + the system package.
    """
    import builtins

    real_import = builtins.__import__

    def _blocker(name, *a, **k):  # type: ignore[no-untyped-def]
        if name == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return real_import(name, *a, **k)

    for mod in [m for m in sys.modules
                if m == "fcntl" or m.startswith("trcc.adapters.system")]:
        sys.modules.pop(mod, None)
    try:
        with patch.object(builtins, "__import__", _blocker):
            import trcc.adapters.system.linux
            import trcc.adapters.system  # noqa: F401
    except ModuleNotFoundError as e:
        return _bug(f"linux.py pulls fcntl at module top: {e}")
    return _ok("linux.py imports cleanly with fcntl absent (lazy import in ioctl methods)")


# ── Reporter map ─────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class BugRepro:
    issue: int
    reporter: str
    os_label: str
    device_label: str
    bug_summary: str
    runner: Callable[[], ReproResult]


REPROS: list[BugRepro] = [
    BugRepro(
        issue=187, reporter="raragundi", os_label="Windows 11 (frozen exe)",
        device_label="n/a (CLI gui/qtgui entry point)",
        bug_summary="'Failed to execute script __main__' — typer.Exit escaped frozen build",
        runner=repro_187_188_191_frozen_systemexit,
    ),
    BugRepro(
        issue=188, reporter="charlesbuihoanthien", os_label="Windows 11 (frozen exe)",
        device_label="n/a (CLI gui/qtgui entry point)",
        bug_summary="'Failed to execute script __main__' (dup of #187)",
        runner=repro_187_188_191_frozen_systemexit,
    ),
    BugRepro(
        issue=191, reporter="charlesbuihoanthien", os_label="Windows 11 (frozen exe)",
        device_label="n/a (CLI gui/qtgui entry point)",
        bug_summary="TRCC never appears — same typer.Exit frozen crash",
        runner=repro_187_188_191_frozen_systemexit,
    ),
    BugRepro(
        issue=194, reporter="Tortillas-IT", os_label="Arch (9800X3D)",
        device_label="n/a (CPU RAPL powercap)",
        bug_summary="no CPU power on AMD — RAPL package domain not read",
        runner=repro_194_rapl_faked_tree,
    ),
    BugRepro(
        issue=216, reporter="em73es", os_label="Arch (AMD)",
        device_label="n/a (pacman package spec)",
        bug_summary="Arch package pulled nvidia-utils on AMD (hard dep, not optdep)",
        runner=repro_216_arch_optdepend,
    ),
    BugRepro(
        issue=142, reporter="Civilgrain", os_label="Bazzite (immutable)",
        device_label="87AD:70DB Wonder Vision Pro (Bulk)",
        bug_summary="GUI won't open — ModuleNotFoundError: numpy",
        runner=repro_142_bazzite_numpy,
    ),
    BugRepro(
        issue=171, reporter="aaron-siegel", os_label="Linux",
        device_label="n/a (CLI — key-based command lookup)",
        bug_summary="`trcc color` → 'LCD index 0 out of range (have 0)'",
        runner=repro_171_color_key_lookup,
    ),
    BugRepro(
        issue=201, reporter="em73es", os_label="Linux",
        device_label="n/a (CLI — gui launch flags)",
        bug_summary="`trcc gui --resume` missing / can't autostart hidden",
        runner=repro_201_gui_resume_flag,
    ),
    BugRepro(
        issue=162, reporter="TuxLux40", os_label="Linux",
        device_label="n/a (daemon — TRCC_DAEMON env)",
        bug_summary="TRCC_DAEMON=1 fork-bombs via ensure_daemon()",
        runner=repro_162_daemon_flag_strip,
    ),
    BugRepro(
        issue=148, reporter="TuxLux40", os_label="Linux",
        device_label="n/a (trccd daemon bring-up)",
        bug_summary="trccd never starts metrics loop → display blank",
        runner=repro_148_daemon_metrics_loop,
    ),
    BugRepro(
        issue=166, reporter="raragundi", os_label="Windows 11",
        device_label="any (adapters/system side-import)",
        bug_summary="linux.py imports fcntl → ModuleNotFoundError on Windows",
        runner=repro_166_linux_import_no_fcntl,
    ),
]


# ── Reporting ────────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREY = "\033[90m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_COLOR = {
    "NOT-REPRODUCED": _GREEN,
    "REPRODUCED": _RED,
    "ERROR-DIFFERENT": _YELLOW,
    "SKIP": _GREY,
}


def main() -> int:
    print(f"{_BOLD}TRCC reported-bugs repro smoke{_RESET}")
    print(f"  {len(REPROS)} reporter scenarios across "
          f"{len({r.issue for r in REPROS})} GitHub issues\n")

    results: list[tuple[BugRepro, ReproResult]] = []
    for r in REPROS:
        try:
            res = r.runner()
        except Exception as e:
            res = ReproResult("ERROR-DIFFERENT", f"runner crashed: {type(e).__name__}: {e}")
        results.append((r, res))

    for r, res in results:
        color = _COLOR[res.status]
        header = f"#{r.issue} {r.reporter} ({r.os_label} / {r.device_label})"
        print(f"{color}{res.status:<17}{_RESET}{header}")
        print(f"                 bug : {r.bug_summary}")
        print(f"                 res : {res.detail}\n")

    counts = {s: sum(1 for _, res in results if res.status == s)
              for s in ("REPRODUCED", "ERROR-DIFFERENT", "NOT-REPRODUCED", "SKIP")}

    print("=" * 76)
    if counts["REPRODUCED"]:
        print(f"  {_RED}REPRODUCED{_RESET}      {counts['REPRODUCED']}/{len(results)}  "
              "(real bugs still on user machines — fix needed)")
    if counts["ERROR-DIFFERENT"]:
        print(f"  {_YELLOW}ERROR-DIFFERENT{_RESET} {counts['ERROR-DIFFERENT']}/{len(results)}  "
              "(triage manually)")
    if counts["NOT-REPRODUCED"]:
        print(f"  {_GREEN}NOT-REPRODUCED{_RESET}  {counts['NOT-REPRODUCED']}/{len(results)}  "
              "(fixed in current code — reporter can retry the latest release)")
    if counts["SKIP"]:
        print(f"  {_GREY}SKIP{_RESET}            {counts['SKIP']}/{len(results)}  "
              "(env-dependent or visual-confirm-only)")
    print("=" * 76)

    return 1 if counts["REPRODUCED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
