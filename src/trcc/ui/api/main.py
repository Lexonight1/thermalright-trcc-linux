"""FastAPI app factory.

build_app() returns a FastAPI instance with the TRCC App stored on
`app.state.trcc`.  Every router reads it via `request.app.state.trcc`
and dispatches Commands.  One App per FastAPI process.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from ...app import App
from . import devices, display, led, system

log = logging.getLogger(__name__)


def build_app(trcc: App | None = None) -> FastAPI:
    """Build the FastAPI app.  Creates a default App if none passed."""
    if trcc is None:
        from ...adapters.render.qt import QtRenderer
        from ...core.ports import Platform
        trcc = App(Platform.detect(), renderer=QtRenderer())

    api = FastAPI(
        title="TRCC API",
        description="REST API for Thermalright LCD/LED cooler control.",
        version="next",
    )
    api.state.trcc = trcc

    api.include_router(devices.router)
    api.include_router(display.router)
    api.include_router(led.router)
    api.include_router(system.router)

    @api.get("/", tags=["meta"])
    def root() -> dict:
        return {
            "name": "TRCC API",
            "version": "next",
            "endpoints": [
                "GET  /devices",
                "POST /devices/{key}/connect",
                "POST /devices/{key}/disconnect",
                "POST /devices/{key}/display/orientation",
                "POST /devices/{key}/display/brightness",
                "POST /devices/{key}/display/theme",
                "POST /devices/{key}/display/tick",
                "POST /devices/{key}/led/colors",
                "GET  /system/info",
                "GET  /system/sensors",
                "POST /system/setup",
            ],
        }

    return api


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the API with uvicorn (blocking)."""
    import uvicorn

    uvicorn.run(build_app(), host=host, port=port, log_level="info")
