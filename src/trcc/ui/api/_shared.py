"""Shared helpers for API routers — converters between Command Results
and Pydantic response schemas."""
from __future__ import annotations

from fastapi import HTTPException

from ...core.models import HandshakeResult, ProductInfo, SensorReading
from ...core.results import (
    BackgroundModeResult,
    BootAnimationResult,
    BrightnessResult,
    ClockFormatResult,
    CloudThemeLoadResult,
    CloudThemesListResult,
    ConnectResult,
    ControlCenterSnapshotResult,
    DateFormatResult,
    DebugReportPayload,
    DeleteThemeResult,
    DisconnectResult,
    DiscoverResult,
    DiskIndexResult,
    DisksListResult,
    DoctorResultPayload,
    FansListResult,
    FirstRunStatusResult,
    FitModeResult,
    FontsListResult,
    GpuDeviceResult,
    GpusListResult,
    HddEnabledResult,
    HealthCheckEntry,
    HealthReportResult,
    ImportConfigResult,
    KeepaliveResult,
    LanguageResult,
    LanguagesListResult,
    LcdSnapshotResult,
    LedColorsResult,
    LedModesListResult,
    LedSnapshotResult,
    LedStylesListResult,
    LoopVideoResult,
    MaskApplyResult,
    MaskPositionResult,
    MasksListResult,
    MaskUploadResult,
    MaskVisibilityResult,
    MemoryRatioResult,
    OrientationResult,
    OverlayBackgroundResult,
    OverlayConfigResult,
    OverlayElementDeleteResult,
    OverlayElementEntry,
    OverlayElementResult,
    OverlayResult,
    PauseVideoResult,
    RefreshIntervalResult,
    RenderResult,
    Result,
    SeekVideoResult,
    SendResult,
    SensorsListResult,
    SensorsResult,
    SetupResult,
    SlideshowResult,
    SplitModeResult,
    TempUnitResult,
    ThemeDcExportResult,
    ThemeExportResult,
    ThemeImportResult,
    ThemeResult,
    ThemesListResult,
    TimeFormatResult,
    UpdateCheckResult,
    UpgradeResult,
    VideoResult,
    WeekStartResult,
)
from .schemas import (
    BackgroundModeResponse,
    BootAnimationResponse,
    BrightnessResponse,
    ClockFormatResponse,
    CloudCategorySchema,
    CloudThemeEntrySchema,
    CloudThemeLoadResponse,
    CloudThemesListResponse,
    ConnectResponse,
    ControlCenterSnapshotResponse,
    DateFormatResponse,
    DebugReportResponse,
    DeleteThemeResponse,
    DisconnectResponse,
    DiscoverResponse,
    DiskEntrySchema,
    DiskIndexResponse,
    DisksListResponse,
    DoctorResponse,
    FanEntrySchema,
    FansListResponse,
    FileEntrySchema,
    FirstRunStatusResponse,
    FitModeResponse,
    FontsListResponse,
    GpuDeviceResponse,
    GpuEntrySchema,
    GpusListResponse,
    HandshakeSchema,
    HddEnabledResponse,
    HealthCheckEntrySchema,
    HealthReportResponse,
    ImportConfigResponse,
    KeepaliveResponse,
    LanguageEntrySchema,
    LanguageResponse,
    LanguagesListResponse,
    LcdSnapshotResponse,
    LedColorsResponse,
    LedModesListResponse,
    LedSnapshotResponse,
    LedStyleEntrySchema,
    LedStylesListResponse,
    LoopVideoResponse,
    MaskApplyResponse,
    MaskPositionResponse,
    MasksListResponse,
    MaskUploadResponse,
    MaskVisibilityResponse,
    MemoryRatioResponse,
    OrientationResponse,
    OverlayBackgroundResponse,
    OverlayConfigResponse,
    OverlayElementDeleteResponse,
    OverlayElementResponse,
    OverlayElementSchema,
    OverlayResponse,
    PauseVideoResponse,
    ProductSchema,
    RefreshIntervalResponse,
    RenderResponse,
    SeekVideoResponse,
    SendResponse,
    SensorCatalogResponse,
    SensorInfoSchema,
    SensorReadingSchema,
    SensorsResponse,
    SetupResponse,
    SlideshowResponse,
    SplitModeResponse,
    TempUnitResponse,
    ThemeDcExportResponse,
    ThemeExportResponse,
    ThemeImportResponse,
    ThemeListEntrySchema,
    ThemeResponse,
    ThemesListResponse,
    TimeFormatResponse,
    UpdateCheckResponse,
    UpgradeResponse,
    VideoResponse,
    WeekStartResponse,
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


def to_mask_apply_response(result: MaskApplyResult) -> MaskApplyResponse:
    return MaskApplyResponse(
        ok=result.ok, message=result.message,
        key=result.key, path=result.path,
    )


def to_mask_position_response(result: MaskPositionResult) -> MaskPositionResponse:
    return MaskPositionResponse(
        ok=result.ok, message=result.message,
        key=result.key, position=result.position,
    )


def to_mask_visibility_response(
    result: MaskVisibilityResult,
) -> MaskVisibilityResponse:
    return MaskVisibilityResponse(
        ok=result.ok, message=result.message,
        key=result.key, visible=result.visible,
    )


def to_theme_export_response(
    result: ThemeExportResult,
) -> ThemeExportResponse:
    return ThemeExportResponse(
        ok=result.ok, message=result.message,
        theme_name=result.theme_name,
        archive_path=result.archive_path,
    )


def to_theme_import_response(
    result: ThemeImportResult,
) -> ThemeImportResponse:
    return ThemeImportResponse(
        ok=result.ok, message=result.message,
        theme_name=result.theme_name,
        path=result.path,
    )


def to_themes_list_response(
    result: ThemesListResult,
) -> ThemesListResponse:
    return ThemesListResponse(
        ok=result.ok, message=result.message,
        directory=result.directory,
        themes=[
            ThemeListEntrySchema(
                name=t.name, resolution=t.resolution, path=t.path,
            )
            for t in result.themes
        ],
    )


def to_led_styles_list_response(
    result: LedStylesListResult,
) -> LedStylesListResponse:
    return LedStylesListResponse(
        ok=result.ok, message=result.message,
        styles=[
            LedStyleEntrySchema(
                style=s.style, model_name=s.model_name,
                pm_byte=s.pm_byte, style_sub=s.style_sub,
                segment_count=s.segment_count, zone_count=s.zone_count,
            )
            for s in result.styles
        ],
    )


def to_led_modes_list_response(
    result: LedModesListResult,
) -> LedModesListResponse:
    return LedModesListResponse(
        ok=result.ok, message=result.message, modes=result.modes,
    )


def to_gpus_list_response(result: GpusListResult) -> GpusListResponse:
    return GpusListResponse(
        ok=result.ok, message=result.message,
        gpus=[
            GpuEntrySchema(
                key=g.key, name=g.name, is_discrete=g.is_discrete,
            )
            for g in result.gpus
        ],
    )


def to_fans_list_response(result: FansListResult) -> FansListResponse:
    return FansListResponse(
        ok=result.ok, message=result.message,
        fans=[
            FanEntrySchema(
                key=f.key, name=f.name, rpm=f.rpm, percent=f.percent,
            )
            for f in result.fans
        ],
    )


def to_lcd_snapshot_response(
    result: LcdSnapshotResult,
) -> LcdSnapshotResponse:
    return LcdSnapshotResponse(
        ok=result.ok, message=result.message, key=result.key,
        orientation=result.orientation,
        brightness=result.brightness,
        current_theme=result.current_theme,
        overlay_enabled=result.overlay_enabled,
        mask_path=result.mask_path,
        mask_visible=result.mask_visible,
        mask_position=result.mask_position,
        fit_mode=result.fit_mode,
        split_mode=result.split_mode,
        time_format=result.time_format,
        date_format=result.date_format,
        temp_unit=result.temp_unit,
    )


def to_led_snapshot_response(
    result: LedSnapshotResult,
) -> LedSnapshotResponse:
    return LedSnapshotResponse(
        ok=result.ok, message=result.message, key=result.key,
        mode=result.mode, color=result.color,
        brightness=result.brightness, global_on=result.global_on,
        test_mode=result.test_mode,
        temp_source=result.temp_source, load_source=result.load_source,
        zone_sync=result.zone_sync,
        zone_sync_interval_ticks=result.zone_sync_interval_ticks,
        selected_zone=result.selected_zone,
        zone_count=result.zone_count,
        segment_count=result.segment_count,
    )


def to_control_center_snapshot_response(
    result: ControlCenterSnapshotResult,
) -> ControlCenterSnapshotResponse:
    return ControlCenterSnapshotResponse(
        ok=result.ok, message=result.message,
        language=result.language, temp_unit=result.temp_unit,
        active_device=result.active_device, active_gpu=result.active_gpu,
        refresh_interval_s=result.refresh_interval_s,
        hdd_enabled=result.hdd_enabled,
    )


def to_clock_format_response(r: ClockFormatResult) -> ClockFormatResponse:
    return ClockFormatResponse(
        ok=r.ok, message=r.message, key=r.key, is_24h=r.is_24h,
    )


def to_week_start_response(r: WeekStartResult) -> WeekStartResponse:
    return WeekStartResponse(
        ok=r.ok, message=r.message, key=r.key, sunday_first=r.sunday_first,
    )


def to_memory_ratio_response(r: MemoryRatioResult) -> MemoryRatioResponse:
    return MemoryRatioResponse(
        ok=r.ok, message=r.message, key=r.key, ratio=r.ratio,
    )


def to_disk_index_response(r: DiskIndexResult) -> DiskIndexResponse:
    return DiskIndexResponse(
        ok=r.ok, message=r.message, key=r.key, index=r.index,
    )


def to_hdd_enabled_response(r: HddEnabledResult) -> HddEnabledResponse:
    return HddEnabledResponse(
        ok=r.ok, message=r.message, enabled=r.enabled,
    )


def to_background_mode_response(
    r: BackgroundModeResult,
) -> BackgroundModeResponse:
    return BackgroundModeResponse(
        ok=r.ok, message=r.message, key=r.key, mode=r.mode,
    )


def to_overlay_background_response(
    r: OverlayBackgroundResult,
) -> OverlayBackgroundResponse:
    return OverlayBackgroundResponse(
        ok=r.ok, message=r.message, key=r.key, color=r.color,
    )


def to_pause_video_response(r: PauseVideoResult) -> PauseVideoResponse:
    return PauseVideoResponse(
        ok=r.ok, message=r.message, key=r.key, paused=r.paused,
    )


def to_seek_video_response(r: SeekVideoResult) -> SeekVideoResponse:
    return SeekVideoResponse(
        ok=r.ok, message=r.message, key=r.key,
        cursor=r.cursor, frame_count=r.frame_count,
    )


def to_loop_video_response(r: LoopVideoResult) -> LoopVideoResponse:
    return LoopVideoResponse(
        ok=r.ok, message=r.message, key=r.key, loop=r.loop,
    )


def to_delete_theme_response(r: DeleteThemeResult) -> DeleteThemeResponse:
    return DeleteThemeResponse(
        ok=r.ok, message=r.message, theme_name=r.theme_name, path=r.path,
    )


def to_mask_upload_response(r: MaskUploadResult) -> MaskUploadResponse:
    return MaskUploadResponse(
        ok=r.ok, message=r.message, key=r.key, path=r.path,
    )


def to_masks_list_response(r: MasksListResult) -> MasksListResponse:
    return MasksListResponse(
        ok=r.ok, message=r.message, directory=r.directory,
        masks=[FileEntrySchema(name=m.name, path=m.path, preview=m.preview)
               for m in r.masks],
    )


def to_fonts_list_response(r: FontsListResult) -> FontsListResponse:
    return FontsListResponse(
        ok=r.ok, message=r.message, fonts=r.fonts,
    )


def to_disks_list_response(r: DisksListResult) -> DisksListResponse:
    return DisksListResponse(
        ok=r.ok, message=r.message,
        disks=[
            DiskEntrySchema(
                index=d.index, device=d.device, mountpoint=d.mountpoint,
            )
            for d in r.disks
        ],
    )


def _element_entry_to_schema(
    e: OverlayElementEntry,
) -> OverlayElementSchema:
    return OverlayElementSchema(
        id=e.id, type=e.type, x=e.x, y=e.y, color=e.color, size=e.size,
        bold=e.bold, italic=e.italic, text=e.text, metric=e.metric,
        format=e.format, show_unit=e.show_unit, source=e.source,
    )


def to_overlay_element_response(
    r: OverlayElementResult,
) -> OverlayElementResponse:
    return OverlayElementResponse(
        ok=r.ok, message=r.message, key=r.key,
        element=_element_entry_to_schema(r.element) if r.element else None,
    )


def to_overlay_element_delete_response(
    r: OverlayElementDeleteResult,
) -> OverlayElementDeleteResponse:
    return OverlayElementDeleteResponse(
        ok=r.ok, message=r.message, key=r.key, element_id=r.element_id,
    )


def to_overlay_config_response(
    r: OverlayConfigResult,
) -> OverlayConfigResponse:
    return OverlayConfigResponse(
        ok=r.ok, message=r.message, key=r.key,
        elements=[_element_entry_to_schema(e) for e in r.elements],
    )


def to_cloud_themes_list_response(
    r: CloudThemesListResult,
) -> CloudThemesListResponse:
    return CloudThemesListResponse(
        ok=r.ok, message=r.message, category=r.category,
        categories=[
            CloudCategorySchema(prefix=c.prefix, name=c.name, count=c.count)
            for c in r.categories
        ],
        themes=[
            CloudThemeEntrySchema(
                id=t.id, category=t.category, category_name=t.category_name,
                preview=t.preview,
            )
            for t in r.themes
        ],
    )


def to_cloud_theme_load_response(
    r: CloudThemeLoadResult,
) -> CloudThemeLoadResponse:
    return CloudThemeLoadResponse(
        ok=r.ok, message=r.message, key=r.key,
        theme_id=r.theme_id, theme_path=r.theme_path,
    )


def to_languages_list_response(
    r: LanguagesListResult,
) -> LanguagesListResponse:
    return LanguagesListResponse(
        ok=r.ok, message=r.message,
        languages=[
            LanguageEntrySchema(
                code=lang.code, name=lang.name,
                translated_keys=lang.translated_keys,
            )
            for lang in r.languages
        ],
    )


def to_theme_dc_export_response(
    r: ThemeDcExportResult,
) -> ThemeDcExportResponse:
    return ThemeDcExportResponse(
        ok=r.ok, message=r.message,
        theme_name=r.theme_name, output_path=r.output_path,
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


def to_health_report_response(
    r: HealthReportResult,
) -> HealthReportResponse:
    return HealthReportResponse(
        ok=r.ok, message=r.message,
        checks=_health_entries_to_schemas(r.checks),
        fail_count=r.fail_count, warn_count=r.warn_count,
        worst_severity=r.worst_severity,
    )


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


def to_update_check_response(r: UpdateCheckResult) -> UpdateCheckResponse:
    return UpdateCheckResponse(
        ok=r.ok, message=r.message,
        local_version=r.local_version,
        latest_version=r.latest_version,
        latest_tag=r.latest_tag,
        release_url=r.release_url,
        update_available=r.update_available,
    )


def to_upgrade_response(r: UpgradeResult) -> UpgradeResponse:
    return UpgradeResponse(
        ok=r.ok, message=r.message,
        package_manager=r.package_manager,
        command=r.command,
        stdout=r.stdout, stderr=r.stderr,
        exit_code=r.exit_code,
    )


def to_slideshow_response(r: SlideshowResult) -> SlideshowResponse:
    return SlideshowResponse(
        ok=r.ok, message=r.message, key=r.key,
        enabled=r.enabled, interval_s=r.interval_s, themes=list(r.themes),
    )


def to_keepalive_response(r: KeepaliveResult) -> KeepaliveResponse:
    return KeepaliveResponse(
        ok=r.ok, message=r.message, key=r.key,
        frames_resent=r.frames_resent, bytes_resent=r.bytes_resent,
    )


def to_first_run_status_response(
    r: FirstRunStatusResult,
) -> FirstRunStatusResponse:
    return FirstRunStatusResponse(
        ok=r.ok, message=r.message,
        is_first_run=r.is_first_run, marker_path=r.marker_path,
    )


def to_video_response(result: VideoResult) -> VideoResponse:
    return VideoResponse(
        ok=result.ok, message=result.message,
        key=result.key, path=result.path,
        frame_count=result.frame_count,
    )


def to_boot_animation_response(
    result: BootAnimationResult,
) -> BootAnimationResponse:
    return BootAnimationResponse(
        ok=result.ok, message=result.message,
        key=result.key,
        frames_uploaded=result.frames_uploaded,
        frames_total=result.frames_total,
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


def to_sensor_catalog_response(result: SensorsListResult) -> SensorCatalogResponse:
    return SensorCatalogResponse(
        ok=result.ok, message=result.message,
        sensors=[
            SensorInfoSchema(sensor_id=s.sensor_id, category=s.category,
                             unit=s.unit, label=s.label)
            for s in result.sensors
        ],
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


def to_time_format_response(result: TimeFormatResult) -> TimeFormatResponse:
    return TimeFormatResponse(
        ok=result.ok, message=result.message, key=result.key, fmt=result.fmt,
    )


def to_date_format_response(result: DateFormatResult) -> DateFormatResponse:
    return DateFormatResponse(
        ok=result.ok, message=result.message, key=result.key, fmt=result.fmt,
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
