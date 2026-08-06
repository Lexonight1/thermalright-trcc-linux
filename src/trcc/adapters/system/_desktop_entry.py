"""XDG desktop integration — the applications-menu entry and its icons.

The distro packages install these into ``/usr/share`` (``release.yml`` does
it for RPM, DEB and Arch alike).  A ``pip`` / ``pipx`` install ships the very
same files inside the wheel and registers them nowhere the desktop looks.

The result is an app that installs cleanly, autostarts on login, and cannot
be found in the applications menu — the user watches it running in the tray
with no way to launch it again once closed.  Distros we package for hide the
gap; SteamOS and anything else without a package hit it every time, because
pip is the only route there (#231).

Everything here is per-user (``~/.local/share``), so it needs no root and
cannot collide with a package: a system-wide entry always wins and is left
untouched.

Linux and BSD share this module for the same reason they share
``XdgDesktopAutostart`` — the XDG spec is identical on both.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ._autostart import gui_launch_command

log = logging.getLogger(__name__)


# The bundled entry + icons, as installed into the wheel.
_ASSETS = Path(__file__).resolve().parents[1].parent / "assets"
_DESKTOP_ASSET = _ASSETS / "trcc-linux.desktop"
_ICON_DIR = _ASSETS / "icons"

_ENTRY_FILENAME = "trcc-linux.desktop"

# Where a distro package puts the entry.  If one is here, this module has
# nothing to do — the package owns the integration.
_SYSTEM_ENTRY = Path("/usr/share/applications") / _ENTRY_FILENAME

# `Icon=trcc` in the entry resolves against the hicolor theme, so each PNG
# lands at hicolor/<size>x<size>/apps/trcc.png — the exact layout the
# packages build.  Sizes are the ones we actually ship.
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_ICON_NAME = "trcc.png"


class XdgDesktopEntry:
    """Registers TRCC in the user's applications menu.

    Idempotent: installing twice rewrites the same files, and a
    system-wide package entry short-circuits the whole thing.
    """

    def __init__(self) -> None:
        xdg = os.environ.get("XDG_DATA_HOME")
        self._base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        self._entry = self._base / "applications" / _ENTRY_FILENAME
        log.info("XdgDesktopEntry: user entry path = %s", self._entry)

    @property
    def path(self) -> Path:
        log.debug("XdgDesktopEntry.path → %s", self._entry)
        return self._entry

    def is_installed(self) -> bool:
        """True when this user can find TRCC in their menu, either way."""
        system = _SYSTEM_ENTRY.is_file()
        user = self._entry.is_file()
        log.debug("XdgDesktopEntry.is_installed: system=%s user=%s", system, user)
        return system or user

    def install(self) -> bool:
        """Write the menu entry + icons for this user.  True if it did.

        Returns False (having done nothing) when a distro package already
        provides the entry system-wide, so a packaged install never grows
        a shadow copy in ``~/.local/share`` that then goes stale.
        """
        if _SYSTEM_ENTRY.is_file():
            log.info("XdgDesktopEntry.install: %s exists — packaged install "
                     "already owns the menu entry, skipping", _SYSTEM_ENTRY)
            return False
        if not _DESKTOP_ASSET.is_file():
            log.warning("XdgDesktopEntry.install: bundled entry missing at %s "
                        "— cannot register a menu entry", _DESKTOP_ASSET)
            return False

        self._entry.parent.mkdir(parents=True, exist_ok=True)
        self._entry.write_text(self._render(), encoding="utf-8")
        self._entry.chmod(0o644)
        log.info("XdgDesktopEntry.install: wrote %s", self._entry)
        self._install_icons()
        return True

    def uninstall(self) -> None:
        """Remove the per-user entry (icons are harmless and left alone)."""
        if self._entry.exists():
            self._entry.unlink()
            log.info("XdgDesktopEntry.uninstall: removed %s", self._entry)
        else:
            log.debug("XdgDesktopEntry.uninstall: %s not present", self._entry)

    def _render(self) -> str:
        """The bundled entry with ``Exec=`` pointed at this install.

        The shipped file says ``Exec=trcc gui``, which assumes the console
        script is on PATH.  It usually is, but a venv install where the
        user never activated the venv would get a menu entry that silently
        does nothing — worse than no entry at all.
        """
        exec_cmd = gui_launch_command()
        lines = [
            f"Exec={exec_cmd}" if line.startswith("Exec=") else line
            for line in _DESKTOP_ASSET.read_text(encoding="utf-8").splitlines()
        ]
        log.info("XdgDesktopEntry._render: Exec=%s", exec_cmd)
        return "\n".join(lines) + "\n"

    def _install_icons(self) -> None:
        """Copy each shipped icon into the user's hicolor theme.

        Without these the entry appears with a generic placeholder, which
        reads as a broken install even though it launches fine.
        """
        installed = 0
        for size in _ICON_SIZES:
            source = _ICON_DIR / f"trcc_{size}x{size}.png"
            if not source.is_file():
                log.debug("XdgDesktopEntry._install_icons: no %s", source)
                continue
            target = (self._base / "icons" / "hicolor"
                      / f"{size}x{size}" / "apps" / _ICON_NAME)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            installed += 1
        log.info("XdgDesktopEntry._install_icons: %d/%d icon size(s) → %s",
                 installed, len(_ICON_SIZES), self._base / "icons" / "hicolor")
