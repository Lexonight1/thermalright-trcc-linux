"""
Central path constants and utilities for TRCC.

All path calculations happen once here. Components import what they need.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING, Optional

log = logging.getLogger(__name__)

# PIL import (done once, shared by all components)
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    if TYPE_CHECKING:
        from PIL import Image, ImageTk

# Base directories (calculated once at import time)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # src/trcc/
SRC_DIR = os.path.dirname(_THIS_DIR)                     # src/
PROJECT_ROOT = os.path.dirname(SRC_DIR)                  # project root

# Asset directories
ASSETS_DIR = os.path.join(SRC_DIR, 'assets')
RESOURCES_DIR = os.path.join(ASSETS_DIR, 'gui')

# User config directory
USER_CONFIG_DIR = os.path.expanduser('~/.trcc')
USER_DATA_DIR = os.path.join(USER_CONFIG_DIR, 'data')


def _has_actual_themes(theme_dir: str) -> bool:
    """Check if a Theme* directory has actual theme subfolders (not just .gitkeep)."""
    if not os.path.isdir(theme_dir):
        return False
    for item in os.listdir(theme_dir):
        # Theme subfolders are like "000a", "001b", etc.
        item_path = os.path.join(theme_dir, item)
        if os.path.isdir(item_path) and not item.startswith('.'):
            return True
    return False


def _find_data_dir() -> str:
    """Find the data directory with themes.

    Search order:
    1. src/data/ (package location)
    2. Project root data/ (development)
    3. ~/.trcc/data/ (user downloads)
    """
    candidates = [
        os.path.join(SRC_DIR, 'data'),
        os.path.join(PROJECT_ROOT, 'data'),
        USER_DATA_DIR,
    ]

    for candidate in candidates:
        if os.path.isdir(candidate):
            # Check if this directory has Theme* folders with actual content
            for item in os.listdir(candidate):
                if item.startswith('Theme'):
                    theme_path = os.path.join(candidate, item)
                    if _has_actual_themes(theme_path):
                        return candidate

    # Fallback to src/data even if empty
    return os.path.join(SRC_DIR, 'data')


# Data directories
DATA_DIR = _find_data_dir()
THEMES_DIR = DATA_DIR  # Theme folders are directly in data/

# Common search paths for resource loading
RESOURCE_SEARCH_PATHS = [RESOURCES_DIR]


def get_theme_dir(width: int, height: int) -> str:
    """Get theme directory for a specific resolution."""
    return os.path.join(DATA_DIR, f'Theme{width}{height}')


def get_web_dir(width: int, height: int) -> str:
    """Get cloud theme Web directory for a resolution.

    Matches Windows layout: Data/USBLCD/Web/{W}{H}/
    Contains bundled preview PNGs + on-demand downloaded MP4s.
    """
    return os.path.join(DATA_DIR, 'Web', f'{width}{height}')


def get_web_masks_dir(width: int, height: int) -> str:
    """Get cloud masks directory for a resolution.

    Matches Windows layout: Data/USBLCD/Web/zt{W}{H}/
    """
    return os.path.join(DATA_DIR, 'Web', f'zt{width}{height}')


def _extract_7z(archive: str, target_dir: str) -> bool:
    """Extract a .7z archive into target_dir. Returns True on success.

    Tries py7zr first, falls back to system 7z command.
    """
    os.makedirs(target_dir, exist_ok=True)

    try:
        import py7zr
        with py7zr.SevenZipFile(archive, 'r') as z:
            z.extractall(target_dir)
        log.info("Extracted %s (py7zr)", os.path.basename(archive))
        return True
    except ImportError:
        pass
    except Exception as e:
        log.warning("py7zr extraction failed: %s", e)

    try:
        result = subprocess.run(
            ['7z', 'x', archive, f'-o{target_dir}', '-y'],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0:
            log.info("Extracted %s (7z CLI)", os.path.basename(archive))
            return True
        log.warning("7z CLI failed (rc=%d): %s", result.returncode, result.stderr.decode())
    except FileNotFoundError:
        log.warning("Neither py7zr nor 7z CLI available — cannot extract %s", archive)
    except Exception as e:
        log.warning("7z CLI extraction failed: %s", e)

    return False


def ensure_themes_extracted(width: int, height: int) -> bool:
    """Extract default themes from .7z archive if not already present.

    Looks for src/data/Theme{W}{H}.7z beside the theme directory and extracts
    it in-place when the directory is missing or empty. Uses py7zr if available,
    falling back to the system 7z command.

    Returns True if themes are available (already existed or freshly extracted).
    """
    theme_dir = get_theme_dir(width, height)
    archive = theme_dir + '.7z'

    if _has_actual_themes(theme_dir):
        return True
    if not os.path.isfile(archive):
        return False

    return _extract_7z(archive, theme_dir)


def ensure_web_extracted(width: int, height: int) -> bool:
    """Extract cloud theme previews from .7z archive if not already present.

    Looks for src/data/Web/{W}{H}.7z beside the web directory and extracts
    it in-place when the directory is missing or empty.

    Returns True if previews are available (already existed or freshly extracted).
    """
    web_dir = get_web_dir(width, height)
    archive = web_dir + '.7z'

    if os.path.isdir(web_dir) and os.listdir(web_dir):
        return True
    if not os.path.isfile(archive):
        return False

    return _extract_7z(archive, web_dir)


def ensure_web_masks_extracted(width: int, height: int) -> bool:
    """Extract cloud mask themes from .7z archive if not already present.

    Looks for src/data/Web/zt{W}{H}.7z beside the masks directory and extracts
    it in-place when the directory is missing or empty.

    Returns True if masks are available (already existed or freshly extracted).
    """
    masks_dir = get_web_masks_dir(width, height)
    archive = masks_dir + '.7z'

    if _has_actual_themes(masks_dir):
        return True
    if not os.path.isfile(archive):
        return False

    return _extract_7z(archive, masks_dir)


def find_resource(filename: str, search_paths: Optional[list] = None) -> Optional[str]:
    """Find a resource file in search paths.

    Args:
        filename: Resource filename (e.g., 'P0播放.png')
        search_paths: List of directories to search (defaults to RESOURCE_SEARCH_PATHS)

    Returns:
        Full path if found, None otherwise
    """
    if search_paths is None:
        search_paths = RESOURCE_SEARCH_PATHS

    for path in search_paths:
        full_path = os.path.join(path, filename)
        if os.path.exists(full_path):
            return full_path
    return None


def load_image(filename: str, search_paths: Optional[list] = None, as_photoimage: bool = True):
    """Load an image from resource paths.

    Args:
        filename: Resource filename (e.g., 'P0播放.png')
        search_paths: List of directories to search (defaults to RESOURCE_SEARCH_PATHS)
        as_photoimage: If True, return ImageTk.PhotoImage; else return PIL.Image

    Returns:
        PhotoImage/Image if found and loaded, None otherwise
    """
    if not PIL_AVAILABLE:
        return None

    path = find_resource(filename, search_paths)
    if not path:
        return None

    try:
        img = Image.open(path)
        if as_photoimage:
            return ImageTk.PhotoImage(img)
        return img
    except Exception:
        return None


def build_search_paths(resource_dir: Optional[str] = None) -> list:
    """Build search paths list with optional custom directory first.

    Args:
        resource_dir: Optional custom directory to search first

    Returns:
        List of directories to search for resources
    """
    paths = []
    if resource_dir:
        paths.append(resource_dir)
    paths.extend(RESOURCE_SEARCH_PATHS)
    return paths


# =========================================================================
# User configuration (persisted to ~/.config/trcc/config.json)
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


def get_saved_resolution() -> tuple:
    """Get saved LCD resolution, defaulting to (320, 320)."""
    config = load_config()
    res = config.get('resolution', [320, 320])
    if isinstance(res, list) and len(res) == 2:
        return tuple(res)
    return (320, 320)


def save_resolution(width: int, height: int):
    """Persist LCD resolution to config."""
    config = load_config()
    config['resolution'] = [width, height]
    save_config(config)


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
