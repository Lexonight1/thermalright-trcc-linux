"""Autostart manager implementations + the shared no-op fallback.

``WindowsAutostart`` writes the user's HKCU Run key.  Other OSes get
``NoopAutostart`` for now (macOS LaunchAgent lands in B.7); each
platform's ``Platform.autostart()`` consumes one of these.
"""
from __future__ import annotations

import logging
import shutil
import sys
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
        return False

    def enable(self) -> None:
        log.debug("NoopAutostart.enable: no-op on this platform")

    def disable(self) -> None:
        log.debug("NoopAutostart.disable: no-op on this platform")

    def refresh(self) -> None:
        pass


# =========================================================================
# Windows — HKCU Run key
# =========================================================================


_WIN_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_DEFAULT_VALUE_NAME = "TRCCNext"


def _resolve_command() -> str:
    """Pick the command line that launches the GUI on user login.

    Prefer the installed console script when on PATH (PyInstaller bundle
    or pip-installed entry point), otherwise fall back to invoking
    ``python -m trcc.next gui`` so dev installs still autostart.
    """
    if (exe := shutil.which("trcc-next")) is not None:
        # Registry values quote the path so spaces in install dirs
        # (Program Files) don't break the launch.
        return f'"{exe}" gui'
    return f'"{sys.executable}" -m trcc.next gui'


def _winreg_module() -> Any:
    """Import ``winreg`` on Windows; return None elsewhere.

    Production code paths only hit this on Windows; tests inject a fake
    module so the protocol logic runs anywhere.
    """
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
    at ``trcc-next gui`` (or ``python -m trcc.next gui`` when the
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
        if self._registry is None:
            return False
        try:
            with self._open_key(write=False) as key:
                stored, _ = self._registry.QueryValueEx(key, self._value_name)
        except OSError:
            return False
        return stored == self._cmd

    def enable(self) -> None:
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
