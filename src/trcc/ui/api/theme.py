"""``/theme/*`` router — save / export / import.

Most endpoints take server-side paths so an automation script can
reference archives that already live on the same host.  Remote
clients without filesystem access use the multipart variants
(``-upload`` / ``-download`` suffixes) which stage to a tempfile
before / after the Command dispatch.

Path sanitization (basename whitelist within ``user_content_dir``) is
applied at the router edge so a malicious ``name`` can't escape into
arbitrary filesystem locations.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...core.commands import (
    DeleteTheme,
    EnsureDataDownload,
    ExportConfig,
    ExportDcTheme,
    ExportTheme,
    ImportConfig,
    ImportTheme,
    ListCloudThemes,
    ListThemes,
    ListWebThemes,
    LoadCloudTheme,
    SaveTheme,
)
from ._shared import (
    http_error_if_failed,
    to_cloud_theme_load_response,
    to_cloud_themes_list_response,
    to_delete_theme_response,
    to_import_config_response,
    to_theme_dc_export_response,
    to_theme_export_response,
    to_theme_import_response,
    to_theme_response,
    to_themes_list_response,
)
from .schemas import (
    CloudThemeLoadRequest,
    CloudThemeLoadResponse,
    CloudThemesListResponse,
    DeleteThemeRequest,
    DeleteThemeResponse,
    EnsureDataResponse,
    ImportConfigResponse,
    ThemeDcExportRequest,
    ThemeDcExportResponse,
    ThemeExportRequest,
    ThemeExportResponse,
    ThemeImportRequest,
    ThemeImportResponse,
    ThemeResponse,
    ThemeSaveRequest,
    ThemesListResponse,
    WebThemeSchema,
)

log = logging.getLogger(__name__)


def _parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse ``"320x320"`` → ``(320, 320)``; raise 400 on bad input."""
    try:
        w_str, h_str = resolution.lower().split("x", 1)
        return int(w_str), int(h_str)
    except (ValueError, AttributeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"bad resolution {resolution!r} — expected WxH (e.g. 320x320)",
        ) from e

router = APIRouter(prefix="/theme", tags=["theme"])


def _safe_basename(value: str) -> str:
    """Strip any directory parts; raise if the result is empty."""
    name = Path(value).name.strip()
    if not name:
        raise HTTPException(400, "name required")
    return name


@router.post("/save", response_model=ThemeResponse)
def save(body: ThemeSaveRequest, request: Request) -> ThemeResponse:
    log.info("api POST /theme/save: key=%s name=%s", body.key, body.name)
    name = _safe_basename(body.name)
    result = request.app.state.trcc.dispatch(SaveTheme(key=body.key, name=name))
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/export", response_model=ThemeExportResponse)
def export(body: ThemeExportRequest,
           request: Request) -> ThemeExportResponse:
    log.info(
        "api POST /theme/export: key=%s theme_name=%s archive_path=%s",
        body.key, body.theme_name, body.archive_path,
    )
    theme_name = _safe_basename(body.theme_name)
    # Archive path is server-controlled — clients pass an absolute path;
    # we accept any writable filesystem location.  CLI users are
    # responsible for choosing where to put the .tr file.
    result = request.app.state.trcc.dispatch(
        ExportTheme(
            key=body.key,
            theme_name=theme_name,
            archive_path=Path(body.archive_path),
        ),
    )
    http_error_if_failed(result)
    return to_theme_export_response(result)


@router.post("/import", response_model=ThemeImportResponse)
def import_(body: ThemeImportRequest,
            request: Request) -> ThemeImportResponse:
    """Import a theme archive from a server-side path."""
    log.info(
        "api POST /theme/import: key=%s archive_path=%s name=%s",
        body.key, body.archive_path, body.name,
    )
    archive = Path(body.archive_path)
    name = body.name.strip()
    if name:
        name = _safe_basename(name)
    result = request.app.state.trcc.dispatch(
        ImportTheme(key=body.key, archive_path=archive, name=name),
    )
    http_error_if_failed(result)
    return to_theme_import_response(result)


@router.post("/import-upload", response_model=ThemeImportResponse)
async def import_upload(
    request: Request,
    key: str,
    archive: UploadFile = File(...),
    name: str = "",
) -> ThemeImportResponse:
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
    paths = request.app.state.trcc.platform.paths()
    uploads_dir = (paths.user_content_dir() / "uploads").resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)
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
    return to_theme_import_response(result)


@router.get("/{key}/{theme_name}/download")
def download(key: str, theme_name: str, request: Request) -> FileResponse:
    """Stream a theme archive as a multipart download.

    Server-side equivalent of ``POST /export`` but bytes flow back over
    the HTTP response instead of landing on the server filesystem.
    The archive is built in a tempfile and ``FileResponse`` cleans it
    up after the connection closes via a ``BackgroundTask``.
    """
    log.info(
        "api GET /theme/{key}/{theme_name}/download: key=%s theme_name=%s",
        key, theme_name,
    )
    safe_name = _safe_basename(theme_name)
    tmp = Path(tempfile.mkstemp(suffix=".tr", prefix="trcc-export-")[1])
    result = request.app.state.trcc.dispatch(
        ExportTheme(key=key, theme_name=safe_name, archive_path=tmp),
    )
    if not result.ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise HTTPException(400, result.message)
    return FileResponse(
        path=tmp,
        media_type="application/octet-stream",
        filename=f"{safe_name}.tr",
        background=BackgroundTask(_unlink_quietly, tmp),
    )


@router.get("/{key}/config-download")
def config_download(key: str, request: Request) -> FileResponse:
    """Stream a device's settings snapshot as a JSON download.

    REST equivalent of the cli ``theme export-config``: bytes flow back
    over the response instead of landing on the server filesystem.  The
    snapshot is written to a tempfile that ``FileResponse`` cleans up
    after the connection closes.
    """
    log.info("api GET /theme/{key}/config-download: key=%s", key)
    tmp = Path(tempfile.mkstemp(suffix=".json", prefix="trcc-config-")[1])
    result = request.app.state.trcc.dispatch(
        ExportConfig(key=key, output_path=tmp),
    )
    if not result.ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise HTTPException(400, result.message)
    safe_key = _safe_basename(key)
    return FileResponse(
        path=tmp,
        media_type="application/json",
        filename=f"{safe_key}-config.json",
        background=BackgroundTask(_unlink_quietly, tmp),
    )


@router.post("/config/import-upload", response_model=ImportConfigResponse)
async def config_import_upload(
    request: Request,
    key: str,
    config: UploadFile = File(...),
) -> ImportConfigResponse:
    """Restore a device's settings from an uploaded JSON snapshot.

    Multipart equivalent of the cli ``theme import-config`` for remote
    clients without server filesystem access.  The upload is staged to a
    tempfile, imported, and the tempfile cleaned up after dispatch.
    """
    log.info(
        "api POST /theme/config/import-upload: key=%s filename=%s",
        key, config.filename,
    )
    paths = request.app.state.trcc.platform.paths()
    uploads_dir = (paths.user_content_dir() / "uploads").resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)
    staged = uploads_dir / f"{uuid.uuid4().hex}.json"
    try:
        with staged.open("wb") as f:
            shutil.copyfileobj(config.file, f)
        result = request.app.state.trcc.dispatch(
            ImportConfig(key=key, input_path=staged),
        )
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
    http_error_if_failed(result)
    return to_import_config_response(result)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


@router.get("/list", response_model=ThemesListResponse)
def list_(
    request: Request,
    directory: str | None = None,
    key: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> ThemesListResponse:
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
            ListThemes(directory=Path(directory)),
        )
    else:
        resolution: tuple[int, int] | None = None
        if key is not None:
            device = request.app.state.trcc.devices.get(key)
            if device is None or device.profile is None:
                return ThemesListResponse(
                    ok=False, directory="", themes=[],
                    message=(f"Device {key} not connected — connect first "
                             "so we know the target resolution"),
                )
            resolution = device.profile.resolution
        elif width is not None and height is not None:
            resolution = (width, height)
        result = request.app.state.trcc.dispatch(
            ListThemes(resolution=resolution),
        )
    http_error_if_failed(result)
    return to_themes_list_response(result)


@router.get("/cloud", response_model=CloudThemesListResponse)
def cloud_list(
    request: Request,
    category: str = "all",
) -> CloudThemesListResponse:
    """List Thermalright cloud catalog (offline — catalog is static)."""
    log.info("api GET /theme/cloud: category=%s", category)
    result = request.app.state.trcc.dispatch(
        ListCloudThemes(category=category),
    )
    http_error_if_failed(result)
    return to_cloud_themes_list_response(result)


@router.get("/web", response_model=list[WebThemeSchema])
def web_gallery(
    request: Request,
    resolution: str,
) -> list[WebThemeSchema]:
    """Cloud-theme preview gallery for a resolution (e.g. ``320x320``).

    Lists the downloaded ``a001.png`` previews under
    ``data/web/{w}{h}`` — ``preview_url`` resolves against the
    ``/static/web`` mount, ``has_video`` flags a sibling ``.mp4``.
    Empty list when nothing is downloaded yet (call ``POST /theme/init``).
    """
    w, h = _parse_resolution(resolution)
    log.info("api GET /theme/web: resolution=%dx%d", w, h)
    result = request.app.state.trcc.dispatch(ListWebThemes(width=w, height=h))
    # Domain entries → HTTP schema: the API owns the URLs (preview served
    # by the /static/web mount; download via the cloud-load route).
    return [
        WebThemeSchema(
            id=e.id,
            category=e.category,
            has_video=e.has_video,
            preview_url=f"/static/web/{w}{h}/{e.id}.png",
            download_url=f"/theme/cloud/{e.id}",
        )
        for e in result.entries
    ]


@router.post("/init", response_model=EnsureDataResponse)
def init_data(
    request: Request,
    resolution: str,
) -> EnsureDataResponse:
    """Prefetch theme/web/mask archives for a resolution (idempotent).

    For remote clients to call on startup before browsing — works with
    no device connected.  Wraps the ``EnsureDataDownload`` command that
    previously had no route.
    """
    w, h = _parse_resolution(resolution)
    log.info("api POST /theme/init: resolution=%dx%d", w, h)
    result = request.app.state.trcc.dispatch(
        EnsureDataDownload(width=w, height=h),
    )
    return EnsureDataResponse(
        ok=result.ok, message=result.message,
        width=result.width, height=result.height,
        themes_ok=result.themes_ok, web_ok=result.web_ok,
        masks_ok=result.masks_ok,
    )


@router.post("/cloud/{key}", response_model=CloudThemeLoadResponse)
def cloud_load(key: str, body: CloudThemeLoadRequest,
                request: Request) -> CloudThemeLoadResponse:
    """Download a cloud theme + apply it to *key*."""
    log.info(
        "api POST /theme/cloud/{key}: key=%s theme_id=%s",
        key, body.theme_id,
    )
    result = request.app.state.trcc.dispatch(
        LoadCloudTheme(key=key, theme_id=body.theme_id),
    )
    http_error_if_failed(result)
    return to_cloud_theme_load_response(result)


@router.post("/{name}/export-dc", response_model=ThemeDcExportResponse)
def export_dc(name: str, body: ThemeDcExportRequest,
              request: Request) -> ThemeDcExportResponse:
    """Write a theme out as legacy ``config1.dc``."""
    log.info(
        "api POST /theme/{name}/export-dc: name=%s key=%s output_path=%s",
        name, body.key, body.output_path,
    )
    safe_name = _safe_basename(name)
    result = request.app.state.trcc.dispatch(ExportDcTheme(
        key=body.key,
        theme_name=safe_name,
        output_path=Path(body.output_path),
    ))
    http_error_if_failed(result)
    return to_theme_dc_export_response(result)


@router.delete("", response_model=DeleteThemeResponse)
def delete(body: DeleteThemeRequest,
           request: Request) -> DeleteThemeResponse:
    """Delete a theme directory at an absolute path.

    Path is confined to ``user_content_dir`` server-side — see
    :class:`DeleteTheme.execute`.
    """
    log.info("api DELETE /theme: path=%s", body.path)
    result = request.app.state.trcc.dispatch(DeleteTheme(path=Path(body.path)))
    http_error_if_failed(result)
    return to_delete_theme_response(result)
