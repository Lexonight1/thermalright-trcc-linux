"""
TRCC Core — Ports, Devices, Models

Ports (ABCs): Device, Renderer — contracts for adapters.
Devices: LCDDevice, LEDDevice — concrete Device implementations.
Builder: ControllerBuilder — assembles devices with DI.
Models: Data classes only (ThemeInfo, DeviceInfo, VideoState, etc.)
"""

from .models import (
    DeviceInfo,
    PlaybackState,
    ThemeInfo,
    ThemeType,
    VideoState,
)

__all__ = [
    'DeviceInfo',
    'PlaybackState',
    'ThemeInfo',
    'ThemeType',
    'VideoState',
]
