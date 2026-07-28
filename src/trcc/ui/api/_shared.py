"""Shared helpers for API routers — converters between Command Results
and Pydantic response schemas."""
from __future__ import annotations

from fastapi import HTTPException

from ...core.models import HandshakeResult, ProductInfo, SensorReading
from ...core.results import (
    ConnectResult,
    DebugReportPayload,
    DiscoverResult,
    DoctorResultPayload,
    HealthCheckEntry,
    ImportConfigResult,
    OverlayElementEntry,
    Result,
    SensorsListResult,
    SensorsResult,
    ThemeResult,
)
from .schemas import (
    ConnectResponse,
    DebugReportResponse,
    DiscoverResponse,
    DoctorResponse,
    HandshakeSchema,
    HealthCheckEntrySchema,
    ImportConfigResponse,
    OverlayElementSchema,
    ProductSchema,
    SensorCatalogResponse,
    SensorInfoSchema,
    SensorReadingSchema,
    SensorsResponse,
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


def to_theme_response(result: ThemeResult) -> ThemeResponse:
    return ThemeResponse(
        ok=result.ok, message=result.message,
        key=result.key, theme_name=result.theme_name,
    )


def _element_entry_to_schema(
    e: OverlayElementEntry,
) -> OverlayElementSchema:
    return OverlayElementSchema(
        id=e.id, type=e.type, x=e.x, y=e.y, color=e.color, size=e.size,
        bold=e.bold, italic=e.italic, text=e.text, metric=e.metric,
        format=e.format, show_unit=e.show_unit, source=e.source,
    )


def _health_entries_to_schemas(
    entries: list[HealthCheckEntry],
) -> list[HealthCheckEntrySchema]:
    return [
        HealthCheckEntrySchema(
            name=e.name, severity=e.severity,
            message=e.message, fix_hint=e.fix_hint,
        )
        for e in entries
    ]


def to_doctor_response(r: DoctorResultPayload) -> DoctorResponse:
    return DoctorResponse(
        ok=r.ok, message=r.message,
        checks=_health_entries_to_schemas(r.checks),
        fail_count=r.fail_count, warn_count=r.warn_count,
        exit_code=r.exit_code, rendered=r.rendered,
    )


def to_debug_report_response(r: DebugReportPayload) -> DebugReportResponse:
    return DebugReportResponse(
        ok=r.ok, message=r.message,
        output_path=r.output_path, rendered_text=r.rendered_text,
    )


def to_sensors_response(result: SensorsResult) -> SensorsResponse:
    return SensorsResponse(
        ok=result.ok, message=result.message,
        readings=[sensor_to_schema(r) for r in result.readings],
    )


def to_sensor_catalog_response(result: SensorsListResult) -> SensorCatalogResponse:
    return SensorCatalogResponse(
        ok=result.ok, message=result.message,
        sensors=[
            SensorInfoSchema(sensor_id=s.sensor_id, category=s.category,
                             unit=s.unit, label=s.label)
            for s in result.sensors
        ],
    )


def to_import_config_response(result: ImportConfigResult) -> ImportConfigResponse:
    return ImportConfigResponse(
        ok=result.ok, message=result.message, key=result.key,
    )


# =========================================================================
# Error handling
# =========================================================================


def http_error_if_failed(result: Result, status_code: int = 400) -> None:
    """Raise HTTPException with the result message if ok is False."""
    if not result.ok:
        raise HTTPException(status_code=status_code, detail=result.message)
