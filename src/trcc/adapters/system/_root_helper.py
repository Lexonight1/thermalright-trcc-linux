"""Running one of TRCC's root helpers from the App, on Linux.

TRCC's grants -- RAM lighting (``trcc-ram-access``), a module's USB devices
(``trcc-module-devices``) -- are each one argv-locked helper in ``assets/``,
packaged to /usr/bin, with a password-free ``-off`` twin that can only take a
grant back.  Every one is run the same way:

* already root: the helper file the package ships, directly;
* taking back, when its twin is packaged: ``pkexec <twin>`` -- no password;
* packaged: ``pkexec /usr/bin/<helper>`` -- TRCC's own polkit action;
* any other install with polkit (pip, pipx, a source checkout): ``pkexec`` runs
  the SAME helper file from the package (``python -I``), under polkit's
  standard run-as-administrator action;
* no polkit at all: nothing runs, and the ``sudo`` command that will is named.
  ``sudo trcc ...`` would not: sudo drops ``~/.local/bin`` from PATH and root's
  Python cannot import a package installed for the user, so the command names
  the interpreter and the helper file instead.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: pkexec's own exits: the user dismissed the prompt, or polkit said no.
PKEXEC_DISMISSED, PKEXEC_REFUSED = 126, 127


def effective_root() -> bool:
    """Whether this process runs as root (``sudo``, ``pkexec``, a root login)."""
    root = os.geteuid() == 0
    log.debug("effective_root: %s", root)
    return root


def sudo_command(asset: Path, args: Sequence[str]) -> str:
    """The terminal command that runs helper file *asset* as root, on any install."""
    command = f"sudo {sys.executable} -I {asset} {' '.join(args)}"
    log.debug("sudo_command: %s", command)
    return command


@dataclass(frozen=True)
class RootHelper:
    """One root helper, and how to run it from here."""
    #: Where a package installs it (``/usr/bin/trcc-ram-access``).
    installed: str
    #: The same file as the package ships it -- what runs when /usr/bin has none.
    asset: Path
    #: ``pkexec <program>`` when that packaged program and TRCC's policy can
    #: run, else None (``linux._pkexec_helper``).
    pkexec: Callable[[str], list[str] | None]
    #: Its password-free twin, which only takes the grant back.
    off: str | None = None
    is_root: Callable[[], bool] = effective_root
    which: Callable[[str], str | None] = shutil.which

    def argv(self, args: Sequence[str],
             off_args: Sequence[str] | None = None) -> list[str] | None:
        """How to run the helper with *args* from here, or None if nothing
        can.  *off_args* given: this takes back, so the twin runs with them
        when it is packaged."""
        helper_file = [sys.executable, "-I", str(self.asset), *args]
        if self.is_root():
            log.info("RootHelper.argv: root runs %s directly", self.asset.name)
            return helper_file
        if (off_args is not None and self.off is not None
                and (off := self.pkexec(self.off)) is not None):
            log.info("RootHelper.argv: pkexec %s, the password-free take-back", self.off)
            return [*off, *off_args]
        if (packaged := self.pkexec(self.installed)) is not None:
            log.info("RootHelper.argv: pkexec %s, TRCC's own polkit action",
                     self.installed)
            return [*packaged, *args]
        if (pkexec := self.which("pkexec")) is not None:
            log.info("RootHelper.argv: pkexec, polkit's run-as-administrator action")
            return [pkexec, *helper_file]
        log.info("RootHelper.argv: no polkit here for %s", self.asset.name)
        return None

    def sudo_command(self, args: Sequence[str]) -> str:
        """The terminal command that runs the helper as root, on any install."""
        log.debug("RootHelper.sudo_command: %s", self.asset.name)
        return sudo_command(self.asset, args)

    def run(self, argv: list[str],
            run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> int:
        """Run *argv*; its exit code, or -1 when it could not run at all."""
        try:
            # No timeout: pkexec returns when the password is typed (0), the
            # prompt is closed or cancelled (126), refused (127), or there is
            # no prompt to show at all (127, at once).  A cap would report a
            # failure that has not happened to someone who stepped away.
            code = run(argv, capture_output=True, text=True, check=False).returncode
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("RootHelper.run: %s failed to run -- %s: %s", argv[0],
                        type(e).__name__, e)
            code = -1
        log.info("RootHelper.run: %s exited %d", self.asset.name, code)
        return code
