"""Application path constants and directory resolution — single source of truth.

Zero project imports. Safe to import from any module without circular deps.
"""
from __future__ import annotations

import os
from pathlib import Path

# Navigate from core/ back to the trcc package root
_TRCC_PKG = str(Path(__file__).resolve().parents[1])

# Asset directories (inside trcc package)
ASSETS_DIR = os.path.join(_TRCC_PKG, 'assets')
RESOURCES_DIR = os.path.join(_TRCC_PKG, 'ui', 'gui', 'assets')

# User config directory (~/.trcc/)
USER_CONFIG_DIR = os.path.expanduser('~/.trcc')
USER_DATA_DIR = os.path.join(USER_CONFIG_DIR, 'data')

# Runtime data directory — always writable (~/.trcc/data/)
DATA_DIR = USER_DATA_DIR

# User-created content (~/.trcc-user/) — survives uninstall and data re-download
USER_CONTENT_DIR = os.path.expanduser('~/.trcc-user')
USER_CONTENT_DATA_DIR = os.path.join(USER_CONTENT_DIR, 'data')
USER_MASKS_WEB_DIR = os.path.join(USER_CONTENT_DATA_DIR, 'web')


# =========================================================================
# Directory resolution — pure path logic, no I/O beyond os.path/os.listdir
# =========================================================================

def _has_any_content(d: str) -> bool:
    """Check if a directory exists and has any files/subdirs."""
    return os.path.isdir(d) and bool(os.listdir(d))


def has_themes(theme_dir: str) -> bool:
    """Check if a directory contains valid theme subdirectories with PNGs."""
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


def theme_dir_name(width: int, height: int) -> str:
    return f'theme{width}{height}'


def web_dir_name(width: int, height: int) -> str:
    return f'{width}{height}'


def masks_dir_name(width: int, height: int) -> str:
    return f'zt{width}{height}'


def output_resolution(w: int, h: int, rotation: int) -> tuple[int, int]:
    """Physical output shape — always swaps for non-square at 90/270."""
    if w != h and rotation in (90, 270):
        return (h, w)
    return (w, h)


def resolve_theme_dir(width: int, height: int) -> str:
    """Resolve the best theme directory path for a resolution.

    Tries user dir first, falls back to package dir. Returns a path string
    (not ThemeDir — paths.py cannot import from models.py).
    """
    name = theme_dir_name(width, height)
    user_dir = os.path.join(USER_DATA_DIR, name)
    if has_themes(user_dir):
        return user_dir
    pkg_dir = os.path.join(DATA_DIR, name)
    if has_themes(pkg_dir):
        return pkg_dir
    return user_dir


def _resolve_web_subdir(
    res_key: str,
    check_fn: object = None,
) -> str:
    """Resolve a web subdirectory, preferring pkg_dir if it has content.

    Falls back to user_dir (always writable — safe on system-wide installs).
    """
    if check_fn is None:
        check_fn = _has_any_content
    pkg_dir = os.path.join(DATA_DIR, 'web', res_key)
    if check_fn(pkg_dir):  # type: ignore[operator]
        return pkg_dir
    return os.path.join(USER_DATA_DIR, 'web', res_key)


def get_web_dir(width: int, height: int) -> str:
    """Get cloud theme Web directory for a resolution."""
    return _resolve_web_subdir(web_dir_name(width, height))


def get_web_masks_dir(width: int, height: int) -> str:
    """Get cloud masks directory for a resolution."""
    return _resolve_web_subdir(masks_dir_name(width, height), check_fn=has_themes)


def is_safe_archive_member(name: str) -> bool:
    """Check that an archive member path doesn't escape the destination (zip slip)."""
    return not (os.path.isabs(name) or '..' in name.split('/'))


def get_user_masks_dir(width: int, height: int) -> str:
    """Get user-created masks directory for a resolution.

    Lives in ~/.trcc-user/data/web/zt{W}{H}/ — separate from cloud masks
    so user content survives uninstall and data re-download.
    """
    return os.path.join(USER_MASKS_WEB_DIR, masks_dir_name(width, height))
