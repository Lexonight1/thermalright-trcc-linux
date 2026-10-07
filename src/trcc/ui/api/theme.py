"""``/theme/*`` router — save / export / import.

Files cross the API as bytes, never as server paths: an archive comes in
through ``-upload`` and goes out through ``-download`` (or an export route's
response), each staged in a tempfile around the Command dispatch.  The routes
that took a server-side path — ``/export`` and ``/import`` — wrote and read
anywhere the process could, and were removed in the trust-boundary pass.

A theme ``name`` is reduced to its basename at the router edge, and a
``?directory=`` must lie inside TRCC's own data (``owned_path``).
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ...core.commands import (
    DeleteTheme,
    DeviceState,
    DownloadCloudTheme,
    EnsureDataDownload,
    ExportCurrentTheme,
    ExportDcTheme,
    ExportOverlay,
    ExportTheme,
    ImportTheme,
    ListCloudThemes,
    ListThemes,
    ListWebThemes,
    LoadCloudTheme,
    SaveTheme,
)
from ...core.models import ThemeDir, parse_resolution
from ...core.results import (
    CloudThemeLoadResult,
    CloudThemesListResult,
    DeleteThemeResult,
    EnsureDataDownloadResult,
    ThemeImportResult,
    ThemesListResult,
)
from ._shared import (
    file_response,
    http_error_if_failed,
    owned_path,
    staging_dir,
    temp_output,
    to_theme_response,
)
from .schemas import (
    CloudThemeDownloadRequest,
    CloudThemeLoadRequest,
    DeleteThemeRequest,
    ExportOverlayRequest,
    ThemeDcExportRequest,
    ThemeResponse,
    ThemeSaveRequest,
    WebThemeSchema,
)

log = logging.getLogger(__name__)


def _parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse ``"320x320"`` → ``(320, 320)``; raise 400 on bad input."""
    log.debug("_parse_resolution: resolution=%s", resolution)
    try:
        return parse_resolution(resolution)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

router = APIRouter(prefix="/theme", tags=["theme"])


def _safe_basename(value: str) -> str:
    """Strip any directory parts; raise if the result is empty."""
    log.debug("_safe_basename: value=%s", value)
    name = Path(value).name.strip()
    if not name:
        raise HTTPException(400, "name required")
    return name


@router.post(
    "/save",
    response_model=ThemeResponse,
    responses={409: {
        "model": ThemeResponse,
        "description": "A theme of that name exists — retry with "
                       "`overwrite: true` to replace it.",
    }},
)
def save(body: ThemeSaveRequest,
         request: Request) -> ThemeResponse | JSONResponse:
    """Save the device's active theme, refusing a name collision at 409.

    A name collision is a NEGOTIATION, not an error: both GUIs dispatch,
    read ``target_exists``, ask the user, and re-dispatch with
    ``overwrite=True``.  ``http_error_if_failed`` would flatten that into
    ``400 {"detail": "<message>"}`` and throw the flag away -- so the one
    field ``ThemeResponse`` carries for this purpose never reached a REST
    client, which was left doing exactly what its schema comment says it
    should not: matching on the message text.

    So the refusal keeps the SAME body as the success, at the status code
    that means it (409 Conflict), using the ``JSONResponse`` idiom the
    auth routes in ``main.py`` already use for a structured non-200.
    """
    log.info("api POST /theme/save: key=%s name=%s overwrite=%s",
             body.key, body.name, body.overwrite)
    name = _safe_basename(body.name)
    result = request.app.state.trcc.dispatch(
        SaveTheme(key=body.key, name=name, overwrite=body.overwrite),
    )
    if result.target_exists:
        log.info("api POST /theme/save: %r exists — 409, client may retry "
                 "with overwrite=true", name)
        return JSONResponse(
            status_code=409,
            content=to_theme_response(result).model_dump(),
        )
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/export-overlay")
def export_overlay(body: ExportOverlayRequest, request: Request) -> Response:
    """Download a theme's overlay config — ``config1.dc`` or ``trcc.json``.

    The CLI has had ``theme export-overlay`` all along; REST could export the
    theme archive and the DC, but not the overlay config on its own.  The file
    comes back in the response: this wrote to any server path the client
    named until the trust-boundary pass.
    """
    log.info("api POST /theme/export-overlay: key=%s theme=%s",
             body.key, body.theme_name)
    safe_name = _safe_basename(body.theme_name)
    with temp_output(".overlay") as out:
        http_error_if_failed(request.app.state.trcc.dispatch(ExportOverlay(
            key=body.key, theme_name=safe_name, output_path=out,
        )))
        # Whichever source the theme had: JSON opens with a brace.
        ext = ".json" if out.read_bytes()[:1] == b"{" else ".dc"
        return file_response(out, "application/octet-stream",
                             f"{safe_name}-overlay{ext}")


@router.post("/import-upload")
async def import_upload(
    request: Request,
    key: str,
    archive: UploadFile = File(...),
    name: str = "",
) -> ThemeImportResult:
    """Import a theme archive uploaded via multipart form-data.

    Remote clients without filesystem access to the server use this
    instead of ``POST /import`` (which references server-side paths).
    The upload is staged to a tempfile, imported, and the tempfile
    cleaned up after dispatch.
    """
    log.info(
        "api POST /theme/import-upload: key=%s filename=%s name=%s",
        key, archive.filename, name,
    )
    uploads_dir = staging_dir(request)
    suffix = Path(archive.filename or "theme.tr").suffix.lower() or ".tr"
    staged = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    try:
        with staged.open("wb") as f:
            shutil.copyfileobj(archive.file, f)
        chosen_name = name.strip()
        if chosen_name:
            chosen_name = _safe_basename(chosen_name)
        result = request.app.state.trcc.dispatch(
            ImportTheme(key=key, archive_path=staged, name=chosen_name),
        )
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
    http_error_if_failed(result)
    return result


@router.get("/{key}/download")
def download_current(key: str, request: Request) -> Response:
    """Stream what the panel shows, saved or not, as a Windows ``.tr``.

    The Windows app's export (``FormCZTV.buttonDaoChu_Click``); the route
    below exports a SAVED theme by name.  Built in a tempfile that
    ``temp_output`` deletes however the route exits.
    """
    log.info("api GET /theme/{key}/download: key=%s", key)
    with temp_output(".tr") as tmp:
        http_error_if_failed(request.app.state.trcc.dispatch(
            ExportCurrentTheme(key=key, archive_path=tmp),
        ))
        return file_response(tmp, "application/octet-stream", "theme.tr")


@router.get("/{key}/{theme_name}/download")
def download(key: str, theme_name: str, request: Request) -> Response:
    """Stream a theme archive as a multipart download.

    Server-side equivalent of ``POST /export`` but bytes flow back over
    the HTTP response instead of landing on the server filesystem.
    The archive is built in a tempfile that ``temp_output`` deletes however
    the route exits.  ``FileResponse``'s cleanup task never ran on a malformed
    ``Range`` header, so every such request leaked the archive.
    """
    log.info(
        "api GET /theme/{key}/{theme_name}/download: key=%s theme_name=%s",
        key, theme_name,
    )
    safe_name = _safe_basename(theme_name)
    with temp_output(".tr") as tmp:
        http_error_if_failed(request.app.state.trcc.dispatch(
            ExportTheme(key=key, theme_name=safe_name, archive_path=tmp),
        ))
        return file_response(tmp, "application/octet-stream",
                             f"{safe_name}.tr")


@router.get("/list")
def list_(
    request: Request,
    directory: str | None = None,
    key: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> ThemesListResult:
    """List themes for a device resolution.

    Pass ``?key=vid:pid`` (resolution from the connected device's
    handshake profile), or ``?width=W&height=H`` for an explicit
    override, or ``?directory=`` to scan an exact dir (escape hatch).
    """
    log.info(
        "api GET /theme/list: directory=%s key=%s width=%s height=%s",
        directory, key, width, height,
    )
    if directory:
        result = request.app.state.trcc.dispatch(
            ListThemes(directory=owned_path(request, directory)),
        )
    else:
        resolution: tuple[int, int] | None = None
        if key is not None:
            state = request.app.state.trcc.dispatch(DeviceState(key=key))
            if not state.ok or state.resolution is None:
                return ThemesListResult(
                    ok=False, directory="", themes=[],
                    message=(f"Device {key} not connected — connect first "
                             "so we know the target resolution"),
                )
            resolution = state.resolution
        elif width is not None and height is not None:
            resolution = (width, height)
        result = request.app.state.trcc.dispatch(
            ListThemes(resolution=resolution),
        )
    http_error_if_failed(result)
    return result


@router.get("/cloud")
def cloud_list(
    request: Request,
    category: str = "all",
) -> CloudThemesListResult:
    """List Thermalright cloud catalog (offline — catalog is static)."""
    log.info("api GET /theme/cloud: category=%s", category)
    result = request.app.state.trcc.dispatch(
        ListCloudThemes(category=category),
    )
    http_error_if_failed(result)
    return result


@router.get("/web", response_model=list[WebThemeSchema])
def web_gallery(
    request: Request,
    resolution: str,
    key: str = "",
) -> list[WebThemeSchema]:
    """Cloud-theme preview gallery for a resolution (e.g. ``320x320``).

    Lists the downloaded ``a001.png`` previews — ``preview_url`` resolves
    against the ``/static/web`` mount, ``has_video`` flags a sibling
    ``.mp4``.  Empty list when nothing is downloaded yet (call
    ``POST /theme/init``).

    ``?key=vid:pid`` is optional and names a *device*, so the gallery reads
    that cooler's own artwork library (a SUB-3 1600x720 panel browses
    ``1600720l``) exactly as the CLI, GUI and qtgui do.  Without it the
    resource stays purely resolution-addressed and reads the generic
    library, which is what nearly every panel uses.
    """
    w, h = _parse_resolution(resolution)
    log.info("api GET /theme/web: resolution=%dx%d key=%s",
             w, h, key or "(generic)")
    result = request.app.state.trcc.dispatch(
        ListWebThemes(width=w, height=h, key=key),
    )
    # Domain entries → HTTP schema: the API owns the URLs (preview served
    # by the /static/web mount; download via the cloud-load route).  The URL
    # segment is the directory the query REPORTS having read, never one
    # re-spelled from w/h -- those disagree for a per-SKU panel, and the
    # re-spelled one names a file that is not there.
    web_dir = Path(result.directory).name or f"{w}{h}"
    log.debug("api GET /theme/web: %d preview(s) under %s",
              len(result.entries), web_dir)
    return [
        WebThemeSchema(
            id=e.id,
            category=e.category,
            has_video=e.has_video,
            preview_url=f"/static/web/{web_dir}/{e.id}.png",
            download_url=f"/theme/cloud/{e.id}",
        )
        for e in result.entries
    ]


@router.post("/init")
def init_data(
    request: Request,
    resolution: str,
) -> EnsureDataDownloadResult:
    """Prefetch theme/web/mask archives for a resolution (idempotent).

    For remote clients to call on startup before browsing — works with
    no device connected.  Wraps the ``EnsureDataDownload`` command that
    previously had no route.
    """
    w, h = _parse_resolution(resolution)
    log.info("api POST /theme/init: resolution=%dx%d", w, h)
    return request.app.state.trcc.dispatch(
        EnsureDataDownload(width=w, height=h),
    )


@router.post("/cloud/download")
def cloud_download(body: CloudThemeDownloadRequest,
                   request: Request) -> CloudThemeLoadResult:
    """Cache a cloud theme locally WITHOUT applying it to a device.

    ``POST /theme/cloud/{key}`` downloads AND applies — it persists the
    background and starts playback.  This is the download half alone, for
    pre-fetching a catalog without disturbing what a panel is showing.  It
    takes no device key because it touches no device.

    Declared BEFORE ``/cloud/{key}``: FastAPI matches in declaration order, so
    a static segment must come first or "download" is swallowed as a key.

    Idempotent — an already-cached theme is not fetched again.
    """
    log.info("api POST /theme/cloud/download: theme_id=%s %dx%d",
             body.theme_id, body.width, body.height)
    result = request.app.state.trcc.dispatch(DownloadCloudTheme(
        theme_id=body.theme_id, resolution=(body.width, body.height),
    ))
    http_error_if_failed(result)
    return result


@router.post("/cloud/{key}")
def cloud_load(key: str, body: CloudThemeLoadRequest,
                request: Request) -> CloudThemeLoadResult:
    """Download a cloud theme + apply it to *key*."""
    log.info(
        "api POST /theme/cloud/{key}: key=%s theme_id=%s",
        key, body.theme_id,
    )
    result = request.app.state.trcc.dispatch(
        LoadCloudTheme(key=key, theme_id=body.theme_id),
    )
    http_error_if_failed(result)
    return result


@router.post("/{name}/export-dc")
def export_dc(name: str, body: ThemeDcExportRequest,
              request: Request) -> Response:
    """Download a theme as legacy ``config1.dc``.

    The file comes back in the response: this wrote to any server path the
    client named until the trust-boundary pass.
    """
    log.info("api POST /theme/{name}/export-dc: name=%s key=%s",
             name, body.key)
    safe_name = _safe_basename(name)
    with temp_output(".dc") as out:
        http_error_if_failed(request.app.state.trcc.dispatch(ExportDcTheme(
            key=body.key, theme_name=safe_name, output_path=out,
        )))
        return file_response(out, "application/octet-stream", ThemeDir.DC)


@router.delete("")
def delete(body: DeleteThemeRequest,
           request: Request) -> DeleteThemeResult:
    """Delete a theme directory at an absolute path.

    Path is confined to ``user_content_dir`` server-side — see
    :class:`DeleteTheme.execute`.
    """
    log.info("api DELETE /theme: path=%s", body.path)
    result = request.app.state.trcc.dispatch(DeleteTheme(path=Path(body.path)))
    http_error_if_failed(result)
    return result
