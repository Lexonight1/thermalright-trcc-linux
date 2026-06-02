"""``/config/*`` router — app-global preferences (control center)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...core.commands import (
    SetDateFormat,
    SetGpuDevice,
    SetLanguage,
    SetRefreshInterval,
    SetTempUnit,
    SetTimeFormat,
)
from ._shared import (
    http_error_if_failed,
    to_date_format_response,
    to_gpu_device_response,
    to_language_response,
    to_refresh_interval_response,
    to_temp_unit_response,
    to_time_format_response,
)
from .schemas import (
    DateFormatRequest,
    DateFormatResponse,
    GpuDeviceRequest,
    GpuDeviceResponse,
    LanguageRequest,
    LanguageResponse,
    RefreshIntervalRequest,
    RefreshIntervalResponse,
    TempUnitRequest,
    TempUnitResponse,
    TimeFormatRequest,
    TimeFormatResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


@router.post("/temp-unit", response_model=TempUnitResponse)
def set_temp_unit(body: TempUnitRequest, request: Request) -> TempUnitResponse:
    log.info("api POST /config/temp-unit: unit=%s", body.unit)
    result = request.app.state.trcc.dispatch(SetTempUnit(unit=body.unit))
    http_error_if_failed(result)
    return to_temp_unit_response(result)


@router.post("/language", response_model=LanguageResponse)
def set_language(body: LanguageRequest, request: Request) -> LanguageResponse:
    log.info("api POST /config/language: language=%s", body.language)
    result = request.app.state.trcc.dispatch(SetLanguage(language=body.language))
    http_error_if_failed(result)
    return to_language_response(result)


@router.post("/gpu", response_model=GpuDeviceResponse)
def set_gpu_device(body: GpuDeviceRequest, request: Request) -> GpuDeviceResponse:
    log.info("api POST /config/gpu: gpu_key=%s", body.gpu_key)
    result = request.app.state.trcc.dispatch(SetGpuDevice(gpu_key=body.gpu_key))
    http_error_if_failed(result)
    return to_gpu_device_response(result)


@router.post("/refresh-interval", response_model=RefreshIntervalResponse)
def set_refresh_interval(
    body: RefreshIntervalRequest, request: Request,
) -> RefreshIntervalResponse:
    log.info("api POST /config/refresh-interval: seconds=%s", body.seconds)
    result = request.app.state.trcc.dispatch(
        SetRefreshInterval(seconds=body.seconds),
    )
    http_error_if_failed(result)
    return to_refresh_interval_response(result)


@router.post("/time-format", response_model=TimeFormatResponse)
def set_time_format(body: TimeFormatRequest, request: Request) -> TimeFormatResponse:
    log.info(
        "api POST /config/time-format: fmt=%s key=%s", body.fmt, body.key,
    )
    result = request.app.state.trcc.dispatch(
        SetTimeFormat(fmt=body.fmt, key=body.key),
    )
    http_error_if_failed(result)
    return to_time_format_response(result)


@router.post("/date-format", response_model=DateFormatResponse)
def set_date_format(body: DateFormatRequest, request: Request) -> DateFormatResponse:
    log.info(
        "api POST /config/date-format: fmt=%s key=%s", body.fmt, body.key,
    )
    result = request.app.state.trcc.dispatch(
        SetDateFormat(fmt=body.fmt, key=body.key),
    )
    http_error_if_failed(result)
    return to_date_format_response(result)
