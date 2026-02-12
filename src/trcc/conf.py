"""Application settings and config persistence for TRCC.

Single source of truth for resolution, paths, preferences, and device settings.
Config is stored at ~/.config/trcc/config.json (XDG-compliant).

Usage:
    from trcc.conf import settings

    settings.width          # LCD width
    settings.height         # LCD height
    settings.resolution     # (width, height) tuple
    settings.theme_dir      # ThemeDir for current resolution
    settings.web_dir        # Cloud theme preview dir
    settings.masks_dir      # Cloud mask overlay dir
    settings.temp_unit      # 0=Celsius, 1=Fahrenheit

    # Low-level config access
    from trcc.conf import load_config, save_config, device_config_key
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .paths import (
    USER_DATA_DIR,
    ThemeDir,
    get_web_dir,
    get_web_masks_dir,
)

log = logging.getLogger(__name__)

# =========================================================================
# Config file location (XDG-compliant)
# =========================================================================

_XDG_CONFIG = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
CONFIG_DIR = os.path.join(_XDG_CONFIG, 'trcc')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

# USBLCD (SCSI/RGB565) supported resolutions
SUPPORTED_RESOLUTIONS = [
    (240, 240),
    (320, 320),
    (480, 480),
    (640, 480),
]


# =========================================================================
# Low-level config persistence
# =========================================================================

def load_config() -> dict:
    """Load user config from disk. Returns empty dict on missing/corrupt file."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict):
    """Save user config to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


# =========================================================================
# Resolution persistence
# =========================================================================

def get_saved_resolution() -> tuple[int, int]:
    """Get saved LCD resolution, defaulting to (320, 320)."""
    config = load_config()
    res = config.get('resolution', [320, 320])
    if isinstance(res, list) and len(res) == 2:
        return (int(res[0]), int(res[1]))
    return (320, 320)


def save_resolution(width: int, height: int):
    """Persist LCD resolution to config."""
    config = load_config()
    config['resolution'] = [width, height]
    save_config(config)


# =========================================================================
# Temperature unit persistence
# =========================================================================

def get_saved_temp_unit() -> int:
    """Get saved temperature unit. 0=Celsius, 1=Fahrenheit. Defaults to 0."""
    return load_config().get('temp_unit', 0)


def save_temp_unit(unit: int):
    """Persist temperature unit to config. 0=Celsius, 1=Fahrenheit."""
    config = load_config()
    config['temp_unit'] = unit
    save_config(config)


# =========================================================================
# Per-device configuration
# =========================================================================

def device_config_key(index: int, vid: int, pid: int) -> str:
    """Build per-device config key, e.g. '0:87cd_70db'."""
    return f"{index}:{vid:04x}_{pid:04x}"


def get_device_config(key: str) -> dict:
    """Get per-device config dict. Returns empty dict if not found."""
    return load_config().get('devices', {}).get(key, {})


def save_device_setting(key: str, setting: str, value):
    """Save a single setting for a device."""
    config = load_config()
    devices = config.setdefault('devices', {})
    dev_cfg = devices.setdefault(key, {})
    dev_cfg[setting] = value
    save_config(config)


# =========================================================================
# Resolution installation markers
# =========================================================================

def clear_installed_resolutions():
    """Remove all resolution-installed markers (used by uninstall)."""
    config = load_config()
    config.pop("installed_resolutions", None)
    save_config(config)


# =========================================================================
# Settings singleton
# =========================================================================

class Settings:
    """Application-wide settings singleton.

    Components read from here instead of calling path helpers directly.
    ``set_resolution()`` updates derived paths and persists to config.
    """

    def __init__(self) -> None:
        # Resolution (from saved config)
        w, h = get_saved_resolution()
        self._width = w
        self._height = h

        # Derived paths (resolved for current resolution)
        self.theme_dir: Optional[ThemeDir] = None
        self.web_dir: Optional[Path] = None
        self.masks_dir: Optional[Path] = None

        # User preferences
        self.temp_unit: int = get_saved_temp_unit()

        # Static paths
        self.user_data_dir = Path(USER_DATA_DIR)

        # Resolve for initial resolution
        if w and h:
            self._resolve_paths()

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    def set_resolution(self, width: int, height: int, persist: bool = True) -> None:
        """Update resolution and re-resolve all derived paths."""
        if (width, height) == (self._width, self._height):
            return
        log.info("Settings: resolution %dx%d → %dx%d",
                 self._width, self._height, width, height)
        self._width = width
        self._height = height
        self._resolve_paths()
        if persist:
            save_resolution(width, height)

    def set_temp_unit(self, unit: int) -> None:
        """Set temperature unit (0=Celsius, 1=Fahrenheit) and persist."""
        self.temp_unit = unit
        save_temp_unit(unit)

    def _resolve_paths(self) -> None:
        """Resolve theme/web/mask directories for current resolution."""
        w, h = self._width, self._height
        self.theme_dir = ThemeDir.for_resolution(w, h)
        self.web_dir = Path(get_web_dir(w, h))
        self.masks_dir = Path(get_web_masks_dir(w, h))


# Module-level singleton — import and use directly
settings = Settings()
