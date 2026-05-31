"""``/trcc/`` router — daemon lifecycle control.

The existing ``/system`` and ``/devices`` namespaces are device- and
metrics-shaped; daemon-control is conceptually different (lifecycle of
the singleton process itself), so it lives under its own prefix —
legacy parity with ``legacy/ui/api/trcc.py``.

Endpoints:

  POST /trcc/kill    — stop the running daemon (the API process exits
                       cleanly; clients re-spawn it on demand via
                       ``ensure_daemon`` if needed)
  GET  /trcc/status  — pid / uptime / device counts.  Reads directly
                       from the in-process state (the API server is
                       almost always running INSIDE the daemon); no
                       IPC hop needed for next/'s layout.
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter, Request

from .schemas import DaemonKillResponse, DaemonStatusResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trcc", tags=["trcc"])


@router.post("/kill", response_model=DaemonKillResponse)
def kill() -> DaemonKillResponse:
    """Stop the running TRCC daemon.

    Sends the shutdown signal via ``daemon.kill_daemon`` (which talks
    to the singleton socket).  Returns ``ok=true`` once the daemon
    has shut down within the timeout, ``ok=false`` otherwise.  The
    API process itself exits as part of the shutdown.
    """
    log.info("api POST /trcc/kill")
    from ...daemon import kill_daemon
    ok = kill_daemon()
    return DaemonKillResponse(
        ok=ok,
        message="daemon shutdown" if ok else "daemon shutdown timed out",
    )


@router.get("/status", response_model=DaemonStatusResponse)
def status(request: Request) -> DaemonStatusResponse:
    """Snapshot of the running daemon: pid, uptime, device counts.

    Used by ops scripts and remote phone clients before issuing
    commands.  When the API server is also the daemon (the common
    layout: ``trcc api`` boots the API inside the daemon), every
    field is populated from in-process state.

    Returns ``running=false`` with zeros elsewhere when called from a
    standalone API process whose own daemon isn't up — distinguishable
    by the absence of a pid.
    """
    log.info("api GET /trcc/status")
    from ...daemon import _started_at
    from ...ipc import daemon_running
    running = daemon_running()
    if not running:
        return DaemonStatusResponse(
            ok=True, running=False,
            message="daemon not running",
        )
    trcc = request.app.state.trcc
    devices = list(trcc.devices.values())
    lcd_count = sum(1 for d in devices if not d.is_led)
    led_count = sum(1 for d in devices if d.is_led)
    uptime = int(time.monotonic() - _started_at) if _started_at else 0
    return DaemonStatusResponse(
        ok=True, running=True,
        pid=os.getpid(),
        uptime_seconds=uptime,
        lcd_count=lcd_count,
        led_count=led_count,
        message=(f"daemon up {uptime}s, "
                 f"{lcd_count} LCD + {led_count} LED device(s)"),
    )
