"""
TRCC Linux - Thermalright LCD Control Center

A Linux implementation of the Thermalright LCD Control Center,
matching the Windows TRCC 2.0.3 protocol.

Features:
- LCD display control via SCSI commands
- System monitoring (CPU, GPU, RAM temperatures)
- Theme support (local, cloud, wallpapers)
- Video and GIF animation playback
- Real-time sensor overlays

Usage:
    # As a library
    from trcc import LCDDriver
    driver = LCDDriver()
    driver.send_frame(image_data)

    # Command line
    trcc-gui          # Launch GUI
    trcc-detect       # Detect LCD device
    trcc-test         # Test display with color cycle
"""

from trcc.__version__ import __version__
from trcc.adapters.device.detector import get_device_path
from trcc.adapters.infra.dc_config import DcConfig
from trcc.adapters.infra.dc_parser import dc_to_overlay_config, parse_dc_file
from trcc.adapters.infra.media_player import VideoDecoder
from trcc.core.models import format_metric

__author__ = "TRCC Linux Contributors"


def detect_devices():
    """Detect connected TRCC LCD devices (platform-aware).

    Convenience for library users — wraps a one-shot platform build +
    detect. For sustained use, build a Trcc explicitly:
        from trcc.core.trcc import Trcc
        from trcc.adapters.system import PlatformFactory
        trcc = Trcc(PlatformFactory.current())
        trcc.discover()
    """
    from trcc.core.ports import Platform
    return list(Platform.for_current_os())


__all__ = [
    # Theme parsing
    "DcConfig",
    # Animation
    "VideoDecoder",
    # Version
    "__version__",
    "dc_to_overlay_config",
    # Detection
    "detect_devices",
    "format_metric",
    "get_device_path",
    "parse_dc_file",
]
