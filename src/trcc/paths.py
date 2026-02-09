"""
Central path constants and utilities for TRCC.

All path calculations happen once here. Components import what they need.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, List, Optional

log = logging.getLogger(__name__)

# PIL import (done once, shared by all components)
# Split so load_image(as_photoimage=False) works without tkinter
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    if TYPE_CHECKING:
        from PIL import Image

try:
    from PIL import ImageTk
    IMAGETK_AVAILABLE = True
except ImportError:
    IMAGETK_AVAILABLE = False
    if TYPE_CHECKING:
        from PIL import ImageTk

# Base directories (calculated once at import time)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # src/trcc/
SRC_DIR = os.path.dirname(_THIS_DIR)                     # src/
PROJECT_ROOT = os.path.dirname(SRC_DIR)                  # project root

# Asset directories (inside trcc package)
ASSETS_DIR = os.path.join(_THIS_DIR, 'assets')
RESOURCES_DIR = os.path.join(ASSETS_DIR, 'gui')

# User config directory
USER_CONFIG_DIR = os.path.expanduser('~/.trcc')
USER_DATA_DIR = os.path.join(USER_CONFIG_DIR, 'data')


def is_safe_archive_member(name: str) -> bool:
    """Check that an archive member path doesn't escape the destination (zip slip protection)."""
    return not (os.path.isabs(name) or '..' in name.split('/'))


def read_sysfs(path: str) -> Optional[str]:
    """Safely read a sysfs/proc file, return stripped content or None."""
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return None


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
    1. trcc/data/ (inside package)
    2. Project root data/ (development fallback)
    3. ~/.trcc/data/ (user downloads)
    """
    candidates = [
        os.path.join(_THIS_DIR, 'data'),
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

    # Fallback to trcc/data even if empty
    return os.path.join(_THIS_DIR, 'data')


# Data directories
DATA_DIR = _find_data_dir()
THEMES_DIR = DATA_DIR  # Theme folders are directly in data/

# Common search paths for resource loading
RESOURCE_SEARCH_PATHS = [RESOURCES_DIR]


def get_theme_dir(width: int, height: int) -> str:
    """Get theme directory for a specific resolution.

    Checks the package data dir first, then the user data dir (~/.trcc/data/).
    Returns whichever has actual theme content, or the package dir as default.
    """
    pkg_dir = os.path.join(DATA_DIR, f'Theme{width}{height}')
    if _has_actual_themes(pkg_dir):
        return pkg_dir
    user_dir = os.path.join(USER_DATA_DIR, f'Theme{width}{height}')
    if _has_actual_themes(user_dir):
        return user_dir
    return pkg_dir


def get_web_dir(width: int, height: int) -> str:
    """Get cloud theme Web directory for a resolution.

    Checks the package data dir first, then the user data dir (~/.trcc/data/).
    Returns whichever has content, or the package dir as default.
    """
    res_key = f'{width}{height}'
    pkg_dir = os.path.join(DATA_DIR, 'Web', res_key)
    if os.path.isdir(pkg_dir) and os.listdir(pkg_dir):
        return pkg_dir
    user_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
    if os.path.isdir(user_dir) and os.listdir(user_dir):
        return user_dir
    return pkg_dir


def get_web_masks_dir(width: int, height: int) -> str:
    """Get cloud masks directory for a resolution.

    Checks the package data dir first, then the user data dir (~/.trcc/data/).
    Returns whichever has content, or the package dir as default.
    """
    res_key = f'zt{width}{height}'
    pkg_dir = os.path.join(DATA_DIR, 'Web', res_key)
    if _has_actual_themes(pkg_dir):
        return pkg_dir
    user_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
    if _has_actual_themes(user_dir):
        return user_dir
    return pkg_dir


def _extract_7z(archive: str, target_dir: str) -> bool:
    """Extract a .7z archive into target_dir. Returns True on success.

    Tries py7zr first, falls back to system 7z command.
    """
    os.makedirs(target_dir, exist_ok=True)

    try:
        import py7zr
        with py7zr.SevenZipFile(archive, 'r') as z:
            safe_names = [n for n in z.getnames() if is_safe_archive_member(n)]
            z.extract(target_dir, targets=safe_names)
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
        log.warning(
            "Neither py7zr nor 7z CLI available — cannot extract %s\n%s",
            archive, _7Z_INSTALL_HELP,
        )
    except Exception as e:
        log.warning("7z CLI extraction failed: %s", e)

    return False


def _ensure_extracted(target_dir: str, archive: str, check_fn) -> bool:
    """Check if content exists via check_fn; if not, extract archive.

    Args:
        target_dir: Directory to extract into
        archive: Path to .7z archive
        check_fn: Callable(str) -> bool to test if extraction is needed

    Returns True if content is available (already existed or freshly extracted).
    """
    if check_fn(target_dir):
        return True
    if not os.path.isfile(archive):
        return False
    return _extract_7z(archive, target_dir)


# =========================================================================
# On-demand theme archive download from GitHub
# =========================================================================

# Base URL for downloading theme archives from the GitHub repo.
# Archives are stored in git and served via raw.githubusercontent.com.
GITHUB_THEME_BASE_URL = (
    "https://raw.githubusercontent.com/Lexonight1/"
    "thermalright-trcc-linux/stable/src/trcc/data/"
)


def _download_archive(url: str, dest_path: str, timeout: int = 60) -> bool:
    """Download a file from URL to dest_path. Returns True on success."""
    import urllib.error
    import urllib.request

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + '.tmp'

    try:
        log.info("Downloading %s ...", os.path.basename(dest_path))
        req = urllib.request.Request(url, headers={'User-Agent': 'trcc-linux'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(tmp_path, 'wb') as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        os.replace(tmp_path, dest_path)
        size_kb = os.path.getsize(dest_path) / 1024
        log.info("Downloaded %s (%.0f KB)", os.path.basename(dest_path), size_kb)
        return True
    except urllib.error.HTTPError as e:
        log.warning("Download failed (%d): %s", e.code, url)
    except Exception as e:
        log.warning("Download failed: %s", e)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return False


def _fetch_theme_archive(width: int, height: int) -> Optional[str]:
    """Download a theme archive from GitHub if not bundled locally.

    Checks the package data dir first, then the user data dir.
    Downloads to user data dir (~/.trcc/data/) if not found anywhere.

    Returns the path to the .7z file, or None if unavailable.
    """
    archive_name = f'Theme{width}{height}.7z'

    # Check package data dir (bundled with pip install)
    pkg_archive = os.path.join(DATA_DIR, archive_name)
    if os.path.isfile(pkg_archive):
        return pkg_archive

    # Check user data dir (previously downloaded)
    user_archive = os.path.join(USER_DATA_DIR, archive_name)
    if os.path.isfile(user_archive):
        return user_archive

    # Download from GitHub
    url = GITHUB_THEME_BASE_URL + archive_name
    if _download_archive(url, user_archive):
        return user_archive

    return None


def ensure_themes_extracted(width: int, height: int) -> bool:
    """Extract default themes from .7z archive if not already present.

    Checks both the package data dir and user data dir for themes.
    If the .7z archive is not found locally, downloads it from GitHub.
    """
    # Check package data dir first
    theme_dir = get_theme_dir(width, height)
    if _has_actual_themes(theme_dir):
        return True

    # Check user data dir
    user_theme_dir = os.path.join(USER_DATA_DIR, f'Theme{width}{height}')
    if _has_actual_themes(user_theme_dir):
        return True

    # Find or download the archive
    archive = _fetch_theme_archive(width, height)
    if archive is None:
        return False

    # Extract to whichever dir is writable (prefer package dir, fall back to user)
    target = theme_dir
    try:
        os.makedirs(target, exist_ok=True)
        # Test writability
        test_file = os.path.join(target, '.write_test')
        with open(test_file, 'w') as f:
            f.write('')
        os.remove(test_file)
    except OSError:
        target = user_theme_dir

    return _extract_7z(archive, target)


def _fetch_web_archive(archive_name: str) -> Optional[str]:
    """Download a Web archive from GitHub if not bundled locally.

    Checks the package Web dir first, then the user data dir.
    Downloads to user data dir (~/.trcc/data/Web/) if not found.

    Returns the path to the .7z file, or None if unavailable.
    """
    pkg_archive = os.path.join(DATA_DIR, 'Web', archive_name)
    if os.path.isfile(pkg_archive):
        return pkg_archive

    user_archive = os.path.join(USER_DATA_DIR, 'Web', archive_name)
    if os.path.isfile(user_archive):
        return user_archive

    url = GITHUB_THEME_BASE_URL + 'Web/' + archive_name
    if _download_archive(url, user_archive):
        return user_archive

    return None


def ensure_web_extracted(width: int, height: int) -> bool:
    """Extract cloud theme previews from .7z archive if not already present.

    Checks both package and user data dirs. Downloads from GitHub if needed.
    """
    def check_fn(d: str) -> bool:
        return os.path.isdir(d) and bool(os.listdir(d))
    res_key = f'{width}{height}'

    # Check package data dir
    web_dir = get_web_dir(width, height)
    if check_fn(web_dir):
        return True

    # Check user data dir
    user_web_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
    if check_fn(user_web_dir):
        return True

    # Find or download archive
    archive = _fetch_web_archive(f'{res_key}.7z')
    if archive is None:
        return False

    # Extract to writable location
    target = web_dir
    try:
        os.makedirs(target, exist_ok=True)
        test_file = os.path.join(target, '.write_test')
        with open(test_file, 'w') as f:
            f.write('')
        os.remove(test_file)
    except OSError:
        target = user_web_dir

    return _extract_7z(archive, target)


def ensure_web_masks_extracted(width: int, height: int) -> bool:
    """Extract cloud mask themes from .7z archive if not already present.

    Checks both package and user data dirs. Downloads from GitHub if needed.
    """
    res_key = f'zt{width}{height}'

    # Check package data dir
    masks_dir = get_web_masks_dir(width, height)
    if _has_actual_themes(masks_dir):
        return True

    # Check user data dir
    user_masks_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
    if _has_actual_themes(user_masks_dir):
        return True

    # Find or download archive
    archive = _fetch_web_archive(f'{res_key}.7z')
    if archive is None:
        return False

    # Extract to writable location
    target = masks_dir
    try:
        os.makedirs(target, exist_ok=True)
        test_file = os.path.join(target, '.write_test')
        with open(test_file, 'w') as f:
            f.write('')
        os.remove(test_file)
    except OSError:
        target = user_masks_dir

    return _extract_7z(archive, target)


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
            if not IMAGETK_AVAILABLE:
                return None
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


# =========================================================================
# Cross-distro compatibility helpers
# =========================================================================

_SG_RAW_INSTALL_HELP = (
    "sg_raw not found. Install sg3_utils for your distro:\n"
    "  Fedora/RHEL:    sudo dnf install sg3_utils\n"
    "  Ubuntu/Debian:  sudo apt install sg3-utils\n"
    "  Arch:           sudo pacman -S sg3_utils\n"
    "  openSUSE:       sudo zypper install sg3_utils\n"
    "  Void:           sudo xbps-install sg3_utils\n"
    "  Alpine:         sudo apk add sg3_utils\n"
    "  Gentoo:         sudo emerge sg3_utils\n"
    "  NixOS:          add sg3_utils to environment.systemPackages"
)

_7Z_INSTALL_HELP = (
    "7z not found and py7zr not installed. Install one:\n"
    "  pip install py7zr          (recommended, pure Python)\n"
    "  ---\n"
    "  Fedora/RHEL:    sudo dnf install p7zip p7zip-plugins\n"
    "  Ubuntu/Debian:  sudo apt install p7zip-full\n"
    "  Arch:           sudo pacman -S p7zip\n"
    "  openSUSE:       sudo zypper install p7zip-full\n"
    "  Void:           sudo xbps-install p7zip\n"
    "  Alpine:         sudo apk add 7zip\n"
    "  Gentoo:         sudo emerge p7zip\n"
    "  NixOS:          add p7zip to environment.systemPackages"
)


def require_sg_raw():
    """Verify sg_raw is available; raise FileNotFoundError with install help if not."""
    if not shutil.which('sg_raw'):
        raise FileNotFoundError(_SG_RAW_INSTALL_HELP)


def has_7z_support() -> bool:
    """Check if 7z extraction is available (py7zr or system 7z)."""
    try:
        import py7zr  # noqa: F401
        return True
    except ImportError:
        pass
    return shutil.which('7z') is not None


def find_scsi_devices() -> List[str]:
    """List available /dev/sg* devices by scanning sysfs dynamically.

    Unlike a hardcoded range(16), this reads the actual entries in
    /sys/class/scsi_generic/ so it works on systems with any number of
    SCSI devices.
    """
    sysfs = '/sys/class/scsi_generic'
    if not os.path.isdir(sysfs):
        return []
    devices = []
    for entry in sorted(os.listdir(sysfs)):
        if entry.startswith('sg'):
            devices.append(entry)
    return devices


# Font search directories across distros.
# Order: bundled assets → user local → distro-specific system paths.
_HOME = os.path.expanduser('~')
FONTS_DIR = os.path.join(ASSETS_DIR, 'fonts')

FONT_SEARCH_DIRS: List[str] = [
    FONTS_DIR,                                          # bundled
    os.path.join(_HOME, '.local/share/fonts'),          # XDG user fonts
    os.path.join(_HOME, '.fonts'),                      # legacy user fonts
    '/usr/local/share/fonts',                           # manually installed
    '/usr/share/fonts/truetype',                        # Debian, Ubuntu, Mint
    '/usr/share/fonts/truetype/dejavu',                 # Debian DejaVu
    '/usr/share/fonts/truetype/noto',                   # Debian Noto
    '/usr/share/fonts/opentype/noto',                   # Debian Noto OpenType
    '/usr/share/fonts/google-noto-sans-cjk-vf-fonts',  # Fedora Noto CJK
    '/usr/share/fonts/google-noto-vf',                  # Fedora Noto VF
    '/usr/share/fonts/google-noto',                     # Fedora Noto
    '/usr/share/fonts/dejavu-sans-fonts',               # Fedora DejaVu
    '/usr/share/fonts/TTF',                             # Arch, Void, Garuda
    '/usr/share/fonts/noto',                            # Alpine, Gentoo
    '/usr/share/fonts/noto-cjk',                        # openSUSE
    '/usr/share/fonts/dejavu',                          # Alpine, openSUSE
    '/run/current-system/sw/share/fonts/truetype',      # NixOS
    '/run/current-system/sw/share/fonts/opentype',      # NixOS
    '/gnu/store/fonts',                                 # Guix (approx)
]
