"""
Common dataclasses for device communication.

HandshakeResult provides common handshake output so callers get the same
metrics regardless of transport.  The app-level ABC is DeviceProtocol in
device_factory.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HandshakeResult:
    """Common output from any device handshake.

    Every protocol (SCSI, HID, LED) produces at least these fields.
    Protocol-specific subclasses add extra fields.
    """

    resolution: Optional[tuple[int, int]] = None
    model_id: int = 0
    serial: str = ""
    raw_response: bytes = field(default=b"", repr=False)
