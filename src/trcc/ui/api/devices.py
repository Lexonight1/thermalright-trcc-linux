"""/devices router — discover / connect / disconnect."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.commands import ConnectDevice, DisconnectDevice, DiscoverDevices
from ._shared import (
    http_error_if_failed,
    to_connect_response,
    to_disconnect_response,
    to_discover_response,
)
from .schemas import ConnectResponse, DisconnectResponse, DiscoverResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=DiscoverResponse)
def list_devices(request: Request) -> DiscoverResponse:
    result = request.app.state.trcc.dispatch(DiscoverDevices())
    return to_discover_response(result)


@router.post("/{key}/connect", response_model=ConnectResponse)
def connect(key: str, request: Request) -> ConnectResponse:
    result = request.app.state.trcc.dispatch(ConnectDevice(key=key))
    http_error_if_failed(result)
    return to_connect_response(result)


@router.post("/{key}/disconnect", response_model=DisconnectResponse)
def disconnect(key: str, request: Request) -> DisconnectResponse:
    result = request.app.state.trcc.dispatch(DisconnectDevice(key=key))
    http_error_if_failed(result, status_code=404)
    return to_disconnect_response(result)
