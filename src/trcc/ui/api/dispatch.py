"""``/dispatch`` -- one door for every Command, TRCC's and every module's.

The bus's own envelope (``{"command": "<Name>", "kwargs": {...}}``, the one
the App's socket speaks), so a module's Commands need no route of their own.
``GET /dispatch`` lists what it takes.  A Command with a file field is refused
here: its own route checks the path (see ``ui/_dispatch_door``).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ... import ipc
from .. import _dispatch_door as door
from .schemas import DispatchRequest

log = logging.getLogger(__name__)

router = APIRouter(tags=["dispatch"])


@router.get("/dispatch")
def list_commands() -> dict[str, Any]:
    """Every Command this door takes -- its fields, what it does, and whether
    it carries files (those go through their own route)."""
    log.info("api GET /dispatch")
    return {"commands": [door.describe(cls) for cls in door.known().values()]}


@router.post("/dispatch")
def dispatch(body: DispatchRequest, request: Request) -> dict[str, Any]:
    """Run any Command by name, as the App's socket does; the Result comes
    back as JSON.  400 for an unknown name, a field it cannot take, a
    Command that carries files, or a refusal."""
    log.info("api POST /dispatch: %s %s", body.command, sorted(body.kwargs))
    try:
        cmd = door.build(body.command, body.kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if door.carries_files(type(cmd)):
        log.warning("api POST /dispatch: %s carries files -- refused here",
                    body.command)
        raise HTTPException(400, f"{body.command} carries files: use its own "
                                 "route, which checks the path")
    result = request.app.state.trcc.dispatch(cmd)
    if not getattr(result, "ok", True):
        raise HTTPException(400, result.message)
    return ipc.encode_result(result)
