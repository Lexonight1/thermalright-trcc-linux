"""Autostart manager implementations + the shared no-op fallback.

  * ``WindowsAutostart``  — writes HKCU\\Software\\Microsoft\\
                            Windows\\CurrentVersion\\Run via ``winreg``
                            (a pip install: not elevated).
  * ``WindowsTaskAutostart`` — a sign-in scheduled task via ``schtasks``
                            (the ``--uac-admin`` installer build).
  * ``MacOSAutostart``    — writes a LaunchAgent plist under
                            ``~/Library/LaunchAgents/`` and
                            ``launchctl bootstrap``s it.
  * ``NoopAutostart``     — fallback for BSD + any future OS we
                            haven't wired yet.

Each platform's ``Platform.autostart()`` consumes one of these via a
local import so the heavy code paths only load when actually needed.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ...core.models import AUTOSTART_TARGETS, DEFAULT_AUTOSTART_TARGET
from ...core.ports import AutostartManager

log = logging.getLogger(__name__)


# =========================================================================
# Noop — every OS that doesn't (yet) wire autostart returns this
# =========================================================================


class NoopAutostart(AutostartManager):
    """Autostart manager that does nothing.

    Used on platforms whose autostart implementation hasn't landed yet
    (macOS LaunchAgent in B.7, BSD reactor service later).  Keeps
    ``Platform.autostart()`` unconditional — no ``if`` guards in callers.
    """

    def is_enabled(self) -> bool:
        log.debug("is_enabled: called")
        return False

    def entry_location(self) -> str:
        log.debug("NoopAutostart.entry_location: none on this OS")
        return ""

    def installed_target(self) -> str | None:
        log.debug("NoopAutostart.installed_target: None")
        return None

    def enable(self, target: str | None = None) -> None:
        log.debug("NoopAutostart.enable: no-op on this platform")

    def disable(self) -> None:
        log.debug("NoopAutostart.disable: no-op on this platform")

    def refresh(self) -> None:
        log.debug("refresh: called")


# =========================================================================
# XDG Autostart — .desktop in ~/.config/autostart/ (Linux + BSD)
# =========================================================================
#
# The XDG Autostart spec is honoured by every major Linux desktop (GNOME,
# KDE, XFCE, Cinnamon, Budgie, MATE, LXQt) AND the same desktops on the
# BSDs.  A simple `.desktop` file in `$XDG_CONFIG_HOME/autostart/` (default
# `~/.config/autostart/`) launches the app on login — no root, pure
# per-user opt-in.  Legacy ran the identical mechanism on both OSes
# (bsd_platform: "XDG .desktop — same as Linux").


_AUTOSTART_FILENAME = "trcc.desktop"

#: Login entries earlier versions wrote under other names.  A name is an
#: entry's identity, and nothing looked for these, so an upgraded user kept a
#: second login entry that turning autostart off could not remove: legacy's
#: ``trcc-linux.desktop`` (Exec ``trcc gui --resume``, ``-m trcc.cli``, or the
#: oldest ``trcc --last-one``), next/'s ``trcc-next.desktop``, and legacy's
#: Windows Run value ``TRCC Linux``.  Removed when they are ours -- never
#: migrated: the current entry already holds the user's latest choice.
_RETIRED_DESKTOP_FILES = ("trcc-linux.desktop", "trcc-next.desktop")
_RETIRED_RUN_VALUES = ("TRCC Linux",)
_OUR_PROGRAMS = frozenset({"trcc", "trcc.exe", "trcc-gui.exe", "trcc-next"})


def runs_trcc(command: str) -> bool:
    """Whether *command* (an ``Exec=`` line or a Run value) starts TRCC.

    The ownership test for an entry under a retired name: a user's own entry
    that happens to share the name is left alone.  The Windows Run value
    quotes its program, so a quoted head is read whole.
    """
    if command.startswith('"') and (end := command.find('"', 1)) != -1:
        program, rest = command[1:end], command[end + 1:].split()
    else:
        program, *rest = command.split() or [""]
    name = re.split(r"[\\/]", program)[-1].lower()
    module = next((rest[i + 1] for i, word in enumerate(rest[:-1])
                   if word == "-m"), "")
    ours = name in _OUR_PROGRAMS or module == "trcc" or module.startswith("trcc.")
    log.debug("runs_trcc: %r -> %s", command, ours)
    return ours

_AUTOSTART_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=TRCC Linux
GenericName=Thermalright Cooler Control
Comment=Auto-start TRCC ({target}) on login
Exec={exec_cmd}
Icon=trcc
Terminal=false
Categories=System;Settings;
X-GNOME-Autostart-enabled=true
StartupNotify=false
"""


class XdgDesktopAutostart(AutostartManager):
    """XDG Autostart adapter — writes/removes ~/.config/autostart/trcc.desktop.

    OS-agnostic: used by both ``LinuxOS`` and the BSDs (the
    XDG spec is identical on each).
    """

    def __init__(self, config_home: Path | None = None) -> None:
        """*config_home* stands in for ``$XDG_CONFIG_HOME`` -- a dev platform
        keeps its entry out of the user's real login items with it."""
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = config_home or (Path(xdg) if xdg else Path.home() / ".config")
        self._path = base / "autostart" / _AUTOSTART_FILENAME
        log.info("XdgDesktopAutostart: desktop file path = %s", self._path)

    @property
    def path(self) -> Path:
        log.debug("XdgDesktopAutostart.path → %s", self._path)
        return self._path

    def is_enabled(self) -> bool:
        enabled = self._path.is_file()
        log.debug("XdgDesktopAutostart.is_enabled → %s (%s)", enabled, self._path)
        return enabled

    def entry_location(self) -> str:
        log.debug("XdgDesktopAutostart.entry_location: %s", self._path)
        return str(self._path)

    def installed_target(self) -> str | None:
        """Read the target back out of the installed ``Exec=`` line."""
        if not self._path.is_file():
            log.debug("XdgDesktopAutostart.installed_target: %s absent",
                      self._path)
            return None
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                target = target_from_command(line[len("Exec="):])
                log.debug("XdgDesktopAutostart.installed_target: %s", target)
                return target
        log.debug("XdgDesktopAutostart.installed_target: no Exec= in %s",
                  self._path)
        return None

    def _retire_old_entries(self) -> None:
        """Remove the login entries earlier versions wrote, when they are ours."""
        for name in _RETIRED_DESKTOP_FILES:
            old = self._path.parent / name
            if not old.is_file():
                continue
            text = old.read_text(encoding="utf-8", errors="replace")
            exec_cmd = next((line[len("Exec="):] for line in text.splitlines()
                             if line.startswith("Exec=")), "")
            # Every entry TRCC ever wrote carries the GNOME key; a copy of the
            # menu entry made by a tweak tool does not.
            if "X-GNOME-Autostart-enabled=" in text and runs_trcc(exec_cmd):
                old.unlink()
                log.info("XdgDesktopAutostart: removed the old entry %s", old)
            else:
                log.warning("XdgDesktopAutostart: %s is not one TRCC wrote — "
                            "left alone", old)

    def enable(self, target: str | None = None) -> None:
        target = target or DEFAULT_AUTOSTART_TARGET
        log.info("XdgDesktopAutostart.enable: writing %s (target=%s)",
                 self._path, target)
        self._retire_old_entries()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._render(target), encoding="utf-8")
        self._path.chmod(0o644)
        log.info("Autostart enabled: %s", self._path)

    def disable(self) -> None:
        log.info("XdgDesktopAutostart.disable: removing %s", self._path)
        self._retire_old_entries()
        if self._path.exists():
            self._path.unlink()
            log.info("Autostart disabled: %s", self._path)
        else:
            log.info("XdgDesktopAutostart.disable: %s did not exist", self._path)

    def refresh(self) -> None:
        """Re-render the .desktop file if present (picks up a new Exec path)."""
        self._retire_old_entries()
        if self._path.exists():
            installed = self.installed_target()
            log.info("XdgDesktopAutostart.refresh: re-rendering %s (target=%s)",
                     self._path, installed)
            # Re-enable with the target ALREADY installed, never the default:
            # refresh repairs a stale path (#201), it must not silently change
            # which ui the user chose to start.
            self.enable(installed)
        else:
            log.debug("XdgDesktopAutostart.refresh: %s not present — nothing to refresh",
                      self._path)

    def _render(self, target: str = DEFAULT_AUTOSTART_TARGET) -> str:
        log.debug("_render: target=%s", target)
        return _AUTOSTART_TEMPLATE.format(
            exec_cmd=self._exec_cmd(target), target=target,
        )

    @staticmethod
    def _exec_cmd(target: str = DEFAULT_AUTOSTART_TARGET) -> str:
        """The autostart launch command.

        ``--resume`` makes the autostarted instance start hidden in the
        system tray (restoring the last-used theme) instead of popping a
        window on every login — the long-standing autostart behaviour that
        regressed when the flag was dropped (#201).
        """
        log.debug("_exec_cmd: target=%s", target)
        return " ".join(autostart_argv(target))


def launch_argv(subcommand: str, *args: str) -> list[str]:
    """argv that launches ``trcc <subcommand> [args…]`` for THIS install.

    The one place that knows how to find the program.  Preference order:

      1. the ``trcc`` console script, when installed and on PATH
      2. ``<sys.executable> -m trcc <subcommand>``

    The second form is robust across pipx / venv / system-python installs
    because ``sys.executable`` is always the right interpreter.

    Three copies of this used to exist — one per platform, each hardcoding
    ``gui`` — which is why ``--resume`` reached Linux and neither of the other
    two for a whole release cycle.
    """
    if (exe := shutil.which("trcc")) is not None:
        argv = [exe, subcommand, *args]
    else:
        argv = [sys.executable, "-m", "trcc", subcommand, *args]
    log.debug("launch_argv(%s): %s", subcommand, argv)
    return argv


def autostart_argv(target: str = DEFAULT_AUTOSTART_TARGET) -> list[str]:
    """argv for an autostart entry — the target plus the flags IT needs.

    Policy lives in ``AUTOSTART_TARGETS``; ``launch_argv`` stays mechanical so
    the applications-menu entry can ask for a bare ``gui`` with no flags.
    """
    args = AUTOSTART_TARGETS[target]
    log.info("autostart_argv: target=%s extra=%s", target, list(args))
    return launch_argv(target, *args)


def target_from_argv(argv: list[str]) -> str | None:
    """Recover the autostart target from an installed entry's argv.

    The installed entry IS the record of what was installed — there is no
    second copy in Settings to drift from it, and two callers NEED the answer:
    XDG ``refresh()`` re-renders by calling ``enable()`` and would otherwise
    reset the user's choice, and Windows ``is_enabled()`` compares against a
    command that must be the one for the INSTALLED target.

    The target is read at its KNOWN position — ``argv[1]``, or ``argv[3]``
    after ``-m trcc`` — not by scanning for the first token that happens to be
    a target name.  Scanning is wrong on a form we do not write:
    ``trcc --log-file daemon gui`` would answer ``daemon``.  Anything that is
    not exactly our shape returns None, because a wrong target is worse than
    no target: it would rewrite a user's entry to something they never chose.
    """
    if len(argv) < 2:
        log.debug("target_from_argv: too short: %s", argv)
        return None
    if argv[1:3] == ["-m", "trcc"]:
        candidate = argv[3] if len(argv) > 3 else ""
    else:
        candidate = argv[1]
    target = candidate if candidate in AUTOSTART_TARGETS else None
    log.debug("target_from_argv: %s -> %s", argv, target)
    return target


def target_from_command(value: str) -> str | None:
    """``target_from_argv`` for a command STRING (Exec= line, registry value).

    The Windows Run key QUOTES the program so a Program Files path survives,
    and a plain split would shred it — so the quoted head is consumed and a
    placeholder put back, keeping ``argv[0] is the program`` true for the
    position rule above.
    """
    log.debug("target_from_command: %r", value)
    if value.startswith('"'):
        end = value.find('"', 1)
        if end != -1:
            return target_from_argv(["<program>", *value[end + 1:].split()])
    return target_from_argv(value.split())


def gui_launch_command(*args: str) -> str:
    """Build a command line that launches the GUI, with *args* appended.

    Preference order:
      1. ``trcc`` console script if installed and on PATH
      2. ``<sys.executable> -m trcc gui``

    The second form is robust across pipx / venv / system-python installs
    because ``sys.executable`` is always the right interpreter.

    Shared by the autostart entry and the application-menu entry — both
    write an ``Exec=`` line and both are wrong in the same way if they
    assume ``trcc`` is on PATH.
    """
    cmd = " ".join(launch_argv("gui", *args))
    log.debug("gui_launch_command: %s", cmd)
    return cmd


# =========================================================================
# Windows — HKCU Run key
# =========================================================================


_WIN_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_DEFAULT_VALUE_NAME = "TRCCNext"


def _resolve_command(target: str = DEFAULT_AUTOSTART_TARGET) -> str:
    """The Run-key command that starts *target* at sign-in, for a pip install.

    ``pythonw.exe -m trcc``, never ``launch_argv``'s choice: ``python.exe`` and
    the pip ``trcc.exe`` launcher are both CONSOLE programs, so an entry naming
    either opens a terminal at every sign-in.  ``pythonw.exe`` ships beside
    ``python.exe`` in every CPython install and venv on Windows.  The flags are
    ``AUTOSTART_TARGETS``' — ``--resume`` keeps the gui in the tray (#201).
    """
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.is_file():
        log.warning("_resolve_command: no %s — the Run entry uses %s, which "
                    "opens a console window at sign-in", pythonw, exe)
        pythonw = exe
    # Registry values quote the program so spaces in install dirs
    # (Program Files) don't break the launch.
    cmd = " ".join([f'"{pythonw}"', "-m", "trcc", target,
                    *AUTOSTART_TARGETS[target]])
    log.debug("_resolve_command(%s): %s", target, cmd)
    return cmd


def _winreg_module() -> Any:
    """Import ``winreg`` on Windows; return None elsewhere.

    Production code paths only hit this on Windows; tests inject a fake
    module so the protocol logic runs anywhere.
    """
    log.debug("_winreg_module: called")
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:                     # pragma: no cover — would only fire on a stripped Python
        return None
    return winreg


class WindowsAutostart(AutostartManager):
    """Autostart via the HKCU Run registry key — for a pip install.

    The Run key fires whenever the user signs in, for a program that does NOT
    need elevation.  Windows blocks elevation in the sign-in path, so the
    installer build (``--uac-admin``) uses :class:`WindowsTaskAutostart`
    instead — this class never starts it.  Writes a single REG_SZ value running
    ``pythonw.exe -m trcc gui --resume``.

    Tests inject a stub ``registry`` module + ``command`` string so the
    full enable / is_enabled / disable cycle runs without touching the
    real winreg.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        registry: Any = None,
        value_name: str = _DEFAULT_VALUE_NAME,
    ) -> None:
        """``registry`` is a winreg-compatible module-like object — duck-typed
        seam so tests can inject an in-memory fake on non-Windows boxes."""
        log.debug("__init__")
        self._cmd = command if command is not None else _resolve_command()
        self._registry: Any = registry if registry is not None else _winreg_module()
        self._value_name = value_name

    # ── AutostartManager ABC ───────────────────────────────────────

    def _stored_value(self) -> str | None:
        """The Run-key value we wrote, or None when absent/unreadable.

        Split out of ``is_enabled`` because ``refresh`` asks a DIFFERENT
        question: is a value present *at all*, whatever it says.  A stale one
        does not equal the current command by definition, so reusing
        ``is_enabled`` there would refuse to fix exactly the entries that need
        fixing.
        """
        if self._registry is None:
            log.debug("WindowsAutostart._stored_value: no winreg — None")
            return None
        try:
            with self._open_key(write=False) as key:
                stored, _ = self._registry.QueryValueEx(key, self._value_name)
        except OSError as e:
            log.debug("WindowsAutostart._stored_value: %s absent (%s)",
                      self._value_name, e)
            return None
        log.debug("WindowsAutostart._stored_value: %r", stored)
        return str(stored)

    def _value_present(self) -> bool:
        """True when the Run key holds our value, whatever its content."""
        present = self._stored_value() is not None
        log.debug("WindowsAutostart._value_present -> %s", present)
        return present

    def _command_for(self, target: str | None) -> str:
        """The Run-key value we would write for *target*.

        ``None`` means "whatever this manager was constructed with" — which is
        how the injected-command seam keeps working, and what an entry naming
        no target falls back to.
        """
        if target is None:
            log.debug("WindowsAutostart._command_for: default %r", self._cmd)
            return self._cmd
        return _resolve_command(target)

    def entry_location(self) -> str:
        location = f"HKCU\\{_WIN_RUN_KEY_PATH}\\{self._value_name}"
        log.debug("WindowsAutostart.entry_location: %s", location)
        return location

    def installed_target(self) -> str | None:
        stored = self._stored_value()
        target = target_from_command(stored) if stored is not None else None
        log.debug("WindowsAutostart.installed_target: %s", target)
        return target

    def is_enabled(self) -> bool:
        """True when the Run key holds our value AND it matches our command.

        Compared against the command for the INSTALLED target, not a fixed
        one: an entry enabled for ``daemon`` must not read as disabled just
        because this manager's default is ``gui``.  An entry naming no target
        falls back to the constructor's command, which keeps the "stale path
        reads as disabled" defence — and the injected-command tests — intact.
        """
        log.info("is_enabled: called")
        stored = self._stored_value()
        if stored is None:
            return False
        return stored == self._command_for(target_from_command(stored))

    def retire_old_values(self) -> None:
        """Remove the Run values earlier versions wrote, when they are ours."""
        if self._registry is None:
            log.debug("WindowsAutostart.retire_old_values: no winreg")
            return
        for name in _RETIRED_RUN_VALUES:
            try:
                with self._open_key(write=False) as key:
                    stored, _ = self._registry.QueryValueEx(key, name)
            except OSError:
                log.debug("WindowsAutostart.retire_old_values: %s absent", name)
                continue
            if not runs_trcc(str(stored)):
                log.warning("WindowsAutostart: Run value %r is not one TRCC "
                            "wrote — left alone", name)
                continue
            try:
                with self._open_key(write=True) as key:
                    self._registry.DeleteValue(key, name)
            except OSError:
                log.exception("WindowsAutostart: could not remove %r", name)
            else:
                log.info("WindowsAutostart: removed the old Run value %r", name)

    def enable(self, target: str | None = None) -> None:
        log.info("enable: target=%s", target)
        if self._registry is None:
            log.debug("WindowsAutostart.enable: winreg unavailable; no-op")
            return
        self.retire_old_values()
        with self._open_key(write=True) as key:
            self._registry.SetValueEx(
                key, self._value_name, 0,
                self._registry.REG_SZ, self._command_for(target),
            )
        log.info("WindowsAutostart: enabled at HKCU\\%s\\%s",
                 _WIN_RUN_KEY_PATH, self._value_name)

    def disable(self) -> None:
        log.info("disable: called")
        if self._registry is None:
            return
        self.retire_old_values()
        try:
            with self._open_key(write=True) as key:
                self._registry.DeleteValue(key, self._value_name)
        except FileNotFoundError:
            log.debug("WindowsAutostart.disable: value missing; nothing to remove")
        except OSError:
            log.exception("WindowsAutostart.disable: failed to delete value")
        else:
            log.info("WindowsAutostart: disabled")

    def refresh(self) -> None:
        """Rewrite the Run key when it holds a stale command.

        This WAS a no-op — "the Run key needs no compilation step" — which
        held only while the command could never change.  It can: #201 added
        ``--resume``, and an already-enabled user's key keeps whatever it was
        written with forever, so the fix would reach new installs and never
        reach them.  Linux picks changes up because XDG ``refresh()``
        re-renders; this is that, for the registry.

        Only rewrites when a value is already present — like every other
        ``refresh``, it must never enable autostart nobody asked for.
        """
        self.retire_old_values()
        if not self._value_present():
            log.debug("WindowsAutostart.refresh: no entry — nothing to refresh")
            return
        installed = self.installed_target()
        log.info("WindowsAutostart.refresh: re-writing %s (target=%s)",
                 self._value_name, installed)
        self.enable(installed)

    # ── Internal: open the Run key in read or write mode ──────────

    def _open_key(self, *, write: bool) -> Any:
        log.debug("_open_key")
        access = (self._registry.KEY_READ
                  if not write else self._registry.KEY_SET_VALUE)
        return self._registry.OpenKeyEx(
            self._registry.HKEY_CURRENT_USER,
            _WIN_RUN_KEY_PATH,
            0,
            access,
        )


# =========================================================================
# Windows — a sign-in scheduled task, for the elevated installer build
# =========================================================================


#: The task's name is its IDENTITY — enable, disable and status all address it
#: by name, and Task Scheduler shows it to the user.  Matches the applications
#: menu entry; distinct from Thermalright's own ``TRCCAppStartup``.
_TASK_NAME = "TRCC Linux"

_SCHTASKS = (Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
             / "System32" / "schtasks.exe")

_TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _render_task_xml(command: str, arguments: str, user: str) -> str:
    """The task definition — pure string, fully testable.

    The shape the C# oracle ends up with (``Form1.cs:211-281``): a sign-in
    trigger at the highest run level, which is what lets an elevated program
    start without a UAC prompt, and settings that keep it alive — no time
    limit (the default is 72 h, then Windows KILLS it) and no battery rules
    (by default a laptop on battery never starts it).  The C# creates a default
    task, reads it back and edits the XML; this writes it whole in one call.
    """
    from xml.sax.saxutils import escape
    log.debug("_render_task_xml: command=%s arguments=%s user=%s",
              command, arguments, user)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo><Description>Start TRCC Linux at sign-in'
        '</Description></RegistrationInfo>\n'
        '  <Triggers><LogonTrigger><Enabled>true</Enabled>'
        f'<UserId>{escape(user)}</UserId></LogonTrigger></Triggers>\n'
        '  <Principals><Principal id="Author">'
        f'<UserId>{escape(user)}</UserId>'
        '<LogonType>InteractiveToken</LogonType>'
        '<RunLevel>HighestAvailable</RunLevel></Principal></Principals>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Actions Context="Author"><Exec>'
        f'<Command>{escape(command)}</Command>'
        f'<Arguments>{escape(arguments)}</Arguments>'
        '</Exec></Actions>\n'
        '</Task>\n'
    )


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``schtasks.exe`` by absolute path — no shell, no console window."""
    log.info("_run_schtasks: %s", args)
    from ...core.models import SUBPROCESS_NO_WINDOW
    return subprocess.run(
        [str(_SCHTASKS), *args], capture_output=True, text=True,
        errors="replace", check=False, timeout=30,
        creationflags=SUBPROCESS_NO_WINDOW,
    )


class WindowsTaskAutostart(AutostartManager):
    """Autostart via a sign-in scheduled task — for the installer build.

    The installer's exes are built ``--uac-admin``, and Windows BLOCKS
    elevation in the sign-in path, Run key included — measured on the win11 VM:
    the entry was present, the user signed in, nothing started.  A task at the
    highest run level is the documented way round, and the C# oracle's.

    The task runs ``trcc-gui.exe`` (the windowed exe; ``trcc.exe`` is a console
    program and would open a terminal at sign-in) with the target and its
    flags.  Enabling or disabling also removes the old Run-key entry this
    replaces, so an upgraded install carries no dead ``TRCCNext`` value.

    ``run``, ``program`` and ``legacy`` are seams so the full cycle runs on the
    Linux dev box against a fake ``schtasks``.
    """

    def __init__(
        self,
        *,
        program: Path | None = None,
        run: Any = None,
        legacy: WindowsAutostart | None = None,
        task_name: str = _TASK_NAME,
    ) -> None:
        self._program = (program if program is not None
                         else Path(sys.executable).with_name("trcc-gui.exe"))
        self._run = run if run is not None else _run_schtasks
        self._legacy = legacy if legacy is not None else WindowsAutostart()
        self._task_name = task_name
        log.debug("WindowsTaskAutostart: task=%r program=%s",
                  task_name, self._program)

    @staticmethod
    def _arguments(target: str) -> str:
        """The task's Arguments for *target*: the target, then ITS flags."""
        arguments = " ".join([target, *AUTOSTART_TARGETS[target]])
        log.debug("WindowsTaskAutostart._arguments(%s): %s", target, arguments)
        return arguments

    def _installed(self) -> tuple[str, str] | None:
        """``(command, arguments)`` of the installed task, or None if absent."""
        import xml.etree.ElementTree as ET
        result = self._run(["/query", "/tn", self._task_name, "/xml"])
        if result.returncode != 0:
            log.debug("WindowsTaskAutostart._installed: no task %r (%s)",
                      self._task_name, result.stderr.strip())
            return None
        try:
            exec_ = ET.fromstring(result.stdout.strip()).find(
                "t:Actions/t:Exec", _TASK_NS)
        except ET.ParseError:
            log.warning("WindowsTaskAutostart._installed: unreadable task XML "
                        "for %r", self._task_name)
            return None
        if exec_ is None:
            log.warning("WindowsTaskAutostart._installed: task %r has no Exec "
                        "action", self._task_name)
            return None
        installed = (exec_.findtext("t:Command", "", _TASK_NS),
                     exec_.findtext("t:Arguments", "", _TASK_NS))
        log.debug("WindowsTaskAutostart._installed: %s", installed)
        return installed

    @staticmethod
    def _target_of(installed: tuple[str, str] | None) -> str | None:
        """The autostart target an installed task runs, read off its Arguments."""
        target = (target_from_argv(["<program>", *installed[1].split()])
                  if installed is not None else None)
        log.debug("WindowsTaskAutostart._target_of: %s -> %s", installed, target)
        return target

    def entry_location(self) -> str:
        location = f"Task Scheduler\\{self._task_name}"
        log.debug("WindowsTaskAutostart.entry_location: %s", location)
        return location

    def installed_target(self) -> str | None:
        target = self._target_of(self._installed())
        log.debug("WindowsTaskAutostart.installed_target: %s", target)
        return target

    def is_enabled(self) -> bool:
        """True when the task exists AND runs this install for its target.

        A task left pointing at a moved or older install reads as disabled, so
        the gui re-enables it and the entry heals — the Run key's rule.
        """
        installed = self._installed()
        target = self._target_of(installed)
        enabled = (installed is not None and target is not None
                   and installed == (str(self._program), self._arguments(target)))
        log.info("WindowsTaskAutostart.is_enabled: %s", enabled)
        return enabled

    def enable(self, target: str | None = None) -> None:
        import tempfile
        target = target or DEFAULT_AUTOSTART_TARGET
        user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
        xml = _render_task_xml(str(self._program), self._arguments(target), user)
        log.info("WindowsTaskAutostart.enable: target=%s user=%s", target, user)
        # schtasks reads a task file as UTF-16, as the C# writes it.
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-16") as f:
            f.write(xml)
        try:
            result = self._run(["/create", "/tn", self._task_name,
                                 "/xml", f.name, "/f"])
        finally:
            Path(f.name).unlink(missing_ok=True)
        if result.returncode != 0:
            log.error("WindowsTaskAutostart.enable: schtasks failed (%d): %s",
                      result.returncode, result.stderr.strip())
            return
        self._legacy.disable()
        log.info("WindowsTaskAutostart: enabled as %s", self.entry_location())

    def disable(self) -> None:
        result = self._run(["/delete", "/tn", self._task_name, "/f"])
        log.info("WindowsTaskAutostart.disable: schtasks exit %d",
                 result.returncode)
        self._legacy.disable()

    def refresh(self) -> None:
        """Rewrite the task — or migrate a Run-key entry — if one exists.

        An install upgraded from the Run-key era has only the old
        ``TRCCNext`` value: that IS the user's choice, so it becomes a task
        for the same target.  Neither present means autostart was never
        chosen, and refresh must not choose it for them.
        """
        self._legacy.retire_old_values()
        target = self.installed_target()
        if target is None and self._legacy._stored_value() is None:
            log.debug("WindowsTaskAutostart.refresh: no task, no Run entry — "
                      "nothing to refresh")
            return
        target = target or self._legacy.installed_target()
        log.info("WindowsTaskAutostart.refresh: re-writing for target=%s",
                 target)
        self.enable(target)


# =========================================================================
# macOS — LaunchAgent plist + launchctl
# =========================================================================


_MAC_LABEL = "com.thermalright.trcc"
_DEFAULT_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"
)


def _macos_argv(target: str = DEFAULT_AUTOSTART_TARGET) -> list[str]:
    """The LaunchAgent's argv: THIS install's program, never a PATH lookup.

    launchd starts an agent with a minimal PATH and Terminal starts one with
    the shell's, so ``launch_argv``'s ``shutil.which("trcc")`` named different
    programs in the two -- and ``refresh`` rewrote the plist on every switch
    between them (3 of 3 alternating launches).  The running interpreter is
    the same either way, as in ``daemon._daemon_spawn_cmd``.  A frozen app
    has no ``-m``: its own binary takes the subcommand.  The flags are
    ``AUTOSTART_TARGETS``' -- ``--resume`` keeps the gui in the tray (#201).
    """
    head = ([sys.executable] if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "trcc"])
    argv = [*head, target, *AUTOSTART_TARGETS[target]]
    log.debug("_macos_argv(%s): %s", target, argv)
    return argv


def _resolve_macos_program_args() -> list[str]:
    """Return the argv that the LaunchAgent should run on login."""
    log.debug("_resolve_macos_program_args: called")
    return _macos_argv()


def _render_plist(program_args: list[str], *, label: str = _MAC_LABEL) -> str:
    """Render the LaunchAgent plist body — pure-string, fully testable."""
    log.debug("_render_plist: label=%s args=%d", label, len(program_args))
    args_xml = "\n".join(f"        <string>{arg}</string>" for arg in program_args)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>Label</key>\n'
        f'    <string>{label}</string>\n'
        '    <key>ProgramArguments</key>\n'
        '    <array>\n'
        f"{args_xml}\n"
        '    </array>\n'
        '    <key>RunAtLoad</key>\n'
        '    <true/>\n'
        '    <key>KeepAlive</key>\n'
        '    <false/>\n'
        '</dict>\n'
        '</plist>\n'
    )


# Callable type alias for tests — runs a ``launchctl`` subcommand and
# returns its exit code.  Production binds it to ``subprocess.run``; the
# fake in tests records every invocation without touching the system.
LaunchctlRunner = Any


def _default_launchctl_runner(args: list[str]) -> int:
    """Run ``launchctl <args>`` and return its returncode."""
    log.debug("_default_launchctl_runner: args=%s", args)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=5,
                              check=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        log.debug("launchctl %s failed: %s", args, e)
        return -1
    if proc.returncode != 0:
        log.debug("launchctl %s exited %d: %s",
                  args, proc.returncode, proc.stderr.strip())
    return proc.returncode


class MacOSAutostart(AutostartManager):
    """Autostart via LaunchAgent plist + ``launchctl bootstrap``.

    LaunchAgents live under ``~/Library/LaunchAgents/`` and fire on
    user login.  No admin / no system-wide service — same UX as the
    Windows HKCU Run key.

    ``enable`` writes the plist + ``launchctl bootstrap gui/<uid>``s
    it; ``disable`` ``bootout``s the agent and unlinks the plist.
    Both are idempotent and tolerant of "already loaded" / "not
    loaded" exit codes.

    DI seam: ``plist_path`` + ``runner`` + ``program_args`` so the
    full enable / disable cycle runs on Linux against a tmpdir + a
    recording runner.
    """

    def __init__(
        self,
        *,
        plist_path: Path | None = None,
        program_args: list[str] | None = None,
        runner: Any = None,
        label: str = _MAC_LABEL,
        uid: int | None = None,
    ) -> None:
        log.debug("__init__")
        self._plist_path = plist_path if plist_path is not None else _DEFAULT_PLIST_PATH
        self._program_args = (
            list(program_args) if program_args is not None
            else _resolve_macos_program_args()
        )
        self._runner: Any = runner if runner is not None else _default_launchctl_runner
        self._label = label
        # launchctl needs the GUI domain identifier; default to the
        # current uid.  Tests inject a fixed uid for stable assertions.
        self._uid = uid if uid is not None else os.getuid()

    @property
    def _domain_target(self) -> str:
        """``gui/<uid>/<label>`` — the launchd service identifier."""
        log.debug("_domain_target")
        return f"gui/{self._uid}/{self._label}"

    @property
    def _domain(self) -> str:
        log.debug("_domain")
        return f"gui/{self._uid}"

    # ── AutostartManager ABC ───────────────────────────────────────

    def is_enabled(self) -> bool:
        """True when the plist file exists on disk.

        ``launchctl print`` would give a more authoritative answer, but
        it spawns a subprocess on every UI tick; file existence is the
        canonical install marker that legacy + iStat / Stats also use.
        """
        log.info("is_enabled: called")
        return self._plist_path.exists()

    def entry_location(self) -> str:
        log.debug("MacOSAutostart.entry_location: %s", self._plist_path)
        return str(self._plist_path)

    def installed_target(self) -> str | None:
        """Read the target back out of the installed plist's ProgramArguments."""
        if not self._plist_path.exists():
            log.debug("MacOSAutostart.installed_target: %s absent",
                      self._plist_path)
            return None
        body = self._plist_path.read_text(encoding="utf-8")
        argv = re.findall(r"<string>(.*?)</string>", body)
        log.debug("MacOSAutostart.installed_target: argv=%s", argv)
        # The Label is the first <string> in the plist; drop it so argv[0] is
        # the program and the position rule holds.
        return target_from_argv(argv[1:]) if argv else None

    def _args_for(self, target: str | None) -> list[str]:
        """``None`` keeps the constructor's argv — the injected-args seam."""
        args = list(self._program_args) if target is None else _macos_argv(target)
        log.debug("MacOSAutostart._args_for(%s): %s", target, args)
        return args

    def _write_if_changed(self, body: str) -> bool:
        """Write the plist only when *body* differs from it; True if it wrote."""
        if (self._plist_path.is_file()
                and self._plist_path.read_text(encoding="utf-8") == body):
            log.debug("MacOSAutostart: %s is current", self._plist_path)
            return False
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        self._plist_path.write_text(body, encoding="utf-8")
        log.info("MacOSAutostart: wrote %s", self._plist_path)
        return True

    def enable(self, target: str | None = None) -> None:
        log.info("enable: target=%s", target)
        self._write_if_changed(
            _render_plist(self._args_for(target), label=self._label))
        # Enabling is the user's request, so the agent is loaded now, written
        # or not.  bootstrap can fail with code 17 ("already loaded") — OK.
        rc = self._runner([
            "launchctl", "bootstrap", self._domain, str(self._plist_path),
        ])
        if rc not in (0, 17):
            log.debug("launchctl bootstrap returned %d", rc)
        log.info("MacOSAutostart: enabled at %s", self._plist_path)

    def disable(self) -> None:
        log.info("disable: called")
        # bootout can fail with code 5 ("not loaded") — that's also OK.
        if self._plist_path.exists():
            rc = self._runner([
                "launchctl", "bootout", self._domain_target,
            ])
            if rc not in (0, 5):
                log.debug("launchctl bootout returned %d", rc)
            try:
                self._plist_path.unlink()
            except OSError:
                log.exception("MacOSAutostart.disable: failed to remove plist")
                return
            log.info("MacOSAutostart: disabled")

    def refresh(self) -> None:
        """Re-render an installed plist when its argv changed — see
        WindowsAutostart.  Never touches launchd.

        ``--resume`` changed the argv, and an existing LaunchAgent would
        otherwise keep launching a visible window forever (#201).  But it ran
        on EVERY UI start through ``enable``: a rewrite and a ``launchctl
        bootstrap`` each time (PR #303).  launchd reads LaunchAgents at the
        next login; ``bootstrap`` on a loaded label fails and reloads nothing,
        and ``bootout`` would kill the UI the agent started.  So: write only a
        difference, and leave the loaded agent alone.
        """
        if not self._plist_path.exists():
            log.debug("MacOSAutostart.refresh: no plist — nothing to refresh")
            return
        installed = self.installed_target()
        if self._write_if_changed(
                _render_plist(self._args_for(installed), label=self._label)):
            log.info("MacOSAutostart.refresh: re-rendered %s (target=%s) — "
                     "launchd reads it at the next login",
                     self._plist_path, installed)
