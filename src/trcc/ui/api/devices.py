"""/devices router — discover / connect / disconnect."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    ConnectDevice,
    DeviceConnectionIssues,
    DisconnectDevice,
    DiscoverDevices,
    ResetDevice,
)
from ...core.results import DisconnectResult
from ._shared import (
    http_error_if_failed,
    product_to_schema,
    to_discover_response,
)
from .schemas import (
    ConnectionIssuesView,
    ConnectView,
    DiscoverResponse,
    ProductSchema,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=DiscoverResponse)
def list_devices(request: Request) -> DiscoverResponse:
    log.info("api GET /devices")
    result = request.app.state.trcc.dispatch(DiscoverDevices())
    return to_discover_response(result)


@router.get("/issues", response_model=ConnectionIssuesView)
def connection_issues(request: Request):
    """Every device that failed to connect, and why.

    The same ``DeviceConnectionIssues`` query the GUIs run — a connect can
    fail before any client is watching, so the failure has to be pullable
    rather than only published on the bus.

    Declared BEFORE ``/{key}``: FastAPI matches in declaration order, so a
    static segment must come first or "issues" is swallowed as a key.
    """
    log.info("api GET /devices/issues")
    return request.app.state.trcc.dispatch(DeviceConnectionIssues())


@router.get("/{key}", response_model=ProductSchema)
def device_detail(key: str, request: Request) -> ProductSchema:
    """Detail for one discovered device — 404 if not currently present."""
    log.info("api GET /devices/%s", key)
    result = request.app.state.trcc.dispatch(DiscoverDevices())
    for product in result.products:
        if product.key == key:
            return product_to_schema(product)
    raise HTTPException(status_code=404, detail=f"device {key} not found")


@router.post("/{key}/connect", response_model=ConnectView)
def connect(key: str, request: Request):
    """Connect *key*.  Returns the full handshake — including the raw
    device response as hex, the field issue triage always asks for."""
    log.info("api POST /devices/{key}/connect: key=%s", key)
    result = request.app.state.trcc.dispatch(ConnectDevice(key=key))
    http_error_if_failed(result)
    return result


@router.post("/{key}/reset")
def reset(key: str, request: Request) -> DisconnectResult:
    """Power-cycle the device: disconnect, reconnect, restore its display.

    For a panel that is stuck — the connection is rebuilt and the persisted
    theme + background are put back, so the device is left showing what it
    showed before rather than disconnected.

    Distinct from ``POST /devices/{key}/display/reset``, which blanks the panel
    to a known colour and leaves the device connected.  This is the CLI's
    ``device reset``, which had no REST equivalent.

    ``ok=False`` means the device did not come back; the message carries the
    connect failure.
    """
    log.info("api POST /devices/{key}/reset: key=%s", key)
    result = request.app.state.trcc.dispatch(ResetDevice(key=key))
    http_error_if_failed(result, status_code=404)
    return result


@router.post("/{key}/disconnect")
def disconnect(key: str, request: Request) -> DisconnectResult:
    log.info("api POST /devices/{key}/disconnect: key=%s", key)
    result = request.app.state.trcc.dispatch(DisconnectDevice(key=key))
    http_error_if_failed(result, status_code=404)
    return result
