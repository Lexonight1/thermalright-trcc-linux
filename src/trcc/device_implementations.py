#!/usr/bin/env python3
"""
Device-specific implementations for different LCD variants.
Each implementation can override protocol details, timing, and commands.
"""

import struct
from abc import ABC, abstractmethod
from typing import Tuple


class LCDDeviceImplementation(ABC):
    """Base class for LCD device implementations"""

    def __init__(self):
        self.width = 320
        self.height = 320
        self.pixel_format = "RGB565"
        self.fbl = None  # FBL identifier (set via auto-detection)
        self._resolution_detected = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Device implementation name"""
        pass

    @property
    def resolution(self) -> Tuple[int, int]:
        """Display resolution (width, height)"""
        return (self.width, self.height)

    def get_poll_command(self) -> Tuple[int, int]:
        """Get poll command (cmd, size)"""
        return (0xF5, 0xE100)

    def get_init_command(self) -> Tuple[int, int]:
        """Get init command (cmd, size)"""
        return (0x1F5, 0xE100)

    def get_frame_chunks(self) -> list:
        """Get frame chunk commands [(cmd, size), ...]"""
        return [
            (0x101F5, 0x10000),
            (0x10101F5, 0x10000),
            (0x20101F5, 0x10000),
            (0x30101F5, 0x2000),
        ]

    def needs_init_per_frame(self) -> bool:
        """Whether device needs init before each frame"""
        return False

    def get_init_delay(self) -> float:
        """Delay after init command (seconds)"""
        return 0.0

    def get_frame_delay(self) -> float:
        """Delay between frames (seconds)"""
        return 0.0

    def rgb_to_bytes(self, r: int, g: int, b: int) -> bytes:
        """Convert RGB to device pixel format"""
        # RGB565 big-endian (default)
        pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return struct.pack('>H', pixel)

    def detect_resolution(self, device_path: str, verbose: bool = False) -> bool:
        """
        Auto-detect display resolution by querying FBL from device.

        Args:
            device_path: Path to SCSI device (e.g., '/dev/sg0')
            verbose: Print debug information

        Returns:
            True if detection succeeded, False otherwise
        """
        try:
            from .fbl_detector import detect_display_resolution  # type: ignore[import-not-found]
        except ImportError:
            try:
                from fbl_detector import detect_display_resolution  # type: ignore[import-not-found]
            except ImportError:
                if verbose:
                    print("[!] fbl_detector module not available")
                return False

        display_info = detect_display_resolution(device_path, verbose=verbose)

        if display_info:
            self.width = display_info.width
            self.height = display_info.height
            self.fbl = display_info.fbl
            self._resolution_detected = True

            if verbose:
                print(f"[✓] Auto-detected resolution: {display_info.resolution_name} (FBL={self.fbl})")

            return True

        if verbose:
            print(f"[!] Failed to auto-detect resolution, using default {self.width}x{self.height}")

        return False

    def set_resolution(self, width: int, height: int):
        """
        Manually set display resolution.

        Args:
            width: Display width in pixels
            height: Display height in pixels
        """
        self.width = width
        self.height = height
        self._resolution_detected = True


class ThermalrightLCDV1(LCDDeviceImplementation):
    """Thermalright LCD Display (VID=0x87CD, PID=0x70DB)

    This is the SCSI LCD device that appears as USBLCD.
    Uses standard SCSI protocol with RGB565 format.
    """

    @property
    def name(self) -> str:
        return "Thermalright LCD v1 (USBLCD)"

    # Uses default implementation from base class


class AliCorpLCDV1(LCDDeviceImplementation):
    """ALi Corp LCD Display (VID=0x0416, PID=0x5406)

    ALi Corp OEM version of the LCD display.
    Also appears as USBLCD via SCSI interface.
    """

    @property
    def name(self) -> str:
        return "ALi Corp LCD v1 (USBLCD)"

    # May need different timing or commands
    # Override methods here if needed


class GenericLCD(LCDDeviceImplementation):
    """Generic fallback implementation"""

    @property
    def name(self) -> str:
        return "Generic LCD"


# Device implementation registry
IMPLEMENTATIONS = {
    "thermalright_lcd_v1": ThermalrightLCDV1,
    "ali_corp_lcd_v1": AliCorpLCDV1,
    "generic": GenericLCD,
}


def get_implementation(implementation_name: str) -> LCDDeviceImplementation:
    """Get device implementation by name"""
    impl_class = IMPLEMENTATIONS.get(implementation_name, GenericLCD)
    return impl_class()


def list_implementations() -> list:
    """List all available implementations"""
    return [
        {
            "name": name,
            "class": impl_class().name
        }
        for name, impl_class in IMPLEMENTATIONS.items()
    ]


if __name__ == '__main__':
    # Print available implementations
    print("Available LCD device implementations:\n")
    for impl_info in list_implementations():
        print(f"  {impl_info['name']}: {impl_info['class']}")

    print("\nExample usage:")
    print("  from device_implementations import get_implementation")
    print("  impl = get_implementation('thermalright_v1')")
    print("  print(impl.name)")
    print("  print(impl.resolution)")
