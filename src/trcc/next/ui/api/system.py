"""/system router — setup, sensors, platform info."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from ...core.commands import (
    CheckForUpdate,
    ControlCenterSnapshot,
    GenerateDebugReport,
    GetFirstRunStatus,
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
    LanguagesListResponse,
    SensorsResponse,
    SetupResponse,
    UpdateCheckResponse,
    UpgradeRequest,
    UpgradeResponse,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/setup", response_model=SetupResponse)
def setup(request: Request) -> SetupResponse:
    result = request.app.state.trcc.dispatch(RunSetup(interactive=False))
    return to_setup_response(result)


@router.get("/sensors", response_model=SensorsResponse)
def sensors(request: Request) -> SensorsResponse:
    result = request.app.state.trcc.dispatch(ReadSensors())
    return to_sensors_response(result)


@router.get("/info")
def info(request: Request) -> dict:
    platform = request.app.state.trcc.platform
    return {
        "distro": platform.distro_name(),
        "install_method": platform.install_method(),
        "config_dir": str(platform.paths().config_dir()),
        "permissions_warnings": platform.check_permissions(),
    }


@router.get("/gpus", response_model=GpusListResponse)
def list_gpus(request: Request) -> GpusListResponse:
    """List GPUs exposed by the sensors aggregator."""
    result = request.app.state.trcc.dispatch(ListGpus())
    return to_gpus_list_response(result)


@router.get("/snapshot", response_model=ControlCenterSnapshotResponse)
def snapshot(request: Request) -> ControlCenterSnapshotResponse:
    """Return the AppSettings snapshot."""
    result = request.app.state.trcc.dispatch(ControlCenterSnapshot())
    return to_control_center_snapshot_response(result)


@router.post("/hdd-enabled", response_model=HddEnabledResponse)
def hdd_enabled(body: HddEnabledRequest,
                request: Request) -> HddEnabledResponse:
    """Toggle inclusion of HDD metrics in sensor broadcasts."""
    result = request.app.state.trcc.dispatch(
        SetHddEnabled(enabled=body.enabled),
    )
    http_error_if_failed(result)
    return to_hdd_enabled_response(result)


@router.get("/fonts", response_model=FontsListResponse)
def list_fonts(request: Request) -> FontsListResponse:
    """List font families Qt can see."""
    result = request.app.state.trcc.dispatch(ListFonts())
    return to_fonts_list_response(result)


@router.get("/disks", response_model=DisksListResponse)
def list_disks(request: Request) -> DisksListResponse:
    """List disk partitions for the LED disk-index selector."""
    result = request.app.state.trcc.dispatch(ListDisks())
    return to_disks_list_response(result)


@router.get("/languages", response_model=LanguagesListResponse)
def list_languages(request: Request) -> LanguagesListResponse:
    """Enumerate UI languages the i18n table supports."""
    result = request.app.state.trcc.dispatch(ListLanguages())
    return to_languages_list_response(result)


@router.get("/health", response_model=HealthReportResponse)
def health(request: Request) -> HealthReportResponse:
    """Run the health check suite + return structured results."""
    result = request.app.state.trcc.dispatch(RunHealthCheck())
    return to_health_report_response(result)


@router.get("/doctor", response_model=DoctorResponse)
def doctor(request: Request) -> DoctorResponse:
    """Same as `/health` but adds an exit code + a rendered text view."""
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
    out = Path(body.output_path) if body.output_path else None
    result = request.app.state.trcc.dispatch(GenerateDebugReport(
        output_path=out, log_tail_lines=body.log_tail_lines,
    ))
    http_error_if_failed(result)
    return to_debug_report_response(result)


@router.get("/check-update", response_model=UpdateCheckResponse)
def check_update(request: Request) -> UpdateCheckResponse:
    """Ask GitHub whether a newer version of trcc-linux is published."""
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
    result = request.app.state.trcc.dispatch(
        RunUpgrade(dry_run=body.dry_run),
    )
    return to_upgrade_response(result)


@router.get("/first-run-status", response_model=FirstRunStatusResponse)
def first_run_status(request: Request) -> FirstRunStatusResponse:
    """Has trcc-next finished onboarding on this machine?"""
    result = request.app.state.trcc.dispatch(GetFirstRunStatus())
    return to_first_run_status_response(result)


@router.post("/mark-setup-done", response_model=FirstRunStatusResponse)
def mark_setup_done(request: Request) -> FirstRunStatusResponse:
    """Mark the first-run flow as completed."""
    result = request.app.state.trcc.dispatch(MarkFirstRunDone())
    return to_first_run_status_response(result)


