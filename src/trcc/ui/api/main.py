"""FastAPI app factory.

build_app() returns a FastAPI instance with the TRCC App stored on
`app.state.trcc`.  Every router reads it via `request.app.state.trcc`
and dispatches Commands.  One App per FastAPI process.

Auth model (port of legacy ``ui/api/__init__.py``):
  * Module-level ``_api_token`` — set via ``configure_auth(token)``.
    When None, no auth enforcement (loopback dev mode).  When set,
    every request must carry a matching ``X-API-Token`` header.
  * Module-level ``_pairing_code`` — 6-char code shown in the
    terminal at server start; remote devices ``POST /pair?code=...``
    to exchange it for the persistent ``_api_token``.
  * ``/health`` and ``/pair`` are exempt from auth so a fresh remote
    can pair without already knowing the token.
  * Request-logging middleware logs method + path + status + latency
    on every call.
"""
from __future__ import annotations

import hmac
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ...__version__ import __version__
from ...app import App
from . import config, devices, display, led, system, theme
from . import trcc as _trcc_router

log = logging.getLogger(__name__)


# ── Token + pairing state ────────────────────────────────────────────

_api_token: str | None = None
_pairing_code: str | None = None
_AUTH_EXEMPT: frozenset[str] = frozenset({"/health", "/pair", "/", "/docs",
                                          "/openapi.json", "/redoc"})


def configure_auth(token: str | None) -> None:
    """Set the persistent API token (or clear it with ``None``).

    Called by the CLI ``serve`` command at server start.  Once set,
    every request to a non-exempt path must include the matching
    ``X-API-Token`` header or the middleware returns 401.
    """
    global _api_token
    _api_token = token
    log.info(
        "configure_auth: token %s",
        "set (auth enforced)" if token else "cleared (no auth)",
    )


def set_pairing_code(code: str | None) -> None:
    """Set the ephemeral 6-char pairing code shown in the terminal.

    Remote devices POST it to ``/pair`` to receive the persistent
    ``_api_token``.  When None, pairing is disabled and ``/pair``
    returns 503 — useful when the operator already provisioned the
    token out-of-band.
    """
    global _pairing_code
    _pairing_code = code
    log.info("set_pairing_code: %s",
             "set" if code else "cleared (pairing disabled)")


def build_app(trcc: App | None = None) -> FastAPI:
    """Build the FastAPI app.  Creates a default App if none passed."""
    if trcc is None:
        # Build through the canonical factory so the API server becomes
        # a daemon client when TRCC_NEXT_DAEMON=1 instead of fighting
        # the daemon for USB (audit bug B4).
        from ..._boot import trcc_next
        from ...adapters.render.qt import QtRenderer
        trcc = trcc_next(renderer=QtRenderer())

    api = FastAPI(
        title="TRCC API",
        description="REST API for Thermalright LCD/LED cooler control.",
        version="next",
    )
    api.state.trcc = trcc

    # ── Request logging — every call gets one INFO line ─────────────
    @api.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        response = await call_next(request)
        ms = (time.monotonic() - start) * 1000
        log.info("API %s %s → %d (%.0fms)",
                 request.method, request.url.path,
                 response.status_code, ms)
        return response

    # ── Token-auth middleware ───────────────────────────────────────
    # When ``_api_token`` is None the API is unauth'd (loopback dev
    # mode default).  When set, every non-exempt path requires a
    # matching ``X-API-Token`` header.  Port of legacy
    # ``ui/api/__init__.py:check_token`` byte-for-byte (hmac compare
    # so a wrong-length token doesn't time-leak).
    @api.middleware("http")
    async def check_token(request: Request, call_next):  # type: ignore[no-untyped-def]
        if _api_token and request.url.path not in _AUTH_EXEMPT:
            header_token = request.headers.get("X-API-Token", "")
            if not hmac.compare_digest(header_token, _api_token):
                log.warning(
                    "API auth: rejected %s %s (token mismatch)",
                    request.method, request.url.path,
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid token"},
                )
        return await call_next(request)

    # ── Health endpoint — exempt from auth ──────────────────────────
    @api.get("/health", tags=["meta"])
    def health() -> dict:
        """Liveness probe — always reachable, no auth required."""
        return {"status": "ok", "version": __version__}

    # ── Pairing endpoint — exempt from auth ─────────────────────────
    @api.post("/pair", tags=["meta"], response_model=None)
    def pair_device(code: str) -> dict | JSONResponse:
        """Exchange the terminal pairing code for the persistent API token.

        Remote devices receive the code out-of-band (operator types
        it into the remote app), POST it here, and store the returned
        ``token`` for all future requests as the ``X-API-Token`` header.
        """
        if not _pairing_code:
            return JSONResponse(
                status_code=503,
                content={
                    "detail":
                    "Pairing not available "
                    "(server started without a pairing code)",
                },
            )
        if not hmac.compare_digest(code.upper(), _pairing_code.upper()):
            log.warning("API pair: rejected (wrong code)")
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid pairing code"},
            )
        if not _api_token:
            return JSONResponse(
                status_code=500,
                content={"detail": "No API token configured"},
            )
        log.info("API pair: remote device paired successfully")
        return {"success": True, "token": _api_token}

    api.include_router(devices.router)
    api.include_router(display.router)
    api.include_router(display.meta_router)
    api.include_router(led.router)
    api.include_router(led.meta_router)
    api.include_router(system.router)
    api.include_router(config.router)
    api.include_router(theme.router)
    api.include_router(_trcc_router.router)

    # ── Static serving for cloud previews ───────────────────────────
    # Mount data/web so the /theme/web gallery's preview_url
    # (/static/web/{w}{h}/<id>.png — and masks under zt{w}{h}/) resolve.
    # Created if absent so the mount succeeds before the first
    # /theme/init download; StaticFiles serves whatever lands there.
    try:
        web_root = trcc.platform.paths().data_dir() / "web"
        web_root.mkdir(parents=True, exist_ok=True)
        api.mount(
            "/static/web", StaticFiles(directory=str(web_root)), name="static-web",
        )
        log.info("static: mounted /static/web → %s", web_root)
    except (OSError, AttributeError) as e:
        log.warning("static: /static/web mount skipped — %s", e)

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
                "POST /devices/{key}/display/color",
                "POST /devices/{key}/display/fit-mode",
                "POST /devices/{key}/display/overlay",
                "POST /devices/{key}/display/split-mode",
                "POST /devices/{key}/display/mask",
                "POST /devices/{key}/display/mask-position",
                "POST /devices/{key}/display/mask-visible",
                "POST /devices/{key}/display/play-video",
                "POST /devices/{key}/display/stop-video",
                "POST /devices/{key}/display/tick",
                "POST /devices/{key}/led/colors",
                "POST /devices/{key}/led/render",
                "GET  /system/info",
                "GET  /system/sensors",
                "POST /system/setup",
                "POST /config/temp-unit",
                "POST /config/language",
                "POST /config/gpu",
                "POST /config/refresh-interval",
                "POST /theme/save",
                "POST /theme/export",
                "POST /theme/import",
            ],
        }

    return api


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the API with uvicorn (blocking)."""
    import uvicorn

    uvicorn.run(build_app(), host=host, port=port, log_level="info")
