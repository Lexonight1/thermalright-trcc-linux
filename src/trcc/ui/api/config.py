"""``/config/*`` router — app-global preferences (control center)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.commands import (
    SetGpuDevice,
    SetLanguage,
    SetRefreshInterval,
    SetTempUnit,
)
from ._shared import (
    http_error_if_failed,
    to_gpu_device_response,
    to_language_response,
    to_refresh_interval_response,
    to_temp_unit_response,
)
from .schemas import (
    GpuDeviceRequest,
    GpuDeviceResponse,
    LanguageRequest,
    LanguageResponse,
    RefreshIntervalRequest,
    RefreshIntervalResponse,
    TempUnitRequest,
    TempUnitResponse,
)

router = APIRouter(prefix="/config", tags=["config"])


@router.post("/temp-unit", response_model=TempUnitResponse)
def set_temp_unit(body: TempUnitRequest, request: Request) -> TempUnitResponse:
    result = request.app.state.trcc.dispatch(SetTempUnit(unit=body.unit))
    http_error_if_failed(result)
    return to_temp_unit_response(result)


@router.post("/language", response_model=LanguageResponse)
def set_language(body: LanguageRequest, request: Request) -> LanguageResponse:
    result = request.app.state.trcc.dispatch(SetLanguage(language=body.language))
    http_error_if_failed(result)
    return to_language_response(result)


@router.post("/gpu", response_model=GpuDeviceResponse)
def set_gpu_device(body: GpuDeviceRequest, request: Request) -> GpuDeviceResponse:
    result = request.app.state.trcc.dispatch(SetGpuDevice(gpu_key=body.gpu_key))
    http_error_if_failed(result)
    return to_gpu_device_response(result)


@router.post("/refresh-interval", response_model=RefreshIntervalResponse)
def set_refresh_interval(
    body: RefreshIntervalRequest, request: Request,
) -> RefreshIntervalResponse:
    result = request.app.state.trcc.dispatch(
        SetRefreshInterval(seconds=body.seconds),
    )
    http_error_if_failed(result)
    return to_refresh_interval_response(result)
