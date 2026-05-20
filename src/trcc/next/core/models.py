"""Domain models — frozen dataclasses + enums.  No logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

# =========================================================================
# Wire protocols and device kinds
# =========================================================================


class Wire(str, Enum):
    """The wire protocol a device speaks over USB."""
    SCSI = "scsi"
    HID = "hid"
    BULK = "bulk"
    LY = "ly"
    LED = "led"


class Kind(str, Enum):
    """High-level device kind visible to UIs."""
    LCD = "lcd"
    LED = "led"


Orientation = Literal[0, 90, 180, 270]
NativeOrientation = Literal["landscape", "portrait"]
TempUnit = Literal["C", "F"]


class FitMode(str, Enum):
    """How a background image/video fits the device canvas.

    WIDTH   — scale to fill width, letterbox top/bottom  (C# buttonTPJCW)
    HEIGHT  — scale to fill height, pillarbox left/right (C# buttonTPJCH)
    STRETCH — fill both axes, distorting aspect
    """
    WIDTH = "width"
    HEIGHT = "height"
    STRETCH = "stretch"


# =========================================================================
# LED styles / segment displays (categorical, enumerable)
# =========================================================================


class LedStyle(str, Enum):
    """LED strip layout style (affects color remap + segment mask).

    Names mirror legacy ``LED_STYLES`` model_name field. Order matches
    legacy style_id (1..12) so the PM-byte → style map in
    ``core/led_protocol.py`` can express its lookups symbolically.
    """
    AX120 = "ax120"     # legacy style_id 1
    PA120 = "pa120"     # legacy style_id 2
    AK120 = "ak120"     # legacy style_id 3
    LC1 = "lc1"         # legacy style_id 4
    LF8 = "lf8"         # legacy style_id 5
    LF12 = "lf12"       # legacy style_id 6
    LF10 = "lf10"       # legacy style_id 7
    CZ1 = "cz1"         # legacy style_id 8
    LC2 = "lc2"         # legacy style_id 9
    LF11 = "lf11"       # legacy style_id 10
    LF15 = "lf15"       # legacy style_id 11
    LF13 = "lf13"       # legacy style_id 12


# =========================================================================
# Product registry entry — immutable hardware description
# =========================================================================


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """One row in the hardware registry.

    Everything known about a specific VID/PID combination at compile time:
    vendor/product strings, wire protocol, native resolution, supported
    rotations, LED style if applicable.
    """
    vid: int
    pid: int
    vendor: str
    product: str
    wire: Wire
    kind: Kind
    device_type: int = 1
    fbl: int | None = None
    native_resolution: tuple[int, int] = (0, 0)
    orientations: tuple[int, ...] = (0,)
    native_orientation: NativeOrientation = "landscape"
    led_style: LedStyle | None = None

    @property
    def key(self) -> str:
        """Stable identifier: '0402:3922'."""
        return f"{self.vid:04x}:{self.pid:04x}"


# =========================================================================
# Runtime discovery — what the OS tells us exists right now
# =========================================================================


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Live device, produced by Platform.scan_devices().

    Matches a ProductInfo by (vid, pid); the path differs per enumeration.
    """
    vid: int
    pid: int
    path: str | None = None
    serial: str | None = None

    @property
    def key(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"


# =========================================================================
# Handshake results
# =========================================================================


@dataclass(frozen=True, slots=True)
class HandshakeResult:
    """Result of Device.connect() — the raw device-reported state."""
    resolution: tuple[int, int]
    model_id: int
    serial: str = ""
    pm_byte: int = 0
    sub_byte: int = 0
    fbl: int | None = None
    raw_response: bytes = b""


@dataclass(frozen=True, slots=True)
class LedHandshakeResult:
    """LED handshake — style + model identifier."""
    pm: int
    sub_type: int
    style: LedStyle | None = None
    model_name: str = ""
    style_sub: int = 0
    raw_response: bytes = b""


# =========================================================================
# Frame & theme models
# =========================================================================


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Decoded video frame — RGB24 bytes.  Handed to Renderer."""
    data: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Theme:
    """A theme loaded from disk — path + config blob."""
    path: Path
    name: str
    resolution: tuple[int, int]
    config: dict = field(default_factory=dict)


# =========================================================================
# Sensor readings
# =========================================================================


@dataclass(frozen=True, slots=True)
class SensorReading:
    """One sensor's current value with metadata."""
    sensor_id: str
    category: str          # "cpu_temp", "gpu_temp", "fan", etc.
    value: float
    unit: str
    label: str = ""


# =========================================================================
# Per-device user settings (mutable, persisted)
# =========================================================================


@dataclass
class DeviceSettings:
    """User prefs for one device.  Persisted to config.json."""
    orientation: int = 0
    brightness: int = 100
    current_theme: str | None = None
    time_format: Literal["12h", "24h"] = "24h"
    date_format: str = "yyyy/MM/dd"
    temp_unit: TempUnit = "C"
    overlay_enabled: bool = True
    mask_position: tuple[int, int] | None = None
    fit_mode: FitMode = FitMode.WIDTH
    # Split-mode (Dynamic Island) style for 1600×720 widescreen panels.
    # 0 = off (no split overlay), 1/2/3 = style A/B/C.  Ignored by
    # rendering on devices whose profile.resolution isn't widescreen.
    split_mode: int = 0
    # User-supplied mask path that overrides the active theme's mask.
    # None = use the theme's bundled mask (or no mask if the theme has none).
    mask_path: str | None = None
    # Show / hide the mask overlay regardless of which mask source is active.
    mask_visible: bool = True
