"""An installed module's USB devices, on Linux: letting this seat reach them.

A module names the USB devices it drives in its ``module.json``; the user
accepts them on its install screen.  ``trcc-module-devices`` (``assets/``,
packaged to /usr/bin) then writes one udev rule for the module -- built by the
helper from the ids alone -- and ``trcc-module-devices-off`` takes it back,
with no password, since it can only remove.  Both run as every TRCC root
helper is run (``_root_helper.RootHelper``).

What is granted is read back from the rule files themselves (world-readable),
so a grant that is already in place asks for no password, and taking back a
module that was never granted runs nothing.

NixOS (``NixosModuleDevices``): /etc is built from the system configuration,
read-only, so no helper can write there; the grant is a line the user adds.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...core.ports import ModuleDevices
from ._root_helper import PKEXEC_DISMISSED, PKEXEC_REFUSED, RootHelper, effective_root

log = logging.getLogger(__name__)

#: Must equal the helper's own constants (gated by a test: the helper may
#: import nothing from trcc).
RULES_DIR = Path("/etc/udev/rules.d")
RULE_GLOB = "70-trcc-module-*.rules"
HELPER = "/usr/bin/trcc-module-devices"
HELPER_OFF = "/usr/bin/trcc-module-devices-off"
#: The helpers as the package ships them -- what root runs when /usr/bin has none.
_ASSETS = Path(__file__).resolve().parents[2] / "assets"
HELPER_ASSET = _ASSETS / "trcc-module-devices"
HELPER_OFF_ASSET = _ASSETS / "trcc-module-devices-off"
#: A device's USB line in one of the helper's rules: its two ids.
_USB_LINE = re.compile(r'ATTR\{idVendor\}=="([0-9a-f]{4})", '
                       r'ATTR\{idProduct\}=="([0-9a-f]{4})"')


def header(key: str) -> str:
    """The first line of the helper's rule for module *key*."""
    log.debug("header: %s", key)
    return (f"# TRCC module {key} -- written by trcc-module-devices; removing "
            "the module removes it.")


class LinuxModuleDevices(ModuleDevices):
    """udev rules written by ``trcc-module-devices``, one per module."""

    def __init__(self, pkexec: Callable[[str], list[str] | None], *,
                 rules_dir: Path = RULES_DIR,
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 is_root: Callable[[], bool] = effective_root,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        log.debug("LinuxModuleDevices: rules in %s", rules_dir)
        self._helper = RootHelper(HELPER, HELPER_ASSET, pkexec, HELPER_OFF,
                                  is_root, which)
        self._rules, self._run = rules_dir, run

    def granted(self, key: str) -> tuple[str, ...]:
        """The devices TRCC's rule for module *key* names -- none when there
        is no such rule, or it is not TRCC's (by its header, never its name)."""
        try:
            text = (self._rules / f"70-trcc-module-{key}.rules").read_text(encoding="utf-8")
        except OSError:
            log.debug("LinuxModuleDevices.granted: %s has no rule", key)
            return ()
        if text.partition("\n")[0] != header(key):
            log.warning("LinuxModuleDevices.granted: %s's rule is not TRCC's -- "
                        "left alone", key)
            return ()
        devices = tuple(f"{vid}:{pid}" for vid, pid in _USB_LINE.findall(text))
        log.debug("LinuxModuleDevices.granted: %s -> %s", key, devices)
        return devices

    def grant(self, key: str, devices: tuple[str, ...]) -> str:
        log.info("LinuxModuleDevices.grant: %s %s", key, devices)
        if not devices:
            self.revoke(key)
            return ""
        if set(self.granted(key)) == set(devices):
            log.info("LinuxModuleDevices.grant: %s already reaches them", key)
            return ""
        args = ["enable", key, *devices]
        if (argv := self._helper.argv(args)) is None:
            command = self._helper.sudo_command(args)
            log.warning("LinuxModuleDevices.grant: no polkit -- run: %s", command)
            return f"to let you reach its USB devices, run in a terminal: {command}"
        code = self._helper.run(argv, self._run)
        if set(self.granted(key)) == set(devices):
            # The rule is in place; a failed exit is udev not told yet.
            if code:
                log.warning("LinuxModuleDevices.grant: %s rule written, udev not "
                            "reloaded (exit %d)", key, code)
                return f"its USB devices unlock once you replug them (exit {code})"
            return ""
        if code in (PKEXEC_DISMISSED, PKEXEC_REFUSED):
            log.warning("LinuxModuleDevices.grant: %s cancelled", key)
            return "its USB devices stay locked -- the password was not given"
        log.warning("LinuxModuleDevices.grant: %s failed, exit %d", key, code)
        return f"could not unlock its USB devices (exit {code})"

    def revoke(self, key: str) -> None:
        log.info("LinuxModuleDevices.revoke: %s", key)
        if not self.granted(key):
            return
        if (argv := self._helper.argv(["disable", key], [key])) is None:
            log.warning("LinuxModuleDevices.revoke: no polkit -- %s stays granted "
                        "until: %s", key, self._helper.sudo_command(["disable", key]))
            return
        self._helper.run(argv, self._run)


class NixosModuleDevices(LinuxModuleDevices):
    """NixOS: /etc is built from the system configuration, read-only, so the
    helper cannot write a rule -- the user declares it, and nothing runs."""

    def grant(self, key: str, devices: tuple[str, ...]) -> str:
        log.warning("NixosModuleDevices.grant: %s %s -- declared, not written",
                    key, devices)
        if not devices:
            return ""
        return ("on NixOS, add a udev rule for its USB devices "
                f"({', '.join(devices)}) to services.udev.extraRules in your "
                "configuration, then run sudo nixos-rebuild switch")

    def revoke(self, key: str) -> None:
        log.info("NixosModuleDevices.revoke: %s -- nothing of TRCC's to remove", key)
