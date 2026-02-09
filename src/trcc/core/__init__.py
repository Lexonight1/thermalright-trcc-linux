"""
TRCC Core - MVC Architecture

Models and Controllers that are GUI-framework independent.
Views (Tkinter, PyQt6, etc.) import from here.

Architecture:
- Models: Pure data classes, no GUI dependencies
- Controllers: Business logic, coordinates models and views
- Views: GUI-specific widgets (in components/ or qt_components/)
"""

from .controllers import (
    DeviceController,
    LCDDeviceController,
    OverlayController,
    ThemeController,
    VideoController,
    create_controller,
)
from .models import (
    DeviceInfo,
    DeviceModel,
    OverlayElement,
    OverlayElementType,
    OverlayModel,
    PlaybackState,
    # Data classes
    ThemeInfo,
    ThemeModel,
    # Enums
    ThemeType,
    VideoModel,
    VideoState,
)

__all__ = [
    # Enums
    'ThemeType',
    'PlaybackState',
    'OverlayElementType',
    # Models
    'ThemeInfo',
    'ThemeModel',
    'DeviceInfo',
    'DeviceModel',
    'VideoState',
    'VideoModel',
    'OverlayElement',
    'OverlayModel',
    # Controllers
    'ThemeController',
    'DeviceController',
    'VideoController',
    'OverlayController',
    'LCDDeviceController',
    'create_controller',
]
