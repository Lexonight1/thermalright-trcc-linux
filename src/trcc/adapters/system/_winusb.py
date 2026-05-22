"""Windows WinUSB driver diagnostic + setup instructions.

The Windows analog of the Linux ``_udev.py`` and FreeBSD ``_devd.py``
installers — but Windows can't silently install kernel drivers without
elevation and a signed driver package.  So this module's job is
**diagnose the state and give the user copy-paste-ready instructions**.

What's actually needed
----------------------
For pyusb / libusb to talk to a TRCC device on Windows, the device's
USB interface must be bound to ``WinUSB.sys`` (or ``libusbK``,
``libusb-win32``).  Windows' default behaviour for a generic USB
device is to bind it to no driver at all (or the wrong one), so libusb
fails with ``NoBackendError`` until the user runs Zadig — see
https://zadig.akeo.ie/ — to swap the driver to WinUSB.

This wizard:

  1. Tries to enumerate every device in the registry via pyusb.
  2. For each device that is **physically present but invisible to
     pyusb**, prints a one-liner naming exactly which device needs
     WinUSB and a Zadig command sequence the user can paste.
  3. Returns 0 if every present device is visible, 1 if any need
     driver work, 2 if pyusb itself isn't usable (libusb-1.0.dll
     missing from PATH — separate fix).

Zero side effects.  Read-only diagnostic.
"""
from __future__ import annotations

import logging
import sys

from ...core.registry import ALL_DEVICES

log = logging.getLogger(__name__)


_ZADIG_URL = "https://zadig.akeo.ie/"


def install(dry_run: bool = False) -> int:
    """Diagnose WinUSB binding state and print actionable steps.

    ``dry_run`` is accepted for parity with the Linux/BSD installers
    but Windows setup is read-only by design (driver installation
    requires UAC + a signed driver package — the user must run Zadig).
    """
    if not _is_windows():
        log.warning("WinUSB wizard is Windows-only — current platform: %s", sys.platform)
        return 0

    if (status := _check_pyusb_backend()) != 0:
        return status

    visible, invisible = _classify_devices()

    if visible:
        print(f"\n  [OK]  {len(visible)} TRCC device(s) visible to pyusb:")
        for vid, pid, label in visible:
            print(f"          {vid:04x}:{pid:04x}  {label}")

    if not invisible:
        print("\n  All connected TRCC devices have a working USB driver. 🍻")
        return 0

    print(f"\n  [!]   {len(invisible)} TRCC device(s) need WinUSB:")
    for vid, pid, label in invisible:
        print(f"          {vid:04x}:{pid:04x}  {label}")

    print()
    print("  To fix:")
    print(f"    1. Download Zadig from {_ZADIG_URL}")
    print("    2. Run as Administrator.")
    print("    3. Options → List All Devices.")
    print("    4. Pick each device above from the dropdown.")
    print('    5. Choose "WinUSB" as the driver and click "Replace Driver".')
    print("    6. Re-run TRCC — the handshake will succeed.")
    print()
    print("  Notes:")
    print("    • If your device is not in the dropdown, unplug + replug.")
    print("    • libusbK / libusb-win32 also work, but WinUSB is preferred.")
    print("    • Replacing a driver is reversible: Device Manager → Uninstall.")
    return 1


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _check_pyusb_backend() -> int:
    """Verify pyusb's libusb-1.0 backend is loadable.

    Returns 0 if ready, 2 if libusb itself is missing — the WinUSB
    wizard can't tell the user what to do until pyusb can talk to the
    USB stack at all.
    """
    try:
        import usb.backend.libusb1
    except ImportError:
        print("  [X]   pyusb is not installed — pip install pyusb")
        return 2

    backend = usb.backend.libusb1.get_backend()
    if backend is None:
        print("  [X]   libusb-1.0.dll not on PATH.")
        print("        TRCC's installer normally ships it next to the .exe;")
        print("        if you're running from source, install libusb manually:")
        print("          https://libusb.info/  →  Downloads → Latest Windows Binaries")
        return 2

    return 0


def _classify_devices() -> tuple[
    list[tuple[int, int, str]], list[tuple[int, int, str]],
]:
    """Split registered devices into (visible, invisible) by VID/PID.

    "Visible" means pyusb returns at least one match.  "Invisible"
    means no match — could be unplugged OR bound to a non-libusb
    driver.  We can't tell the two apart without enumerating Windows'
    own driver tree, so the instructions cover both cases.
    """
    import usb.core

    visible: list[tuple[int, int, str]] = []
    invisible: list[tuple[int, int, str]] = []
    for (vid, pid), product in sorted(ALL_DEVICES.items()):
        label = f"{product.vendor} {product.product}"
        if any(usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or []):
            visible.append((vid, pid, label))
        else:
            invisible.append((vid, pid, label))
    return visible, invisible
