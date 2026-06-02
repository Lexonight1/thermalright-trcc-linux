"""AssetService — single point of asset (image) path resolution.

Architectural role: replaces legacy ``gui/assets.py``'s global state +
100-line constant table with a small service that:

* resolves a base name to a Path (auto-appends ``.png`` if missing);
* caches resolutions so repeated lookups don't re-walk the dir;
* loads ``QPixmap`` lazily — UIs that never paint never load images;
* falls back to a placeholder pixmap when a name isn't on disk, so a
  missing asset shows as a transparent box rather than a hard crash.

Constants for specific asset names live on ``Assets``.  Panels reference
``Assets.<NAME>`` instead of bare strings so name typos surface as
``AttributeError`` immediately.
"""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

log = logging.getLogger(__name__)


# =========================================================================
# Default asset directory
# =========================================================================

# next/ ships assets under ``src/trcc/assets/gui`` (same on-disk location
# as legacy).  Installed packages can override via ``set_assets_dir``.
def _default_assets_dir() -> Path:
    trcc_mod = sys.modules.get("trcc")
    trcc_file = getattr(trcc_mod, "__file__", None) if trcc_mod else None
    if trcc_file is None:
        return Path.cwd() / "assets" / "gui"
    return Path(trcc_file).resolve().parent / "assets" / "gui"


_PKG_ASSETS_DIR = _default_assets_dir()

_assets_dir: Path = _PKG_ASSETS_DIR


def set_assets_dir(path: Path) -> None:
    """Override the asset root (used by alt-install adapters)."""
    global _assets_dir
    _assets_dir = path
    _resolve.cache_clear()
    log.debug("AssetService dir set to %s", path)


@lru_cache(maxsize=512)
def _resolve(name: str) -> Path:
    """Resolve a name → Path.  Auto-appends ``.png`` if no extension."""
    direct = _assets_dir / name
    if direct.exists():
        return direct
    if "." not in name:
        png = _assets_dir / f"{name}.png"
        if png.exists():
            return png
    return direct  # may not exist — caller guards


@lru_cache(maxsize=512)
def _pixmap(name: str) -> QPixmap:
    """Load + cache a pixmap by name; placeholder if missing."""
    path = _resolve(name)
    if not path.is_file():
        log.debug("AssetService: missing asset %r at %s", name, path)
        return _placeholder()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        log.debug("AssetService: failed to load %r as pixmap", name)
        return _placeholder()
    return pixmap


def _placeholder() -> QPixmap:
    """1×1 transparent pixmap — used when an asset isn't on disk."""
    pixmap = QPixmap(1, 1)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


# =========================================================================
# Public API
# =========================================================================


class Assets:
    """Asset name constants + resolver methods.

    Constants get added when a panel needs them — keeps this file from
    accumulating dead names that no one references.
    """

    # Window chrome
    APP_ICON = "trcc_icon.png"
    SPLASH_BG = "splash_bg.png"
    ABOUT_BG = "sidebar_about_bg.png"

    @staticmethod
    def path(name: str) -> Path:
        """Resolve *name* to a Path (may not exist)."""
        return _resolve(name)

    @staticmethod
    def pixmap(name: str) -> QPixmap:
        """Load *name* as a QPixmap; placeholder if missing."""
        return _pixmap(name)

    @staticmethod
    def exists(name: str) -> bool:
        """True if *name* resolves to an on-disk file."""
        return _resolve(name).is_file()
