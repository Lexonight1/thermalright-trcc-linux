"""The opt-in grant to reach RGB memory's bus, on Linux.

RGB memory's lighting controller answers on the chipset SMBus, which only root
can open.  ``trcc-ram-access`` (``assets/``, packaged to /usr/bin) installs or
removes a udev rule that tags that one bus ``uaccess`` for the user at the
seat; this adapter reports where the user stands and runs the helper:

* packaged install: ``pkexec /usr/bin/trcc-ram-access`` -- TRCC's own polkit
  action (``auth_admin``: the password every time);
* any other install with polkit (pip, pipx, a source checkout): ``pkexec`` runs
  the SAME helper file from the package (``python -I``), under polkit's
  standard run-as-administrator action -- also the password every time;
* already root: that helper file, directly;
* no polkit at all: the status names the ``sudo`` command that will.
  ``sudo trcc ...`` would not: sudo drops ``~/.local/bin`` from PATH and root's
  Python cannot import a package installed for the user, so the command names
  the interpreter and the helper file instead.
* NixOS (``NixosRamAccess``): /etc is built from the system configuration, so
  the helper cannot write there; the grant is the flake's
  ``programs.trcc-linux.ramLighting`` and the status names that line.

``status`` reads the filesystem only.  Nothing here touches the bus.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from ...core.models import RamAccessState, RamAccessStatus
from ...core.ports import RamAccess
from ._smbus import find_smbus

log = logging.getLogger(__name__)

#: Must equal the helper's own constants (gated by a test: the helper may
#: import nothing from trcc).
RULE_PATH = Path("/etc/udev/rules.d/70-trcc-ram-lighting.rules")
HEADER = ("# TRCC RAM lighting -- written by trcc-ram-access; "
          "`trcc system ram-lighting disable` removes it.")
HELPER = "/usr/bin/trcc-ram-access"
#: The first line of the rule ``programs.trcc-linux.ramLighting`` declares on
#: NixOS (``packaging/nixos/``, gated equal by a test).
NIXOS_HEADER = ("# TRCC RAM lighting -- declared by programs.trcc-linux.ramLighting "
                "in the NixOS configuration.")
NIXOS_OPTION = "programs.trcc-linux.ramLighting"
#: Turning OFF only takes access away, so its own polkit action asks the
#: person at the seat for no password (``assets/trcc-ram-access-off``).
HELPER_OFF = "/usr/bin/trcc-ram-access-off"
#: The helper as the package ships it -- what root runs when /usr/bin has none.
HELPER_ASSET = Path(__file__).resolve().parents[2] / "assets" / "trcc-ram-access"

# pkexec's own exits: the user dismissed the prompt, or polkit said no.
_PKEXEC_DISMISSED, _PKEXEC_REFUSED = 126, 127


def _effective_root() -> bool:
    """Whether this process runs as root (``sudo``, ``pkexec``, a root login)."""
    root = os.geteuid() == 0
    log.debug("_effective_root: %s", root)
    return root


class LinuxRamAccess(RamAccess):
    """Where this user stands on the RAM bus, and the helper that changes it."""

    #: The first line that makes the rule file TRCC's.
    _HEADER: ClassVar[str] = HEADER

    def __init__(self, pkexec: Callable[[str], list[str] | None],
                 *, find: Callable[[], tuple[int, ...]] = find_smbus,
                 dev: Path = Path("/dev"), rule: Path = RULE_PATH,
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 is_root: Callable[[], bool] = _effective_root,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        log.debug("LinuxRamAccess: rule=%s", rule)
        self._pkexec, self._find, self._dev = pkexec, find, dev
        self._rule, self._run, self._is_root = rule, run, is_root
        self._which = which

    def status(self) -> RamAccessStatus:
        state = self._state()
        status = RamAccessStatus(state, _MESSAGES[state],
                                 self._command_for("enable"))
        log.info("LinuxRamAccess.status: %s", status)
        return status

    def enable(self) -> RamAccessStatus:
        log.info("LinuxRamAccess.enable")
        return self._change("enable")

    def disable(self) -> RamAccessStatus:
        log.info("LinuxRamAccess.disable")
        return self._change("disable")

    # ── Reading where the user stands ────────────────────────────────

    def _state(self) -> RamAccessState:
        numbers = self._find()
        if not numbers:
            log.info("_state: no chipset SMBus")
            return RamAccessState.NO_BUS
        ours = self._ours()
        if self._is_root():
            # Root reaches every node, so access says nothing about the user:
            # only the rule file does.
            log.info("_state: root -- rule installed=%s", ours)
            return RamAccessState.ON if ours else RamAccessState.OFF
        access = all(os.access(self._dev / f"i2c-{n}", os.R_OK | os.W_OK)
                     for n in numbers)
        log.info("_state: buses=%s access=%s ours=%s", numbers, access, ours)
        if access:
            return RamAccessState.ON if ours else RamAccessState.ELSEWHERE
        return RamAccessState.NOT_APPLIED if ours else RamAccessState.OFF

    def _ours(self) -> bool:
        """Whether TRCC's rule is installed -- by its header, never its name."""
        try:
            first = self._rule.read_text(encoding="utf-8").partition("\n")[0]
        except OSError:
            log.debug("_ours: no %s", self._rule)
            return False
        log.debug("_ours: %s", first == self._HEADER)
        return first == self._HEADER

    def _command_for(self, action: str) -> str:
        """Empty when this App can do *action* itself, else the command."""
        can = self._argv(action) is not None
        log.debug("_command_for: %s here=%s", action, can)
        return "" if can else sudo_command(action)

    # ── Changing it ──────────────────────────────────────────────────

    def _argv(self, action: str) -> list[str] | None:
        """How to run the helper from here, or None if nothing can."""
        helper_file = [sys.executable, "-I", str(HELPER_ASSET), action]
        if self._is_root():
            log.info("_argv: root runs the packaged helper file directly")
            return helper_file
        if (action == "disable"
                and (off := self._pkexec(HELPER_OFF)) is not None):
            log.info("_argv: pkexec, the password-free turn-off")
            return off
        if (packaged := self._pkexec(HELPER)) is not None:
            log.info("_argv: pkexec, TRCC's own polkit action")
            return [*packaged, action]
        if (pkexec := self._which("pkexec")) is not None:
            log.info("_argv: pkexec, polkit's run-as-administrator action")
            return [pkexec, *helper_file]
        log.info("_argv: no polkit here")
        return None

    def _change(self, action: str) -> RamAccessStatus:
        argv = self._argv(action)
        if argv is None:
            command = sudo_command(action)
            log.warning("LinuxRamAccess: cannot %s RAM access from here -- "
                        "run: %s", action, command)
            return RamAccessStatus(self._state(),
                                   f"Run in a terminal: {command}", command)
        try:
            # No timeout: pkexec returns when the password is typed (0), the
            # prompt is closed or cancelled (126), refused (127), or there is
            # no prompt to show at all (127, at once).  A cap would report a
            # failure that has not happened to someone who stepped away.
            result = self._run(argv, capture_output=True, text=True,
                               check=False)
            code = result.returncode
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("LinuxRamAccess: %s failed to run -- %s: %s", argv[0],
                        type(e).__name__, e)
            code = -1
        log.info("LinuxRamAccess: %s exited %d", action, code)
        state = self._state()
        return RamAccessStatus(state, _outcome(action, code, state),
                               self._command_for(action))


def sudo_command(action: str) -> str:
    """The terminal command that runs the helper as root, on any install."""
    command = f"sudo {sys.executable} -I {HELPER_ASSET} {action}"
    log.debug("sudo_command: %s", command)
    return command


_MESSAGES = {
    RamAccessState.ON: "RAM lighting is on",
    RamAccessState.ELSEWHERE: ("TRCC can reach the RAM through another "
                               "program's permission (OpenRGB's, say)"),
    RamAccessState.OFF: "RAM lighting is off",
    RamAccessState.NOT_APPLIED: ("RAM lighting is installed but not active in "
                                 "this session -- log out and back in"),
    RamAccessState.NO_BUS: "No memory bus (chipset SMBus) found",
    RamAccessState.UNSUPPORTED: "RAM lighting is not available here yet",
}


def _outcome(action: str, code: int, state: RamAccessState) -> str:
    """What happened, in words, for the helper's exit *code*."""
    log.debug("_outcome: %s %d %s", action, code, state.value)
    if code in (_PKEXEC_DISMISSED, _PKEXEC_REFUSED):
        return "Cancelled -- nothing was changed"
    if code == 0 or state in (RamAccessState.ON, RamAccessState.OFF):
        return _MESSAGES[state]
    return (f"Could not {action} RAM lighting (exit {code}) -- "
            f"{_MESSAGES[state].lower()}")


def _nixos_line(enabled: bool) -> str:
    """The configuration line that switches it, on NixOS."""
    line = f"{NIXOS_OPTION} = {'true' if enabled else 'false'};"
    log.debug("_nixos_line: %s", line)
    return line


def _nixos_how(enabled: bool) -> str:
    """How to switch it on NixOS, in words."""
    log.debug("_nixos_how: %s", enabled)
    return (f"set {_nixos_line(enabled)} in your NixOS configuration, run "
            "sudo nixos-rebuild switch, then restart")


class NixosRamAccess(LinuxRamAccess):
    """NixOS: /etc is built from the system configuration, read-only, so the
    helper cannot write its rule -- the grant is declared instead, by the
    flake's ``programs.trcc-linux.ramLighting``, and status reads that rule
    the same way.  Nothing here runs a helper: switching it names the line.

    Off reads ``UNSUPPORTED``: no button can switch it from here, and every
    window shows that state as its message alone.
    """

    _HEADER: ClassVar[str] = NIXOS_HEADER

    def status(self) -> RamAccessStatus:
        state = self._state()
        if state is RamAccessState.OFF:
            status = RamAccessStatus(
                RamAccessState.UNSUPPORTED,
                f"RAM lighting is off -- to enable it, {_nixos_how(True)}",
                _nixos_line(True))
        else:
            status = RamAccessStatus(state, _MESSAGES[state])
        log.info("NixosRamAccess.status: %s", status)
        return status

    def _change(self, action: str) -> RamAccessStatus:
        enabled = action == "enable"
        state = self.status().state
        log.warning("NixosRamAccess: %s is declared, not switched -- %s", action,
                    _nixos_line(enabled))
        return RamAccessStatus(
            state, f"RAM lighting is part of your NixOS configuration -- to "
                   f"{action} it, {_nixos_how(enabled)}", _nixos_line(enabled))
