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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

log = logging.getLogger(__name__)


# =========================================================================
# Default asset directory
# =========================================================================

# qtgui SHARES gui's asset tree (``ui/gui/assets``) — one colour copy, no
# duplicate monochrome set is shipped.  Every asset loaded here is greyscaled at
# runtime (see ``_pixmap``), so qtgui renders the same chrome in a neutral grey
# skin while the user's machine does the greyscale on demand — lazily, only for
# the assets it actually displays, and cached.  Installed packages can override
# the root via ``set_assets_dir``.
def _default_assets_dir() -> Path:
    trcc_mod = sys.modules.get("trcc")
    trcc_file = getattr(trcc_mod, "__file__", None) if trcc_mod else None
    if trcc_file is None:
        return Path.cwd() / "ui" / "gui" / "assets"
    return Path(trcc_file).resolve().parent / "ui" / "gui" / "assets"


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
    """Load + cache a pixmap by name; placeholder if missing.

    Every asset is GREYSCALED on load (alpha preserved) so qtgui looks like
    the gui — same chrome, same layout — but in a neutral grey skin instead of
    the legacy full-colour proprietary look.  Live device renders never pass
    through here, so the actual frame shown to the device keeps its true colour.
    """
    path = _resolve(name)
    if not path.is_file():
        log.debug("AssetService: missing asset %r at %s", name, path)
        return _placeholder()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        log.debug("AssetService: failed to load %r as pixmap", name)
        return _placeholder()
    return _greyscale(pixmap)


def _greyscale(pixmap: QPixmap) -> QPixmap:
    """Desaturate a pixmap to grey, preserving its alpha channel.

    ``Format_Grayscale8`` is opaque, so the original alpha is re-applied via a
    ``DestinationIn`` composite — transparent chrome stays transparent.
    """
    src = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    grey = src.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
        QImage.Format.Format_ARGB32,
    )
    painter = QPainter(grey)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_DestinationIn,
    )
    painter.drawImage(0, 0, src)
    painter.end()
    return QPixmap.fromImage(grey)


def _placeholder() -> QPixmap:
    """1×1 transparent pixmap — used when an asset isn't on disk."""
    pixmap = QPixmap(1, 1)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def thumbnail_icon(path: Path, size: int = 96) -> QIcon:
    """A scaled COLOUR :class:`QIcon` for a content-preview image.

    Used by the theme / mask / background browsers to show what each entry
    actually looks like.  Unlike chrome assets (:func:`_pixmap`), previews are
    NOT greyscaled — they're content, same as the live device render.  A
    missing / unloadable file yields a transparent placeholder icon so the grid
    still lays out cleanly.
    """
    if path.is_file():
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            return QIcon(pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
    log.debug("thumbnail_icon: no preview at %s", path)
    return QIcon(_placeholder())


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
