"""
Transport-layer ABC hierarchy for all USB device types.

Two categories of devices plug into DeviceProtocol (factory.py adapter layer):
  - Frame devices (SCSI, HID, Bulk) send image frames to LCD screens.
  - LED devices send RGB color data to LED controllers.

Both share a universal contract (UsbDevice: handshake + close), then
specialize with their data-sending method.

Hierarchy:
    UsbDevice (ABC) — handshake() + close()
    ├── FrameDevice (ABC) — + send_frame()
    │   ├── ScsiDevice
    │   ├── BulkDevice
    │   └── HidDevice (ABC)
    │       ├── HidDeviceType2
    │       └── HidDeviceType3
    └── LedDevice (ABC) — + send_data() + is_sending
        └── LedHidSender
"""

from abc import ABC, abstractmethod

from trcc.legacy.core.models import HandshakeResult


class UsbDevice(ABC):
    """Universal contract for all USB devices.

    Every device — frame-sending or LED — performs a handshake to
    negotiate capabilities and must release resources on close.
    """

    @abstractmethod
    def handshake(self) -> HandshakeResult:
        """Perform device handshake and return capabilities."""

    @abstractmethod
    def close(self) -> None:
        """Release device resources."""


class FrameDevice(UsbDevice):
    """Contract for USB LCD devices that send image frames.

    Adds send_frame() to the universal handshake + close contract.
    Not for LED devices (which send color data, not image frames).
    """

    @abstractmethod
    def send_frame(self, image_data: bytes) -> bool:
        """Send one image frame to the LCD device.

        Args:
            image_data: Frame bytes (RGB565, JPEG, etc. depending on device).

        Returns:
            True if the frame was sent successfully.
        """


class LedDevice(UsbDevice):
    """Contract for USB LED devices that send RGB color data.

    Adds send_data() and is_sending to the universal contract.
    Not for LCD frame devices (which send image frames, not LED data).
    """

    @abstractmethod
    def send_data(self, packet: bytes) -> bool:
        """Send an LED data packet to the device.

        Args:
            packet: Complete LED packet (header + RGB payload).

        Returns:
            True if the packet was sent successfully.
        """

    @property
    @abstractmethod
    def is_sending(self) -> bool:
        """Whether a send is currently in progress."""
