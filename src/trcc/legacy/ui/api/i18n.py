"""Internationalization endpoints — language listing and selection."""
from __future__ import annotations

import logging

from fastapi import APIRouter

log = logging.getLogger(__name__)

router = APIRouter(prefix="/i18n", tags=["i18n"])


@router.get("/languages")
def get_languages() -> dict:
    """List all available languages with ISO 639-1 codes and native names."""
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    return {"languages": {code: name for code, name in LANGUAGE_NAMES.items()}}


@router.get("/language")
def get_language() -> dict:
    """Get the current language code."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    code = _trcc().settings.lang
    return {"code": code, "name": LANGUAGE_NAMES.get(code, code)}


@router.put("/language/{code}")
def set_language(code: str) -> dict:
    """Set the application language by ISO 639-1 code."""
    from fastapi import HTTPException

    from trcc.legacy._boot import trcc
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    result = trcc().control_center.set_language(code)
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail=result.error or f"Unknown language code '{code}'",
        )
    return {"code": code, "name": LANGUAGE_NAMES.get(code, code)}
