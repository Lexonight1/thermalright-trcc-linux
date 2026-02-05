"""
Central path constants and utilities for TRCC.

All path calculations happen once here. Components import what they need.
"""
import json
import os

# PIL import (done once, shared by all components)
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    ImageTk = None
    PIL_AVAILABLE = False

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


def find_resource(filename: str, search_paths: list = None) -> str | None:
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


def load_image(filename: str, search_paths: list = None, as_photoimage: bool = True):
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


def build_search_paths(resource_dir: str = None) -> list:
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
