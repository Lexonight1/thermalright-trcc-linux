"""Pydantic request/response schemas for the REST API.

Pydantic models live here (in the UI adapter) — they're HTTP concerns,
not domain concerns.  Core ports stay framework-blind.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

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
    native_resolution: Tuple[int, int]
    orientations: Tuple[int, ...]
    native_orientation: str


class HandshakeSchema(BaseModel):
    resolution: Tuple[int, int]
    model_id: int
    serial: str = ""
    pm_byte: int = 0
    sub_byte: int = 0
    fbl: Optional[int] = None


class ResultBase(BaseModel):
    ok: bool
    message: str = ""


class DiscoverResponse(ResultBase):
    products: List[ProductSchema] = []


class ConnectResponse(ResultBase):
    key: str = ""
    handshake: Optional[HandshakeSchema] = None


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


class LedColorsResponse(ResultBase):
    key: str = ""
    colors: List[Tuple[int, int, int]] = []


class SensorReadingSchema(BaseModel):
    sensor_id: str
    category: str
    value: float
    unit: str
    label: str = ""


class SensorsResponse(ResultBase):
    readings: List[SensorReadingSchema] = []


class SetupResponse(ResultBase):
    exit_code: int = 0
    warnings: List[str] = []


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
    colors: List[Tuple[int, int, int]] = Field(..., min_length=1)
    global_on: bool = True
    brightness: int = Field(100, ge=0, le=100)
