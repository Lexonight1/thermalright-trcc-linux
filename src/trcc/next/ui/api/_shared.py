"""Shared helpers for API routers — converters between Command Results
and Pydantic response schemas."""
from __future__ import annotations

from fastapi import HTTPException

from ...core.models import HandshakeResult, ProductInfo, SensorReading
from ...core.results import (
    BrightnessResult,
    ConnectResult,
    DisconnectResult,
    DiscoverResult,
    FitModeResult,
    GpuDeviceResult,
    LanguageResult,
    LedColorsResult,
    OrientationResult,
    OverlayResult,
    RefreshIntervalResult,
    RenderResult,
    Result,
    SendResult,
    SensorsResult,
    SetupResult,
    SplitModeResult,
    TempUnitResult,
    ThemeResult,
)
from .schemas import (
    BrightnessResponse,
    ConnectResponse,
    DisconnectResponse,
    DiscoverResponse,
    FitModeResponse,
    GpuDeviceResponse,
    HandshakeSchema,
    LanguageResponse,
    LedColorsResponse,
    OrientationResponse,
    OverlayResponse,
    ProductSchema,
    RefreshIntervalResponse,
    RenderResponse,
    SendResponse,
    SensorReadingSchema,
    SensorsResponse,
    SetupResponse,
    SplitModeResponse,
    TempUnitResponse,
    ThemeResponse,
)

# =========================================================================
# Converters
# =========================================================================


def product_to_schema(p: ProductInfo) -> ProductSchema:
    return ProductSchema(
        key=p.key, vid=p.vid, pid=p.pid,
        vendor=p.vendor, product=p.product,
        wire=p.wire.value, kind=p.kind.value,
        native_resolution=p.native_resolution,
        orientations=p.orientations,
        native_orientation=p.native_orientation,
    )


def handshake_to_schema(h: HandshakeResult | None) -> HandshakeSchema | None:
    if h is None:
        return None
    return HandshakeSchema(
        resolution=h.resolution,
        model_id=h.model_id,
        serial=h.serial,
        pm_byte=h.pm_byte,
        sub_byte=h.sub_byte,
        fbl=h.fbl,
    )


def sensor_to_schema(r: SensorReading) -> SensorReadingSchema:
    return SensorReadingSchema(
        sensor_id=r.sensor_id,
        category=r.category,
        value=r.value,
        unit=r.unit,
        label=r.label,
    )


def to_discover_response(result: DiscoverResult) -> DiscoverResponse:
    return DiscoverResponse(
        ok=result.ok, message=result.message,
        products=[product_to_schema(p) for p in result.products],
    )


def to_connect_response(result: ConnectResult) -> ConnectResponse:
    return ConnectResponse(
        ok=result.ok, message=result.message,
        key=result.key,
        handshake=handshake_to_schema(result.handshake),
    )


def to_disconnect_response(result: DisconnectResult) -> DisconnectResponse:
    return DisconnectResponse(ok=result.ok, message=result.message, key=result.key)


def to_orientation_response(result: OrientationResult) -> OrientationResponse:
    return OrientationResponse(
        ok=result.ok, message=result.message,
        key=result.key, degrees=result.degrees,
    )


def to_brightness_response(result: BrightnessResult) -> BrightnessResponse:
    return BrightnessResponse(
        ok=result.ok, message=result.message,
        key=result.key, percent=result.percent,
    )


def to_theme_response(result: ThemeResult) -> ThemeResponse:
    return ThemeResponse(
        ok=result.ok, message=result.message,
        key=result.key, theme_name=result.theme_name,
    )


def to_render_response(result: RenderResult) -> RenderResponse:
    return RenderResponse(
        ok=result.ok, message=result.message,
        key=result.key,
        bytes_sent=result.bytes_sent,
        theme_name=result.theme_name,
    )


def to_send_response(result: SendResult) -> SendResponse:
    return SendResponse(
        ok=result.ok, message=result.message,
        key=result.key,
        bytes_sent=result.bytes_sent,
    )


def to_fit_mode_response(result: FitModeResult) -> FitModeResponse:
    return FitModeResponse(
        ok=result.ok, message=result.message,
        key=result.key, mode=result.mode,
    )


def to_overlay_response(result: OverlayResult) -> OverlayResponse:
    return OverlayResponse(
        ok=result.ok, message=result.message,
        key=result.key, enabled=result.enabled,
    )


def to_split_mode_response(result: SplitModeResult) -> SplitModeResponse:
    return SplitModeResponse(
        ok=result.ok, message=result.message,
        key=result.key, mode=result.mode,
    )


def to_led_response(result: LedColorsResult) -> LedColorsResponse:
    return LedColorsResponse(
        ok=result.ok, message=result.message,
        key=result.key, colors=result.colors,
    )


def to_sensors_response(result: SensorsResult) -> SensorsResponse:
    return SensorsResponse(
        ok=result.ok, message=result.message,
        readings=[sensor_to_schema(r) for r in result.readings],
    )


def to_setup_response(result: SetupResult) -> SetupResponse:
    return SetupResponse(
        ok=result.ok, message=result.message,
        exit_code=result.exit_code,
        warnings=result.warnings,
    )


def to_temp_unit_response(result: TempUnitResult) -> TempUnitResponse:
    return TempUnitResponse(
        ok=result.ok, message=result.message, unit=result.unit,
    )


def to_language_response(result: LanguageResult) -> LanguageResponse:
    return LanguageResponse(
        ok=result.ok, message=result.message, language=result.language,
    )


def to_gpu_device_response(result: GpuDeviceResult) -> GpuDeviceResponse:
    return GpuDeviceResponse(
        ok=result.ok, message=result.message, gpu_key=result.gpu_key,
    )


def to_refresh_interval_response(result: RefreshIntervalResult) -> RefreshIntervalResponse:
    return RefreshIntervalResponse(
        ok=result.ok, message=result.message, seconds=result.seconds,
    )


# =========================================================================
# Error handling
# =========================================================================


def http_error_if_failed(result: Result, status_code: int = 400) -> None:
    """Raise HTTPException with the result message if ok is False."""
    if not result.ok:
        raise HTTPException(status_code=status_code, detail=result.message)
