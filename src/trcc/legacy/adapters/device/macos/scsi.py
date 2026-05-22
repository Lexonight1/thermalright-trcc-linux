"""macOS SCSI passthrough via USB BOT.

macOS's IOUSBMassStorageClass claims USB mass-storage exclusively and
there is no sg_raw equivalent.  We detach the kernel driver and frame
SCSI CDBs as USB Bulk-Only Transport — the shared implementation lives
in `_usb_bot_scsi.py` (also used by FreeBSD/OpenBSD).

Requires root (or a signed app with Apple entitlements) and libusb
(`brew install libusb`).
"""
from __future__ import annotations

from trcc.legacy.adapters.device._usb_bot_scsi import UsbBotScsiTransport


class MacOSScsiTransport(UsbBotScsiTransport):
    """USB BOT SCSI transport for macOS."""

    _platform_name = "macOS"
    _pyusb_install_hint = "pyusb not installed — brew install libusb && pip install pyusb"
