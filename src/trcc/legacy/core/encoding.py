"""RGB565 encoding utilities — pure functions, no I/O."""
from __future__ import annotations

import struct


def byte_order_for(protocol: str, resolution: tuple[int, int],
                   fbl: int | None = None) -> str:
    """Determine RGB565 byte order. Delegates to DeviceProfile."""
    from .models import get_profile
    if fbl is not None:
        return get_profile(fbl).byte_order
    # Fallback: 320x320 → big-endian, else little-endian
    return '>' if resolution == (320, 320) else '<'


def rgb_to_bytes(r: int, g: int, b: int, byte_order: str = '>') -> bytes:
    """Convert single RGB pixel to RGB565 bytes."""
    pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return struct.pack(f'{byte_order}H', pixel)
