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
from ...core.results import (
    DateFormatResult,
    GpuDeviceResult,
    LanguageResult,
    RefreshIntervalResult,
    TempUnitResult,
    TimeFormatResult,
)
from ._shared import (
    http_error_if_failed,
)
from .schemas import (
    DateFormatRequest,
    GpuDeviceRequest,
    LanguageRequest,
    RefreshIntervalRequest,
    TempUnitRequest,
    TimeFormatRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


@router.post("/temp-unit")
def set_temp_unit(body: TempUnitRequest, request: Request) -> TempUnitResult:
    log.info("api POST /config/temp-unit: unit=%s", body.unit)
    result = request.app.state.trcc.dispatch(SetTempUnit(unit=body.unit))
    http_error_if_failed(result)
    return result


@router.post("/language")
def set_language(body: LanguageRequest, request: Request) -> LanguageResult:
    log.info("api POST /config/language: language=%s", body.language)
    result = request.app.state.trcc.dispatch(SetLanguage(language=body.language))
    http_error_if_failed(result)
    return result


@router.post("/gpu")
def set_gpu_device(body: GpuDeviceRequest, request: Request) -> GpuDeviceResult:
    log.info("api POST /config/gpu: gpu_key=%s", body.gpu_key)
    result = request.app.state.trcc.dispatch(SetGpuDevice(gpu_key=body.gpu_key))
    http_error_if_failed(result)
    return result


@router.post("/refresh-interval")
def set_refresh_interval(
    body: RefreshIntervalRequest, request: Request,
) -> RefreshIntervalResult:
    log.info("api POST /config/refresh-interval: seconds=%s", body.seconds)
    result = request.app.state.trcc.dispatch(
        SetRefreshInterval(seconds=body.seconds),
    )
    http_error_if_failed(result)
    return result


@router.post("/time-format")
def set_time_format(body: TimeFormatRequest, request: Request) -> TimeFormatResult:
    log.info(
        "api POST /config/time-format: fmt=%s key=%s", body.fmt, body.key,
    )
    result = request.app.state.trcc.dispatch(
        SetTimeFormat(fmt=body.fmt, key=body.key),
    )
    http_error_if_failed(result)
    return result


@router.post("/date-format")
def set_date_format(body: DateFormatRequest, request: Request) -> DateFormatResult:
    log.info(
        "api POST /config/date-format: fmt=%s key=%s", body.fmt, body.key,
    )
    result = request.app.state.trcc.dispatch(
        SetDateFormat(fmt=body.fmt, key=body.key),
    )
    http_error_if_failed(result)
    return result
