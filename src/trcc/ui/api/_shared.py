"""Shared helpers for API routers — converters between Command Results
and Pydantic response schemas.

Most routes return their Command's Result verbatim (FastAPI serializes the
stdlib dataclass).  What is left here is the handful of cases where the HTTP
view genuinely differs from the domain Result — see each converter.
"""
from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import Response
from starlette.datastructures import URL

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


def product_to_schema(p: ProductInfo, key: str) -> ProductSchema:
    """The HTTP view of one discovered UNIT: its product, under ``key``.

    ``key`` is the unit's own (``vid:pid``, or ``vid:pid@port`` for one of two
    identical coolers), never ``p.key``, which names the model and gave two
    twins the same address (#287).  See :class:`ProductSchema`."""
    log.debug("product_to_schema: key=%s p=%s", key, p)
    return ProductSchema(
        key=key, vid=p.vid, pid=p.pid,
        vendor=p.vendor, product=p.product,
        wire=p.wire.value, kind=p.kind.value,
        native_resolution=p.native_resolution,
        orientations=p.orientations,
    )


def to_discover_response(result: DiscoverResult) -> DiscoverResponse:
    log.debug("to_discover_response: result=%s", result)
    return DiscoverResponse(
        ok=result.ok, message=result.message,
        products=[product_to_schema(p, key) for key, p in result.units()],
    )


def to_theme_response(result: ThemeResult) -> ThemeResponse:
    """Deliberate narrowing: ``ThemeResult.theme_path`` is a server-side
    absolute path and stays off the wire.  Everything else is exposed."""
    log.debug("to_theme_response: result=%s", result)
    return ThemeResponse(
        ok=result.ok, message=result.message,
        key=result.key, theme_name=result.theme_name,
        target_exists=result.target_exists,
    )


def to_import_config_response(result: ImportConfigResult) -> ImportConfigResponse:
    """Deliberate narrowing: ``ImportConfigResult.input_path`` is a
    server-side absolute path and stays off the wire."""
    log.debug("to_import_config_response: result=%s", result)
    return ImportConfigResponse(
        ok=result.ok, message=result.message, key=result.key,
    )


# =========================================================================
# Error handling
# =========================================================================


def http_error_if_failed(result: Result, status_code: int = 400) -> None:
    """Raise HTTPException with the result message if ok is False."""
    log.debug("http_error_if_failed: result=%s status_code=%s", result, status_code)
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


# =========================================================================
# The trust boundary
#
# The API is the one face a stranger can reach, so a request may not name a
# path TRCC does not own, and a browser may not drive a token-less server.
# The CLI and the GUI are the local user and keep every path.
# =========================================================================

#: Host names a token-less server answers to.  It binds loopback only (``trcc
#: api`` refuses anything else without ``--token``), so any other Host is a
#: DNS-rebinding page that has pointed its own name at 127.0.0.1.
_LOOPBACK: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_client(host: str | None, origin: str | None) -> bool:
    """May this request drive a token-less server?

    *host* is the request URL's host, which Starlette takes from the ``Host``
    header; *origin* is the raw ``Origin`` header.  A browser attaches
    ``Origin`` to every cross-origin POST and WebSocket handshake, and a
    non-browser client sends none — so a foreign Origin is a web page, and a
    foreign Host is a rebinding one.  Measured before this rule: a multipart
    upload and both WebSockets were accepted from ``https://evil.example``,
    and the live preview streamed to it.
    """
    local = host in _LOOPBACK and (
        origin is None or URL(origin).hostname in _LOOPBACK)
    log.debug("is_loopback_client: host=%s origin=%s -> %s", host, origin, local)
    return local


async def admit_ws(ws: WebSocket) -> bool:
    """Token, or loopback-only when there is none — else close with 1008.

    One gate for both WebSockets, which had their own copies of the token
    check and none of the browser one.  Closing before ``accept`` sends the
    policy-violation code without saying why.
    """
    import hmac

    from .main import _api_token
    # Nothing computed from the token is logged -- CodeQL's
    # clear-text-logging rule rightly treats it as the secret itself.
    if _api_token:
        admitted = hmac.compare_digest(ws.query_params.get("token", ""),
                                       _api_token)
        reason = "bad token"
    else:
        admitted = is_loopback_client(ws.url.hostname, ws.headers.get("origin"))
        reason = "not a loopback client"
    if not admitted:
        log.warning("admit_ws: refused %s (%s)", ws.url.path, reason)
        await ws.close(code=1008)
        return False
    log.info("admit_ws: admitted %s", ws.url.path)
    return True


def owned_path(request: Request, raw: str) -> Path:
    """The existing file *raw* names inside a TRCC data root, or 400.

    Resolved against user content and program data, then REBUILT one
    component at a time from ``iterdir()`` — so every filesystem call runs on a
    path TRCC enumerated, never on the request.  That is the only shape
    CodeQL's path-injection rule has accepted here: resolve-then-contain was
    tried twice (``c46c6c74``, ``8d841923``) and stayed flagged.  A relative
    *raw* is relative to user content.  Measured before this: 8 read routes
    opened any file the process could read.
    """
    paths = request.app.state.trcc.dispatch(GetPaths())
    roots = [Path(r).resolve() for r in (paths.user_content_dir, paths.data_dir)
             if r]
    if not roots:
        log.warning("owned_path: GetPaths returned no roots (%s)",
                    paths.message)
        raise HTTPException(500, "data directories unavailable")
    wanted = (roots[0] / raw).resolve()
    for root in roots:
        if wanted.parts[:len(root.parts)] != root.parts:
            continue
        node: Path | None = root
        for part in wanted.parts[len(root.parts):]:
            node = next((c for c in node.iterdir() if c.name == part),
                        None) if node is not None and node.is_dir() else None
        if node is not None:
            log.debug("owned_path: %r -> %s", raw, node)
            return node
    log.warning("owned_path: refused %r — not an existing path under %s",
                raw, [str(r) for r in roots])
    raise HTTPException(400, "path must name an existing file inside TRCC's "
                             "data directories (~/.trcc or ~/.trcc-user)")


@contextmanager
def temp_output(suffix: str) -> Iterator[Path]:
    """A path a Command can write to, deleted however the route exits.

    For the routes that hand a file BACK instead of writing where the client
    says.  The descriptor is closed here — the old ``mkstemp(...)[1]`` leaked
    one per download — and the file goes in ``finally``, because
    ``FileResponse``'s cleanup task never ran on a malformed ``Range`` header
    (3 of 3 leaked).
    """
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="trcc-api-")
    os.close(fd)
    path = Path(name)
    log.debug("temp_output: %s", path)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def file_response(path: Path, media_type: str, filename: str) -> Response:
    """*path*'s bytes as a download named *filename*."""
    log.debug("file_response: %s as %s (%s)", path.name, filename, media_type)
    return Response(
        path.read_bytes(), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
