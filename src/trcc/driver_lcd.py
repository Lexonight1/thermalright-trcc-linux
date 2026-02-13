#!/usr/bin/env python3
"""
Unified LCD Driver
Combines device detection with implementation-specific protocols.
"""

import logging
from typing import Optional

from .device_detector import DetectedDevice, detect_devices, get_default_device
from .device_implementations import LCDDeviceImplementation, get_implementation
from .device_scsi import _build_header, _scsi_read, _scsi_write

log = logging.getLogger(__name__)


class LCDDriver:
    """Unified LCD driver with auto-detection and implementation selection"""

    def __init__(self, device_path: Optional[str] = None, vid: Optional[int] = None, pid: Optional[int] = None, auto_detect_resolution: bool = True):
        """
        Initialize LCD driver.

        Args:
            device_path: Explicit SCSI device path (e.g., '/dev/sg0')
            vid: USB Vendor ID (for manual selection)
            pid: USB Product ID (for manual selection)
            auto_detect_resolution: Auto-detect display resolution via FBL query
        """
        self.device_info: Optional[DetectedDevice] = None
        self.device_path: Optional[str] = device_path
        self.implementation: Optional[LCDDeviceImplementation] = None
        self.initialized = False

        if device_path:
            # Manual device path specified
            self._init_with_path(device_path)
        elif vid and pid:
            # Find device by VID/PID
            self._init_by_vid_pid(vid, pid)
        else:
            # Auto-detect
            self._init_auto_detect()

        # Auto-detect resolution via FBL if requested
        if auto_detect_resolution and self.device_path and self.implementation:
            self.implementation.detect_resolution(self.device_path, verbose=False)

    def _init_with_path(self, device_path: str):
        """Initialize with explicit device path"""
        self.device_path = device_path
        # Try to detect device info
        devices = detect_devices()
        for dev in devices:
            if dev.scsi_device == device_path:
                self.device_info = dev
                self.implementation = get_implementation(dev.implementation)
                return

        # Fallback to generic
        self.implementation = get_implementation("generic")

    def _init_by_vid_pid(self, vid: int, pid: int):
        """Initialize by finding device with specific VID/PID"""
        devices = detect_devices()
        for dev in devices:
            if dev.vid == vid and dev.pid == pid:
                self.device_info = dev
                self.device_path = dev.scsi_device
                self.implementation = get_implementation(dev.implementation)
                return

        raise RuntimeError(f"Device with VID={vid:04X} PID={pid:04X} not found")

    def _init_auto_detect(self):
        """Auto-detect device"""
        device = get_default_device()
        if not device:
            raise RuntimeError("No LCD device found")

        self.device_info = device
        self.device_path = device.scsi_device
        self.implementation = get_implementation(device.implementation)

    def init_device(self):
        """Initialize device (call once at startup)"""
        if self.initialized:
            return
        assert self.implementation is not None
        log.debug("Initializing LCD device at %s", self.device_path)

        assert self.device_path is not None

        # Step 1: Poll device
        poll_cmd, poll_size = self.implementation.get_poll_command()
        poll_header = _build_header(poll_cmd, poll_size)
        log.debug("Poll: cmd=0x%X, size=0x%X", poll_cmd, poll_size)
        _scsi_read(self.device_path, poll_header[:16], poll_size)

        # Step 2: Init
        init_cmd, init_size = self.implementation.get_init_command()
        init_header = _build_header(init_cmd, init_size)
        log.debug("Init: cmd=0x%X, size=0x%X", init_cmd, init_size)
        _scsi_write(self.device_path, init_header, b'\x00' * init_size)

        self.initialized = True
        log.info("LCD device initialized: %s (%s)", self.device_path,
                 self.implementation.name if self.implementation else "unknown")

    def send_frame(self, image_data: bytes, force_init: bool = False):
        """
        Send frame to display.

        Args:
            image_data: RGB565 image data (320x320x2 bytes)
            force_init: Force device initialization before frame
        """
        if not self.implementation:
            raise RuntimeError("No implementation loaded")

        # Init if needed (poll + init handshake before first frame)
        if force_init or not self.initialized:
            self.init_device()

        # Get frame chunks from implementation
        chunks = self.implementation.get_frame_chunks()
        total_size = sum(size for _, size in chunks)

        # Pad image data if needed
        if len(image_data) < total_size:
            image_data += b'\x00' * (total_size - len(image_data))

        # Send chunks
        assert self.device_path is not None
        log.debug("Sending frame: %d bytes in %d chunks", total_size, len(chunks))
        offset = 0
        for cmd, size in chunks:
            header = _build_header(cmd, size)
            _scsi_write(self.device_path, header, image_data[offset:offset + size])
            offset += size

    def create_solid_color(self, r: int, g: int, b: int) -> bytes:
        """Create solid color frame"""
        if not self.implementation:
            raise RuntimeError("No implementation loaded")

        width, height = self.implementation.resolution
        pixel = self.implementation.rgb_to_bytes(r, g, b)
        return pixel * (width * height)

    def load_image(self, path: str) -> bytes:
        """Load and convert image to device format"""
        if not self.implementation:
            raise RuntimeError("No implementation loaded")

        try:
            from PIL import Image
            assert self.implementation is not None
            width, height = self.implementation.resolution
            img = Image.open(path).convert('RGB').resize((width, height))
            data = bytearray()
            for y in range(height):
                for x in range(width):
                    r, g, b = img.getpixel((x, y))  # type: ignore[misc]
                    data.extend(self.implementation.rgb_to_bytes(r, g, b))
            return bytes(data)
        except ImportError:
            raise RuntimeError("PIL not installed. Run: pip install Pillow")

    def get_info(self) -> dict:
        """Get device and implementation info"""
        info = {
            "device_path": self.device_path,
            "initialized": self.initialized,
        }

        if self.device_info:
            info.update({
                "vendor": self.device_info.vendor_name,
                "product": self.device_info.product_name,
                "vid": f"{self.device_info.vid:04X}",
                "pid": f"{self.device_info.pid:04X}",
                "usb_path": self.device_info.usb_path,
            })

        if self.implementation:
            info.update({
                "implementation": self.implementation.name,
                "resolution": f"{self.implementation.resolution[0]}x{self.implementation.resolution[1]}",
                "pixel_format": self.implementation.pixel_format,
            })

        return info


if __name__ == '__main__':
    # Test device detection and info
    try:
        driver = LCDDriver()
        info = driver.get_info()

        print("LCD Driver initialized successfully:\n")
        for key, value in info.items():
            print(f"  {key}: {value}")

    except RuntimeError as e:
        print(f"Error: {e}")
