"""
Theme pack download manager for TRCC Linux.

Thin CLI adapter wrapping DataManager (paths.py) for theme pack operations.
All downloading and extraction is delegated to DataManager.

Usage:
    trcc download                          # list available packs
    trcc download themes-320x320           # download 320x320 themes
    trcc download themes-480               # shorthand for 480x480
    trcc download themes-320x320 --force   # re-download even if exists
    trcc download themes-320x320 --info    # show pack details
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import trcc.legacy.conf as _conf
from trcc.legacy.core.models import FBL_TO_RESOLUTION
from trcc.legacy.core.paths import theme_dir_name

from .data_repository import DataManager

log = logging.getLogger(__name__)


# =========================================================================
# Pack registry — built dynamically from known resolutions
# =========================================================================

@dataclass(frozen=True)
class PackInfo:
    """Metadata for a downloadable theme pack."""

    name: str
    resolution: str
    width: int
    height: int
    archive: str
    size_kb: int

    @property
    def url(self) -> str:
        return f"{DataManager.GITHUB_BASE_URL}{self.archive}"



# HID sub-variant resolutions not in FBL_TO_RESOLUTION (FBL 224 overloads).
# These depend on PM sub-bytes in device_hid.py but have their own theme archives.
_EXTRA_RESOLUTIONS: set[tuple[int, int]] = {
    (480, 800), (480, 854), (540, 960), (800, 480), (960, 540),
}


def _all_resolutions() -> list[tuple[int, int]]:
    """All supported resolutions: FBL table + HID sub-variants."""
    return sorted(set(FBL_TO_RESOLUTION.values()) | _EXTRA_RESOLUTIONS)


def _build_registry() -> dict[str, PackInfo]:
    """Build theme pack registry from all known resolutions."""
    registry: dict[str, PackInfo] = {}
    for w, h in _all_resolutions():
        pack_id = f"themes-{w}x{h}"
        archive = f"{theme_dir_name(w, h)}.7z"
        archive_path = os.path.join(str(_conf.settings.user_data_dir), archive)
        size_kb = os.path.getsize(archive_path) // 1024 if os.path.isfile(archive_path) else 0
        registry[pack_id] = PackInfo(
            name=f"TRCC Themes {w}x{h}",
            resolution=f"{w}x{h}",
            width=w,
            height=h,
            archive=archive,
            size_kb=size_kb,
        )
    return registry


def _build_short_aliases(registry: dict[str, PackInfo]) -> dict[str, str]:
    """Build short aliases for square resolutions (themes-320 → themes-320x320)."""
    aliases: dict[str, str] = {}
    for pack_id, info in registry.items():
        if info.width == info.height:
            aliases[f"themes-{info.width}"] = pack_id
    return aliases


_THEME_REGISTRY: dict[str, PackInfo] | None = None
_SHORT_ALIASES: dict[str, str] | None = None


def _get_registry() -> dict[str, PackInfo]:
    """Lazy-build theme registry on first access."""
    global _THEME_REGISTRY, _SHORT_ALIASES
    if _THEME_REGISTRY is None:
        _THEME_REGISTRY = _build_registry()
        _SHORT_ALIASES = _build_short_aliases(_THEME_REGISTRY)
    return _THEME_REGISTRY


def _get_aliases() -> dict[str, str]:
    """Lazy-build short aliases on first access."""
    _get_registry()  # ensures both are built
    return _SHORT_ALIASES  # type: ignore[return-value]


def _resolve_pack_name(name: str) -> str:
    """Resolve short aliases to canonical pack names."""
    return _get_aliases().get(name, name)


# =========================================================================
# ThemeDownloader — CLI adapter for DataManager
# =========================================================================

class ThemeDownloader:
    """Download and manage TRCC theme packs via DataManager."""

    @staticmethod
    def _theme_dir(w: int, h: int) -> Path:
        """Path to extracted theme directory (prefers user dir)."""
        user = _conf.settings.user_data_dir / theme_dir_name(w, h)
        if user.exists():
            return user
        pkg = _conf.settings.user_data_dir / theme_dir_name(w, h)
        if pkg.exists():
            return pkg
        return user  # default to user dir for installs

    @staticmethod
    def _is_installed(w: int, h: int) -> bool:
        """Check if themes are extracted for a resolution."""
        d = ThemeDownloader._theme_dir(w, h)
        return d.exists() and any(d.iterdir())

    @staticmethod
    def _theme_count(w: int, h: int) -> int:
        """Count theme subdirectories for a resolution."""
        d = ThemeDownloader._theme_dir(w, h)
        if not d.exists():
            return 0
        return sum(1 for item in d.iterdir() if item.is_dir())

    # ── Public API ────────────────────────────────────────────────────

    @staticmethod
    def list_available() -> None:
        """List available theme packs with install status."""
        print("Available theme packs:")
        print("=" * 60)

        for pack_id, info in _get_registry().items():
            installed = ThemeDownloader._is_installed(info.width, info.height)
            status = ""
            if installed:
                count = ThemeDownloader._theme_count(info.width, info.height)
                status = f" [installed, {count} themes]"

            alias = f" (or: themes-{info.width})" if info.width == info.height else ""
            size = f"{info.size_kb}KB" if info.size_kb > 0 else "on-demand"

            print(f"\n  {pack_id}{alias}{status}")
            print(f"    {info.name}, Size: ~{size}")

        print("\n" + "=" * 60)
        print("Install with: trcc download <pack-name>")
        print("Example: trcc download themes-320x320")
        print("         trcc download themes-480  (shorthand for 480x480)")

    @staticmethod
    def show_info(pack_name: str) -> None:
        """Show detailed info about a theme pack."""
        pack_name = _resolve_pack_name(pack_name)
        if pack_name not in _get_registry():
            print(f"Unknown theme pack: {pack_name}")
            print("Use 'trcc download --list' to see available packs")
            return

        info = _get_registry()[pack_name]
        theme_dir = ThemeDownloader._theme_dir(info.width, info.height)

        print(f"\n{info.name}")
        print("=" * 40)
        print(f"Pack ID:     {pack_name}")
        print(f"Resolution:  {info.resolution}")
        print(f"Archive:     {info.archive}")
        print(f"Size:        ~{info.size_kb}KB")

        if ThemeDownloader._is_installed(info.width, info.height):
            count = ThemeDownloader._theme_count(info.width, info.height)
            print(f"\nInstalled:   Yes ({count} themes)")
            print(f"  Location:  {theme_dir}")
        else:
            print("\nInstalled:   No")

        print(f"\nSource:      {info.url}")

    @staticmethod
    def download_pack(pack_name: str, force: bool = False) -> int:
        """Download and install a theme pack via DataManager.

        Returns:
            0 on success, non-zero on failure.
        """
        pack_name = _resolve_pack_name(pack_name)
        if pack_name not in _get_registry():
            print(f"Unknown theme pack: {pack_name}")
            print("Use 'trcc download --list' to see available packs")
            return 1

        info = _get_registry()[pack_name]
        w, h = info.width, info.height

        # Already installed?
        if not force and ThemeDownloader._is_installed(w, h):
            count = ThemeDownloader._theme_count(w, h)
            print(f"{pack_name} already installed ({count} themes)")
            print("Use --force to reinstall")
            return 0

        # Force: remove existing first
        if force:
            for d in (_conf.settings.user_data_dir / theme_dir_name(w, h),
                      _conf.settings.user_data_dir / theme_dir_name(w, h)):
                if d.exists():
                    log.info("Removing %s for reinstall", d)
                    shutil.rmtree(d)

        print(f"Downloading {info.name} ({info.resolution})...")

        if not DataManager.ensure_themes(w, h):
            print(f"Failed to download/extract {info.archive}")
            return 1

        count = ThemeDownloader._theme_count(w, h)
        print(f"\n[OK] Installed {count} themes to {ThemeDownloader._theme_dir(w, h)}")
        return 0

    @staticmethod
    def remove_pack(pack_name: str) -> int:
        """Remove an installed theme pack from user data directory."""
        pack_name = _resolve_pack_name(pack_name)
        if pack_name not in _get_registry():
            print(f"Unknown theme pack: {pack_name}")
            return 1

        info = _get_registry()[pack_name]
        user_dir = _conf.settings.user_data_dir / theme_dir_name(info.width, info.height)

        if not user_dir.exists():
            print(f"Theme pack '{pack_name}' is not installed in user directory")
            return 1

        print(f"Removing {pack_name} from {user_dir}...")
        shutil.rmtree(user_dir)
        print(f"[OK] Removed {pack_name}")
        return 0


# =========================================================================
# Backward-compat aliases (used by cli.py and tests)
# =========================================================================
list_available = ThemeDownloader.list_available
show_info = ThemeDownloader.show_info
download_pack = ThemeDownloader.download_pack
remove_pack = ThemeDownloader.remove_pack
