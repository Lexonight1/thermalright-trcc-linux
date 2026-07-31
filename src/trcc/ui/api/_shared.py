"""Shared helpers for API routers — converters between Command Results
and Pydantic response schemas.

Most routes return their Command's Result verbatim (FastAPI serializes the
stdlib dataclass).  What is left here is the handful of cases where the HTTP
view genuinely differs from the domain Result — see each converter.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, Request

from ...core.commands import GetPaths
from ...core.models import ProductInfo
from ...core.results import (
    DiscoverResult,
    ImportConfigResult,
    Result,
    ThemeResult,
)
from .schemas import (
    DiscoverResponse,
    ImportConfigResponse,
    ProductSchema,
    ThemeResponse,
)

log = logging.getLogger(__name__)

# =========================================================================
# Converters
#
# Only the deliberate narrowings + projections live here.  Every other route
# returns its Command's Result verbatim.
# =========================================================================


def product_to_schema(p: ProductInfo) -> ProductSchema:
    """Materialize ``ProductInfo.key`` — a derived property that stdlib
    dataclass serialization would drop.  See :class:`ProductSchema`."""
    return ProductSchema(
        key=p.key, vid=p.vid, pid=p.pid,
        vendor=p.vendor, product=p.product,
        wire=p.wire.value, kind=p.kind.value,
        native_resolution=p.native_resolution,
        orientations=p.orientations,
    )


def to_discover_response(result: DiscoverResult) -> DiscoverResponse:
    return DiscoverResponse(
        ok=result.ok, message=result.message,
        products=[product_to_schema(p) for p in result.products],
    )


def to_theme_response(result: ThemeResult) -> ThemeResponse:
    """Deliberate narrowing: ``ThemeResult.theme_path`` is a server-side
    absolute path and stays off the wire.  Everything else is exposed."""
    return ThemeResponse(
        ok=result.ok, message=result.message,
        key=result.key, theme_name=result.theme_name,
        target_exists=result.target_exists,
    )


def to_import_config_response(result: ImportConfigResult) -> ImportConfigResponse:
    """Deliberate narrowing: ``ImportConfigResult.input_path`` is a
    server-side absolute path and stays off the wire."""
    return ImportConfigResponse(
        ok=result.ok, message=result.message, key=result.key,
    )


# =========================================================================
# Error handling
# =========================================================================


def http_error_if_failed(result: Result, status_code: int = 400) -> None:
    """Raise HTTPException with the result message if ok is False."""
    if not result.ok:
        raise HTTPException(status_code=status_code, detail=result.message)


def staging_dir(request: Request) -> Path:
    """The upload staging directory, created if absent.

    Four routes staged multipart uploads into
    ``platform.paths().user_content_dir() / "uploads"`` with the same three
    lines each — and that reach does not exist on the ``AppProxy`` a
    daemon-mode client holds (#249).  One helper, one ``GetPaths`` dispatch,
    and the location is the app's answer rather than each route's assumption.
    """
    result = request.app.state.trcc.dispatch(GetPaths())
    if not result.uploads_dir:
        # Empty is ABSENT, not a location.  ``Path("")`` is ``Path(".")``, so
        # a falsy value here would silently stage user uploads into whatever
        # directory the process happens to be running in.  Refuse instead —
        # and say so, because the alternative failure mode is uploads landing
        # somewhere nobody chose, with nothing raised and nothing logged.
        log.warning("staging_dir: GetPaths returned no uploads_dir (%s) — "
                    "refusing to stage into the working directory",
                    result.message)
        raise HTTPException(500, "upload staging directory unavailable")
    path = Path(result.uploads_dir).resolve()
    log.debug("staging_dir: %s", path)
    path.mkdir(parents=True, exist_ok=True)
    return path
