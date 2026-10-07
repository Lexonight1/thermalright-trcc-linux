"""Shared helpers + file-extension constants for the Command bus."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._safe import is_under
from ..errors import (
    DeviceNotConnectedError,
)
from ..events import (
    BrightnessChanged,
    LedColorsChanged,
    LedSettingsChanged,
    SlideshowChanged,
    SplitModeChanged,
)
from ..models import (
    Capability,
    Kind,
    OverlayElement,
    ThemeDir,
    oriented_resolution,
    parse_device_key,
)
from ..registry import find_product
from ..results import (
    HealthCheckEntry,
    OverlayElementEntry,
    OverlayLayoutResult,
    SlideshowResult,
)

if TYPE_CHECKING:
    from ...app import App
    from ...services.theme_directories import ThemeDirectories
    from ..models import DeviceSettings, ProductInfo

from ..logs import per_frame
from ..models import MEDIA, MediaKind

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


def oriented_theme_path(
    app: App, key: str, stored: Path, degrees: int | None = None,
) -> Path | None:
    """Re-root a stored theme path to the device's orientation dir.

    Non-square panels keep per-oriented theme catalogs (``theme854480`` vs
    ``theme480854``).  ``current_theme`` is an absolute path into ONE of them;
    at a different orientation the same-named theme in the matching dir is the
    variant to load.  ``None`` when that folder has no theme of the name -- a
    theme the user saved in the other orientation, or a portrait archive that
    never downloaded.  It used to return ``stored`` then, on the belief that
    the renderer rotates landscape art: on a widescreen panel the canvas is
    portrait and ``bg_fit`` draws a landscape background as solid black, so a
    rotation showed black frames and a clipped overlay, and a restart at 90
    squashed the landscape video.  The caller loads Theme1 instead
    (``_fallback_theme``), as the C# loads the folder's own list.

    ``degrees`` is the authoritative orientation; pass it from an
    ``OrientationChanged`` event (``App._on_orientation_changed``) where
    ``settings`` may not be updated yet.  Omit it (the restore path,
    ``RestoreDeviceState``) to read the already-persisted ``settings`` value.
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
    # Re-root for ORIENTATION only — preserve the tree the user actually
    # selected (their saved theme vs the shipped one).  A user-saved theme and
    # a shipped theme can share a name (they coexist); re-resolving shipped-first
    # would silently swap the user's last preview for the shipped default of the
    # same name on every rotation/restore, losing their changes. Fall back to
    # ``stored`` when the same-name oriented variant isn't on disk in that tree
    # (the renderer pixel-rotates the art).
    if is_under(stored, paths.user_content_dir()):
        bases = [paths.user_theme_dir(bw, bh)]
        others = [paths.user_theme_dir(bh, bw)]
    else:
        # Per-SKU library first, generic second.  A 1600x720 panel at SUB 3
        # keeps its themes in ``theme7201600l``; looking only in
        # ``theme7201600`` finds nothing, falls back to ``stored``, and the
        # oriented-theme swap quietly stops happening for exactly the coolers
        # that have their own artwork.  Both are tried because the variant
        # archive is a separate download that may not have landed.
        libs = app.libraries(key)
        bases = [libs.theme_dir(bw, bh)]
        if bases[0] != paths.theme_dir(bw, bh):
            bases.append(paths.theme_dir(bw, bh))
        others = [libs.theme_dir(bh, bw), paths.theme_dir(bh, bw)]
    # A theme kept anywhere but a folder of this panel -- a directory handed
    # to ``load-theme`` -- has no orientation twin: it is the user's own path.
    if stored.parent not in {*bases, *others}:
        log.debug("oriented_theme_path: %s is outside the panel's folders",
                  stored)
        return stored
    for base in bases:
        cand = base / stored.name
        if cand.exists():
            log.debug("_oriented_theme_path: resolved %s", cand)
            return cand
    log.info("oriented_theme_path: no %r in the %dx%d folder", stored.name,
             bw, bh)
    return None


def orientation_catalog(app: App, key: str, degrees: int) -> str | None:
    """The theme folder *key* shows at *degrees* -- the C#'s ``ThemeML``.

    Named by ``Paths`` from the resolution and the SKU's variant, never by the
    folder found on disk, which falls back to the generic one while a variant
    archive is missing: the key a panel's per-folder values are kept under
    must not change with what has downloaded.  0 and 180 name one folder, as do 90 and
    270 (the C#'s ``themeDirection % 180``).  ``None`` with no live profile.
    """
    device = app.devices.get(key)
    if device is None or device.profile is None:
        log.debug("orientation_catalog: %s has no live profile", key)
        return None
    bw, bh = oriented_resolution(device.profile.resolution, degrees)
    return app.platform.paths().theme_dir(
        bw, bh, app.libraries(key).theme_variant).name


def enter_orientation(app: App, key: str, degrees: int | None = None) -> str:
    """Swap in *key*'s values for the folder it shows at *degrees* -- its own
    theme, brightness, split mode and slideshow, kept per folder as the C#
    keeps them per ``Theme.dc``.  Returns the settings verdict: ``"same"``,
    ``"restored"`` or ``"first"`` (``""`` with no live profile).

    Every value the swap changes is announced with the event a UI already
    follows, so all of them show the folder's own state.
    """
    if degrees is None:
        degrees = app.settings.for_device(key).orientation
    catalog = orientation_catalog(app, key, degrees)
    if catalog is None:
        log.info("enter_orientation: %s not connected -- nothing to swap", key)
        return ""
    s = app.settings.for_device(key)
    before = (s.brightness, s.split_mode, s.slideshow_enabled,
              s.slideshow_interval_s, list(s.slideshow_themes))
    visit = app.settings.enter_catalog(key, catalog)
    s = app.settings.for_device(key)
    log.info("enter_orientation: %s %d° -> %s (%s)", key, degrees, catalog,
             visit)
    if s.brightness != before[0]:
        app.events.publish(BrightnessChanged(key=key, percent=s.brightness))
    if s.split_mode != before[1]:
        app.events.publish(SplitModeChanged(key=key, mode=s.split_mode))
    if (s.slideshow_enabled, s.slideshow_interval_s,
            list(s.slideshow_themes)) != before[2:]:
        _publish_slideshow(app, key)
    return visit


def overlay_elements_to_dc(
    elements: list[dict[str, Any]], *,
    rotation: int = 0, overlay_enabled: bool = True,
    allow_empty: bool = False, flags: dict[str, Any] | None = None,
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
        # The device's screencast region: re-applying a user mask reads its
        # DC (ApplyMask seeds Jp* from it), so a mask that wrote the codec's
        # default would reset the user's region.
        **(flags or {}),
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

    # The mask records what is ON SCREEN.  This used to concatenate the mask
    # layer and the user layer, which drew from a different rule than the
    # renderer's — so a mask could persist elements nothing was showing.
    elements = device_overlay_layout(app, key)
    # allow_empty=True → the user mask always keeps a config1.dc, even when
    # every metric is removed, so it stays an editable unit.
    try:
        size = app.renderer.surface_size(app.renderer.open_image(mask_file))
    except Exception as e:
        log.warning("persist_user_mask_dc: cannot size %s (%s) — skip",
                    mask_file, e)
        return
    dc = overlay_elements_to_dc(
        elements, allow_empty=True, flags=user_mask_flags(app, key, size))
    mask_dc = ThemeDir(mask_file.parent).dc
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
    frame_log.debug("_require_connected_device: key=%s", key)
    device = app.get(key)
    if not device.is_connected:
        # DeviceNotConnectedError propagates PAST App.dispatch (no try/except
        # there), so without this the rejected wire command is invisible in the
        # log.  Warn with the key — the universal "acted before connect" trace.
        log.warning("%s: wire command dispatched before ConnectDevice — "
                    "device attached but not connected", key)
        # The dispatcher already tried to connect a USES_DEVICE Command's
        # device, so "dispatch ConnectDevice first" is no advice at all when it
        # failed -- the recorded reason is ("permission denied on /dev/sg1").
        reason = app.connect_issue(key) or "dispatch ConnectDevice first"
        raise DeviceNotConnectedError(f"{key} not connected — {reason}")
    return device


def _publish_if_disconnect(app: App, key: str, exc: BaseException) -> None:
    """After a Command's ``except TransportError``: announce a lost panel once.

    Whether the panel is gone is the device's answer (``is_connected``), not
    the exception's type -- see :meth:`App.note_lost`, which the send worker's
    fire-and-forget failures reach too.  Nothing in the App re-runs discovery
    on ``DeviceDisconnected``; the event tells every window the panel is gone.
    """
    log.debug("_publish_if_disconnect: key=%s exc=%s",
              key, type(exc).__name__)
    app.note_lost(key)


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


def _rendered_surface(app: App, key: str) -> Any | None:
    """The surface the display last parked for *key*; None with no renderer.

    ``_invalidate_scene``'s sibling, and the same seam for the same reason:
    with no Renderer attached nothing was ever rendered, and ``app.display``
    would raise rather than answer.
    """
    log.debug("_rendered_surface: key=%s", key)
    if app._renderer is None:  # pyright: ignore[reportPrivateUsage]
        return None
    return app.display.rendered_surface(key)


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
        vid, pid, _ = parse_device_key(key)
    except ValueError:
        return None
    product = find_product(vid, pid)
    if product is None or product.native_resolution == (0, 0):
        return None
    return product.native_resolution


def _resolve_oriented_resolution(app: App, key: str,
                                 degrees: int | None = None,
                                 ) -> tuple[int, int] | None:
    """The device resolution adjusted for its user orientation -- *degrees*,
    or the saved one.

    Cloud assets (themes / backgrounds / masks) are catalogued per ORIENTED
    resolution — the C# keys every ``Web\\{res}\\`` directory on ``directionB``
    (``GetWebBackgroundImageDirectory``: ``854480`` ↔ ``480854``).  So cloud
    lookups/downloads must use this, not the native ``_resolve_resolution``
    (which is the wire/device-buffer size and stays orientation-agnostic).
    Returns ``None`` when the native resolution can't be resolved.
    """
    log.debug("_resolve_oriented_resolution: app=%s key=%s", app, key)
    native = _resolve_resolution(app, key)
    if native is None:
        return None
    if degrees is None:
        degrees = app.settings.for_device(key).orientation
    return oriented_resolution(native, degrees)


def _resolve_mask_path(path: Path) -> Path | None:
    """Resolve a mask reference to a renderable image file.

    Accepts a direct image file OR a legacy mask directory (containing
    ``01.png``).  Returns the file path renderers can ``open_image``,
    or ``None`` when neither shape matches.
    """
    log.debug("_resolve_mask_path: path=%s", path)
    if path.is_file() and MEDIA.kind_of(path) is MediaKind.IMAGE:
        return path
    if path.is_dir():
        legacy = ThemeDir(path).mask
        if legacy.is_file():
            return legacy
    return None


def _not_an_led(app: App, key: str) -> str | None:
    """Refusal message when *key* is provably not an LED device, else ``None``.

    Every settings-only LED Command wrote straight to ``app.settings`` without
    checking what it was aimed at, so ``trcc led color <lcd-key> ffffff``
    answered "LED color set to #ffffff" and exited 0 — on a device with no LED
    hardware at all — while quietly stashing LED settings under an LCD's key
    (#252).  Only the two wire-touching Commands (``SetLedColors``,
    ``RenderLed``) ever checked; this is their guard for the settings half, so
    the whole LED surface refuses in one voice.

    Gating in the Command rather than at each UI is what makes it uniform:
    CLI, API, GUI and qtgui all dispatch these same Commands, and both
    command-only edges already translate a failed Result on their own
    (``dispatch_echo`` exits 1, ``http_error_if_failed`` raises).

    Deliberately ternary — "not an LED" and "cannot tell" are different
    answers:

    * **attached** — the device object answers ``is_led`` authoritatively.
    * **not attached** — the VID/PID registry answers, so an *unplugged* LCD
      is refused too.
    * **unknown VID/PID** — allowed.  We cannot prove anything about a key the
      registry has never seen, and settings are legitimately written before a
      device is ever attached (the GUI seeds a profile, the CLI pre-configures
      one), so refusing here would break a real flow to guess at a fake one.
    """
    log.debug("_not_an_led: key=%s", key)
    refusal = f"{key} is not an LED device"
    device = app.devices.get(key)
    if device is not None:
        return None if device.is_led else refusal
    product = _registry_product(key)
    if product is None:
        return None
    return None if product.kind is Kind.LED else refusal


def _registry_product(key: str) -> ProductInfo | None:
    """The registry row *key* names, or ``None`` when the registry cannot say.

    ``None`` covers a key that does not parse and a VID/PID the registry has
    never seen — the "cannot tell" answer both refusal helpers allow.
    """
    try:
        vid, pid, _ = parse_device_key(key)
    except ValueError:
        frame_log.debug("_registry_product: %r is not a device key", key)
        return None
    product = find_product(vid, pid)
    frame_log.debug("_registry_product: %s -> %s", key,
                    product.product if product is not None else None)
    return product


def _lacks(app: App, key: str, capability: Capability) -> str | None:
    """Refusal message when *key*'s device provably lacks *capability*.

    The LCD half of what :func:`_not_an_led` is for the LED half.  Every frame
    Command assumed its device draws frames, so an LED controller handed
    ``SendColor`` rendered a 320x320 frame for a device with no screen, had it
    refused at the wire — and was already marked held by then, which froze its
    animation and its sensor refresh until an LED setting changed.

    Same ternary as :func:`_not_an_led`, keyed on the declared capability set
    rather than on "is it an LED", so a device that is neither an LCD nor an
    LED is refused by what it cannot do instead of being taken for an LCD:

    * **attached** — the device's own ``ProductInfo`` answers.
    * **not attached** — the VID/PID registry answers.
    * **unknown VID/PID** — allowed; nothing is proven about a key never seen.

    Called per tick by ``RenderAndSend``, so it logs on the frame family.
    """
    device = app.devices.get(key)
    product = device.info if device is not None else _registry_product(key)
    if product is None or capability in product.capabilities:
        frame_log.debug("_lacks: %s has %s (or cannot tell)", key,
                        capability.value)
        return None
    frame_log.debug("_lacks: %s (%s) lacks %s", key, product.kind.value,
                    capability.value)
    return (f"{key} ({product.kind.value.upper()}) has no "
            f"{capability.value} capability")


def _publish_led_settings_changed(app: App, key: str) -> None:
    """Single event for any LED settings mutation — UIs subscribe once.

    Publishes both ``LedColorsChanged`` (UI panels refresh their widgets) and
    ``LedSettingsChanged`` (the render observer re-renders the device + preview
    immediately, instead of waiting for the next sensor tick).
    """
    log.debug("_publish_led_settings_changed: key=%s", key)
    # A deliberate LED change ends a SetLedColors hold — released BEFORE the
    # publish, so the observer's re-render below is not skipped as held.
    if key in app.held:
        log.info("_publish_led_settings_changed: %s released from a "
                 "SetLedColors hold", key)
        app.held.discard(key)
    app.events.publish(LedColorsChanged(key=key, color_count=0))
    app.events.publish(LedSettingsChanged(key=key))


def resolve_overlay_layout(app: App, key: str) -> OverlayLayoutResult:
    """What is on ``key``'s screen — the ONE place that answer is built.

    The lookup half of :func:`services.overlay.effective_overlay_layout`:
    reads the device's three layers off the App once and hands the pure data
    to the service.  Returns the finished Result, so every reader of "what is
    on screen" -- the Query, the snapshot's clock formats -- gets one answer.

    One settings read and one source computation serve both, which is why
    this returns the whole answer rather than just the elements.
    """
    from ...services.overlay import effective_overlay_layout, overlay_source

    s = app.settings.for_device(key)
    theme = app.active_themes.get(key)
    elements = effective_overlay_layout(
        theme.config if theme is not None else {}, s.user_overlay_elements,
    )
    source = overlay_source(s.user_overlay_elements)
    log.debug(
        "resolve_overlay_layout: key=%s source=%s elements=%d enabled=%s",
        key, source, len(elements), s.overlay_enabled,
    )
    return OverlayLayoutResult(
        ok=True, key=key,
        elements=[
            _element_to_entry(OverlayElement.from_dict(e)) for e in elements
        ],
        source=source,
        enabled=s.overlay_enabled,
        theme_name=theme.name if theme is not None else "",
        message=f"{len(elements)} element(s) from the {source} layer"
                f"{'' if s.overlay_enabled else ' (overlay disabled)'}",
    )


def _element_to_entry(e: OverlayElement) -> OverlayElementEntry:
    """Flat OverlayElementEntry view for Result types."""
    log.debug("_element_to_entry: id=%s type=%s", e.id, e.type)
    return OverlayElementEntry(
        id=e.id, type=e.type, x=e.x, y=e.y, color=e.color, size=e.size,
        font=e.font, bold=e.bold, italic=e.italic, text=e.text,
        metric=e.metric, format=e.format, show_unit=e.show_unit,
        source=e.source,
    )


def _theme_directories(app: App, key: str) -> ThemeDirectories | None:
    """The theme browser's directories for *key*, the #136 portrait fallback
    applied — or None before the device has a canvas (not handshaken).

    One composition for the ``ResolveThemeDirectories`` Query and the name
    search, so a name picked from the browser resolves where it was listed.
    """
    from ...services.theme_directories import resolve_theme_directories

    device = app.devices.get(key)
    profile = device.profile if device is not None else None
    if profile is None:
        log.debug("_theme_directories: %s has no canvas yet", key)
        return None
    orientation = app.settings.for_device(key).orientation
    log.debug("_theme_directories: %s canvas=%s orientation=%s",
              key, profile.resolution, orientation)
    return resolve_theme_directories(
        app.libraries(key),
        canvas_size=profile.resolution,
        lcd_size=oriented_resolution(profile.resolution, orientation),
        is_rotated=orientation in (90, 270),
    )


def _search_theme_by_name(
    app: App, key: str, name: str,
) -> Path | None:
    """Locate a theme directory by name across this device's roots.

    Used by RestoreDeviceState to recover legacy ``current_theme`` values
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
    (``trcc.json`` or ``config1.dc``) — the store answers that
    (``ContentStore.is_theme_dir``), since which markers count is its
    layout knowledge.

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
        # Per-SKU libraries first: a cooler with its own artwork should find
        # its own copy of a theme name before the generic one.  Ordinary
        # panels resolve variant to "" and these collapse to the three
        # candidates this always had.
        libs = app.libraries(key)
        candidates.append(libs.theme_dir(w, h) / name)
        candidates.append(libs.cloud_theme_dir(w, h) / name)
        candidates.append(paths.theme_dir(w, h) / name)
        candidates.append(paths.user_theme_dir(w, h) / name)
        candidates.append(paths.cloud_theme_dir(w, h) / name)
    # The browser's own folders when the #136 fallback moved them: a turned
    # panel with no portrait themes LISTS the landscape ones, and a slideshow
    # picked from that list named themes this search could not find.
    dirs = _theme_directories(app, key)
    if dirs is not None and dirs.portrait_fallback:
        log.debug("_search_theme_by_name: %s portrait fallback — also %s",
                  key, dirs.theme_dir)
        candidates += [dirs.theme_dir / name, dirs.user_theme_dir / name]
    # "image:foo" → single-image/foo (LoadImage layout).
    if name.startswith("image:"):
        candidates.append(
            paths.user_content_dir() / "single-image" / name[len("image:"):],
        )
    for c in candidates:
        if app.themes.is_theme_dir(c):
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


def _publish_slideshow(app: App, key: str) -> None:
    """Tell every UI the slideshow's saved state, whoever changed it."""
    s = app.settings.for_device(key)
    log.info("_publish_slideshow: %s enabled=%s themes=%d interval=%ss",
             key, s.slideshow_enabled, len(s.slideshow_themes),
             s.slideshow_interval_s)
    app.events.publish(SlideshowChanged(
        key=key, enabled=s.slideshow_enabled,
        interval_s=float(s.slideshow_interval_s),
        themes=tuple(s.slideshow_themes),
    ))


def _drive_slideshow(app: App, key: str) -> None:
    """Rotate *key*'s slideshow while it is switched on, and only then.

    The one place that decides, for ``SetSlideshow`` and for the session's
    restore: "on" and "rotating" used to be two switches every UI paired by
    hand, and a saved slideshow never resumed when the App started.
    """
    from ...services.slideshow_driver import SlideshowDriver, task_key

    if app.settings.for_device(key).slideshow_enabled:
        log.info("_drive_slideshow: %s is on — driving it", key)
        app.add_task(SlideshowDriver(app, key))
    else:
        log.info("_drive_slideshow: %s is off — not driving it", key)
        app.remove_task(task_key(key))


def _autostart_path(app: App) -> str:
    """Extract the manager's filesystem path when available."""
    mgr = app.platform.autostart()
    location = mgr.entry_location()
    log.debug("_autostart_path: %s", location)
    return location


def device_overlay_layout(app: App, key: str) -> list[dict[str, Any]]:
    """The ONE overlay layout for *key*, as flat dicts — what is on screen.

    Every consumer that needs "the elements this device is showing" asks here:
    the renderer's own resolution, the saved theme's bake, the user-mask DC
    rewrite, the mask upload seed, the legacy DC export.

    Before this existed the render path RESOLVED (one layer wins) while the
    export and mask-authoring paths STACKED (``mask + user`` concatenated), so
    the same question had two answers and exporting a theme could write
    elements that were never drawn.  ``CLAUDE.md`` carried those three sites as
    a known deferred bug for exactly that reason.
    """
    from ...services.overlay import resolve_overlay_elements

    theme = app.active_themes.get(key)
    s = app.settings.for_device(key)
    layout = resolve_overlay_elements(
        theme.config if theme is not None else {}, s.user_overlay_elements,
    )
    log.debug("device_overlay_layout: key=%s → %d element(s)", key, len(layout))
    return layout


def as_working_layer(elements: Any) -> list[OverlayElement]:
    """Raw element dicts → the device's working overlay layer.

    One conversion for the three places a source change establishes that layer
    — a theme load, a mask apply, a save's re-seed — so they cannot disagree
    about what "adopt this layout" means.  Non-dict entries are dropped rather
    than raising: a hand-edited ``trcc.json`` should cost the user one element,
    not the whole theme.

    ``OverlayElement.from_dict`` keeps an id the source already carries and
    mints a stable one otherwise, which is what makes the adopted elements
    addressable by Update / Delete / Flash straight away — a theme parsed from
    ``config1.dc`` carries no ids at all.
    """
    out = [
        OverlayElement.from_dict(dict(e))
        for e in (elements or ()) if isinstance(e, dict)
    ]
    log.debug("as_working_layer: %d element(s)", len(out))
    return out


def native_canvas(app: App, key: str) -> tuple[int, int, str]:
    """The device's NATIVE canvas for *key*, and which source answered.

    Native, never oriented: a ``.zt`` is authored for the panel's own pixels
    and the firmware applies the mount rotation itself.  Rotating here as
    well would encode the turn twice.  This is deliberately NOT
    :class:`PreviewSize`, which folds the user orientation AND the composed
    theme canvas because it answers a different question -- how big to DRAW a
    preview.  Sizing an authored asset from that is how a preview and its
    export come to disagree (#291).

    Prefers an attached device's handshake profile, then its scanned
    ``native_resolution``, then the product registry -- that last step is what
    lets a user stage a video theme BEFORE plugging the cooler in.

    ``(0, 0, "unknown")`` when nothing resolves.  The third element names the
    arm that answered, because a reporter pasting "which size did it pick, and
    why" is the whole diagnostic and the size alone cannot say.

    ONE ladder.  It was written twice -- here (as ``_native_size``, for
    ``LoadVideo`` + ``ExportVideoClip``) and in
    ``ui/qtgui/panels/_browser_base.AssetBrowserPanel._target_resolution`` --
    and the two gated differently: this arm accepts an ATTACHED device, that
    one required a CONNECTED one, so a handshaken device that had since
    dropped fell back to the registry in a browser and used its real panel
    here.  They agree for a scanned device, because both answers come from the
    registry; they disagree for a device that answered a handshake once.
    """
    device = app.devices.get(key)
    if device is not None:
        if device.profile is not None:
            log.debug("native_canvas: %s from handshake profile", key)
            return (*device.profile.resolution, "handshake")
        if device.info.native_resolution != (0, 0):
            log.debug("native_canvas: %s from scanned DeviceInfo", key)
            return (*device.info.native_resolution, "scan")
    try:
        vid, pid, _ = parse_device_key(key)
    except ValueError:
        log.warning("native_canvas: %r is not a VID:PID key", key)
        return (0, 0, "unknown")
    product = find_product(vid, pid)
    if product is None:
        log.warning("native_canvas: %s is not in the product registry", key)
        return (0, 0, "unknown")
    log.debug("native_canvas: %s from the product registry", key)
    return (*product.native_resolution, "registry")


# ── The screencast region (the C#'s JpX/JpY/JpW/JpH + myYcbk) ─────────────


def screencast_box(app: App, key: str) -> tuple[int, int, int, int] | None:
    """*key*'s screencast region as the box on screen, or None with no panel.

    What every UI shows and what ``StartScreencast`` casts when it is given no
    region: the stored DC rect, or the C#'s default, in screen axes.
    """
    from ..geometry import screencast_axes
    from ..models import SCREENCAST_DEFAULT_RECT

    device = app.devices.get(key)
    profile = device.profile if device is not None else None
    if profile is None:
        log.debug("screencast_box: %s has no panel yet", key)
        return None
    s = app.settings.for_device(key)
    return screencast_axes(s.screencast_rect or SCREENCAST_DEFAULT_RECT,
                           profile.resolution, s.orientation)


def store_screencast_box(app: App, key: str, box: tuple[int, int, int, int],
                         hide_border: bool) -> None:
    """Store a box on screen as *key*'s region; a running cast follows it.

    The capture driver reads ``screencast_region`` every tick, so updating it
    moves a live cast without restarting it -- the C# re-reads Jp* each tick.
    """
    from ..geometry import screencast_axes

    profile = app.devices[key].profile
    assert profile is not None, f"{key} has no panel"
    native = screencast_axes(box, profile.resolution,
                             app.settings.for_device(key).orientation)
    log.debug("store_screencast_box: %s box=%s -> rect=%s", key, box, native)
    _apply_screencast_rect(app, key, native, hide_border)


def seed_screencast_rect(app: App, key: str, config: dict[str, Any],
                         source: str) -> None:
    """Take a theme's or mask's DC region as *key*'s -- the C# reads Jp* from
    every DC it loads (FormCZTV.cs:6805).  A missing or empty rect keeps the
    current one: a ``trcc.json``-only theme says nothing about it."""
    rect = config.get("screencast_rect")
    if not (isinstance(rect, (list, tuple)) and len(rect) == 4
            and all(isinstance(v, int) for v in rect) and rect[2] > 0 and rect[3] > 0):
        log.debug("seed_screencast_rect: %s carries no region (%r) — %s keeps "
                  "its own", source, rect, key)
        return
    hide = bool(config.get("screencast_border", True))
    log.info("seed_screencast_rect: %s <- %s %s hide_border=%s",
             key, source, tuple(rect), hide)
    _apply_screencast_rect(app, key, (rect[0], rect[1], rect[2], rect[3]), hide)


def screencast_dc_flags(s: DeviceSettings) -> dict[str, Any]:
    """A device's region as the DC fields a saved theme or mask carries --
    what the C# writes from its live Jp* on save (FormCZTV.cs:7290, 7433)."""
    from ..models import SCREENCAST_DEFAULT_RECT

    flags = {"screencast_rect": s.screencast_rect or SCREENCAST_DEFAULT_RECT,
             "screencast_border": s.screencast_hide_border}
    log.debug("screencast_dc_flags: %s", flags)
    return flags


def user_mask_flags(app: App, key: str, size: tuple[int, int]) -> dict[str, Any]:
    """A user mask's DC fields beyond its metrics: the device's screencast
    region, and the mask's CENTRE.

    The C# centres an uploaded mask on itself -- ``XvalMB = W / 2``,
    ``YvalMB = H / 2`` (FormCZTV.cs:5821, :6032) -- which draws it at the
    top-left.  Our writer left the codec's (0, 0), which draws a mask at minus
    half its size whenever it is not full-size, as every portrait upload is
    against the landscape profile: a quarter of it on the panel.
    """
    w, h = size
    flags = {**screencast_dc_flags(app.settings.for_device(key)),
             "mask_position": (w // 2, h // 2)}
    log.debug("user_mask_flags: %s %dx%d -> centre %s", key, w, h,
              flags["mask_position"])
    return flags


def fit_mask_upload(app: App, source: Path,
                    canvas: tuple[int, int]) -> tuple[bytes | None, tuple[int, int]]:
    """An uploaded mask's size, and its PNG when it had to shrink to fit
    *canvas* as the C# does -- None when it fits and is stored as given.

    FormCZTV.cs:5786-5809: wider than the canvas -> scale to its width, then
    to its height if still too tall; else taller -> scale to its height; never
    enlarged, aspect kept, integer arithmetic.
    """
    renderer = app.renderer
    surface = renderer.open_image(source)
    w, h = renderer.surface_size(surface)
    cw, ch = canvas
    if w > cw:
        nw, nh = cw, h * cw // w
        if nh > ch:
            nw, nh = w * ch // h, ch
    elif h > ch:
        nw, nh = w * ch // h, ch
    else:
        log.debug("fit_mask_upload: %s %dx%d fits %dx%d", source.name, w, h, cw, ch)
        return None, (w, h)
    log.info("fit_mask_upload: %s %dx%d -> %dx%d to fit %dx%d",
             source.name, w, h, nw, nh, cw, ch)
    return renderer.encode_png(renderer.resize(surface, nw, nh)), (nw, nh)


def _apply_screencast_rect(app: App, key: str, native: tuple[int, int, int, int],
                           hide_border: bool) -> None:
    """The one write: store, let a live cast follow, tell every UI."""
    from ..events import ScreencastRegionChanged

    app.settings.set_screencast_rect(key, native, hide_border)
    box = screencast_box(app, key)
    if box is None:
        log.debug("_apply_screencast_rect: %s stored, no panel to announce", key)
        return
    live = app.settings.for_device(key).screencast_region
    if live is not None and live[:4] != box:
        log.info("_apply_screencast_rect: %s's running cast moves to %s", key, box)
        app.settings.set_screencast_region(key, (*box, live[4]))
    app.events.publish(ScreencastRegionChanged(key=key, x=box[0], y=box[1],
                                               w=box[2], h=box[3],
                                               hide_border=hide_border))
