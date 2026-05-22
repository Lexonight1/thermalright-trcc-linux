"""GUI panels — one class per top-level screen."""

from .about_panel import AboutPanel
from .cloud_theme_browser import CloudThemeBrowser
from .configuration_panel import ConfigurationPanel
from .device_panel import DevicePanel
from .display_panel import DisplayPanel
from .led_panel import LedPanel
from .local_theme_browser import LocalThemeBrowser
from .mask_browser import MaskBrowser
from .overlay_editor import OverlayEditorPanel
from .preview_panel import PreviewPanel
from .screencast_panel import ScreencastPanel
from .sidebar import ActivitySidebar
from .status_panel import StatusPanel
from .system_panel import SystemPanel

__all__ = [
    "AboutPanel",
    "ActivitySidebar",
    "CloudThemeBrowser",
    "ConfigurationPanel",
    "DevicePanel",
    "DisplayPanel",
    "LedPanel",
    "LocalThemeBrowser",
    "MaskBrowser",
    "OverlayEditorPanel",
    "PreviewPanel",
    "ScreencastPanel",
    "StatusPanel",
    "SystemPanel",
]
