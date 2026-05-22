"""Asset loader for PySide6 GUI components.

Centralizes all asset resolution — auto-appends .png for base names,
handles localized variants, and provides pixmap loading.

All GUI assets live in gui/assets/ and are extracted from Windows TRCC
resources using tools/extract_resx_images.py.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

log = logging.getLogger(__name__)

# Bundled asset directory (inside package)
_PKG_ASSETS_DIR = Path(__file__).parent / 'assets'

# Resolved at runtime by the platform adapter via set_assets_dir().
# Falls back to package dir until the builder initializes it.
_ASSETS_DIR = _PKG_ASSETS_DIR


def set_assets_dir(path: Path) -> None:
    """Set the resolved asset directory (called by platform adapter)."""
    global _ASSETS_DIR
    _ASSETS_DIR = path
    _resolve.cache_clear()
    log.debug("Assets dir set to %s", path)


@lru_cache(maxsize=256)
def _resolve(name: str) -> Path:
    """Resolve asset name to filesystem path, auto-appending .png if needed.

    Data layer stores base names without extension (e.g. "led_preview_ax120").
    All GUI assets are .png — this bridges that gap in one place.
    """
    path = _ASSETS_DIR / name
    if path.exists():
        return path
    if '.' not in name:
        png = _ASSETS_DIR / f"{name}.png"
        if png.exists():
            return png
    return path  # return original (non-existent) for consistent error handling


class Assets:
    """Centralized asset resolution for all GUI components.

    Handles .png auto-appending, pixmap loading, existence checks,
    and localized asset variants. Single entry point — no free functions.
    """

    # Form1 background (full window with sidebar + gold bar + sensor grid)
    FORM1_BG = 'app_main_bg.png'

    # Main form backgrounds
    FORM_CZTV_BG = 'app_form_bg.png'

    # Theme panel backgrounds (732x652)
    THEME_LOCAL_BG = 'app_theme_base_bg.png'
    THEME_WEB_BG = 'app_theme_gallery_bg.png'
    THEME_MASK_BG = 'app_theme_gallery_bg.png'
    THEME_SETTING_BG = 'app_theme_settings_bg.png'

    # Preview frame backgrounds (500x500)
    PREVIEW_320X320 = 'preview_320x320.png'
    PREVIEW_320X240 = 'preview_320x240.png'
    PREVIEW_240X320 = 'preview_240x320.png'
    PREVIEW_240X240 = 'preview_240x240.png'
    PREVIEW_360X360 = 'preview_360x360_round.png'
    PREVIEW_480X480 = 'preview_480x480.png'

    # Tab buttons (normal/selected)
    TAB_LOCAL = 'app_tab_local.png'
    TAB_LOCAL_ACTIVE = 'app_tab_local_active.png'
    TAB_CLOUD = 'app_tab_cloud.png'
    TAB_CLOUD_ACTIVE = 'app_tab_cloud_active.png'
    TAB_MASK = 'app_tab_mask.png'
    TAB_MASK_ACTIVE = 'app_tab_mask_active.png'
    TAB_SETTINGS = 'app_tab_settings.png'
    TAB_SETTINGS_ACTIVE = 'app_tab_settings_active.png'

    # Bottom control buttons
    BTN_SAVE = 'app_save.png'
    BTN_EXPORT = 'app_export.png'
    BTN_IMPORT = 'app_import.png'

    # Title bar buttons
    BTN_HELP = 'app_help.png'
    BTN_POWER = 'app_power.png'
    BTN_POWER_HOVER = 'app_power_hover.png'

    # Video controls background
    VIDEO_CONTROLS_BG = 'preview_video_controls_bg.png'

    # Settings panel sub-backgrounds (from UCThemeSetting.resx)
    SETTINGS_CONTENT = 'settings_overlay.png'
    SETTINGS_PARAMS = 'settings_params.png'

    # UCThemeSetting sub-component backgrounds (from .resx)
    OVERLAY_GRID_BG = 'settings_overlay_grid_bg.png'        # 472x430
    OVERLAY_ADD_BG = 'settings_overlay_add_bg.png'      # 230x430
    OVERLAY_COLOR_BG = 'settings_overlay_color_bg.png'  # 230x374
    OVERLAY_TABLE_BG = 'settings_overlay_table_bg.png'  # 230x54

    # Video cut background (from FormCZTV.resx)
    VIDEO_CUT_BG = 'video_cut_bg.png'                # 500x702

    # Play/Pause icons
    ICON_PLAY = 'preview_play.png'
    ICON_PAUSE = 'preview_pause.png'

    # Sidebar (UCDevice)
    SIDEBAR_BG = 'sidebar_bg.png'
    SENSOR_BTN = 'sidebar_sensor.png'
    SENSOR_BTN_ACTIVE = 'sidebar_sensor_active.png'
    ABOUT_BTN = 'sidebar_about.png'
    ABOUT_BTN_ACTIVE = 'sidebar_about_active.png'

    # About / Control Center panel (UCAbout)
    ABOUT_BG = 'app_about_bg.png'
    ABOUT_LOGOUT = 'app_power.png'
    ABOUT_LOGOUT_HOVER = 'app_power_hover.png'
    CHECKBOX_OFF = 'shared_checkbox_off.png'
    CHECKBOX_ON = 'shared_checkbox_on.png'
    TOGGLE_ON = 'shared_toggle_on.png'
    TOGGLE_OFF = 'shared_toggle_off.png'
    PLUS = 'display_mode_plus.png'
    MINUS = 'display_mode_minus.png'
    UPDATE_BTN = 'about_update.png'
    SYSINFO_BG = 'sidebar_sysinfo_bg.png'

    @classmethod
    def path(cls, name: str) -> Path:
        """Resolve asset name to full path (.png auto-appended if needed)."""
        return _resolve(name)

    @classmethod
    def get(cls, name: str) -> str | None:
        """Return asset path as string if it exists, else None.

        Uses forward slashes — Qt stylesheets interpret backslashes
        as CSS escapes (C:\\Users → C:Users).
        """
        p = _resolve(name)
        return p.as_posix() if p.exists() else None

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if an asset file exists (.png auto-appended if needed)."""
        return _resolve(name).exists()

    @classmethod
    @lru_cache(maxsize=128)
    def load_pixmap(cls, name: str,
                    width: int | None = None,
                    height: int | None = None) -> QPixmap:
        """Load a QPixmap from assets directory.

        Args:
            name: Asset filename or base name (.png auto-appended).
            width: Optional scale width.
            height: Optional scale height.

        Returns:
            QPixmap (empty if file not found).
        """
        p = _resolve(name)
        if not p.exists():
            log.warning("Asset not found: %s", name)
            return QPixmap()

        # Use forward slashes — Qt handles them on all platforms,
        # avoids Windows backslash issues with sandboxed Python paths.
        pixmap = QPixmap(p.as_posix())
        if width and height:
            pixmap = pixmap.scaled(
                width, height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap

    @classmethod
    def get_preview_for_resolution(cls, width: int, height: int) -> str:
        """Get preview frame asset name for a resolution."""
        name = f'preview_{width}x{height}.png'
        if cls.exists(name):
            return name
        name_alt = f'preview_{width}X{height}.png'
        if cls.exists(name_alt):
            return name_alt
        return cls.PREVIEW_320X320

    @classmethod
    def get_localized(cls, base_name: str, lang: str = 'en') -> str:
        """Get localized asset name with language suffix.

        Args:
            base_name: Base asset name (e.g., 'P0CZTV' or 'P0CZTV.png').
            lang: ISO 639-1 language code ('en', 'de', 'fr', 'zh', etc.).

        Returns:
            Localized asset name if exists, else base name.
        """
        if lang == 'zh':
            # Simplified Chinese is the base asset (no suffix)
            return base_name

        # Map ISO code to legacy C# suffix for asset filenames
        from trcc.core.models import ISO_TO_LEGACY
        suffix = ISO_TO_LEGACY.get(lang, lang)

        # Split extension if present, insert lang suffix before it
        if '.' in base_name:
            stem, ext = base_name.rsplit('.', 1)
            localized = f"{stem}{suffix}.{ext}"
        else:
            localized = f"{base_name}{suffix}"

        if cls.exists(localized):
            return localized
        return base_name
