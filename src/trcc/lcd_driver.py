#!/usr/bin/env python3
"""
Unified LCD Driver
Combines device detection with implementation-specific protocols.
"""

import binascii
import os
import struct
import subprocess
import tempfile
from typing import Optional

try:
    from .device_detector import DetectedDevice, detect_devices, get_default_device
    from .device_implementations import LCDDeviceImplementation, get_implementation
    from .paths import require_sg_raw
except ImportError:
    from trcc.device_detector import (  # type: ignore[no-redef]
        DetectedDevice,
        detect_devices,
        get_default_device,
    )
    from trcc.device_implementations import (  # type: ignore[no-redef]
        LCDDeviceImplementation,
        get_implementation,
    )
    from trcc.paths import require_sg_raw  # type: ignore[no-redef]


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

    def _crc32(self, data: bytes) -> int:
        """Calculate CRC32"""
        return binascii.crc32(data) & 0xFFFFFFFF

    def _build_header(self, cmd: int, size: int) -> bytes:
        """Build 20-byte SCSI command header"""
        header_16 = struct.pack('<I', cmd) + b'\x00' * 8 + struct.pack('<I', size)
        crc = self._crc32(header_16)
        return header_16 + struct.pack('<I', crc)

    @staticmethod
    def _check_sg_raw():
        """Verify sg_raw is available, raise helpful error if not."""
        require_sg_raw()

    def _scsi_read(self, cdb: bytes, length: int) -> bytes:
        """Execute SCSI READ command"""
        if not self.device_path:
            raise RuntimeError("No device path available")
        self._check_sg_raw()

        cdb_hex = ' '.join(f'{b:02x}' for b in cdb)
        cmd = ['sg_raw', '-r', str(length), self.device_path] + cdb_hex.split()

        result = subprocess.run(cmd, capture_output=True, timeout=10)
        return result.stdout if result.returncode == 0 else b''

    def _scsi_write(self, header: bytes, data: bytes) -> bool:
        """Execute SCSI WRITE command"""
        if not self.device_path:
            raise RuntimeError("No device path available")
        self._check_sg_raw()

        cdb = list(header[:16])
        cdb_hex = ' '.join(f'{b:02x}' for b in cdb)

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            tmp_path = f.name

        cmd = ['sg_raw', '-s', str(len(data)), '-i', tmp_path, self.device_path] + cdb_hex.split()

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        finally:
            os.unlink(tmp_path)

    def init_device(self):
        """Initialize device (call once at startup)"""
        if self.initialized:
            return
        assert self.implementation is not None

        # Step 1: Poll device
        poll_cmd, poll_size = self.implementation.get_poll_command()
        poll_header = self._build_header(poll_cmd, poll_size)
        self._scsi_read(poll_header[:16], poll_size)

        # Step 2: Init
        init_cmd, init_size = self.implementation.get_init_command()
        init_header = self._build_header(init_cmd, init_size)
        self._scsi_write(init_header, b'\x00' * init_size)

        self.initialized = True

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
        offset = 0
        for cmd, size in chunks:
            header = self._build_header(cmd, size)
            self._scsi_write(header, image_data[offset:offset + size])
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
