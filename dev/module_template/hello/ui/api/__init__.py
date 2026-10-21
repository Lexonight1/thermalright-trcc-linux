"""``GET /modules/hello`` -- what Hello adds to the API.

A module's ``ui/api/`` exports ``ROUTERS``, the FastAPI routers it adds.
Like the CLI's, they are added for every installed module; an off module's
route answers that it is off.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...core import Greet, HelloResult

log = logging.getLogger(__name__)

router = APIRouter(prefix="/modules/hello", tags=["hello"])


@router.get("")
def greet(request: Request, name: str = "world") -> HelloResult:
    """Hello's greeting."""
    log.info("api GET /modules/hello: name=%r", name)
    return request.app.state.trcc.dispatch(Greet(name=name))


ROUTERS = (router,)
