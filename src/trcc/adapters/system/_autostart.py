"""Autostart manager implementations + the shared no-op fallback.

  * ``WindowsAutostart``  — writes HKCU\\Software\\Microsoft\\
                            Windows\\CurrentVersion\\Run via ``winreg``
                            (pip and installer builds alike: neither is
                            elevated, so the Run key starts both).
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
# systemd user service — the App itself, outliving the desktop session
# =========================================================================

_SERVICE_NAME = "trccd.service"

#: Bound to ``default.target``, never to ``graphical-session.target``: a
#: logout into another session -- Steam's Gamescope, which CachyOS reaches by
#: a real logout -- stopped a session-bound App and left the panel dark
#: (#311).  The user manager starts this again at the next login, and the App
#: shows the saved display by itself.
_SERVICE_TEMPLATE = """\
[Unit]
Description=TRCC Linux -- the App that drives your panels
Documentation=https://github.com/Lexonight1/thermalright-trcc-linux

[Service]
Type=simple
ExecStart={exec_cmd}
Restart=on-failure
RestartSec=5
Environment=QT_QPA_PLATFORM=offscreen

[Install]
WantedBy=default.target
"""


def _systemctl_user(*args: str) -> int:
    """``systemctl --user ARGS``; its exit code, or 127 without systemd."""
    if shutil.which("systemctl") is None:
        log.warning("_systemctl_user: no systemctl -- this system does not "
                    "run systemd")
        return 127
    try:
        done = subprocess.run(["systemctl", "--user", *args], check=False,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("_systemctl_user %s: %s", " ".join(args), e)
        return 1
    log.info("_systemctl_user %s -> %d %s", " ".join(args), done.returncode,
             done.stderr.strip())
    return done.returncode


class SystemdUserService:
    """The App as this user's systemd service, so it keeps the panels going
    through a logout into another session (#311).  Per-user: no root.

    ``ExecStart`` is this install's own launch command -- the one the
    autostart entry uses -- so pipx, venv and package installs all work.
    """

    def __init__(self, config_home: Path | None = None,
                 systemctl: Any = None) -> None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = config_home or (Path(xdg) if xdg else Path.home() / ".config")
        self._path = base / "systemd" / "user" / _SERVICE_NAME
        self._systemctl = systemctl or _systemctl_user
        log.info("SystemdUserService: unit path = %s", self._path)

    @property
    def path(self) -> Path:
        log.debug("SystemdUserService.path -> %s", self._path)
        return self._path

    def render(self) -> str:
        """The unit, its ``ExecStart`` pointed at this install."""
        exec_cmd = " ".join(f'"{a}"' if " " in a else a
                            for a in launch_argv("daemon"))
        log.info("SystemdUserService.render: ExecStart=%s", exec_cmd)
        return _SERVICE_TEMPLATE.format(exec_cmd=exec_cmd)

    def install(self) -> bool:
        """Write the unit, then enable and start it.  True when running."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self.render(), encoding="utf-8")
        log.info("SystemdUserService.install: wrote %s", self._path)
        started = (self._systemctl("daemon-reload") == 0
                   and self._systemctl("enable", "--now", _SERVICE_NAME) == 0)
        log.info("SystemdUserService.install: enabled and started=%s", started)
        return started

    @staticmethod
    def removal_command() -> str:
        """The one line that takes it out again."""
        log.debug("SystemdUserService.removal_command")
        return (f"systemctl --user disable --now {_SERVICE_NAME} && "
                f"rm ~/.config/systemd/user/{_SERVICE_NAME}")


# =========================================================================
# Windows — HKCU Run key
# =========================================================================


_WIN_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_DEFAULT_VALUE_NAME = "TRCCNext"


def _resolve_command(target: str = DEFAULT_AUTOSTART_TARGET) -> str:
    """The Run-key command that starts *target* at sign-in.

    Never a console program: an entry naming one opens a terminal at every
    sign-in.  The installer build runs ``trcc-gui.exe``, the windowed twin of
    ``trcc.exe`` beside it.  A pip install runs ``pythonw.exe -m trcc``, never
    ``launch_argv``'s choice: ``python.exe`` and the pip ``trcc.exe`` launcher
    are both console programs.  ``pythonw.exe`` ships beside ``python.exe`` in
    every CPython install and venv on Windows.  The flags are
    ``AUTOSTART_TARGETS``' — ``--resume`` keeps the gui in the tray (#201).
    """
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        program, module = exe.with_name("trcc-gui.exe"), []
    else:
        program, module = exe.with_name("pythonw.exe"), ["-m", "trcc"]
        if not program.is_file():
            log.warning("_resolve_command: no %s — the Run entry uses %s, "
                        "which opens a console window at sign-in", program, exe)
            program = exe
    # Registry values quote the program so spaces in install dirs
    # (Program Files) don't break the launch.
    cmd = " ".join([f'"{program}"', *module, target,
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
    """Autostart via the HKCU Run registry key — every Windows build.

    The Run key fires whenever the user signs in, for a program that does NOT
    need elevation — and no TRCC build does: TRCC's SYSTEM service drives the
    panels and reads PawnIO for it.  Writes a single REG_SZ value running
    ``trcc-gui.exe gui --resume`` (installer) or ``pythonw.exe -m trcc gui
    --resume`` (pip).

    Builds before the service were elevated (``--uac-admin``), which Windows
    will not start from the Run key, so they used a sign-in task at the
    highest run level, as the C# does (``Form1.cs:211-281``).  :meth:`refresh`
    carries that task's target over; the task itself is the installer's to
    delete, since a task made elevated refuses a normal process (measured on
    the win11 VM, 2026-10-19: "Access is denied").

    Tests inject a stub ``registry`` module + ``command`` string (and a fake
    ``schtasks``) so the full cycle runs without touching the real winreg.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        registry: Any = None,
        value_name: str = _DEFAULT_VALUE_NAME,
        schtasks: Any = None,
    ) -> None:
        """``registry`` is a winreg-compatible module-like object — duck-typed
        seam so tests can inject an in-memory fake on non-Windows boxes;
        ``schtasks`` stands in for ``schtasks.exe`` the same way."""
        log.debug("__init__")
        self._cmd = command if command is not None else _resolve_command()
        self._registry: Any = registry if registry is not None else _winreg_module()
        self._value_name = value_name
        self._schtasks = schtasks if schtasks is not None else _run_schtasks

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

        Only rewrites when an entry is already present — like every other
        ``refresh``, it must never enable autostart nobody asked for.  The old
        sign-in task counts: it IS the user's choice, so an install upgraded
        from the elevated builds gets a Run value for the same target.
        """
        self.retire_old_values()
        if not self._value_present():
            if (target := self._old_task_target()) is not None:
                log.info("WindowsAutostart.refresh: the sign-in task %r runs "
                         "%s — carrying it over to the Run key", _TASK_NAME,
                         target)
                self.enable(target)
                return
            log.debug("WindowsAutostart.refresh: no entry — nothing to refresh")
            return
        installed = self.installed_target()
        log.info("WindowsAutostart.refresh: re-writing %s (target=%s)",
                 self._value_name, installed)
        self.enable(installed)

    def _old_task_target(self) -> str | None:
        """The target the old sign-in task runs, or None without one.

        Read off the task's Exec Arguments (``gui --resume``) by the same
        position rule as a Run value.  A task we cannot read or parse answers
        None: refresh then changes nothing, which beats guessing a target.
        """
        import xml.etree.ElementTree as ET
        result = self._schtasks(["/query", "/tn", _TASK_NAME, "/xml"])
        if result.returncode != 0:
            log.debug("WindowsAutostart._old_task_target: no task %r (%s)",
                      _TASK_NAME, result.stderr.strip())
            return None
        try:
            arguments = ET.fromstring(result.stdout.strip()).findtext(
                "t:Actions/t:Exec/t:Arguments", None, _TASK_NS)
        except ET.ParseError:
            log.warning("WindowsAutostart._old_task_target: unreadable task "
                        "XML for %r", _TASK_NAME)
            return None
        target = (target_from_argv(["<program>", *arguments.split()])
                  if arguments is not None else None)
        log.info("WindowsAutostart._old_task_target: %r -> %s", arguments,
                 target)
        return target

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
# Windows — the sign-in task the elevated builds used, read to carry it over
# =========================================================================


#: The old task's name is its IDENTITY, and the installer deletes it by this
#: name (tests/test_installer_uninstall.py gates the match).  Distinct from
#: Thermalright's own ``TRCCAppStartup``.
_TASK_NAME = "TRCC Linux"

_SCHTASKS = (Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
             / "System32" / "schtasks.exe")

_TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``schtasks.exe`` by absolute path — no shell, no console window.

    A missing or hung ``schtasks`` answers as a failed query does: there is
    then no task to carry over, which is no reason to fail a refresh.
    """
    log.info("_run_schtasks: %s", args)
    from ...core.models import SUBPROCESS_NO_WINDOW
    try:
        return subprocess.run(
            [str(_SCHTASKS), *args], capture_output=True, text=True,
            errors="replace", check=False, timeout=30,
            creationflags=SUBPROCESS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("_run_schtasks: %s could not run: %s", _SCHTASKS, e)
        return subprocess.CompletedProcess(args, 1, "", str(e))


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
