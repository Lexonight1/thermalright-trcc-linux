"""
Central path constants and directory utilities for TRCC.

All path calculations happen once here. Components import what they need.
Config persistence lives in conf.py; device utilities live in their respective modules.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import List, Optional

log = logging.getLogger(__name__)

# =========================================================================
# Module-level path constants (calculated once at import time)
# =========================================================================

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # src/trcc/
SRC_DIR = os.path.dirname(_THIS_DIR)                     # src/
PROJECT_ROOT = os.path.dirname(SRC_DIR)                  # project root

# Asset directories (inside trcc package)
ASSETS_DIR = os.path.join(_THIS_DIR, 'assets')
RESOURCES_DIR = os.path.join(ASSETS_DIR, 'gui')

# User config directory
USER_CONFIG_DIR = os.path.expanduser('~/.trcc')
USER_DATA_DIR = os.path.join(USER_CONFIG_DIR, 'data')

# Theme file conventions (used across models, controllers, theme_downloader, etc.)
THEME_BG = '00.png'          # Background image
THEME_MASK = '01.png'        # Mask overlay image
THEME_PREVIEW = 'Theme.png'  # Thumbnail preview
THEME_DC = 'config1.dc'      # Binary overlay config
THEME_JSON = 'config.json'   # JSON config (custom themes)


# =========================================================================
# SysUtils — cross-distro system utilities
# =========================================================================

class SysUtils:
    """Cross-distro system utility functions (sysfs, SCSI, dependency checks)."""

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

    @staticmethod
    def read_sysfs(path: str) -> Optional[str]:
        """Safely read a sysfs/proc file, return stripped content or None."""
        try:
            with open(path, 'r') as f:
                return f.read().strip()
        except Exception:
            return None

    @staticmethod
    def find_scsi_devices() -> List[str]:
        """List available /dev/sg* devices by scanning sysfs dynamically."""
        sysfs = '/sys/class/scsi_generic'
        if not os.path.isdir(sysfs):
            return []
        return [e for e in sorted(os.listdir(sysfs)) if e.startswith('sg')]

    @staticmethod
    def require_sg_raw() -> None:
        """Verify sg_raw is available; raise FileNotFoundError with install help if not."""
        if not shutil.which('sg_raw'):
            raise FileNotFoundError(SysUtils._SG_RAW_INSTALL_HELP)

    @staticmethod
    def has_7z_support() -> bool:
        """Check if 7z CLI is available."""
        return shutil.which('7z') is not None


# =========================================================================
# ThemeDir — standard theme directory layout
# =========================================================================

class ThemeDir:
    """Standard theme directory layout.

    Encapsulates theme file paths, validation, and resolution-based lookup.

    Usage:
        td = ThemeDir(some_path)       # wrap an existing directory
        td = ThemeDir.for_resolution(320, 320)  # resolve best dir for resolution
        td.bg.exists()                 # check if 00.png exists
    """

    __slots__ = ('path',)

    def __init__(self, path: str | os.PathLike):
        from pathlib import Path as _Path
        self.path = _Path(path)

    @staticmethod
    def has_themes(theme_dir: str) -> bool:
        """Check if a Theme* directory has actual theme subfolders with image content.

        A valid theme subdir must contain at least one .png file.
        Skips dotfiles, Custom_* placeholder dirs, and dirs with only config files.
        """
        if not os.path.isdir(theme_dir):
            return False
        for item in os.listdir(theme_dir):
            item_path = os.path.join(theme_dir, item)
            if (os.path.isdir(item_path)
                    and not item.startswith('.')
                    and not item.startswith('Custom_')):
                if any(f.endswith('.png') for f in os.listdir(item_path)):
                    return True
        return False

    @classmethod
    def for_resolution(cls, width: int, height: int) -> ThemeDir:
        """Resolve the best theme directory for a resolution.

        Checks package data dir first, then user data dir (~/.trcc/data/).
        Returns whichever has actual theme content.
        """
        name = f'Theme{width}{height}'
        pkg_dir = os.path.join(DATA_DIR, name)
        if ThemeDir.has_themes(pkg_dir):
            return cls(pkg_dir)
        user_dir = os.path.join(USER_DATA_DIR, name)
        if ThemeDir.has_themes(user_dir):
            return cls(user_dir)
        return cls(pkg_dir)

    @property
    def bg(self):
        """Background image (00.png)."""
        return self.path / THEME_BG

    @property
    def mask(self):
        """Mask overlay image (01.png)."""
        return self.path / THEME_MASK

    @property
    def preview(self):
        """Thumbnail preview (Theme.png)."""
        return self.path / THEME_PREVIEW

    @property
    def dc(self):
        """Binary overlay config (config1.dc)."""
        return self.path / THEME_DC

    @property
    def json(self):
        """JSON config for custom themes (config.json)."""
        return self.path / THEME_JSON

    @property
    def zt(self):
        """Theme.zt animation file."""
        return self.path / 'Theme.zt'

    def is_valid(self) -> bool:
        """Check if directory contains valid theme files."""
        return self.preview.exists() or self.dc.exists() or self.bg.exists()

    def exists(self) -> bool:
        """Check if directory exists."""
        return self.path.exists()

    def __truediv__(self, other):
        """Allow ThemeDir / 'subpath' to return a Path."""
        return self.path / other

    def __str__(self):
        return str(self.path)


# =========================================================================
# Data directory resolution (runs at import time)
# =========================================================================

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
            for item in os.listdir(candidate):
                if item.startswith('Theme'):
                    theme_path = os.path.join(candidate, item)
                    if ThemeDir.has_themes(theme_path):
                        log.debug("Data dir: %s (found themes in %s)", candidate, item)
                        return candidate

    fallback = os.path.join(_THIS_DIR, 'data')
    log.debug("Data dir: %s (fallback — no themes found yet)", fallback)
    return fallback


DATA_DIR = _find_data_dir()
THEMES_DIR = DATA_DIR

RESOURCE_SEARCH_PATHS = [RESOURCES_DIR]


# =========================================================================
# DataManager — archive extraction, downloading, resolution management
# =========================================================================

class DataManager:
    """Archive extraction, on-demand downloading, and resolution tracking."""

    GITHUB_BASE_URL = (
        "https://raw.githubusercontent.com/Lexonight1/"
        "thermalright-trcc-linux/stable/src/trcc/data/"
    )

    _7Z_INSTALL_HELP = (
        "7z not found. Install p7zip for your distro:\n"
        "  Fedora/RHEL:    sudo dnf install p7zip p7zip-plugins\n"
        "  Ubuntu/Debian:  sudo apt install p7zip-full\n"
        "  Arch:           sudo pacman -S p7zip\n"
        "  openSUSE:       sudo zypper install p7zip-full\n"
        "  Void:           sudo xbps-install p7zip\n"
        "  Alpine:         sudo apk add 7zip\n"
        "  Gentoo:         sudo emerge p7zip\n"
        "  NixOS:          add p7zip to environment.systemPackages"
    )

    # ------------------------------------------------------------------
    # Archive safety
    # ------------------------------------------------------------------

    @staticmethod
    def is_safe_archive_member(name: str) -> bool:
        """Check that an archive member path doesn't escape the destination (zip slip)."""
        return not (os.path.isabs(name) or '..' in name.split('/'))

    # ------------------------------------------------------------------
    # Archive extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_7z(archive: str, target_dir: str) -> bool:
        """Extract a .7z archive into target_dir using 7z CLI. Returns True on success."""
        os.makedirs(target_dir, exist_ok=True)
        try:
            result = subprocess.run(
                ['7z', 'x', archive, f'-o{target_dir}', '-y'],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0:
                log.info("Extracted %s", os.path.basename(archive))
                return True
            log.warning("7z failed (rc=%d): %s", result.returncode, result.stderr.decode())
        except FileNotFoundError:
            log.warning(
                "7z not found — cannot extract %s\n%s",
                archive, DataManager._7Z_INSTALL_HELP,
            )
        except Exception as e:
            log.warning("7z extraction failed: %s", e)
        return False

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    @staticmethod
    def download_archive(url: str, dest_path: str, timeout: int = 60) -> bool:
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

    # ------------------------------------------------------------------
    # Fetch + extract pipeline
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_and_extract(
        label: str,
        pkg_dir: str,
        user_dir: str,
        archive_name: str,
        check_fn,
        fetch_fn,
    ) -> bool:
        """Unified fetch-and-extract for themes, web previews, and masks.

        1. Check pkg_dir (dev mode) and user_dir for existing content via check_fn.
        2. If neither has content, locate or download the .7z via fetch_fn.
        3. Always extract to user_dir (~/.trcc/data/) so data survives pip upgrades.
        """
        if check_fn(pkg_dir):
            log.debug("%s: found at %s", label, pkg_dir)
            return True
        if check_fn(user_dir):
            log.debug("%s: found at %s", label, user_dir)
            return True

        log.info("%s not found — fetching %s ...", label, archive_name)

        archive = fetch_fn(archive_name)
        if archive is None:
            log.warning(
                "%s: could not obtain %s (no local copy, download failed)",
                label, archive_name,
            )
            return False

        os.makedirs(user_dir, exist_ok=True)
        ok = DataManager.extract_7z(archive, user_dir)
        if ok:
            log.info("%s ready at %s", label, user_dir)
        else:
            log.warning("%s: extraction of %s failed", label, archive_name)
        return ok

    @staticmethod
    def _fetch_theme_archive(archive_name: str) -> Optional[str]:
        """Locate or download a Theme .7z archive."""
        pkg = os.path.join(DATA_DIR, archive_name)
        if os.path.isfile(pkg):
            return pkg
        user = os.path.join(USER_DATA_DIR, archive_name)
        if os.path.isfile(user):
            return user
        url = DataManager.GITHUB_BASE_URL + archive_name
        if DataManager.download_archive(url, user):
            return user
        return None

    @staticmethod
    def _fetch_web_archive(archive_name: str) -> Optional[str]:
        """Locate or download a Web .7z archive."""
        pkg = os.path.join(DATA_DIR, 'Web', archive_name)
        if os.path.isfile(pkg):
            return pkg
        user = os.path.join(USER_DATA_DIR, 'Web', archive_name)
        if os.path.isfile(user):
            return user
        url = DataManager.GITHUB_BASE_URL + 'Web/' + archive_name
        if DataManager.download_archive(url, user):
            return user
        return None

    # ------------------------------------------------------------------
    # Public ensure_* API
    # ------------------------------------------------------------------

    @staticmethod
    def _has_any_content(d: str) -> bool:
        """Check if a directory exists and has any files/subdirs."""
        return os.path.isdir(d) and bool(os.listdir(d))

    @staticmethod
    def ensure_themes(width: int, height: int) -> bool:
        """Extract default themes from .7z archive if not already present."""
        name = f'Theme{width}{height}'
        return DataManager._fetch_and_extract(
            label=f"Themes {width}x{height}",
            pkg_dir=os.path.join(DATA_DIR, name),
            user_dir=os.path.join(USER_DATA_DIR, name),
            archive_name=f'{name}.7z',
            check_fn=ThemeDir.has_themes,
            fetch_fn=DataManager._fetch_theme_archive,
        )

    @staticmethod
    def ensure_web(width: int, height: int) -> bool:
        """Extract cloud theme previews from .7z archive if not already present."""
        res_key = f'{width}{height}'
        return DataManager._fetch_and_extract(
            label=f"Web previews {width}x{height}",
            pkg_dir=os.path.join(DATA_DIR, 'Web', res_key),
            user_dir=os.path.join(USER_DATA_DIR, 'Web', res_key),
            archive_name=f'{res_key}.7z',
            check_fn=DataManager._has_any_content,
            fetch_fn=DataManager._fetch_web_archive,
        )

    @staticmethod
    def ensure_web_masks(width: int, height: int) -> bool:
        """Extract cloud mask themes from .7z archive if not already present."""
        res_key = f'zt{width}{height}'
        return DataManager._fetch_and_extract(
            label=f"Mask themes {width}x{height}",
            pkg_dir=os.path.join(DATA_DIR, 'Web', res_key),
            user_dir=os.path.join(USER_DATA_DIR, 'Web', res_key),
            archive_name=f'{res_key}.7z',
            check_fn=ThemeDir.has_themes,
            fetch_fn=DataManager._fetch_web_archive,
        )

    @staticmethod
    def ensure_all(width: int, height: int) -> None:
        """Download + extract all archives for a resolution (skips if already done)."""
        if DataManager.is_resolution_installed(width, height):
            return
        DataManager.ensure_themes(width, height)
        DataManager.ensure_web(width, height)
        DataManager.ensure_web_masks(width, height)
        DataManager.mark_resolution_installed(width, height)

    # ------------------------------------------------------------------
    # Resolution installation tracking
    # ------------------------------------------------------------------

    @staticmethod
    def is_resolution_installed(width: int, height: int) -> bool:
        """Check if theme data for this resolution has already been downloaded.

        Verifies both the config marker AND that theme files physically exist.
        """
        from .conf import load_config

        key = f"{width}x{height}"
        if key not in load_config().get("installed_resolutions", []):
            log.debug("Resolution %s: not in installed_resolutions", key)
            return False
        name = f"Theme{width}{height}"
        pkg = os.path.join(DATA_DIR, name)
        user = os.path.join(USER_DATA_DIR, name)
        if ThemeDir.has_themes(pkg):
            log.debug("Resolution %s: verified at %s", key, pkg)
            return True
        if ThemeDir.has_themes(user):
            log.debug("Resolution %s: verified at %s", key, user)
            return True
        log.warning(
            "Resolution %s: config says installed but no data at %s or %s",
            key, pkg, user,
        )
        return False

    @staticmethod
    def mark_resolution_installed(width: int, height: int) -> None:
        """Record that theme data for this resolution is ready."""
        from .conf import load_config, save_config

        config = load_config()
        installed: list = config.get("installed_resolutions", [])
        key = f"{width}x{height}"
        if key not in installed:
            installed.append(key)
            config["installed_resolutions"] = installed
            save_config(config)

    # ------------------------------------------------------------------
    # Directory resolution
    # ------------------------------------------------------------------

    @staticmethod
    def get_web_dir(width: int, height: int) -> str:
        """Get cloud theme Web directory for a resolution."""
        res_key = f'{width}{height}'
        pkg_dir = os.path.join(DATA_DIR, 'Web', res_key)
        if os.path.isdir(pkg_dir) and os.listdir(pkg_dir):
            return pkg_dir
        user_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
        if os.path.isdir(user_dir) and os.listdir(user_dir):
            return user_dir
        return pkg_dir

    @staticmethod
    def get_web_masks_dir(width: int, height: int) -> str:
        """Get cloud masks directory for a resolution."""
        res_key = f'zt{width}{height}'
        pkg_dir = os.path.join(DATA_DIR, 'Web', res_key)
        if ThemeDir.has_themes(pkg_dir):
            return pkg_dir
        user_dir = os.path.join(USER_DATA_DIR, 'Web', res_key)
        if ThemeDir.has_themes(user_dir):
            return user_dir
        return pkg_dir


# =========================================================================
# Resources — GUI resource file finding
# =========================================================================

class Resources:
    """GUI resource file finding and search path management."""

    @staticmethod
    def find(filename: str, search_paths: Optional[list] = None) -> Optional[str]:
        """Find a resource file in search paths."""
        if search_paths is None:
            search_paths = RESOURCE_SEARCH_PATHS
        for path in search_paths:
            full_path = os.path.join(path, filename)
            if os.path.exists(full_path):
                return full_path
        return None

    @staticmethod
    def build_search_paths(resource_dir: Optional[str] = None) -> list:
        """Build search paths list with optional custom directory first."""
        paths = []
        if resource_dir:
            paths.append(resource_dir)
        paths.extend(RESOURCE_SEARCH_PATHS)
        return paths


# =========================================================================
# Font search directories across distros
# =========================================================================

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


# =========================================================================
# Backward-compat aliases (module-level names for existing importers)
# =========================================================================

# SysUtils
read_sysfs = SysUtils.read_sysfs
find_scsi_devices = SysUtils.find_scsi_devices
require_sg_raw = SysUtils.require_sg_raw
has_7z_support = SysUtils.has_7z_support

# ThemeDir
_has_actual_themes = ThemeDir.has_themes

# DataManager
is_safe_archive_member = DataManager.is_safe_archive_member
_extract_7z = DataManager.extract_7z
_download_archive = DataManager.download_archive
_fetch_and_extract = DataManager._fetch_and_extract
_fetch_theme_archive_by_name = DataManager._fetch_theme_archive
_fetch_web_archive = DataManager._fetch_web_archive
_has_any_content = DataManager._has_any_content
ensure_themes_extracted = DataManager.ensure_themes
ensure_web_extracted = DataManager.ensure_web
ensure_web_masks_extracted = DataManager.ensure_web_masks
ensure_all_data = DataManager.ensure_all
is_resolution_installed = DataManager.is_resolution_installed
mark_resolution_installed = DataManager.mark_resolution_installed
get_web_dir = DataManager.get_web_dir
get_web_masks_dir = DataManager.get_web_masks_dir
GITHUB_THEME_BASE_URL = DataManager.GITHUB_BASE_URL

# ThemeDir convenience wrapper
def get_theme_dir(width: int, height: int) -> str:
    """Thin wrapper around ThemeDir.for_resolution() for backward compatibility."""
    return str(ThemeDir.for_resolution(width, height))

# Resources
find_resource = Resources.find
build_search_paths = Resources.build_search_paths
