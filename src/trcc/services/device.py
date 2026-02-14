"""Device detection, selection, and frame sending service.

Pure Python, no Qt dependencies.
Absorbed from DeviceController + DeviceModel in controllers.py/models.py.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from ..core.models import DeviceInfo, LCDDeviceConfig

log = logging.getLogger(__name__)


class DeviceService:
    """Device lifecycle: detect, select, handshake, send."""

    def __init__(self) -> None:
        self._devices: list[DeviceInfo] = []
        self._selected: DeviceInfo | None = None
        self._send_lock = threading.Lock()
        self._send_busy = False

    # ── Detection ────────────────────────────────────────────────────

    def detect(self) -> list[DeviceInfo]:
        """Scan for all connected LCD/LED/Bulk devices via device_detector."""
        log.debug("DeviceService: scanning for devices...")
        try:
            from ..device_detector import DetectedDevice, DeviceDetector

            raw: list[DetectedDevice] = DeviceDetector.detect()
            self._devices = [
                DeviceInfo(
                    name=f"{d.vendor_name} {d.product_name}",
                    path=d.scsi_device or f"hid:{d.vid:04x}:{d.pid:04x}",
                    vendor=d.vendor_name,
                    product=d.product_name,
                    model=d.model,
                    vid=d.vid,
                    pid=d.pid,
                    device_index=i,
                    protocol=d.protocol,
                    device_type=d.device_type,
                    implementation=d.implementation,
                )
                for i, d in enumerate(raw)
            ]
        except ImportError:
            self._devices = []

        log.info("DeviceService: found %d device(s)", len(self._devices))
        for d in self._devices:
            log.debug("  %s [%04X:%04X] %s res=%s",
                      d.name, d.vid, d.pid, d.protocol, d.resolution)

        # Auto-select first device
        if self._devices and not self._selected:
            self._selected = self._devices[0]

        return self._devices

    # ── Selection ────────────────────────────────────────────────────

    def select(self, device: DeviceInfo) -> None:
        """Select a device."""
        self._selected = device

    @property
    def selected(self) -> DeviceInfo | None:
        """Currently selected device."""
        return self._selected

    @property
    def devices(self) -> list[DeviceInfo]:
        """List of detected devices."""
        return self._devices

    # ── Handshake ────────────────────────────────────────────────────

    def handshake(self, device: DeviceInfo) -> Any:
        """Run protocol handshake for HID/Bulk devices.

        Returns:
            HandshakeResult or None on error/import failure.
        """
        try:
            from ..device_factory import DeviceProtocolFactory

            protocol = DeviceProtocolFactory.get_protocol(device)
            if hasattr(protocol, 'handshake'):
                return protocol.handshake()
        except Exception as e:
            log.error("Handshake error: %s", e)
        return None

    # ── Send ─────────────────────────────────────────────────────────

    def send_rgb565(self, data: bytes, width: int, height: int) -> bool:
        """Send pre-converted RGB565 bytes to selected device.

        Thread-safe: only one send at a time.
        """
        with self._send_lock:
            if self._send_busy:
                log.debug("send_rgb565: already busy, skipping")
                return False
            self._send_busy = True

        try:
            from ..device_factory import DeviceProtocolFactory

            log.debug("send_rgb565: device=%s protocol=%s %dx%d (%d bytes)",
                      self._selected.path if self._selected else 'None',
                      self._selected.protocol if self._selected else 'None',
                      width, height, len(data))
            protocol = DeviceProtocolFactory.get_protocol(self._selected)
            success = protocol.send_image(data, width, height)
            log.debug("send_rgb565: send_image returned %s", success)
            return success
        except Exception as e:
            log.error("Device send error: %s", e)
            return False
        finally:
            with self._send_lock:
                self._send_busy = False

    def send_image(self, image: Any, width: int, height: int,
                   byte_order: str = '>') -> bool:
        """Convert PIL Image to RGB565 and send to device."""
        from .image import ImageService

        rgb565 = ImageService.to_rgb565(image, byte_order)
        return self.send_rgb565(rgb565, width, height)

    def send_pil(self, image: Any, width: int, height: int) -> bool:
        """Encode PIL Image for device and send.

        Bulk devices use JPEG (C# ImageToJpg), SCSI/HID use RGB565.
        """
        from .image import ImageService

        device = self._selected
        protocol = device.protocol if device else 'scsi'

        if protocol == 'bulk':
            jpeg = ImageService.to_jpeg(image)
            return self.send_rgb565(jpeg, width, height)

        byte_order = ImageService.byte_order_for(
            protocol,
            device.resolution if device else (320, 320),
        )
        return self.send_image(image, width, height, byte_order)

    def send_rgb565_async(self, data: bytes, width: int, height: int) -> None:
        """Send RGB565 bytes in a background thread. Thread-safe."""
        if self.is_busy:
            log.debug("send_rgb565_async: busy, skipping")
            return

        log.debug("send_rgb565_async: starting worker thread (%d bytes)", len(data))

        def worker():
            self.send_rgb565(data, width, height)

        threading.Thread(target=worker, daemon=True).start()

    def send_pil_async(self, image: Any, width: int, height: int) -> None:
        """Convert PIL to RGB565 and send in background thread."""
        if self.is_busy:
            return

        def worker():
            self.send_pil(image, width, height)

        threading.Thread(target=worker, daemon=True).start()

    @property
    def is_busy(self) -> bool:
        """Check if a send is in progress."""
        with self._send_lock:
            return self._send_busy

    # ── LCD resolution detection ────────────────────────────────────

    @staticmethod
    def detect_lcd_resolution(config: LCDDeviceConfig, device_path: str,
                              verbose: bool = False) -> bool:
        """Auto-detect SCSI LCD resolution via poll byte[0] → fbl_to_resolution().

        Mutates config.width/height/fbl/resolution_detected on success.
        Resolution is now detected in ScsiDevice.handshake() directly,
        so this is only needed for pre-handshake discovery.
        """
        from ..core.models import fbl_to_resolution
        from ..device_scsi import _build_header, _scsi_read

        try:
            poll_header = _build_header(0xF5, 0xE100)
            response = _scsi_read(device_path, poll_header[:16], 0xE100)
            if not response:
                if verbose:
                    log.warning("Empty poll response from %s", device_path)
                return False

            fbl = response[0]
            width, height = fbl_to_resolution(fbl)
            config.width = width
            config.height = height
            config.fbl = fbl
            config.resolution_detected = True
            if verbose:
                log.info("Auto-detected resolution: %dx%d (FBL=%d)",
                         width, height, fbl)
            return True
        except Exception as e:
            if verbose:
                log.warning("Failed to auto-detect resolution: %s", e)
            return False

    # ── Protocol info ────────────────────────────────────────────────

    def get_protocol_info(self) -> Optional[Any]:
        """Get protocol/backend info for the selected device."""
        try:
            from ..device_factory import get_protocol_info

            return get_protocol_info(self._selected)
        except ImportError:
            return None
