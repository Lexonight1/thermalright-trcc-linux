"""FreeBSD / OpenBSD SCSI passthrough via USB BOT.

The umass kernel driver claims USB mass-storage devices; camcontrol
can't pipe SCSI data-out reliably.  We detach the kernel driver and
frame SCSI CDBs as USB Bulk-Only Transport — the shared implementation
lives in `_usb_bot_scsi.py` (also used by macOS).

Requires: `pkg install py-pyusb` (libusb is in base FreeBSD).
"""
from __future__ import annotations

from trcc.legacy.adapters.device._usb_bot_scsi import UsbBotScsiTransport


class BSDScsiTransport(UsbBotScsiTransport):
    """USB BOT SCSI transport for FreeBSD / OpenBSD."""

    _platform_name = "BSD"
    _pyusb_install_hint = "pyusb not installed — pkg install py-pyusb"
