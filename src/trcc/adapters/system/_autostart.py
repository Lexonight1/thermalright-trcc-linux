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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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

    def enable(self) -> None:
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
Comment=Auto-start TRCC GUI on login
Exec={exec_cmd}
Icon=trcc-linux
Terminal=false
Categories=System;Settings;
X-GNOME-Autostart-enabled=true
StartupNotify=false
"""


class XdgDesktopAutostart(AutostartManager):
    """XDG Autostart adapter — writes/removes ~/.config/autostart/trcc.desktop.

    OS-agnostic: used by both ``LinuxPlatform`` and ``BSDPlatform`` (the
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

    def enable(self) -> None:
        log.info("XdgDesktopAutostart.enable: writing %s", self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._render(), encoding="utf-8")
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
            log.info("XdgDesktopAutostart.refresh: re-rendering %s", self._path)
            self.enable()
        else:
            log.debug("XdgDesktopAutostart.refresh: %s not present — nothing to refresh",
                      self._path)

    def _render(self) -> str:
        return _AUTOSTART_TEMPLATE.format(exec_cmd=self._exec_cmd())

    @staticmethod
    def _exec_cmd() -> str:
        """Build the launch command.

        Preference order:
          1. ``trcc`` console script if installed and on PATH
          2. ``<sys.executable> -m trcc gui``

        The second form is robust across pipx / venv / system-python
        installs because ``sys.executable`` is always the right interpreter.

        ``--resume`` makes the autostarted instance start hidden in the
        system tray (restoring the last-used theme) instead of popping a
        window on every login — the long-standing autostart behaviour that
        regressed when the flag was dropped (#201).
        """
        if (resolved := shutil.which("trcc")):
            return f"{resolved} gui --resume"
        return f"{sys.executable} -m trcc gui --resume"


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
    if (exe := shutil.which("trcc")) is not None:
        # Registry values quote the path so spaces in install dirs
        # (Program Files) don't break the launch.
        return f'"{exe}" gui'
    return f'"{sys.executable}" -m trcc gui'


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
        self._cmd = command if command is not None else _resolve_command()
        self._registry: Any = registry if registry is not None else _winreg_module()
        self._value_name = value_name

    # ── AutostartManager ABC ───────────────────────────────────────

    def is_enabled(self) -> bool:
        """True when the Run key holds our value AND it matches our command.

        Mismatched value means the user (or an old install) wrote a
        different launch line — we report enabled=False so the next
        ``enable()`` rewrites it.  Defensive — never silently inherit
        a stale path.
        """
        log.info("is_enabled: called")
        if self._registry is None:
            return False
        try:
            with self._open_key(write=False) as key:
                stored, _ = self._registry.QueryValueEx(key, self._value_name)
        except OSError:
            return False
        return stored == self._cmd

    def enable(self) -> None:
        log.info("enable: called")
        if self._registry is None:
            log.debug("WindowsAutostart.enable: winreg unavailable; no-op")
            return
        with self._open_key(write=True) as key:
            self._registry.SetValueEx(
                key, self._value_name, 0,
                self._registry.REG_SZ, self._cmd,
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
        """No-op: the Run key needs no compilation step."""

    # ── Internal: open the Run key in read or write mode ──────────

    def _open_key(self, *, write: bool) -> Any:
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
    if (exe := shutil.which("trcc")) is not None:
        return [exe, "gui"]
    return [sys.executable, "-m", "trcc", "gui"]


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
        return f"gui/{self._uid}/{self._label}"

    @property
    def _domain(self) -> str:
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

    def enable(self) -> None:
        log.info("enable: called")
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        body = _render_plist(self._program_args, label=self._label)
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
        """No-op: the plist needs no rebuild between sessions."""
