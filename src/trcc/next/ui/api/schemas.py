"""Pydantic request/response schemas for the REST API.

Pydantic models live here (in the UI adapter) — they're HTTP concerns,
not domain concerns.  Core ports stay framework-blind.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# =========================================================================
# Response shapes
# =========================================================================


class ProductSchema(BaseModel):
    """Flat view of ProductInfo for HTTP clients."""
    key: str
    vid: int
    pid: int
    vendor: str
    product: str
    wire: str
    kind: str
    native_resolution: tuple[int, int]
    orientations: tuple[int, ...]
    native_orientation: str


class HandshakeSchema(BaseModel):
    resolution: tuple[int, int]
    model_id: int
    serial: str = ""
    pm_byte: int = 0
    sub_byte: int = 0
    fbl: int | None = None


class ResultBase(BaseModel):
    ok: bool
    message: str = ""


class DiscoverResponse(ResultBase):
    products: list[ProductSchema] = []


class ConnectResponse(ResultBase):
    key: str = ""
    handshake: HandshakeSchema | None = None


class DisconnectResponse(ResultBase):
    key: str = ""


class OrientationResponse(ResultBase):
    key: str = ""
    degrees: int = 0


class BrightnessResponse(ResultBase):
    key: str = ""
    percent: int = 100


class ThemeResponse(ResultBase):
    key: str = ""
    theme_name: str = ""


class RenderResponse(ResultBase):
    key: str = ""
    bytes_sent: int = 0
    theme_name: str = ""


class SendResponse(ResultBase):
    key: str = ""
    bytes_sent: int = 0


class FitModeResponse(ResultBase):
    key: str = ""
    mode: str = "width"


class OverlayResponse(ResultBase):
    key: str = ""
    enabled: bool = True


class SplitModeResponse(ResultBase):
    key: str = ""
    mode: int = 0


class MaskApplyResponse(ResultBase):
    key: str = ""
    path: str = ""


class MaskPositionResponse(ResultBase):
    key: str = ""
    position: tuple[int, int] | None = None


class MaskVisibilityResponse(ResultBase):
    key: str = ""
    visible: bool = True


class ThemeExportResponse(ResultBase):
    theme_name: str = ""
    archive_path: str = ""


class ThemeImportResponse(ResultBase):
    theme_name: str = ""
    path: str = ""


class VideoResponse(ResultBase):
    key: str = ""
    path: str = ""
    frame_count: int = 0


class LedColorsResponse(ResultBase):
    key: str = ""
    colors: list[tuple[int, int, int]] = []


class SensorReadingSchema(BaseModel):
    sensor_id: str
    category: str
    value: float
    unit: str
    label: str = ""


class SensorsResponse(ResultBase):
    readings: list[SensorReadingSchema] = []


class SetupResponse(ResultBase):
    exit_code: int = 0
    warnings: list[str] = []


# ── Control-center settings ──────────────────────────────────────────


class TempUnitResponse(ResultBase):
    unit: str = "C"


class LanguageResponse(ResultBase):
    language: str = "en"


class GpuDeviceResponse(ResultBase):
    gpu_key: str | None = None


class RefreshIntervalResponse(ResultBase):
    seconds: float = 2.0


# =========================================================================
# Request bodies
# =========================================================================


class OrientationRequest(BaseModel):
    degrees: int = Field(..., ge=0, le=270)


class BrightnessRequest(BaseModel):
    percent: int = Field(..., ge=0, le=100)


class ThemeRequest(BaseModel):
    path: str


class LedColorsRequest(BaseModel):
    colors: list[tuple[int, int, int]] = Field(..., min_length=1)
    global_on: bool = True
    brightness: int = Field(100, ge=0, le=100)


class ColorRequest(BaseModel):
    """Solid-color frame request — three 0-255 channels."""
    r: int = Field(..., ge=0, le=255)
    g: int = Field(..., ge=0, le=255)
    b: int = Field(..., ge=0, le=255)


class FitModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(width|height|stretch)$")


class OverlayRequest(BaseModel):
    enabled: bool


class SplitModeRequest(BaseModel):
    mode: int = Field(..., ge=0, le=3)


class MaskApplyRequest(BaseModel):
    """Path to a mask image file (resolved server-side for security)."""
    path: str = Field(..., min_length=1)


class MaskPositionRequest(BaseModel):
    """Either both x,y set (offsets ≥ 0) or both None (clear)."""
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)


class MaskVisibilityRequest(BaseModel):
    visible: bool


class ThemeSaveRequest(BaseModel):
    """Save the device's active theme under a new name (basename only)."""
    key: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class ThemeExportRequest(BaseModel):
    """Export an existing theme by name to a server-side archive path."""
    theme_name: str = Field(..., min_length=1)
    archive_path: str = Field(..., min_length=1)


class ThemeImportRequest(BaseModel):
    """Import a theme from a server-side archive path.

    ``name`` defaults to the archive filename stem when blank.
    """
    archive_path: str = Field(..., min_length=1)
    name: str = ""


class PlayVideoRequest(BaseModel):
    """Play a video as a device's background. Server-side path."""
    path: str = Field(..., min_length=1)
    fps: int = Field(15, ge=1, le=60)


# ── Control-center settings ──────────────────────────────────────────


class TempUnitRequest(BaseModel):
    unit: str = Field(..., pattern="^[CF]$")


class LanguageRequest(BaseModel):
    language: str = Field(..., min_length=1)


class GpuDeviceRequest(BaseModel):
    gpu_key: str = Field("", description="Sensor key, e.g. 'nvidia:0'. '' = auto.")


class RefreshIntervalRequest(BaseModel):
    seconds: float = Field(..., ge=0.1, le=60.0)
