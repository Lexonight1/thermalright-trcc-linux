"""Theme-directory resolution — toolkit-free, for the LCD device View.

The per-resolution browser directories (local themes, user themes, cloud
backgrounds, cloud masks) + the catalog dims a device's theme/mask/cutter
panels are pointed at are a pure function of the device's geometry: its
pre-rotation canvas size, its post-rotation LCD size, and whether the user
rotated it.  That logic — including the #136 portrait-fallback rule (a rotated
device with no portrait theme dir on disk browses the landscape dir, while its
cloud/mask catalogs stay portrait) — used to live inline in
``LCDHandler._update_theme_directories``, untestable without a QWidget.

Lifting it here leaves the handler a thin View (resolve → poke widgets) and
makes the dims-selection + fallback rules unit-testable against a fake ``Paths``
with zero Qt.  The heavier first-install auto-load (an ``iterdir`` walk that
dispatches a theme load) stays in the View — that FS walk belongs behind a
repository port, a later increment.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ...core.libraries import DeviceLibraries

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ThemeDirectories:
    """Resolved browser directories + catalog dims for one device geometry."""

    catalog_size: tuple[int, int]   # (bw, bh) the catalogs/preview are sized to
    theme_dir: Path                 # local theme browser dir (portrait-fallback applied)
    user_theme_dir: Path            # user-saved theme dir (tracks theme_dir's dims)
    web_dir: Path                   # cloud background catalog dir
    masks_dir: Path                 # cloud mask catalog dir
    portrait_fallback: bool         # rotated but no portrait theme dir → landscape used


def resolve_theme_directories(
    libraries: DeviceLibraries,
    *,
    canvas_size: tuple[int, int],
    lcd_size: tuple[int, int],
    is_rotated: bool,
) -> ThemeDirectories:
    """Resolve the browser directories + catalog dims for a device geometry.

    Catalog dims (cloud themes / masks / cutters + preview) follow the rotated
    (portrait) dims when the device is rotated, else the canvas — unconditionally,
    mirroring legacy's device-owned dir resolution.  Only the LOCAL theme browser
    falls back to the landscape dir when no portrait theme dir is on disk (the
    render pipeline pixel-rotates that landscape art at encode time). (#136)
    """
    cw, ch = canvas_size
    bw, bh = lcd_size if is_rotated else (cw, ch)

    web_dir = libraries.cloud_theme_dir(bw, bh)
    masks_dir = libraries.cloud_mask_dir(bw, bh)
    theme_dir = libraries.theme_dir(bw, bh)
    # User-saved themes are the user's own art, not a shipped library — one
    # directory per resolution, no SKU split.
    user_theme_dir = libraries.paths.user_theme_dir(bw, bh)

    portrait_fallback = is_rotated and not theme_dir.exists()
    if portrait_fallback:
        log.info(
            "resolve_theme_directories: no portrait theme dir at %s — local "
            "browser falls back to landscape %dx%d (render pixel-rotates); "
            "cloud/mask catalogs stay portrait %dx%d",
            theme_dir, cw, ch, bw, bh,
        )
        theme_dir = libraries.theme_dir(cw, ch)
        user_theme_dir = libraries.paths.user_theme_dir(cw, ch)

    log.debug(
        "resolve_theme_directories: catalog=%dx%d rotated=%s theme_dir=%s "
        "user_theme_dir=%s web_dir=%s masks_dir=%s fallback=%s",
        bw, bh, is_rotated, theme_dir, user_theme_dir, web_dir, masks_dir,
        portrait_fallback,
    )
    return ThemeDirectories(
        catalog_size=(bw, bh),
        theme_dir=theme_dir,
        user_theme_dir=user_theme_dir,
        web_dir=web_dir,
        masks_dir=masks_dir,
        portrait_fallback=portrait_fallback,
    )


def oriented_theme_reload_target(
    active_theme_path: Path, dirs: ThemeDirectories,
) -> Path | None:
    """The theme to reload after a rotation switches the catalog, or ``None``.

    A rotation swaps the browser catalog (``theme1600720`` ↔ ``theme7201600``)
    but not the rendered theme, so the device keeps the old-orientation bg — a
    landscape image letterboxed into the portrait buffer (#169 "not filling").
    The C# re-authors the theme per orientation; the equivalent here is to reload
    the **same-name variant** from the new catalog, whose oriented ``00.png``
    fills the buffer.

    Returns that variant's path (user dir wins, then cloud dir), or ``None`` when
    the active theme is already in the new catalog, or has no variant there (a
    custom theme, or the #136 portrait-fallback where the dir resolves back to
    landscape) — in which case the caller keeps the current theme and the render
    pipeline pixel-rotates the landscape art.  Pure decision; the View applies it.
    """
    name = active_theme_path.name
    for candidate in (dirs.user_theme_dir / name, dirs.theme_dir / name):
        if candidate == active_theme_path:
            return None
        if candidate.exists():
            return candidate
    return None
