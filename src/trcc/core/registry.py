"""Product registry — `(vid, pid) → ProductInfo` map.

Adding a new device = append a row.  Zero code changes elsewhere — the
App picks the right Device subclass via its `wire` field, and every
other per-device value is pure data.

Native resolution is the raw pixel buffer the device expects.  For HID
and some SCSI devices the actual resolution is confirmed at handshake
via the PM byte; the value here is the default / product-advertised
resolution used until confirmation.
"""
from __future__ import annotations

from typing import Tuple

from .models import Kind, ProductInfo, Wire

# =========================================================================
# ALL_DEVICES — hardware registry
# =========================================================================


ALL_DEVICES: dict[Tuple[int, int], ProductInfo] = {

    # --- SCSI LCD (USB mass-storage passthrough) -----------------------
    (0x87CD, 0x70DB): ProductInfo(
        vid=0x87CD, pid=0x70DB,
        vendor="Thermalright",
        product="LCD Display (v1)",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),
    (0x0416, 0x5406): ProductInfo(
        vid=0x0416, pid=0x5406,
        vendor="Winbond",
        product="LCD Display",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),
    (0x0402, 0x3922): ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp",
        product="Frozen Warframe LCD 320x320",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),

    # --- HID LCD Type 2 ("H" variant, DA/DB/DC/DD) ---------------------
    (0x0416, 0x5302): ProductInfo(
        vid=0x0416, pid=0x5302,
        vendor="Winbond",
        product="USB Display (HID Type 2)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=2,
        native_resolution=(240, 320),
        orientations=(0, 90, 180, 270),
        native_orientation="portrait",
    ),

    # --- HID LCD Type 3 ("ALi" variant, F5 prefix) ---------------------
    (0x0418, 0x5303): ProductInfo(
        vid=0x0418, pid=0x5303,
        vendor="ALi Corp",
        product="LCD Display (HID Type 3)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),
    (0x0418, 0x5304): ProductInfo(
        vid=0x0418, pid=0x5304,
        vendor="ALi Corp",
        product="LCD Display (HID Type 3, alt)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),

    # --- Raw bulk LCD (USBLCDNew vendor-specific) ----------------------
    (0x87AD, 0x70DB): ProductInfo(
        vid=0x87AD, pid=0x70DB,
        vendor="ChiZhu Tech",
        product="GrandVision 360 AIO",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=4, fbl=72,
        native_resolution=(480, 480),
        orientations=(0, 90, 180, 270),
    ),

    # --- LY bulk LCD (Trofeo Vision 9.16 ultrawide) --------------------
    (0x0416, 0x5408): ProductInfo(
        vid=0x0416, pid=0x5408,
        vendor="Winbond",
        product="Trofeo Vision 9.16 LCD (LY)",
        wire=Wire.LY, kind=Kind.LCD,
        device_type=5, fbl=192,
        native_resolution=(1920, 462),
        orientations=(0, 180),
    ),
    (0x0416, 0x5409): ProductInfo(
        vid=0x0416, pid=0x5409,
        vendor="Winbond",
        product="Trofeo Vision 9.16 LCD (LY1)",
        wire=Wire.LY, kind=Kind.LCD,
        device_type=5, fbl=192,
        native_resolution=(1920, 462),
        orientations=(0, 180),
    ),

    # --- RGB LED controllers (HID 64-byte reports) ---------------------
    (0x0416, 0x8001): ProductInfo(
        vid=0x0416, pid=0x8001,
        vendor="Winbond",
        product="LED Controller (FormLED)",
        wire=Wire.LED, kind=Kind.LED,
        device_type=1,
        native_resolution=(0, 0),
        orientations=(0,),
        # led_style resolved at runtime from PM byte — Phase 12 maps PM → LedStyle
    ),
}


# =========================================================================
# Lookups
# =========================================================================


def find_product(vid: int, pid: int) -> ProductInfo | None:
    """Look up a product by VID/PID, or None if unknown."""
    return ALL_DEVICES.get((vid, pid))


def products_by_wire(wire: Wire) -> list[ProductInfo]:
    """Return all products using a given wire protocol."""
    return [p for p in ALL_DEVICES.values() if p.wire is wire]


def products_by_kind(kind: Kind) -> list[ProductInfo]:
    """Return all products of a given kind (LCD or LED)."""
    return [p for p in ALL_DEVICES.values() if p.kind is kind]
