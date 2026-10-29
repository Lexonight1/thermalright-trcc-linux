#!/usr/bin/env python3
"""Drive a module's USB-device grant on THIS machine -- the real helper, the
real polkit prompt, the real udev -- and show what changed, then take it back.

The App reaches a module's devices through one port, ``ModuleDevices``
(``Platform.module_devices()``); this tool calls that same port, so what it
shows is what an install and a removal do.  Two steps, each printed:

1. **grant** -- polkit asks for the password; then the rule file
   (``/etc/udev/rules.d/70-trcc-module-<key>.rules``) and each of the
   device's ``/dev`` nodes with its named-user ACL entries.
2. **revoke** -- the rule is gone and the ACL entries with it.

It refuses TRCC's own panels (``core.registry.ALL_DEVICES``): granting one to
"every program" is a second owner on its wire.  Pick a device nothing uses --
a USB hub is ideal -- and never a keyboard or a mouse: a granted hidraw node
lets every program you run read it.

    PYTHONPATH=src python3.12 dev/tools/drive_module_devices.py 05e3:0608
    PYTHONPATH=src python3.12 dev/tools/drive_module_devices.py 05e3:0608 --keep

``--keep`` stops after the grant, to look around; run it again without
``--keep`` to take it back (a grant already in place asks nothing).
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from trcc.adapters.system import current_platform
from trcc.adapters.system._module_devices import RULES_DIR
from trcc.core.models import MODULE_DEVICE
from trcc.core.registry import ALL_DEVICES

log = logging.getLogger(__name__)

_USB = Path("/sys/bus/usb/devices")
_HIDRAW = Path("/sys/class/hidraw")


def nodes(device: str) -> list[str]:
    """``/dev`` nodes of every attached *device*: its USB nodes, then the
    hidraw nodes of its OWN HID devices (``0003:VVVV:PPPP.n``) -- never those
    of a device plugged in below it, which is what the rule matches too."""
    out = []
    for dev in sorted(_USB.glob("*")):
        try:
            ids = f"{(dev / 'idVendor').read_text().strip()}:" \
                  f"{(dev / 'idProduct').read_text().strip()}"
            bus, num = int((dev / "busnum").read_text()), int((dev / "devnum").read_text())
        except (OSError, ValueError):
            continue
        if ids == device:
            out.append(f"/dev/bus/usb/{bus:03d}/{num:03d}")
    hid = "0003:" + device.upper()
    out += [f"/dev/{node.name}" for node in sorted(_HIDRAW.glob("hidraw*"))
            if (node / "device").resolve().name.rpartition(".")[0] == hid]
    return out


def show(key: str, device: str, step: str) -> None:
    """The rule file and each node's named-user ACL entries, as they are now."""
    rule = RULES_DIR / f"70-trcc-module-{key}.rules"
    print(f"\n== after {step} ==")
    print(f"{rule}: " + ("present" if rule.exists() else "absent"))
    if rule.exists():
        print("".join(f"    {line}\n" for line in rule.read_text().splitlines()), end="")
    for node in nodes(device) or ["(device not attached)"]:
        acl = subprocess.run(["getfacl", "-cpn", node], capture_output=True, text=True,
                             check=False).stdout if node.startswith("/dev") else ""
        users = [line for line in acl.splitlines()
                 if line.startswith("user:") and not line.startswith("user::")]
        print(f"  {node}: {', '.join(users) or 'no named-user entry'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("device", help="vvvv:pppp, as lsusb prints it")
    parser.add_argument("--key", default="drivetest", help="the module key the rule is for")
    parser.add_argument("--keep", action="store_true", help="grant only; do not take it back")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not MODULE_DEVICE.fullmatch(args.device):
        parser.error(f"{args.device!r} is not vvvv:pppp as lsusb prints it")
    vid, pid = (int(part, 16) for part in args.device.split(":"))
    if (vid, pid) in ALL_DEVICES:
        parser.error(f"{args.device} is a TRCC panel -- never grant one to every program")
    devices = current_platform().module_devices()
    log.info("drive: %s for %s through %s", args.device, args.key, type(devices).__name__)
    show(args.key, args.device, "nothing (before)")
    note = devices.grant(args.key, (args.device,))
    print(f"\ngrant said: {note or '(nothing -- reachable)'}")
    show(args.key, args.device, "grant")
    if args.keep:
        return 0
    devices.revoke(args.key)
    show(args.key, args.device, "revoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
