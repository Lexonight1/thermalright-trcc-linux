"""Domain models — frozen dataclasses + enums.  No logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, Optional, Tuple

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
    """LED strip layout style (affects color remap + segment mask)."""
    AX120 = "ax120"
    PA120 = "pa120"
    AK120 = "ak120"
    LC1 = "lc1"
    LF8 = "lf8"
    LF12 = "lf12"
    LF10 = "lf10"
    CZ1 = "cz1"
    LC2 = "lc2"
    LF11 = "lf11"


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
    fbl: Optional[int] = None
    native_resolution: Tuple[int, int] = (0, 0)
    orientations: Tuple[int, ...] = (0,)
    native_orientation: NativeOrientation = "landscape"
    led_style: Optional[LedStyle] = None

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
    path: Optional[str] = None
    serial: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"


# =========================================================================
# Handshake results
# =========================================================================


@dataclass(frozen=True, slots=True)
class HandshakeResult:
    """Result of Device.connect() — the raw device-reported state."""
    resolution: Tuple[int, int]
    model_id: int
    serial: str = ""
    pm_byte: int = 0
    sub_byte: int = 0
    fbl: Optional[int] = None
    raw_response: bytes = b""


@dataclass(frozen=True, slots=True)
class LedHandshakeResult:
    """LED handshake — style + model identifier."""
    pm: int
    sub_type: int
    style: Optional[LedStyle] = None
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
    resolution: Tuple[int, int]
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
    current_theme: Optional[str] = None
    time_format: Literal["12h", "24h"] = "24h"
    date_format: str = "yyyy/MM/dd"
    temp_unit: TempUnit = "C"
    overlay_enabled: bool = True
    mask_position: Optional[Tuple[int, int]] = None
    fit_mode: FitMode = FitMode.WIDTH
