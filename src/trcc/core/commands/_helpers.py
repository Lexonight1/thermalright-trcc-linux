"""Shared helpers + file-extension constants for the Command bus."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import (
    DeviceDisconnectedError,
    DeviceNotConnectedError,
)
from ..events import (
    DeviceDisconnected,
    LedColorsChanged,
)
from ..models import OverlayElement
from ..registry import find_product
from ..results import (
    HealthCheckEntry,
    OverlayElementEntry,
    SlideshowResult,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


_VIDEO_EXTS_FOR_SAVE = frozenset({".mp4", ".mov", ".webm", ".zt", ".mkv", ".avi"})


_VIDEO_EXTS_OK = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"})


_BG_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_MASK_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_LEGACY_MASK_FILENAME = "01.png"


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_VIDEO_EXTS_FOR_LOAD = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt",
})


_UPGRADE_COMMANDS: dict[str, tuple[str, ...]] = {
    "dnf":          ("sudo", "dnf", "upgrade", "-y", "trcc-linux"),
    "apt":          ("sudo", "apt", "upgrade", "-y", "trcc-linux"),
    "pacman":       ("sudo", "pacman", "-Syu", "--noconfirm", "trcc-linux"),
    "zypper":       ("sudo", "zypper", "update", "-y", "trcc-linux"),
    "xbps-install": ("sudo", "xbps-install", "-u", "trcc-linux"),
    "apk":          ("sudo", "apk", "upgrade", "trcc-linux"),
}


def _json_default_tuple(obj: Any) -> Any:
    """tuple → list for JSON serialisation (no other coercions)."""
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON-serialisable")


def _require_connected_device(app: App, key: str) -> Any:
    """Fetch a connected device by key, or raise.

    Centralises the ``app.get(key) → is_connected check`` pattern that
    every wire-touching Command must perform.  Returns the device on
    success; otherwise raises one of two errors the caller handles
    differently:

      * :class:`DeviceNotFoundError` — device not attached at all
        (key never seen by ``scan_devices``).  Callers catch this and
        return their per-Command failure ``Result`` (different Result
        shape per Command, so the helper can't construct one).
      * :class:`DeviceNotConnectedError` — device attached but its
        transport isn't open (handshake never ran or was reset).
        Callers let this propagate to the dispatch wrapper, which
        logs uniformly.

    The not-connected error string is single-sourced here so an edit
    to the wording doesn't have to land in every wire-touching Command.

    Not used by every site that checks ``is_connected``: SaveTheme
    (line ~738) and RunKeepalive (line ~5061) intentionally return a
    per-Command Result on disconnect instead of raising; the isinstance-
    gated Commands (UploadBootAnimation, SetLedColors, SetLedSegment)
    do their type check before the connect check and disappear entirely
    once capability dispatch lands (see §4 of the SOLID/DRY plan).
    """
    device = app.get(key)
    if not device.is_connected:
        raise DeviceNotConnectedError(
            f"{key} not connected — dispatch ConnectDevice first"
        )
    return device


def _publish_if_disconnect(app: App, key: str, exc: BaseException) -> None:
    """Publish ``DeviceDisconnected`` if *exc* is the auto-detach signal.

    Called inside every Command's ``except TransportError`` block.
    The exception from ``Device.send`` is :class:`DeviceDisconnectedError`
    when the recovery tracker hit the consecutive-failure threshold —
    transport is already closed by the device, ``is_connected`` is
    False.  Observers (sidebar, system tray, daemon clients) listen
    for ``DeviceDisconnected`` to re-run discovery.

    Plain :class:`TransportError` (transient bus errors) does NOT
    publish the event — the device is still attached, the caller just
    saw one bad send.
    """
    if isinstance(exc, DeviceDisconnectedError):
        log.info("auto-disconnect: %s closed after recovery threshold", key)
        app.events.publish(DeviceDisconnected(key=key))


def _invalidate_scene(app: App, key: str) -> None:
    """Drop the per-device scene cache if the display service is wired.

    Settings changes that affect rendering (fit, mask, overlay, split)
    need to bust the cache so the next render rebuilds with the new
    setting. Pure settings writes don't need it; this helper is the
    seam.
    """
    if app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
        app.display.invalidate(key)


def _resolve_resolution(app: App, key: str) -> tuple[int, int] | None:
    """Best-effort resolution lookup from a device key.

    Tries (1) connected device's handshake profile, (2) its DeviceInfo
    native_resolution, (3) the product registry entry's
    native_resolution.  Returns ``None`` when none yield a known size
    (unknown product or malformed key).
    """
    device = app.devices.get(key)
    if device is not None:
        if device.profile is not None:
            return device.profile.resolution
        if device.info.native_resolution != (0, 0):
            return device.info.native_resolution
    try:
        vid_s, pid_s = key.split(":")
        vid = int(vid_s, 16)
        pid = int(pid_s, 16)
    except ValueError:
        return None
    product = find_product(vid, pid)
    if product is None or product.native_resolution == (0, 0):
        return None
    return product.native_resolution


def _resolve_mask_path(path: Path) -> Path | None:
    """Resolve a mask reference to a renderable image file.

    Accepts a direct image file OR a legacy mask directory (containing
    ``01.png``).  Returns the file path renderers can ``open_image``,
    or ``None`` when neither shape matches.
    """
    if path.is_file() and path.suffix.lower() in _MASK_IMAGE_EXTS:
        return path
    if path.is_dir():
        legacy = path / _LEGACY_MASK_FILENAME
        if legacy.is_file():
            return legacy
    return None


def _publish_led_settings_changed(app: App, key: str) -> None:
    """Single event for any LED settings mutation — UIs subscribe once."""
    app.events.publish(LedColorsChanged(key=key, color_count=0))


def _element_to_entry(e: OverlayElement) -> OverlayElementEntry:
    """Flat OverlayElementEntry view for Result types."""
    return OverlayElementEntry(
        id=e.id, type=e.type, x=e.x, y=e.y, color=e.color, size=e.size,
        bold=e.bold, italic=e.italic, text=e.text, metric=e.metric,
        format=e.format, source=e.source,
    )


def _search_theme_by_name(
    app: App, key: str, name: str,
) -> Path | None:
    """Locate a theme directory by name across this device's roots.

    Used by RestoreLastTheme to recover legacy ``current_theme`` values
    (display names like ``"image:00"``, ``"Custom_Theme1"``) that
    pre-date persisting the absolute path.

    Search order:
      1. ``theme_dir(w,h)/<name>``           — pkg + GitHub-downloaded
      2. ``user_theme_dir(w,h)/<name>``      — user-saved layout
      3. ``cloud_theme_dir(w,h)/<name>``     — cloud cache
      4. ``user_content_dir()/single-image/<name_after_image_prefix>``
         — LoadImage's flat single-image cache (different layout, not
         a theme; only consulted for ``image:<name>`` keys)

    Each candidate must be a directory containing a theme config
    (``trcc.json`` or ``config1.dc``) — guarded by
    ``ThemeService`` semantics.

    The pre-cutover ``user_content_dir()/<name>`` flat candidate was
    dropped — every next/ theme writer now lands at the per-resolution
    path.  Users with legacy flat themes on disk must run
    ``dev/tools/migrate_legacy_themes.py`` once to move them into place.
    """
    paths = app.platform.paths()
    resolution = _resolve_resolution(app, key)
    candidates: list[Path] = []
    if resolution is not None:
        w, h = resolution
        candidates.append(paths.theme_dir(w, h) / name)
        candidates.append(paths.user_theme_dir(w, h) / name)
        candidates.append(paths.cloud_theme_dir(w, h) / name)
    # "image:foo" → single-image/foo (LoadImage layout).
    if name.startswith("image:"):
        candidates.append(
            paths.user_content_dir() / "single-image" / name[len("image:"):],
        )
    from ...services.theme import _has_theme_marker
    for c in candidates:
        if c.is_dir() and _has_theme_marker(c):
            return c
    return None


def _health_entries(checks: list) -> list[HealthCheckEntry]:
    """Map adapter HealthCheckResult → Result-layer HealthCheckEntry."""
    return [
        HealthCheckEntry(
            name=c.name, severity=c.severity,
            message=c.message, fix_hint=c.fix_hint,
        )
        for c in checks
    ]


def _slideshow_snapshot(settings, key: str) -> SlideshowResult:
    s = settings.for_device(key)
    return SlideshowResult(
        ok=True, key=key,
        enabled=s.slideshow_enabled,
        interval_s=s.slideshow_interval_s,
        themes=list(s.slideshow_themes),
        message=(f"Slideshow {'on' if s.slideshow_enabled else 'off'} "
                 f"({len(s.slideshow_themes)} theme(s), "
                 f"every {s.slideshow_interval_s:.0f}s)"),
    )


def _autostart_path(app: App) -> str:
    """Extract the manager's filesystem path when available."""
    mgr = app.platform.autostart()
    return str(getattr(mgr, "path", "")) or ""
