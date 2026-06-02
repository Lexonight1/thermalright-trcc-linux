"""/system router — setup, sensors, platform info."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request

from ...core.commands import (
    CheckForUpdate,
    ControlCenterSnapshot,
    DisableAutostart,
    EnableAutostart,
    GenerateDebugReport,
    GetFirstRunStatus,
    GetPlatformInfo,
    ListDisks,
    ListFonts,
    ListGpus,
    ListLanguages,
    MarkFirstRunDone,
    ReadSensors,
    RunDoctor,
    RunHealthCheck,
    RunSetup,
    RunUpgrade,
    SetHddEnabled,
)
from ._shared import (
    http_error_if_failed,
    to_control_center_snapshot_response,
    to_debug_report_response,
    to_disks_list_response,
    to_doctor_response,
    to_first_run_status_response,
    to_fonts_list_response,
    to_gpus_list_response,
    to_hdd_enabled_response,
    to_health_report_response,
    to_languages_list_response,
    to_sensors_response,
    to_setup_response,
    to_update_check_response,
    to_upgrade_response,
)
from .schemas import (
    AppStatusEntry,
    AppStatusResponse,
    AutostartRequest,
    AutostartResponse,
    ControlCenterSnapshotResponse,
    DebugReportRequest,
    DebugReportResponse,
    DisksListResponse,
    DoctorResponse,
    FirstRunStatusResponse,
    FontsListResponse,
    GpusListResponse,
    HddEnabledRequest,
    HddEnabledResponse,
    HealthReportResponse,
    LanguageResponse,
    LanguagesListResponse,
    SensorsResponse,
    SetupResponse,
    UpdateCheckResponse,
    UpgradeRequest,
    UpgradeResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/setup", response_model=SetupResponse)
def setup(request: Request) -> SetupResponse:
    log.info("api POST /system/setup")
    result = request.app.state.trcc.dispatch(RunSetup(interactive=False))
    return to_setup_response(result)


@router.get("/sensors/{category}", response_model=SensorsResponse)
def sensors_by_category(category: str,
                        request: Request) -> SensorsResponse:
    """Filter the live sensor list by category prefix.

    Convenience wrapper around ``GET /system/sensors``: keeps the same
    response shape but returns only readings whose ``.category``
    starts with the URL path component.  Useful for dashboards that
    only want CPU readings (``/system/sensors/cpu``) without paging
    through every GPU + disk + fan + memory entry.

    Empty result is a valid response (HTTP 200 with empty list); the
    caller distinguishes "category absent from the device" from
    "category typo" via the readings count.
    """
    log.info("api GET /system/sensors/{category}: category=%s", category)
    result = request.app.state.trcc.dispatch(ReadSensors())
    filtered_readings = [
        r for r in result.readings
        if r.category.startswith(category)
    ]
    response = to_sensors_response(result)
    response.readings = [
        r for r in response.readings if r.category.startswith(category)
    ]
    response.message = (
        f"{len(filtered_readings)} {category!r} reading(s) "
        f"(filtered from {len(result.readings)})"
    )
    return response


@router.get("/sensors", response_model=SensorsResponse)
def sensors(request: Request) -> SensorsResponse:
    log.info("api GET /system/sensors")
    result = request.app.state.trcc.dispatch(ReadSensors())
    return to_sensors_response(result)


@router.get("/metrics")
def metrics(request: Request) -> dict[str, float]:
    """Raw flat metric map: ``sensor_id`` → current (personalized) value.

    ``/system/sensors`` returns the categorized list with labels/units;
    this is the flat ``{id: value}`` shape scripts that read specific
    keys (e.g. ``cpu:temp``) want, without paging the list.
    """
    log.info("api GET /system/metrics")
    result = request.app.state.trcc.dispatch(ReadSensors())
    return {r.sensor_id: r.value for r in result.readings}


@router.get("/info")
def info(request: Request) -> dict:
    log.info("api GET /system/info")
    # Dispatch GetPlatformInfo rather than reaching into ``platform``
    # directly — keeps the endpoint on the Command bus so it works
    # unchanged through the daemon proxy.
    r = request.app.state.trcc.dispatch(GetPlatformInfo())
    return {
        "distro": r.distro_name,
        "install_method": r.install_method,
        "config_dir": r.config_dir,
        "data_dir": r.data_dir,
        "user_content_dir": r.user_content_dir,
        "log_file": r.log_file,
        "permissions_warnings": r.permission_warnings,
    }


@router.get("/gpus", response_model=GpusListResponse)
def list_gpus(request: Request) -> GpusListResponse:
    """List GPUs exposed by the sensors aggregator."""
    log.info("api GET /system/gpus")
    result = request.app.state.trcc.dispatch(ListGpus())
    return to_gpus_list_response(result)


@router.get("/snapshot", response_model=ControlCenterSnapshotResponse)
def snapshot(request: Request) -> ControlCenterSnapshotResponse:
    """Return the AppSettings snapshot."""
    log.info("api GET /system/snapshot")
    result = request.app.state.trcc.dispatch(ControlCenterSnapshot())
    return to_control_center_snapshot_response(result)


@router.post("/hdd-enabled", response_model=HddEnabledResponse)
def hdd_enabled(body: HddEnabledRequest,
                request: Request) -> HddEnabledResponse:
    """Toggle inclusion of HDD metrics in sensor broadcasts."""
    log.info("api POST /system/hdd-enabled: enabled=%s", body.enabled)
    result = request.app.state.trcc.dispatch(
        SetHddEnabled(enabled=body.enabled),
    )
    http_error_if_failed(result)
    return to_hdd_enabled_response(result)


@router.get("/fonts", response_model=FontsListResponse)
def list_fonts(request: Request) -> FontsListResponse:
    """List font families Qt can see."""
    log.info("api GET /system/fonts")
    result = request.app.state.trcc.dispatch(ListFonts())
    return to_fonts_list_response(result)


@router.get("/disks", response_model=DisksListResponse)
def list_disks(request: Request) -> DisksListResponse:
    """List disk partitions for the LED disk-index selector."""
    log.info("api GET /system/disks")
    result = request.app.state.trcc.dispatch(ListDisks())
    return to_disks_list_response(result)


@router.get("/languages", response_model=LanguagesListResponse)
def list_languages(request: Request) -> LanguagesListResponse:
    """Enumerate UI languages the i18n table supports."""
    log.info("api GET /system/languages")
    result = request.app.state.trcc.dispatch(ListLanguages())
    return to_languages_list_response(result)


@router.get("/language", response_model=LanguageResponse)
def current_language(request: Request) -> LanguageResponse:
    """Return the currently active UI language (ISO 639-1 code).

    Read-only — set via ``POST /config/language``.  Legacy parity with
    ``GET /i18n/language``.
    """
    log.info("api GET /system/language")
    lang = request.app.state.trcc.settings.app.language
    return LanguageResponse(ok=True, language=lang, message=lang)


@router.get("/status", response_model=AppStatusResponse)
def app_status(request: Request) -> AppStatusResponse:
    """Unified snapshot: app-level prefs + per-device attach list.

    Legacy parity with ``GET /app/status`` — single round-trip for a
    dashboard / mobile client that wants the app's full state without
    enumerating ``/system/snapshot`` + ``/devices`` + per-device routes.

    Per-device entries carry only key/product/connected; clients that
    need full state follow up with the device-specific snapshot routes.
    """
    log.info("api GET /system/status")
    trcc = request.app.state.trcc
    app_settings = trcc.settings.app
    autostart_enabled = trcc.platform.autostart().is_enabled()

    lcd_devices: list[AppStatusEntry] = []
    led_devices: list[AppStatusEntry] = []
    for device in trcc.devices.values():
        entry = AppStatusEntry(
            key=device.key,
            product=device.info.product,
            connected=device.is_connected,
        )
        if device.is_led:
            led_devices.append(entry)
        else:
            lcd_devices.append(entry)

    return AppStatusResponse(
        ok=True,
        language=app_settings.language,
        temp_unit=app_settings.temp_unit,
        hdd_enabled=app_settings.hdd_enabled,
        refresh_interval_s=app_settings.refresh_interval_s,
        active_gpu=app_settings.active_gpu,
        autostart_enabled=autostart_enabled,
        lcd_devices=lcd_devices,
        led_devices=led_devices,
        message=(f"{len(lcd_devices)} LCD + {len(led_devices)} LED, "
                 f"lang={app_settings.language}, "
                 f"temp={app_settings.temp_unit}"),
    )


@router.get("/autostart", response_model=AutostartResponse)
def autostart_status(request: Request) -> AutostartResponse:
    """Snapshot the autostart entry — whether it's installed + its path."""
    log.info("api GET /system/autostart")
    mgr = request.app.state.trcc.platform.autostart()
    enabled = mgr.is_enabled()
    return AutostartResponse(
        ok=True, enabled=enabled,
        message="autostart enabled" if enabled else "autostart disabled",
    )


@router.post("/autostart", response_model=AutostartResponse)
def set_autostart(body: AutostartRequest,
                  request: Request) -> AutostartResponse:
    """Toggle the OS autostart entry (per-user, no sudo).

    Dispatches :class:`EnableAutostart` or :class:`DisableAutostart`
    based on ``body.enabled``.  Legacy parity with ``PUT /app/autostart``;
    POST in next/ so the verb is conventional for state-changing
    endpoints in this API.
    """
    log.info("api POST /system/autostart: enabled=%s", body.enabled)
    trcc = request.app.state.trcc
    command = EnableAutostart() if body.enabled else DisableAutostart()
    result = trcc.dispatch(command)
    return AutostartResponse(
        ok=result.ok,
        enabled=result.enabled,
        path=result.path,
        message=result.message,
    )


@router.get("/health", response_model=HealthReportResponse)
def health(request: Request) -> HealthReportResponse:
    """Run the health check suite + return structured results."""
    log.info("api GET /system/health")
    result = request.app.state.trcc.dispatch(RunHealthCheck())
    return to_health_report_response(result)


@router.get("/doctor", response_model=DoctorResponse)
def doctor(request: Request) -> DoctorResponse:
    """Same as `/health` but adds an exit code + a rendered text view."""
    log.info("api GET /system/doctor")
    result = request.app.state.trcc.dispatch(RunDoctor())
    return to_doctor_response(result)


@router.post("/debug-report", response_model=DebugReportResponse)
def debug_report(body: DebugReportRequest,
                 request: Request) -> DebugReportResponse:
    """Generate a debug report bundle.

    With ``output_path`` set, the report is also written to that
    server-side path; without it, the rendered text comes back in the
    response body only.
    """
    log.info(
        "api POST /system/debug-report: output_path=%s log_tail_lines=%s",
        body.output_path, body.log_tail_lines,
    )
    out = Path(body.output_path) if body.output_path else None
    result = request.app.state.trcc.dispatch(GenerateDebugReport(
        output_path=out, log_tail_lines=body.log_tail_lines,
    ))
    http_error_if_failed(result)
    return to_debug_report_response(result)


@router.get("/check-update", response_model=UpdateCheckResponse)
def check_update(request: Request) -> UpdateCheckResponse:
    """Ask GitHub whether a newer version of trcc-linux is published."""
    log.info("api GET /system/check-update")
    result = request.app.state.trcc.dispatch(CheckForUpdate())
    return to_update_check_response(result)


@router.post("/upgrade", response_model=UpgradeResponse)
def upgrade(body: UpgradeRequest,
            request: Request) -> UpgradeResponse:
    """Upgrade trcc-linux via the detected package manager.

    Pass ``dry_run=true`` to get the command without executing it —
    GUIs should always probe with dry-run first and confirm before
    running with sudo.
    """
    log.info("api POST /system/upgrade: dry_run=%s", body.dry_run)
    result = request.app.state.trcc.dispatch(
        RunUpgrade(dry_run=body.dry_run),
    )
    return to_upgrade_response(result)


@router.get("/first-run-status", response_model=FirstRunStatusResponse)
def first_run_status(request: Request) -> FirstRunStatusResponse:
    """Has trcc finished onboarding on this machine?"""
    log.info("api GET /system/first-run-status")
    result = request.app.state.trcc.dispatch(GetFirstRunStatus())
    return to_first_run_status_response(result)


@router.post("/mark-setup-done", response_model=FirstRunStatusResponse)
def mark_setup_done(request: Request) -> FirstRunStatusResponse:
    """Mark the first-run flow as completed."""
    log.info("api POST /system/mark-setup-done")
    result = request.app.state.trcc.dispatch(MarkFirstRunDone())
    return to_first_run_status_response(result)


