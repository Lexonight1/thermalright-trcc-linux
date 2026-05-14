"""
Data Repository — central data access for TRCC.

Abstracts where data lives (package, user cache, GitHub) so callers
just ask for what they need. Path constants, archive management,
and system utilities all go through this module.

Classes:
    SysUtils      — cross-distro system utilities (sysfs, sg_raw, 7z)
    ThemeDir      — re-exported from core/models.py + resolution lookup functions
    DataManager   — archive extraction, on-demand downloading, resolution tracking
    Resources     — GUI resource file finding

Config persistence lives in conf.py; device protocols in their respective modules.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...core.models import ThemeDir  # noqa: F401 — re-export for back-compat
from ...core.paths import (
    _TRCC_PKG,
    ASSETS_DIR,
    RESOURCES_DIR,
    USER_CONFIG_DIR,  # noqa: F401 — re-export (universal config dir)
    _has_any_content,
    has_themes,
    masks_dir_name,
    theme_dir_name,
    web_dir_name,
)
from ...core.platform import SUBPROCESS_NO_WINDOW as _NO_WINDOW

log = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(_TRCC_PKG).parents[1])

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
    def read_sysfs(path: str) -> str | None:
        """Safely read a sysfs/proc file, return stripped content or None."""
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None

    @staticmethod
    def find_scsi_devices() -> list[str]:
        """List available /dev/sg* devices by scanning sysfs dynamically."""
        sysfs = '/sys/class/scsi_generic'
        if not os.path.isdir(sysfs):
            return []
        return [e for e in sorted(os.listdir(sysfs)) if e.startswith('sg')]

    @staticmethod
    def find_scsi_block_devices() -> list[str]:
        """List available /dev/sd* block devices by scanning sysfs.

        Fallback for systems where the ``sg`` kernel module is not loaded —
        the SCSI subsystem still creates ``/sys/block/sdX`` and SG_IO ioctl
        works on block devices too.  Callers filter by VID/PID.
        """
        sysfs = '/sys/block'
        if not os.path.isdir(sysfs):
            return []
        return [e for e in sorted(os.listdir(sysfs)) if e.startswith('sd')]

    _sg_raw_path: str | None = None
    _sg_raw_checked: bool = False

    @staticmethod
    def require_sg_raw() -> None:
        """Verify sg_raw is available; raise FileNotFoundError with install help if not."""
        if not SysUtils._sg_raw_checked:
            SysUtils._sg_raw_path = shutil.which('sg_raw')
            SysUtils._sg_raw_checked = True
        if not SysUtils._sg_raw_path:
            raise FileNotFoundError(SysUtils._SG_RAW_INSTALL_HELP)

    @staticmethod
    def has_7z_support() -> bool:
        """Check if 7z CLI is available."""
        return shutil.which('7z') is not None


# =========================================================================
# Data directory resolution (runs at import time)
# =========================================================================

def _find_pkg_data_dir() -> str:
    """Find the package data directory (for bundled .7z archives in dev mode).

    Only used for locating .7z archives before downloading from GitHub.
    """
    for candidate in [os.path.join(_TRCC_PKG, 'data'),
                      os.path.join(_PROJECT_ROOT, 'data')]:
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(_TRCC_PKG, 'data')


# All runtime data goes to ~/.trcc/data/ — always writable, works on
# pip, pipx, pacman, dnf, apt installs. No read-only pkg_dir issues.
_PKG_DATA_DIR = _find_pkg_data_dir()

RESOURCE_SEARCH_PATHS = [RESOURCES_DIR]


# =========================================================================
# DataManager — archive extraction, downloading, resolution management
# =========================================================================

class DataManager:
    """Archive extraction, on-demand downloading, and resolution tracking."""

    GITHUB_BASE_URL = (
        "https://raw.githubusercontent.com/Lexonight1/"
        "thermalright-trcc-linux/main/src/trcc/data/"
    )

    @staticmethod
    def _7z_install_help() -> str:
        from trcc.adapters.system import PlatformFactory
        return PlatformFactory.current().archive_tool_install_help()

    # ------------------------------------------------------------------
    # Archive safety
    # ------------------------------------------------------------------

    @staticmethod
    def is_safe_archive_member(name: str) -> bool:
        """Check that an archive member path doesn't escape the destination (zip slip)."""
        from trcc.core.paths import is_safe_archive_member
        return is_safe_archive_member(name)

    # ------------------------------------------------------------------
    # Archive extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_7z(archive: str, target_dir: str) -> bool:
        """Extract a .7z archive into target_dir using 7z CLI. Returns True on success."""
        log.debug("extract_7z: %s → %s", archive, target_dir)
        os.makedirs(target_dir, exist_ok=True)
        try:
            # Validate archive members before extraction (zip-slip prevention)
            listing = subprocess.run(
                ['7z', 'l', '-slt', archive],
                capture_output=True, text=True, timeout=30,
                creationflags=_NO_WINDOW,
            )
            if listing.returncode != 0:
                log.warning("extract_7z: listing failed (rc=%d): %s",
                            listing.returncode, listing.stderr.strip())
                return False
            archive_norm = os.path.normpath(archive)
            for line in listing.stdout.splitlines():
                if line.startswith('Path = '):
                    member = line[7:]
                    # Skip the archive path itself (7z lists it first)
                    if os.path.normpath(member) == archive_norm:
                        continue
                    if not DataManager.is_safe_archive_member(member):
                        log.warning("Blocked unsafe archive member: %s", member)
                        return False

            result = subprocess.run(
                ['7z', 'x', archive, f'-o{target_dir}', '-y'],
                capture_output=True, timeout=120,
                creationflags=_NO_WINDOW,
            )
            if result.returncode == 0:
                log.info("extract_7z: OK %s", os.path.basename(archive))
                return True
            log.warning("extract_7z: failed (rc=%d): %s",
                        result.returncode, result.stderr.decode(errors='replace'))
        except FileNotFoundError:
            log.warning(
                "extract_7z: 7z not found — cannot extract %s\n%s",
                archive, DataManager._7z_install_help(),
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("extract_7z: failed: %s: %s", type(e).__name__, e)
        return False

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    @staticmethod
    def download_archive(url: str, dest_path: str, timeout: int = 60) -> bool:
        """Download a file from URL to dest_path. Returns True on success."""
        import ssl
        import urllib.error
        import urllib.request

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp_path = dest_path + '.tmp'

        # macOS Python doesn't access the system Keychain for SSL certs,
        # and PyInstaller bundles lack system CA paths. Use certifi's
        # Mozilla CA bundle — works on all platforms and bundle types.
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass

        try:
            log.info("Downloading %s ...", os.path.basename(dest_path))
            req = urllib.request.Request(url, headers={'User-Agent': 'trcc-linux'})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
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
            e.close()
        except (urllib.error.URLError, OSError, TimeoutError, ssl.SSLError) as e:
            log.warning("Download failed: %s: %s", type(e).__name__, e)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return False

    # ------------------------------------------------------------------
    # Post-extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unwrap_nested_dir(target_dir: str) -> None:
        """Flatten a single wrapping subdirectory after extraction.

        Some .7z archives wrap all contents in a subdirectory matching the
        archive name (e.g. ``1600720.7z`` → ``1600720/a001.png``).  When
        extracted to ``target_dir`` this creates double nesting.  If
        ``target_dir`` contains exactly one entry and it's a directory,
        move its contents up and remove the empty wrapper.
        """
        try:
            entries = os.listdir(target_dir)
        except OSError:
            return
        if len(entries) != 1:
            return
        nested = os.path.join(target_dir, entries[0])
        if not os.path.isdir(nested):
            return
        log.debug("Unwrapping nested directory: %s", nested)
        for item in os.listdir(nested):
            src = os.path.join(nested, item)
            dst = os.path.join(target_dir, item)
            shutil.move(src, dst)
        os.rmdir(nested)

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

        try:
            os.makedirs(user_dir, exist_ok=True)
        except OSError as e:
            log.warning("%s: cannot create %s: %s: %s",
                        label, user_dir, type(e).__name__, e)
            return False
        if (ok := DataManager.extract_7z(archive, user_dir)):
            # Some archives wrap contents in a single subdirectory that matches
            # the target dir name (e.g. 1600720.7z contains 1600720/a001.png).
            # This creates double nesting: user_dir/1600720/a001.png instead of
            # user_dir/a001.png.  Flatten if detected.
            DataManager._unwrap_nested_dir(user_dir)
            log.info("%s ready at %s", label, user_dir)
        else:
            log.warning("%s: extraction of %s failed", label, archive_name)
        return ok

    @staticmethod
    def _fetch_archive(archive_name: str, subdir: str = '') -> str | None:
        """Locate or download a .7z archive.

        Args:
            archive_name: Filename of the archive (e.g. 'theme320320.7z').
            subdir: Optional subdirectory (e.g. 'web') under data dirs.
        """
        for base in (_PKG_DATA_DIR, DataManager._data_dir()):
            path = os.path.join(base, subdir, archive_name) if subdir else os.path.join(base, archive_name)
            if os.path.isfile(path):
                log.debug("_fetch_archive: found local %s", path)
                return path
            log.debug("_fetch_archive: not at %s", path)
        user = os.path.join(DataManager._data_dir(), subdir, archive_name) if subdir else os.path.join(DataManager._data_dir(), archive_name)
        url_path = f'{subdir}/{archive_name}' if subdir else archive_name
        url = DataManager.GITHUB_BASE_URL + url_path
        log.info("_fetch_archive: downloading %s", url)
        if DataManager.download_archive(url, user):
            return user
        log.warning("_fetch_archive: all sources exhausted for %s", archive_name)
        return None

    # ------------------------------------------------------------------
    # Public ensure_* API
    # ------------------------------------------------------------------

    @staticmethod
    def _data_dir() -> str:
        """Get user data directory from platform adapter via settings."""
        from trcc.conf import settings
        return str(settings.user_data_dir)

    @staticmethod
    def ensure_themes(width: int, height: int) -> bool:
        """Extract default themes from .7z archive if not already present."""
        name = theme_dir_name(width, height)
        return DataManager._fetch_and_extract(
            label=f"Themes {width}x{height}",
            pkg_dir=os.path.join(DataManager._data_dir(), name),
            user_dir=os.path.join(DataManager._data_dir(), name),
            archive_name=f'{name}.7z',
            check_fn=has_themes,
            fetch_fn=DataManager._fetch_archive,
        )

    @staticmethod
    def _fetch_web_archive(archive_name: str) -> str | None:
        """Bound fetcher for the 'web' subdirectory — named, not a lambda."""
        return DataManager._fetch_archive(archive_name, 'web')

    @staticmethod
    def ensure_web(width: int, height: int) -> bool:
        """Extract cloud theme previews from .7z archive if not already present."""
        res_key = web_dir_name(width, height)
        return DataManager._fetch_and_extract(
            label=f"Web previews {width}x{height}",
            pkg_dir=os.path.join(DataManager._data_dir(), 'web', res_key),
            user_dir=os.path.join(DataManager._data_dir(), 'web', res_key),
            archive_name=f'{res_key}.7z',
            check_fn=_has_any_content,
            fetch_fn=DataManager._fetch_web_archive,
        )

    @staticmethod
    def ensure_web_masks(width: int, height: int) -> bool:
        """Extract cloud mask themes from .7z archive if not already present."""
        res_key = masks_dir_name(width, height)
        return DataManager._fetch_and_extract(
            label=f"Mask themes {width}x{height}",
            pkg_dir=os.path.join(DataManager._data_dir(), 'web', res_key),
            user_dir=os.path.join(DataManager._data_dir(), 'web', res_key),
            archive_name=f'{res_key}.7z',
            check_fn=has_themes,
            fetch_fn=DataManager._fetch_web_archive,
        )

    @staticmethod
    def ensure_all(
        width: int,
        height: int,
        progress_fn: callable | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Ensure all archives are extracted for a resolution (idempotent).

        Each ensure_* checks for existing content before extracting, so this is
        safe to call every startup. Non-square devices also ensure both
        orientations so rotating immediately shows local content.

        progress_fn: optional callable(str) — called with a status message at
        each step so CLI adapters can print progress.  GUI/API pass None.
        """
        def _report(msg: str) -> None:
            if progress_fn is not None:
                progress_fn(msg)
            else:
                log.info(msg)

        def _run(label: str, fn: callable, *args: Any) -> None:  # type: ignore[type-arg]
            _report(f"Downloading {label}...")
            try:
                if not fn(*args):
                    log.warning("ensure_all: %s returned False", label)
            except Exception:
                # Top-level fan-out wrapper for the data-ensure pipeline:
                # download/extract surface is wide (urllib, ssl, subprocess, OSError);
                # broad catch keeps subsequent ensures running on partial failure.
                log.exception("ensure_all: %s failed", label)

        log.info("ensure_all: starting %dx%d", width, height)
        _run(f"themes {width}x{height}", DataManager.ensure_themes, width, height)
        _run(f"web {width}x{height}", DataManager.ensure_web, width, height)
        _run(f"masks {width}x{height}", DataManager.ensure_web_masks, width, height)
        if width != height:
            log.debug("ensure_all: non-square — also ensuring portrait %dx%d", height, width)
            _run(f"web {height}x{width}", DataManager.ensure_web, height, width)
            _run(f"masks {height}x{width}", DataManager.ensure_web_masks, height, width)
        DataManager.mark_resolution_installed(width, height)
        _report(f"Data ready for {width}x{height}.")

    # ------------------------------------------------------------------
    # Resolution installation tracking
    # ------------------------------------------------------------------

    @staticmethod
    def is_resolution_installed(width: int, height: int) -> bool:
        """Check if theme data for this resolution has already been downloaded.

        Verifies both the config marker AND that theme files physically exist.
        """
        from trcc.conf import load_config

        key = f"{width}x{height}"
        if key not in load_config().get("installed_resolutions", []):
            log.debug("Resolution %s: not in installed_resolutions", key)
            return False
        name = theme_dir_name(width, height)
        pkg = os.path.join(DataManager._data_dir(), name)
        user = os.path.join(DataManager._data_dir(), name)
        if has_themes(pkg):
            log.debug("Resolution %s: verified at %s", key, pkg)
            return True
        if has_themes(user):
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
        from trcc.conf import load_config, save_config

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
        from trcc.conf import settings
        return settings._path_resolver.web_dir(width, height)

    @staticmethod
    def get_web_masks_dir(width: int, height: int) -> str:
        """Get cloud masks directory for a resolution."""
        from trcc.conf import settings
        return settings._path_resolver.web_masks_dir(width, height)


# =========================================================================
# Resources — GUI resource file finding
# =========================================================================

class Resources:
    """GUI resource file finding and search path management."""

    @staticmethod
    def find(filename: str, search_paths: list | None = None) -> str | None:
        """Find a resource file in search paths."""
        if search_paths is None:
            search_paths = RESOURCE_SEARCH_PATHS
        for path in search_paths:
            full_path = os.path.join(path, filename)
            if os.path.exists(full_path):
                return full_path
        return None

    @staticmethod
    def build_search_paths(resource_dir: str | None = None) -> list:
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

FONT_SEARCH_DIRS: list[str] = [
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


