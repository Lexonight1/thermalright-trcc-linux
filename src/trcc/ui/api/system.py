"""/system router — setup, sensors, platform info."""
from __future__ import annotations

import logging
from dataclasses import replace

from fastapi import APIRouter, Request

from ...core.commands import (
    CheckForUpdate,
    ControlCenterSnapshot,
    DisableAutostart,
    EnableAutostart,
    GenerateDebugReport,
    GetAutostartStatus,
    GetFirstRunStatus,
    GetPlatformInfo,
    GetSensorDashboard,
    ListDevices,
    ListDisks,
    ListDiskSensors,
    ListFans,
    ListFonts,
    ListGpus,
    ListLanguages,
    ListMemorySlots,
    ListSensors,
    MarkFirstRunDone,
    RamLighting,
    ReadSensors,
    RefreshAutostart,
    RgbFollow,
    RunDoctor,
    RunHealthCheck,
    RunQuickstart,
    RunSetup,
    RunUpgrade,
    SetDiskDevice,
    SetHddEnabled,
    SetRamLighting,
    SetRgbFollow,
    SetSensorDashboard,
)
from ...core.models import Kind, PanelConfig, SensorBinding
from ...core.results import (
    AutostartResult,
    ControlCenterSnapshotResult,
    DebugReportPayload,
    DiskDeviceResult,
    DiskSensorsResult,
    DisksListResult,
    DoctorResultPayload,
    FansListResult,
    FirstRunStatusResult,
    FontsListResult,
    GpusListResult,
    HddEnabledResult,
    HealthReportResult,
    LanguageResult,
    LanguagesListResult,
    MemorySlotsResult,
    QuickstartResult,
    RamLightingResult,
    RgbFollowResult,
    SensorDashboardResult,
    SensorsListResult,
    SensorsResult,
    SetupResult,
    UpdateCheckResult,
    UpgradeResult,
)
from ._shared import http_error_if_failed
from .schemas import (
    AppStatusEntry,
    AppStatusResponse,
    AutostartRequest,
    DebugReportRequest,
    DiskDeviceRequest,
    HddEnabledRequest,
    RamLightingRequest,
    RgbFollowRequest,
    SensorDashboardRequest,
    UpgradeRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/setup")
def setup(request: Request) -> SetupResult:
    log.info("api POST /system/setup")
    # PREVIEW ONLY, deliberately.  This passed ``interactive=False`` — which
    # read as "no prompts" and MEANT "change nothing", so this route has never
    # applied anything.  Behaviour is unchanged, but it now says so: letting an
    # HTTP call write /etc/udev/rules.d and re-exec as root is a security
    # decision for the maintainer, not a bug to quietly fix.
    result = request.app.state.trcc.dispatch(RunSetup(dry_run=True))
    return result


@router.get("/sensors/catalog")
def sensor_catalog(request: Request) -> SensorsListResult:
    """Every sensor this machine can measure — identities, no values.

    /system/sensors answers "what do they read"; this answers "what exists".
    The CLI has had `system list-sensors` all along, so a script could not ask
    the one question a script most wants to ask first. (unified-UI contract)

    Declared BEFORE /sensors/{category}: FastAPI matches in declaration
    order, so a static segment must come first or "catalog" is swallowed
    as a {category} value (it was — the route returned readings).
    """
    log.info("api GET /system/sensors/catalog")
    return request.app.state.trcc.dispatch(ListSensors())


@router.get("/dashboard")
def sensor_dashboard(request: Request) -> SensorDashboardResult:
    """The sensor-dashboard layout — the grid the GUI's System Info screen edits.

    Stored at ``<config_dir>/system_config.json``.  Until this route existed
    the file was readable by exactly one UI: the desktop GUI imported the
    persistence adapter directly, so no script could see a user's panel
    layout, let alone build one. (unified-UI contract)

    Non-destructive: unbound rows are auto-mapped against this machine's
    sensors on every read and NOT written back, so the answer heals itself
    when hardware changes.  ``auto_mapped`` reports how many rows that filled
    — non-zero means what you are holding differs from what is on disk.

    Declared BEFORE /sensors/{category} is not a concern here (different
    prefix), but it IS why the catalog route above sits where it does.
    """
    log.info("api GET /system/dashboard")
    return request.app.state.trcc.dispatch(GetSensorDashboard())


@router.post("/dashboard")
def set_sensor_dashboard(body: SensorDashboardRequest,
                         request: Request) -> SensorDashboardResult:
    """Replace the sensor-dashboard layout wholesale.

    One bulk verb covers rebind / add / delete / rename — the same shape
    ``POST /display/overlay/config`` uses, and the same reason: the caller
    already holds the whole grid it edited.  An empty ``panels`` list is
    refused (it would make the next read fall back to defaults — a wipe
    dressed up as a write), which surfaces as ``ok=false``.
    """
    log.info("api POST /system/dashboard: panels=%d", len(body.panels))
    result = request.app.state.trcc.dispatch(SetSensorDashboard(
        panels=tuple(
            PanelConfig(
                category_id=panel.category_id,
                name=panel.name,
                sensors=[
                    SensorBinding(label=b.label, sensor_id=b.sensor_id,
                                  unit=b.unit)
                    for b in panel.sensors
                ],
            )
            for panel in body.panels
        ),
    ))
    # A refusal is a client error (an empty layout), so it gets a 400 rather
    # than a 200 carrying ok=false — same shape POST /theme/cloud/{key} uses.
    http_error_if_failed(result)
    return result


@router.get("/sensors/{category}")
def sensors_by_category(category: str,
                        request: Request) -> SensorsResult:
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
    filtered = [r for r in result.readings if r.category.startswith(category)]
    # ``metrics`` is the whole-box snapshot, not per-category — it rides
    # along unfiltered so a dashboard can scope the list without losing it.
    return replace(
        result,
        readings=filtered,
        message=(f"{len(filtered)} {category!r} reading(s) "
                 f"(filtered from {len(result.readings)})"),
    )


@router.get("/sensors")
def sensors(request: Request) -> SensorsResult:
    log.info("api GET /system/sensors")
    return request.app.state.trcc.dispatch(ReadSensors())


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


@router.get("/disk-sensors")
def list_disk_sensors(request: Request) -> DiskSensorsResult:
    """Drive thermal sensors — the list ``disk_temp`` comes from."""
    log.info("api GET /system/disk-sensors")
    return request.app.state.trcc.dispatch(ListDiskSensors())


@router.post("/disk-sensors/active")
def set_active_disk(body: DiskDeviceRequest, request: Request) -> DiskDeviceResult:
    """Pin which drive supplies ``disk_temp``.  Empty key = hottest."""
    log.info("api POST /system/disk-sensors/active: key=%s", body.disk_key)
    return request.app.state.trcc.dispatch(SetDiskDevice(disk_key=body.disk_key))


@router.get("/gpus")
def list_gpus(request: Request) -> GpusListResult:
    """List GPUs exposed by the sensors aggregator."""
    log.info("api GET /system/gpus")
    result = request.app.state.trcc.dispatch(ListGpus())
    return result


@router.get("/fans")
def list_fans(request: Request) -> FansListResult:
    """List fans the sensors aggregator exposes, with live readings.

    Read-only diagnostic (#145/#207) — snapshot() maps fans to theme slots
    automatically; this just surfaces what the box exposes.
    """
    log.info("api GET /system/fans")
    result = request.app.state.trcc.dispatch(ListFans())
    return result


@router.get("/snapshot")
def snapshot(request: Request) -> ControlCenterSnapshotResult:
    """Return the AppSettings snapshot."""
    log.info("api GET /system/snapshot")
    result = request.app.state.trcc.dispatch(ControlCenterSnapshot())
    return result


@router.post("/hdd-enabled")
def hdd_enabled(body: HddEnabledRequest,
                request: Request) -> HddEnabledResult:
    """Toggle inclusion of HDD metrics in sensor broadcasts."""
    log.info("api POST /system/hdd-enabled: enabled=%s", body.enabled)
    result = request.app.state.trcc.dispatch(
        SetHddEnabled(enabled=body.enabled),
    )
    http_error_if_failed(result)
    return result


@router.get("/rgb-follow")
def rgb_follow_status(request: Request) -> RgbFollowResult:
    """What follows the cooler's colours, and what it is doing (#160)."""
    log.info("api GET /system/rgb-follow")
    return request.app.state.trcc.dispatch(RgbFollow())


@router.post("/rgb-follow")
def rgb_follow_set(body: RgbFollowRequest,
                   request: Request) -> RgbFollowResult:
    """Pick what follows the cooler's colours: off, openrgb or ram (#160)."""
    log.info("api POST /system/rgb-follow: mode=%s host=%r port=%s",
             body.mode.value, body.host, body.port)
    result = request.app.state.trcc.dispatch(SetRgbFollow(
        mode=body.mode, host=body.host, port=body.port, source=body.source,
        mapping=body.mapping,
        targets=None if body.targets is None else tuple(body.targets)))
    http_error_if_failed(result)
    return result


@router.get("/ram-lighting")
def ram_lighting_status(request: Request) -> RamLightingResult:
    """Whether TRCC may reach RGB memory -- read from files, no bus traffic."""
    log.info("api GET /system/ram-lighting")
    return request.app.state.trcc.dispatch(RamLighting())


@router.post("/ram-lighting")
def ram_lighting_set(body: RamLightingRequest,
                     request: Request) -> RamLightingResult:
    """Switch RAM lighting on or off: a password prompt at the desktop,
    answered when it is typed or closed."""
    log.info("api POST /system/ram-lighting: enabled=%s", body.enabled)
    result = request.app.state.trcc.dispatch(
        SetRamLighting(enabled=body.enabled))
    http_error_if_failed(result)
    return result


@router.get("/fonts")
def list_fonts(request: Request) -> FontsListResult:
    """List font families Qt can see."""
    log.info("api GET /system/fonts")
    result = request.app.state.trcc.dispatch(ListFonts())
    return result


@router.get("/disks")
def list_disks(request: Request) -> DisksListResult:
    """List mounted partitions — NOT the drive list `disk_temp` comes from."""
    log.info("api GET /system/disks")
    result = request.app.state.trcc.dispatch(ListDisks())
    return result


@router.get("/languages")
def list_languages(request: Request) -> LanguagesListResult:
    """Enumerate UI languages the i18n table supports."""
    log.info("api GET /system/languages")
    result = request.app.state.trcc.dispatch(ListLanguages())
    return result


@router.get("/language")
def current_language(request: Request) -> LanguageResult:
    """Return the currently active UI language (ISO 639-1 code).

    Read-only — set via ``POST /config/language``.  Legacy parity with
    ``GET /i18n/language``.
    """
    log.info("api GET /system/language")
    # Via the Command bus, not .settings — the API's App is an AppProxy
    # under TRCC_DAEMON=1, and that exposes dispatch() only (same shape
    # as #249's play-video crash).
    lang = request.app.state.trcc.dispatch(ControlCenterSnapshot()).language
    return LanguageResult(ok=True, language=lang, message=lang)


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
    # App prefs via the bus, not .settings (#249).  The comment here used to
    # say converting this route "needs an autostart-state + attached-device
    # Command" — both already existed.  Autostart now goes through
    # ``GetAutostartStatus``, and the device loop through ``ListDevices``.
    # This route is daemon-safe end to end now.
    app_settings = trcc.dispatch(ControlCenterSnapshot())
    autostart_enabled = trcc.dispatch(GetAutostartStatus()).enabled

    lcd_devices: list[AppStatusEntry] = []
    led_devices: list[AppStatusEntry] = []
    # ``DeviceEntry.kind`` is ``device.info.kind.value``, and across all 10
    # ``ALL_DEVICES`` rows ``Kind.LED`` <-> ``Wire.LED`` <-> the one ``Led``
    # adapter — so this is a faithful stand-in for the old ``device.is_led``.
    for entry_ in trcc.dispatch(ListDevices()).devices:
        entry = AppStatusEntry(
            key=entry_.key,
            product=entry_.product,
            connected=entry_.connected,
        )
        if entry_.kind == Kind.LED.value:
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


@router.get("/memory-slots")
def memory_slots(request: Request) -> MemorySlotsResult:
    """DRAM slots — identity everywhere, timings on Linux (empty = not probed)."""
    log.info("api GET /system/memory-slots")
    return request.app.state.trcc.dispatch(ListMemorySlots())


@router.get("/autostart")
def autostart_status(request: Request) -> AutostartResult:
    """Snapshot the autostart entry — whether it's installed + its path."""
    log.info("api GET /system/autostart")
    return request.app.state.trcc.dispatch(GetAutostartStatus())


@router.post("/autostart")
def set_autostart(body: AutostartRequest,
                  request: Request) -> AutostartResult:
    """Toggle the OS autostart entry (per-user, no sudo).

    Dispatches :class:`EnableAutostart` or :class:`DisableAutostart`
    based on ``body.enabled``.  Legacy parity with ``PUT /app/autostart``;
    POST in next/ so the verb is conventional for state-changing
    endpoints in this API.
    """
    log.info("api POST /system/autostart: enabled=%s target=%s",
             body.enabled, body.target)
    trcc = request.app.state.trcc
    command = (EnableAutostart(target=body.target) if body.enabled
               else DisableAutostart())
    result = trcc.dispatch(command)
    return result


@router.post("/autostart/refresh")
def refresh_autostart(request: Request) -> AutostartResult:
    """Re-render an existing autostart entry so it picks up a new launch path.

    The repair for a moved install (#201).  Does NOT enable autostart — with
    no entry installed it reports so and changes nothing, which is what
    separates it from ``POST /system/autostart {"enabled": true}``.
    """
    log.info("api POST /system/autostart/refresh")
    return request.app.state.trcc.dispatch(RefreshAutostart())


@router.get("/health")
def health(request: Request) -> HealthReportResult:
    """Run the health check suite + return structured results."""
    log.info("api GET /system/health")
    result = request.app.state.trcc.dispatch(RunHealthCheck())
    return result


@router.get("/doctor")
def doctor(request: Request) -> DoctorResultPayload:
    """Same as `/health` but adds an exit code + a rendered text view."""
    log.info("api GET /system/doctor")
    return request.app.state.trcc.dispatch(RunDoctor())


@router.post("/quickstart")
def quickstart(request: Request) -> QuickstartResult:
    """Walk the new-user happy path — doctor, then scan — as one sequence.

    ``/system/doctor`` and ``/devices`` each answer half. Nothing returned the
    SEQUENCE, so a REST client onboarding a user had to know the order and the
    stop-on-first-failure rule itself, and the CLI's `trcc quickstart` was the
    only place that knowledge existed.

    Runs no handshake of its own: the caller decides whether to connect the
    first device found, which is a user confirmation and not ours to assume.
    """
    log.info("api POST /system/quickstart")
    return request.app.state.trcc.dispatch(RunQuickstart())


@router.post("/debug-report")
def debug_report(body: DebugReportRequest,
                 request: Request) -> DebugReportPayload:
    """Generate a debug report; the rendered text comes back in the body.

    It took an ``output_path`` too, and wrote there — anywhere the process
    could, over an existing file included — until the trust-boundary pass.
    ``trcc report -o`` still writes a file, locally.
    """
    log.info("api POST /system/debug-report: log_tail_lines=%s",
             body.log_tail_lines)
    result = request.app.state.trcc.dispatch(GenerateDebugReport(
        log_tail_lines=body.log_tail_lines,
    ))
    http_error_if_failed(result)
    return result


@router.get("/check-update")
def check_update(request: Request) -> UpdateCheckResult:
    """Ask GitHub whether a newer version of trcc-linux is published."""
    log.info("api GET /system/check-update")
    result = request.app.state.trcc.dispatch(CheckForUpdate())
    return result


@router.post("/upgrade")
def upgrade(body: UpgradeRequest,
            request: Request) -> UpgradeResult:
    """The command that upgrades this TRCC install — returned, never run.

    It used to run the OS package manager as root from this request.
    ``dry_run`` in the body is still accepted and changes nothing.
    """
    log.info("api POST /system/upgrade: dry_run=%s", body.dry_run)
    result = request.app.state.trcc.dispatch(
        RunUpgrade(),
    )
    return result


@router.get("/first-run-status")
def first_run_status(request: Request) -> FirstRunStatusResult:
    """Has trcc finished onboarding on this machine?"""
    log.info("api GET /system/first-run-status")
    result = request.app.state.trcc.dispatch(GetFirstRunStatus())
    return result


@router.post("/mark-setup-done")
def mark_setup_done(request: Request) -> FirstRunStatusResult:
    """Mark the first-run flow as completed."""
    log.info("api POST /system/mark-setup-done")
    result = request.app.state.trcc.dispatch(MarkFirstRunDone())
    return result


