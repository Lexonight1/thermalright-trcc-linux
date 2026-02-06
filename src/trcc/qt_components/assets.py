"""
Asset loader for PyQt6 GUI components.

Loads background images and icons from assets/gui/ directory.
Images are extracted from Windows TRCC resources using tools/extract_resx_images.py
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

# Asset directory (relative to this file)
ASSETS_DIR = Path(__file__).parent.parent.parent / 'assets' / 'gui'


@lru_cache(maxsize=128)
def get_asset_path(name: str) -> Path:
    """
    Get full path to an asset file.

    Args:
        name: Asset filename (e.g., 'P0CZTV.png')

    Returns:
        Full path to the asset file
    """
    return ASSETS_DIR / name


@lru_cache(maxsize=64)
def load_pixmap(name: str, scale_width: int | None = None, scale_height: int | None = None) -> QPixmap:
    """
    Load a pixmap from assets directory.

    Args:
        name: Asset filename
        scale_width: Optional width to scale to
        scale_height: Optional height to scale to

    Returns:
        QPixmap (empty if file not found)
    """
    path = get_asset_path(name)
    if not path.exists():
        print(f"[!] Asset not found: {name}")
        return QPixmap()

    pixmap = QPixmap(str(path))

    if scale_width and scale_height:
        pixmap = pixmap.scaled(
            scale_width, scale_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

    return pixmap


def asset_exists(name: str) -> bool:
    """Check if an asset file exists."""
    return get_asset_path(name).exists()


# ============================================================================
# Asset name constants matching Windows resource names
# ============================================================================

class Assets:
    """Windows resource names mapped to asset filenames."""

    # Main form backgrounds
    FORM_CZTV_BG = 'P0CZTV.png'
    FORM_CZTV_BG_EN = 'P0CZTVen.png'

    # Theme panel backgrounds (732x652)
    THEME_LOCAL_BG = 'P0本地主题.png'
    THEME_LOCAL_BG_EN = 'P0本地主题en.png'
    THEME_WEB_BG = 'P0云端背景.png'
    THEME_WEB_BG_EN = 'P0云端背景en.png'
    THEME_MASK_BG = 'P0云端主题.png'
    THEME_MASK_BG_EN = 'P0云端主题en.png'
    THEME_SETTING_BG = 'P0主题设置.png'

    # Preview frame backgrounds (500x500)
    PREVIEW_320X320 = 'P预览320X320.png'
    PREVIEW_320X240 = 'P预览320X240.png'
    PREVIEW_240X320 = 'P预览240X320.png'
    PREVIEW_240X240 = 'P预览240X240.png'
    PREVIEW_360X360 = 'P预览360360圆.png'
    PREVIEW_480X480 = 'P预览480X480.png'

    # Tab buttons (normal/selected)
    TAB_LOCAL = 'P本地主题.png'
    TAB_LOCAL_ACTIVE = 'P本地主题a.png'
    TAB_CLOUD = 'P云端背景.png'
    TAB_CLOUD_ACTIVE = 'P云端背景a.png'
    TAB_MASK = 'P云端主题.png'
    TAB_MASK_ACTIVE = 'P云端主题a.png'
    TAB_SETTINGS = 'P主题设置.png'
    TAB_SETTINGS_ACTIVE = 'P主题设置a.png'

    # Bottom control buttons
    BTN_SAVE = 'P保存主题.png'
    BTN_EXPORT = 'P导出.png'
    BTN_IMPORT = 'P导入.png'

    # Title bar buttons
    BTN_HELP = 'P帮助.png'
    BTN_POWER = 'Alogout默认.png'
    BTN_POWER_HOVER = 'Alogout选中.png'

    # Video controls background
    VIDEO_CONTROLS_BG = 'ucBoFangQiKongZhi1.BackgroundImage.png'

    # Settings panel sub-backgrounds (from UCThemeSetting.resx)
    SETTINGS_CONTENT = 'P01内容.png'
    SETTINGS_CONTENT_EN = 'P01内容en.png'
    SETTINGS_PARAMS = 'P01参数面板.png'
    SETTINGS_PARAMS_EN = 'P01参数面板en.png'

    # UCThemeSetting sub-component backgrounds (from .resx)
    OVERLAY_GRID_BG = 'ucXiTongXianShi1.BackgroundImage.png'        # 472x430
    OVERLAY_ADD_BG = 'ucXiTongXianShiAdd1.BackgroundImage.png'      # 230x430
    OVERLAY_COLOR_BG = 'ucXiTongXianShiColor1.BackgroundImage.png'  # 230x374
    OVERLAY_TABLE_BG = 'ucXiTongXianShiTable1.BackgroundImage.png'  # 230x54

    # Video cut background (from FormCZTV.resx)
    VIDEO_CUT_BG = 'ucVideoCut1.BackgroundImage.png'                # 500x702

    # Play/Pause icons
    ICON_PLAY = 'P0播放.png'
    ICON_PAUSE = 'P0暂停.png'

    # Sidebar (UCDevice)
    SIDEBAR_BG = 'A0硬件列表.png'
    SENSOR_BTN = 'A1传感器.png'
    SENSOR_BTN_ACTIVE = 'A1传感器a.png'
    ABOUT_BTN = 'A1关于.png'
    ABOUT_BTN_ACTIVE = 'A1关于a.png'

    # About / Control Center panel (UCAbout)
    ABOUT_BG = 'A0关于.png'
    ABOUT_LOGOUT = 'Alogout默认.png'
    ABOUT_LOGOUT_HOVER = 'Alogout选中.png'
    CHECKBOX_OFF = 'P点选框.png'
    CHECKBOX_ON = 'P点选框A.png'

    @classmethod
    def get_preview_for_resolution(cls, width: int, height: int) -> str:
        """Get preview frame asset name for resolution."""
        name = f'P预览{width}X{height}.png'
        if asset_exists(name):
            return name
        # Try alternate naming
        name_alt = f'P预览{width}x{height}.png'
        if asset_exists(name_alt):
            return name_alt
        # Fall back to 320x320
        return cls.PREVIEW_320X320

    @classmethod
    def get_localized(cls, base_name: str, lang: str = 'en') -> str:
        """
        Get localized asset name.

        Args:
            base_name: Base asset name (e.g., 'P0CZTV.png')
            lang: Language code ('en', 'tc', 'd', 'f', etc.)

        Returns:
            Localized asset name if exists, else base name
        """
        if lang == 'cn' or lang == '':
            return base_name

        # Try language suffix
        name_parts = base_name.rsplit('.', 1)
        localized = f"{name_parts[0]}{lang}.{name_parts[1]}"

        if asset_exists(localized):
            return localized
        return base_name
