"""Autostart manager implementations + the shared no-op fallback.

  * ``WindowsAutostart``  — writes HKCU\\Software\\Microsoft\\
                            Windows\\CurrentVersion\\Run via ``winreg``.
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

_AUTOSTART_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=TRCC (next)
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

    def __init__(self) -> None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
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

    def enable(self, target: str | None = None) -> None:
        target = target or DEFAULT_AUTOSTART_TARGET
        log.info("XdgDesktopAutostart.enable: writing %s (target=%s)",
                 self._path, target)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._render(target), encoding="utf-8")
        self._path.chmod(0o644)
        log.info("Autostart enabled: %s", self._path)

    def disable(self) -> None:
        log.info("XdgDesktopAutostart.disable: removing %s", self._path)
        if self._path.exists():
            self._path.unlink()
            log.info("Autostart disabled: %s", self._path)
        else:
            log.info("XdgDesktopAutostart.disable: %s did not exist", self._path)

    def refresh(self) -> None:
        """Re-render the .desktop file if present (picks up a new Exec path)."""
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


def _resolve_command() -> str:
    """Pick the command line that launches the GUI on user login.

    Prefer the installed console script when on PATH (PyInstaller bundle
    or pip-installed entry point), otherwise fall back to invoking
    ``python -m trcc gui`` so dev installs still autostart.
    """
    log.debug("_resolve_command: called")
    # ``--resume`` for the same reason Linux passes it (#201): an autostarted
    # instance belongs in the tray, not in your face at every login.  Windows
    # and macOS never got it — #201 was reported on Linux and the fix landed
    # only on the XDG path, so two of the three platforms popped a window.
    argv = autostart_argv()
    # Registry values quote the program so spaces in install dirs
    # (Program Files) don't break the launch.
    return " ".join([f'"{argv[0]}"', *argv[1:]])


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
    """Autostart via the HKCU Run registry key.

    The Run key fires whenever the user logs in; no admin / no
    scheduled task / no service.  Writes a single REG_SZ value pointing
    at ``trcc gui`` (or ``python -m trcc gui`` when the
    console script isn't on PATH yet).

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
        argv = autostart_argv(target)
        cmd = " ".join([f'"{argv[0]}"', *argv[1:]])
        log.debug("WindowsAutostart._command_for(%s): %r", target, cmd)
        return cmd

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

    def enable(self, target: str | None = None) -> None:
        log.info("enable: target=%s", target)
        if self._registry is None:
            log.debug("WindowsAutostart.enable: winreg unavailable; no-op")
            return
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
# macOS — LaunchAgent plist + launchctl
# =========================================================================


_MAC_LABEL = "com.thermalright.trcc"
_DEFAULT_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"
)


def _resolve_macos_program_args() -> list[str]:
    """Return the argv that the LaunchAgent should run on login."""
    log.debug("_resolve_macos_program_args: called")
    # ``--resume`` — see _resolve_command: launch hidden in the tray, the
    # behaviour Linux has had since #201 and these two platforms had not.
    return autostart_argv()


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
        args = list(self._program_args) if target is None else autostart_argv(target)
        log.debug("MacOSAutostart._args_for(%s): %s", target, args)
        return args

    def enable(self, target: str | None = None) -> None:
        log.info("enable: target=%s", target)
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        body = _render_plist(self._args_for(target), label=self._label)
        self._plist_path.write_text(body, encoding="utf-8")
        # bootstrap can fail with code 17 ("already loaded") — that's OK.
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
        """Leave the LaunchAgent untouched on application startup."""
        return
