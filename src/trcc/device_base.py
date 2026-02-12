"""
Base classes for device communication handlers.

DeviceHandler defines the uniform interface shared by all protocols
(SCSI, HID LCD, LED).  HandshakeResult provides common handshake
output so callers get the same metrics regardless of transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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


class DeviceHandler(ABC):
    """Base for all low-level device communication handlers.

    Subclasses:
        ScsiDevice  — SCSI protocol via sg_raw subprocess
        HidDevice   — HID LCD Type 2 / Type 3 via USB bulk
        LedHidSender — LED RGB via HID reports
    """

    @abstractmethod
    def handshake(self) -> HandshakeResult:
        """Initialize device and return capabilities."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
        ...
