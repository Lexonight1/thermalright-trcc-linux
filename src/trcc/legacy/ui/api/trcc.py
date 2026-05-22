"""Daemon-control endpoints under ``/trcc/``.

The existing ``/system`` and ``/devices`` namespaces are device- and
metrics-shaped; daemon-control is conceptually different (lifecycle of
the singleton process itself), so it lives under its own prefix.

Endpoints:

  POST /trcc/kill    — stop the running daemon
  GET  /trcc/status  — daemon pid / uptime / device counts; ``running``
                       is False when no daemon is up.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trcc", tags=["trcc"])


@router.post("/kill")
def kill() -> dict:
    """Stop the running TRCC daemon.

    Returns ``{"success": true}`` once the daemon has shut down (or
    ``{"success": false}`` on timeout). The API server itself keeps
    running — only the daemon process the API was proxying to dies.
    Subsequent UI calls will auto-spawn a fresh daemon on demand.
    """
    from trcc.legacy.daemon import kill_daemon
    return {"success": kill_daemon()}


@router.get("/status")
def status() -> dict:
    """Snapshot of the running daemon: pid, uptime, device counts.

    Useful for ops (is the daemon alive?), monitoring (uptime
    threshold), and remote phone clients that want to confirm a
    healthy daemon before issuing commands.

    When no daemon is running, returns ``{"running": false}`` —
    distinguishable from a successful query by the absence of pid /
    uptime fields.
    """
    from trcc.legacy.ipc import daemon_running, send_manifold_request
    if not daemon_running():
        return {"running": False}
    response = send_manifold_request("_meta", "status", (), {}, timeout=2.0)
    if not response.get("success"):
        # Don't leak the IPC error verbatim — it can include exception
        # types and serialized arguments from the daemon-side dispatch
        # (`f"{type(e).__name__}: {e}"`).  Log it; return a generic
        # signal to the HTTP client.  CodeQL py/stack-trace-exposure.
        log.warning("daemon status query failed: %s", response.get("error"))
        return {"running": False, "error": "daemon status unavailable"}
    # Coerce every IPC response field to its declared type before
    # returning to the HTTP client.  The IPC payload is server-controlled
    # but CodeQL's data-flow analysis sees `response` as a tainted source
    # (anything across a serialization boundary).  Explicit ``int(...)``
    # is a recognized sanitizer for py/stack-trace-exposure and matches
    # the OpenAPI schema we advertise.
    def _safe_int(v: object) -> int:
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float, str)):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
        return 0

    return {
        "running": True,
        "pid": _safe_int(response.get("pid")),
        "uptime_seconds": _safe_int(response.get("uptime_seconds")),
        "lcd_count": _safe_int(response.get("lcd_count")),
        "led_count": _safe_int(response.get("led_count")),
    }
