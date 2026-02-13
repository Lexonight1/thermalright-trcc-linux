"""
SCSI LCD device configuration — resolution, pixel format, frame chunking.

Single concrete class replacing the ABC + 3 empty subclasses that all
shared identical behaviour.  The only difference was the ``name`` string,
now stored as a plain attribute via a lookup dict.
"""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from .device_scsi import _get_frame_chunks

# Implementation key → display name
_IMPL_NAMES: dict[str, str] = {
    "thermalright_lcd_v1": "Thermalright LCD v1 (USBLCD)",
    "ali_corp_lcd_v1": "ALi Corp LCD v1 (USBLCD)",
    "generic": "Generic LCD",
}


class LCDDeviceImplementation:
    """SCSI LCD device — protocol commands, resolution, pixel format."""

    def __init__(self, impl_key: str = "generic"):
        self.name: str = _IMPL_NAMES.get(impl_key, "Generic LCD")
        self.width: int = 320
        self.height: int = 320
        self.pixel_format: str = "RGB565"
        self.fbl: Optional[int] = None
        self._resolution_detected: bool = False

    @property
    def resolution(self) -> Tuple[int, int]:
        """Display resolution (width, height)."""
        return (self.width, self.height)

    def get_poll_command(self) -> Tuple[int, int]:
        """Get poll command (cmd, size)."""
        return (0xF5, 0xE100)

    def get_init_command(self) -> Tuple[int, int]:
        """Get init command (cmd, size)."""
        return (0x1F5, 0xE100)

    def get_frame_chunks(self) -> list:
        """Get frame chunk commands [(cmd, size), ...] for current resolution.

        Delegates to device_scsi._get_frame_chunks() — single source of truth.
        """
        return _get_frame_chunks(self.width, self.height)

    def needs_init_per_frame(self) -> bool:
        """Whether device needs init before each frame."""
        return False

    def get_init_delay(self) -> float:
        """Delay after init command (seconds)."""
        return 0.0

    def get_frame_delay(self) -> float:
        """Delay between frames (seconds)."""
        return 0.0

    @property
    def pixel_byte_order(self) -> str:
        """RGB565 byte order: '>' big-endian or '<' little-endian.

        Windows TRCC ImageTo565 uses big-endian only for 320x320 (is320x320)
        and SPIMode=2 (FBL 51/53). All other resolutions use little-endian.
        """
        if (self.width, self.height) == (320, 320):
            return '>'
        return '<'

    def rgb_to_bytes(self, r: int, g: int, b: int) -> bytes:
        """Convert RGB to device pixel format (RGB565)."""
        pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return struct.pack(f'{self.pixel_byte_order}H', pixel)

    def detect_resolution(self, device_path: str, verbose: bool = False) -> bool:
        """Auto-detect display resolution by querying FBL from device."""
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

    def set_resolution(self, width: int, height: int) -> None:
        """Manually set display resolution."""
        self.width = width
        self.height = height
        self._resolution_detected = True

    @staticmethod
    def get(name: str) -> LCDDeviceImplementation:
        """Get device implementation by name."""
        return LCDDeviceImplementation(name)

    @staticmethod
    def list_all() -> list[dict[str, str]]:
        """List all available implementations."""
        return [
            {"name": key, "class": display_name}
            for key, display_name in _IMPL_NAMES.items()
        ]


# Backward-compat aliases
ThermalrightLCDV1 = LCDDeviceImplementation
AliCorpLCDV1 = LCDDeviceImplementation
GenericLCD = LCDDeviceImplementation
IMPLEMENTATIONS = _IMPL_NAMES
get_implementation = LCDDeviceImplementation.get
list_implementations = LCDDeviceImplementation.list_all
