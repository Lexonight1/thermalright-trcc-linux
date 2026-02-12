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

__version__ = "1.0.0"
__author__ = "TRCC Linux Contributors"

# Core exports
from trcc.dc_config import DcConfig
from trcc.dc_parser import dc_to_overlay_config, parse_dc_file
from trcc.device_detector import detect_devices, get_device_path

# Animation
from trcc.gif_animator import GIFAnimator, GIFThemeLoader
from trcc.lcd_driver import LCDDriver
from trcc.system_info import format_metric, get_all_metrics

# Optional video support
try:
    from trcc.gif_animator import VideoPlayer
except ImportError:
    VideoPlayer = None

__all__ = [
    # Version
    "__version__",
    # Core
    "LCDDriver",
    "detect_devices",
    "get_device_path",
    # System info
    "get_all_metrics",
    "format_metric",
    # Theme parsing
    "DcConfig",
    "parse_dc_file",
    "dc_to_overlay_config",
    # Animation
    "GIFAnimator",
    "GIFThemeLoader",
    "VideoPlayer",
]
