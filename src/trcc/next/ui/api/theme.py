"""``/theme/*`` router — save / export / import.

All three endpoints take server-side paths.  Multipart upload for
``import`` is intentionally deferred — the API user is expected to put
archives on the server's filesystem and reference them by path.

Path sanitization (basename whitelist within ``user_content_dir``) is
applied at the router edge so a malicious ``name`` can't escape into
arbitrary filesystem locations.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import ExportTheme, ImportTheme, SaveTheme
from ._shared import (
    http_error_if_failed,
    to_theme_export_response,
    to_theme_import_response,
    to_theme_response,
)
from .schemas import (
    ThemeExportRequest,
    ThemeExportResponse,
    ThemeImportRequest,
    ThemeImportResponse,
    ThemeResponse,
    ThemeSaveRequest,
)

router = APIRouter(prefix="/theme", tags=["theme"])


def _safe_basename(value: str) -> str:
    """Strip any directory parts; raise if the result is empty."""
    name = Path(value).name.strip()
    if not name:
        raise HTTPException(400, "name required")
    return name


@router.post("/save", response_model=ThemeResponse)
def save(body: ThemeSaveRequest, request: Request) -> ThemeResponse:
    name = _safe_basename(body.name)
    result = request.app.state.trcc.dispatch(SaveTheme(key=body.key, name=name))
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/export", response_model=ThemeExportResponse)
def export(body: ThemeExportRequest,
           request: Request) -> ThemeExportResponse:
    theme_name = _safe_basename(body.theme_name)
    # Archive path is server-controlled — clients pass an absolute path;
    # we accept any writable filesystem location.  CLI users are
    # responsible for choosing where to put the .tr file.
    result = request.app.state.trcc.dispatch(
        ExportTheme(theme_name=theme_name, archive_path=Path(body.archive_path)),
    )
    http_error_if_failed(result)
    return to_theme_export_response(result)


@router.post("/import", response_model=ThemeImportResponse)
def import_(body: ThemeImportRequest,
            request: Request) -> ThemeImportResponse:
    """Import a theme archive from a server-side path."""
    archive = Path(body.archive_path)
    name = body.name.strip()
    if name:
        name = _safe_basename(name)
    result = request.app.state.trcc.dispatch(
        ImportTheme(archive_path=archive, name=name),
    )
    http_error_if_failed(result)
    return to_theme_import_response(result)
