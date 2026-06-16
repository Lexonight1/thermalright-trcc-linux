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
    LedSettingsChanged,
)
from ..models import OverlayElement, oriented_resolution
from ..registry import find_product
from ..results import (
    HealthCheckEntry,
    OverlayElementEntry,
    SlideshowResult,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


def oriented_theme_path(
    app: App, key: str, stored: Path, degrees: int | None = None,
) -> Path:
    """Re-root a stored theme path to the device's orientation dir.

    Non-square panels keep per-oriented theme catalogs (``theme854480`` vs
    ``theme480854``).  ``current_theme`` is an absolute path into ONE of them;
    at a different orientation the same-named theme in the matching dir is the
    variant to load — otherwise a portrait-rotated device shows the landscape
    theme on a portrait canvas (and vice versa).  Falls back to ``stored`` when
    no oriented variant is on disk (the renderer pixel-rotates the art).

    ``degrees`` is the authoritative orientation; pass it from an
    ``OrientationChanged`` event (``App._on_orientation_changed``) where
    ``settings`` may not be updated yet.  Omit it (the restore path,
    ``RestoreLastTheme``) to read the already-persisted ``settings`` value.
    Shared so connect-restore + runtime rotation resolve the oriented dir
    identically.
    """
    device = app.devices.get(key)
    if device is None or device.profile is None:
        return stored
    if degrees is None:
        degrees = app.settings.for_device(key).orientation
    bw, bh = oriented_resolution(device.profile.resolution, degrees)
    paths = app.platform.paths()
    for base in (paths.theme_dir(bw, bh), paths.user_theme_dir(bw, bh)):
        cand = base / stored.name
        if cand.exists():
            return cand
    return stored


_VIDEO_EXTS_FOR_SAVE = frozenset({".mp4", ".mov", ".webm", ".zt", ".mkv", ".avi"})


_VIDEO_EXTS_OK = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"})


_BG_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_MASK_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_LEGACY_MASK_FILENAME = "01.png"


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


_VIDEO_EXTS_FOR_LOAD = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt",
})


def overlay_elements_to_dc(
    elements: list[dict[str, Any]], *,
    rotation: int = 0, overlay_enabled: bool = True,
    allow_empty: bool = False,
) -> bytes | None:
    """Serialise overlay elements into ``config1.dc`` (``0xDD``) bytes.

    The single mask-metrics writer shared by every user-mask path:
    ``UploadCustomMask`` (a fresh upload) and ``persist_user_mask_dc`` (a
    later metric edit) both call this, so a user mask is the same
    self-contained ``{01.png, config1.dc}`` unit a cloud mask is — and
    ``ApplyMask`` reloads them identically.

    With *allow_empty* True an empty overlay still serialises to a valid
    zero-element DC — user masks ALWAYS carry an editable ``config1.dc``,
    even before any metric is placed.  With it False, an empty overlay
    returns ``None`` (the caller can leave the mask image-only).
    """
    if not elements and not allow_empty:
        log.debug("overlay_elements_to_dc: no elements — image-only mask")
        return None
    from ...services._dc import Writer
    dc = Writer().serialize({
        "elements": elements,
        "overlay_enabled": overlay_enabled,
        "rotation": rotation,
        "mask_visible": True,
    })
    log.info("overlay_elements_to_dc: %d element(s) → %d DC byte(s)",
             len(elements), len(dc))
    return dc


def persist_user_mask_dc(app: App, key: str) -> None:
    """Rewrite the active USER mask's ``config1.dc`` from the current overlay.

    A user-uploaded mask (under ``user_mask_dir``) is the user's OWN
    editable mask: when they change its metric placement, persist the new
    layout to its ``config1.dc`` so it survives re-apply — keeping it the
    same editable ``{01.png, config1.dc}`` unit a cloud mask is.  Subscribed
    to ``OverlayChanged``.

    No-op (returns) when there is no active mask, the resolution can't be
    resolved, or the active mask is NOT a user-catalog mask — cloud / program
    masks are read-only and never rewritten.  Drops a stale ``config1.dc``
    when the overlay becomes empty so the mask stays honestly image-only.
    """
    settings = app.settings.for_device(key)
    mask_path = settings.mask_path
    if not mask_path:
        return
    resolution = _resolve_oriented_resolution(app, key)
    if resolution is None:
        log.debug("persist_user_mask_dc: no resolution for %s — skip", key)
        return
    mask_file = Path(mask_path)
    user_root = app.platform.paths().user_mask_dir(*resolution)
    try:
        is_user_mask = mask_file.resolve().parent.parent == user_root.resolve()
    except OSError:
        return
    if not is_user_mask:
        log.debug("persist_user_mask_dc: %s is not a user-catalog mask — skip",
                  mask_file)
        return

    elements: list[dict[str, Any]] = []
    if settings.mask_overlay_elements is not None:
        elements = [e.to_dict() for e in settings.mask_overlay_elements]
    elements += [e.to_dict() for e in settings.user_overlay_elements]
    # allow_empty=True → the user mask always keeps a config1.dc, even when
    # every metric is removed, so it stays an editable unit.
    dc = overlay_elements_to_dc(elements, allow_empty=True)
    mask_dc = mask_file.parent / "config1.dc"
    try:
        if dc is not None:
            mask_dc.write_bytes(dc)
            log.info("persist_user_mask_dc: rewrote %s/config1.dc (%d byte(s))",
                     mask_file.parent.name, len(dc))
    except OSError as e:
        log.warning("persist_user_mask_dc: write failed (%s)", e)


_UPGRADE_COMMANDS: dict[str, tuple[str, ...]] = {
    "dnf":          ("sudo", "dnf", "upgrade", "-y", "trcc-linux"),
    "apt":          ("sudo", "apt", "upgrade", "-y", "trcc-linux"),
    "pacman":       ("sudo", "pacman", "-Syu", "--noconfirm", "trcc-linux"),
    "zypper":       ("sudo", "zypper", "update", "-y", "trcc-linux"),
    "xbps-install": ("sudo", "xbps-install", "-u", "trcc-linux"),
    "apk":          ("sudo", "apk", "upgrade", "trcc-linux"),
}


# Install the NVIDIA NVML python reader (pynvml) when an NVIDIA GPU is
# detected.  ``pkexec`` (graphical polkit prompt) rather than ``sudo`` —
# this is dispatched from the GUI, which has no TTY for a sudo password.
# Argv is a fixed list per package manager (never interpolate input).
# Only distros whose package name is verified are auto-installable; an
# unmapped PM falls back to detect-and-guide (the doctor prints the
# manual command) rather than guessing a wrong package name.
_GPU_READER_INSTALL_COMMANDS: dict[str, tuple[str, ...]] = {
    "dnf":    ("pkexec", "dnf", "install", "-y", "python3-pynvml"),
    "apt":    ("pkexec", "apt-get", "install", "-y", "python3-pynvml"),
    "pacman": ("pkexec", "pacman", "-S", "--noconfirm", "python-nvidia-ml-py"),
}


def _json_default_tuple(obj: Any) -> Any:
    """tuple → list for JSON serialisation (no other coercions)."""
    log.debug("_json_default_tuple: type=%s", type(obj).__name__)
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
    log.debug("_require_connected_device: key=%s", key)
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
    log.debug("_publish_if_disconnect: key=%s exc=%s",
              key, type(exc).__name__)
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
    log.debug("_invalidate_scene: key=%s", key)
    if app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
        app.display.invalidate(key)


def _resolve_resolution(app: App, key: str) -> tuple[int, int] | None:
    """Best-effort resolution lookup from a device key.

    Tries (1) connected device's handshake profile, (2) its DeviceInfo
    native_resolution, (3) the product registry entry's
    native_resolution.  Returns ``None`` when none yield a known size
    (unknown product or malformed key).
    """
    log.debug("_resolve_resolution: key=%s", key)
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


def _resolve_oriented_resolution(app: App, key: str) -> tuple[int, int] | None:
    """The device resolution adjusted for its current user orientation.

    Cloud assets (themes / backgrounds / masks) are catalogued per ORIENTED
    resolution — the C# keys every ``Web\\{res}\\`` directory on ``directionB``
    (``GetWebBackgroundImageDirectory``: ``854480`` ↔ ``480854``).  So cloud
    lookups/downloads must use this, not the native ``_resolve_resolution``
    (which is the wire/device-buffer size and stays orientation-agnostic).
    Returns ``None`` when the native resolution can't be resolved.
    """
    native = _resolve_resolution(app, key)
    if native is None:
        return None
    orientation = app.settings.for_device(key).orientation
    return oriented_resolution(native, orientation)


def _resolve_mask_path(path: Path) -> Path | None:
    """Resolve a mask reference to a renderable image file.

    Accepts a direct image file OR a legacy mask directory (containing
    ``01.png``).  Returns the file path renderers can ``open_image``,
    or ``None`` when neither shape matches.
    """
    log.debug("_resolve_mask_path: path=%s", path)
    if path.is_file() and path.suffix.lower() in _MASK_IMAGE_EXTS:
        return path
    if path.is_dir():
        legacy = path / _LEGACY_MASK_FILENAME
        if legacy.is_file():
            return legacy
    return None


def _publish_led_settings_changed(app: App, key: str) -> None:
    """Single event for any LED settings mutation — UIs subscribe once.

    Publishes both ``LedColorsChanged`` (UI panels refresh their widgets) and
    ``LedSettingsChanged`` (the render observer re-renders the device + preview
    immediately, instead of waiting for the next sensor tick).
    """
    log.debug("_publish_led_settings_changed: key=%s", key)
    app.events.publish(LedColorsChanged(key=key, color_count=0))
    app.events.publish(LedSettingsChanged(key=key))


def _element_to_entry(e: OverlayElement) -> OverlayElementEntry:
    """Flat OverlayElementEntry view for Result types."""
    log.debug("_element_to_entry: id=%s type=%s", e.id, e.type)
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
    log.debug("_search_theme_by_name: key=%s name=%s", key, name)
    paths = app.platform.paths()
    resolution = _resolve_oriented_resolution(app, key)
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
    log.debug("_health_entries: checks=%d", len(checks))
    return [
        HealthCheckEntry(
            name=c.name, severity=c.severity,
            message=c.message, fix_hint=c.fix_hint,
        )
        for c in checks
    ]


def _slideshow_snapshot(settings, key: str) -> SlideshowResult:
    log.debug("_slideshow_snapshot: key=%s", key)
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
    log.debug("_autostart_path: called")
    mgr = app.platform.autostart()
    return str(getattr(mgr, "path", "")) or ""
