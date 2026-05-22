"""Pydantic request/response models and shared helpers for all API endpoints."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

# ── Shared helpers ────────────────────────────────────────────────────

_NON_SERIALIZABLE_KEYS = frozenset({"image", "colors"})


def dispatch_result(result: dict) -> dict:
    """Convert dispatcher result dict to JSON-safe API response. Raises 400 on failure."""
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    return {k: v for k, v in result.items() if k not in _NON_SERIALIZABLE_KEYS}


def parse_hex_or_400(hex_color: str) -> tuple[int, int, int]:
    """Parse hex color string to (r, g, b). Raises 400 on invalid format."""
    from trcc.legacy.core.models import parse_hex_color

    rgb = parse_hex_color(hex_color)
    if rgb is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid hex color (use 6-digit hex, e.g. 'ff0000')",
        )
    return rgb

# ── Device models ──────────────────────────────────────────────────────

class DeviceResponse(BaseModel):
    id: int
    name: str
    vid: int
    pid: int
    protocol: str
    resolution: tuple[int, int]
    path: str


class ThemeResponse(BaseModel):
    name: str
    category: str
    is_animated: bool
    has_config: bool
    preview_url: str = ""


class WebThemeResponse(BaseModel):
    id: str
    category: str
    preview_url: str
    has_video: bool = False
    download_url: str = ""


class WebThemeDownloadResponse(BaseModel):
    id: str
    cached_path: str
    resolution: str
    already_cached: bool = False


class VideoStatusResponse(BaseModel):
    playing: bool = False
    paused: bool = False
    progress: float = 0.0
    current_time: str = ""
    total_time: str = ""
    fps: float = 0.0
    source: str = ""
    loop: bool = False


class MaskResponse(BaseModel):
    name: str
    preview_url: str


# ── Shared request models ─────────────────────────────────────────────

class HexColorRequest(BaseModel):
    hex: str


class ModeRequest(BaseModel):
    mode: str


class ToggleRequest(BaseModel):
    on: bool


# ── Display request models ─────────────────────────────────────────────

class BrightnessRequest(BaseModel):
    level: int = Field(ge=1, le=3)


class RotationRequest(BaseModel):
    degrees: int


class SplitRequest(BaseModel):
    mode: int = Field(ge=0, le=3)


# ── LED request models ─────────────────────────────────────────────────

class LEDBrightnessRequest(BaseModel):
    level: int = Field(ge=0, le=100)


class LEDSensorRequest(BaseModel):
    source: str


class ZoneSyncRequest(BaseModel):
    enabled: bool
    interval: int | None = None


class ClockFormatRequest(BaseModel):
    is_24h: bool


class TempUnitRequest(BaseModel):
    unit: int  # 0=Celsius, 1=Fahrenheit


# ── Theme request models ───────────────────────────────────────────────

class ThemeLoadRequest(BaseModel):
    name: str
    resolution: str | None = None


class ThemeSaveRequest(BaseModel):
    name: str


class ScreencastRequest(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    fps: int = 10


class ScreencastStatusResponse(BaseModel):
    running: bool = False
    backend: str = ""
    fps: int = 0
    region: dict = {}
    frames: int = 0
