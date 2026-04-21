"""GUI panels — one class per top-level tab or page."""

from .device_panel import DevicePanel
from .display_panel import DisplayPanel
from .led_panel import LedPanel

__all__ = ["DevicePanel", "DisplayPanel", "LedPanel"]
