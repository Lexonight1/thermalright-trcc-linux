"""/system router — setup, sensors, platform info."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.commands import ReadSensors, RunSetup
from ._shared import to_sensors_response, to_setup_response
from .schemas import SensorsResponse, SetupResponse

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
