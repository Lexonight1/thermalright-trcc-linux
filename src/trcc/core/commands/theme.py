"""Theme + config + cloud + mask + image/video load Commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .._safe import is_safe_user_name
from ..errors import (
    HttpFetchError,
    ThemeError,
    TransportError,
    TrccError,
)
from ..events import (
    ErrorOccurred,
    FrameSent,
    ThemeExported,
    ThemeImported,
    ThemeLoaded,
    ThemeSaved,
)
from ..registry import find_product
from ..results import (
    CloudCategoryEntry,
    CloudThemeEntryResult,
    CloudThemeLoadResult,
    CloudThemesListResult,
    DeleteThemeResult,
    EnsureDataDownloadResult,
    ExportConfigResult,
    FileEntry,
    ImportConfigResult,
    MasksListResult,
    MaskUploadResult,
    ThemeDcExportResult,
    ThemeExportResult,
    ThemeImportResult,
    ThemeListEntry,
    ThemeResult,
    ThemesListResult,
    WebThemesListResult,
)
from ._base import Command
from ._helpers import (
    _IMAGE_EXTS,
    _LEGACY_MASK_FILENAME,
    _VIDEO_EXTS_FOR_LOAD,
    _VIDEO_EXTS_FOR_SAVE,
    _json_default_tuple,
    _publish_if_disconnect,
    _resolve_mask_path,
    _resolve_oriented_resolution,
    _search_theme_by_name,
    oriented_theme_path,
    overlay_elements_to_dc,
)
from .device import (
    ApplyMask,
    PlayVideo,
    SetMaskPosition,
    StopVideo,
)

if TYPE_CHECKING:
    from ...app import App
    from ..models import DeviceSettings, Theme

log = logging.getLogger(__name__)


def _theme_preview(theme_dir: Path) -> str:
    """Tile image for a theme dir: ``Theme.png`` → ``00.png`` → any ``*.png``
    → ``""``.  Single source for the browser tile so every UI agrees."""
    for name in ("Theme.png", "00.png"):
        candidate = theme_dir / name
        if candidate.is_file():
            return str(candidate)
    any_png = next(theme_dir.glob("*.png"), None)
    return str(any_png) if any_png is not None else ""


_THEME_MARKERS = ("trcc.json", "config1.dc", "config.json")


def _prefer_user_theme(app: App, candidate: Path) -> Path:
    """Apply the user-wins precedence (the same domain rule as ListThemes) to a
    RESTORE path.

    If *candidate* is a SHIPPED theme (under ``data_dir``) that a same-named USER
    theme shadows — the same relative path under ``user_data_dir`` carrying a
    theme marker — return the user equivalent so a stale program-path pointer
    self-heals on the next launch.  Otherwise return *candidate* unchanged.

    Restore-only by design: NOT applied to runtime rotation (which shares
    ``oriented_theme_path``), so rotating a device never surprise-swaps the
    theme — only a fresh restore re-resolves which theme a name means.  Pure
    path mapping, so it also covers the no-device restore path. (#theme-collision)
    """
    paths = app.platform.paths()
    data_root = paths.data_dir().resolve()
    cand = candidate.resolve()
    if not cand.is_relative_to(data_root):
        return candidate   # user-saved or external path — keep as stored
    user_equiv = paths.user_data_dir() / cand.relative_to(data_root)
    if user_equiv.is_dir() and any(
        (user_equiv / m).exists() for m in _THEME_MARKERS
    ):
        log.info("RestoreLastTheme: shipped theme %s shadowed by user theme %s "
                 "— restoring the user theme (user-wins)", candidate, user_equiv)
        return user_equiv
    return candidate


@dataclass(frozen=True, slots=True)
class LoadTheme(Command[ThemeResult]):
    """Parse a theme, persist it, render the first frame, and send it.

    If the device isn't attached, the theme is still persisted so it
    takes effect on next connect.  If no Renderer is attached to the
    App, the send step is skipped (parse + persist only).
    """
    key: str
    path: Path
    # Explicit user theme switch (default) establishes the theme's own state
    # and DROPS the device's overrides — live overlay edits, the applied mask,
    # AND the cloud-background / video override — so the new theme starts
    # clean from its bundled assets.  RestoreLastTheme passes
    # ``reset_overrides=False`` — a reconnect / restart / view-switch keep-
    # alive PRESERVES those overrides (all persisted in config.json) instead
    # of reverting to the theme's bundled layout/background.
    reset_overrides: bool = True

    def execute(self, app: App) -> ThemeResult:
        log.info("LoadTheme: key=%s path=%s reset_overrides=%s",
                 self.key, self.path, self.reset_overrides)
        try:
            theme = app.themes.load(self.path)
        except ThemeError as e:
            log.warning("LoadTheme: theme load failed for %s: %s",
                        self.path, e)
            app.events.publish(ErrorOccurred(message=str(e), kind="theme",
                                             key=self.key))
            return ThemeResult(ok=False, key=self.key, message=str(e))

        # Persist the absolute path — names are display strings, paths
        # are the stable reference RestoreLastTheme needs.
        app.settings.set_current_theme(self.key, str(theme.path.resolve()))
        # Single source of truth for "stop the previous video + clear the
        # cloud-background override + invalidate the scene cache": ``StopVideo``
        # (publishes VideoStopped, clears background_path, unloads playback).
        # ONLY on an explicit load — on a restore (reconnect / view-switch
        # keep-alive) the user's current cloud background + playback must
        # survive, otherwise picking the System-Info tab silently reverted the
        # background to the theme's bundled one (StopVideo cleared the override).
        if self.reset_overrides:
            StopVideo(key=self.key).execute(app)
        app.active_themes[self.key] = theme
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))
        log.info(
            "LoadTheme: %s loaded — prior playback + cloud-bg override "
            "cleared, scene cache invalidated, active theme persisted",
            theme.name,
        )

        # Explicit switch establishes the theme's own overlay layout: drop
        # any live user edits so the render shows THIS theme's elements (or
        # its mask's, applied just below), not edits made against the theme
        # the user just left.  Skipped on restore (reset_overrides=False) so a
        # reconnect keeps the persisted edits.  The user layer is the single
        # live-edit source resolved by ``resolve_overlay_elements``.
        if self.reset_overrides and app.settings.for_device(
            self.key
        ).user_overlay_elements:
            log.info("LoadTheme: clearing live user overlay edits for %s "
                     "(explicit theme switch)", self.key)
            app.settings.set_user_overlay_elements(self.key, [])

        # If device is attached + connected + Renderer available, send an
        # immediate first frame.  Otherwise the theme is saved for the
        # next connect / tick.
        theme_path_str = str(theme.path.resolve())

        # A theme can carry an attached mask under a top-level ``mask``
        # key — a library ref (``web/zt{w}{h}/<id>``, written by SaveTheme)
        # or a legacy absolute path.  ``mask_path`` resolves either shape
        # to the absolute ``01.png`` (relative refs would never load via
        # ``Path("web/...")``, which is relative to cwd).  ``ApplyMask``
        # then applies the image + auto-position; the mask's overlay
        # layout, when the theme has one, lives in the theme's own inline
        # ``elements`` — ApplyMask won't clobber it unless the resolved
        # mask dir carries its own ``config1.dc``.
        # On restore (reset_overrides=False) the user's last-applied mask is
        # already persisted — do NOT re-apply the theme's bundled mask, which
        # would override it (and ApplyMask would wipe the restored edits).
        embedded_mask = theme.config.get("mask") if self.reset_overrides else None
        if isinstance(embedded_mask, str) and embedded_mask:
            resolved_mask = app.themes.mask_path(theme)
            if resolved_mask is not None:
                apply = ApplyMask(key=self.key, path=resolved_mask).execute(app)
                if apply.ok:
                    log.info("LoadTheme: applied mask %s → %s (%s)",
                             embedded_mask, resolved_mask, theme.name)
                else:
                    log.warning(
                        "LoadTheme: theme %s mask %s resolved to %s but "
                        "ApplyMask failed: %s", theme.name, embedded_mask,
                        resolved_mask, apply.message,
                    )
            else:
                log.warning(
                    "LoadTheme: theme %s declares mask %r but it did not "
                    "resolve — skipping", theme.name, embedded_mask,
                )

        # Theme's OWN 01.png mask: the DC trailer's mask_position is
        # the mask CENTER on the canvas; the renderer wants the
        # TOP-LEFT.  Same conversion ApplyMask runs — port the legacy
        # behavior here so a freshly-loaded theme renders its bundled
        # mask at the right spot, not stored-center-as-top-left.
        from ...services.overlay import OverlayService
        theme_mask = theme.path / _LEGACY_MASK_FILENAME
        pos = theme.config.get("mask_position")
        if (
            self.reset_overrides
            and theme_mask.is_file() and isinstance(pos, (list, tuple))
            and len(pos) == 2
        ):
            device = app.devices.get(self.key)
            canvas: tuple[int, int] = (0, 0)
            if device is not None and device.profile is not None:
                canvas = device.profile.resolution
            if canvas != (0, 0) and app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
                try:
                    img = app._renderer.open_image(theme_mask)  # pyright: ignore[reportPrivateUsage]
                    mw, mh = app._renderer.surface_size(img)  # pyright: ignore[reportPrivateUsage]
                except Exception as e:
                    log.warning(
                        "LoadTheme: failed to size %s (%s) — using stored center",
                        theme_mask, e,
                    )
                    mw, mh = (0, 0)
                if mw > 0 and mh > 0:
                    px, py = OverlayService.calculate_mask_position(
                        theme.path, (mw, mh), canvas,
                    )
                    SetMaskPosition(key=self.key, x=px, y=py).execute(app)
                    log.info(
                        "LoadTheme: %s mask %dx%d on %dx%d canvas → "
                        "top-left (%d, %d) [center stored = %r]",
                        theme.name, mw, mh, canvas[0], canvas[1], px, py,
                        list(pos),
                    )
        # NB: do NOT dispatch SetMaskVisible from theme.config here.
        # Legacy's ``OverlayService.theme_mask_visible`` defaults True
        # and only toggles via explicit user action — the DC trailer's
        # ``mask_visible`` field is a theme-design metadata flag
        # ("this theme had a mask"), not a runtime visibility override.

        device = app.devices.get(self.key)
        if device is None or not device.is_connected:
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Theme '{theme.name}' saved (device not connected)",
            )
        if app._renderer is None:  # pyright: ignore[reportPrivateUsage]
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Theme '{theme.name}' saved (no Renderer attached)",
            )

        # Video-backed themes (Theme.{mp4,mov,webm,zt}) go through the
        # PlayVideo pipeline so the same VideoStarted event fires for
        # local + cloud + user-loaded videos — UI handler subscribes
        # once and starts its animation timer for any of them.  Static
        # themes (00.png) keep the build-frame-and-send path below.
        video_path = app.themes.video_path(theme)
        if video_path is not None:
            log.info(
                "LoadTheme: %s has bundled video %s — dispatching PlayVideo",
                theme.name, video_path.name,
            )
            play = PlayVideo(key=self.key, path=video_path).execute(app)
            if not play.ok:
                return ThemeResult(
                    ok=False, key=self.key, theme_name=theme.name,
                    theme_path=theme_path_str,
                    message=f"Theme '{theme.name}': {play.message}",
                )
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=(f"Theme '{theme.name}' loaded — playing "
                         f"{video_path.name} ({play.frame_count} frame(s))"),
            )

        # Read live sensors so the first frame the device sees after a
        # theme load has real metric values painted, not the no-sensor
        # warning at every overlay element.  RenderAndSend takes over
        # from the next tick onward.  Personalize via the same helper
        # used by MetricsLoop + ReadSensors + RenderAndSend so this
        # one-shot first-frame matches the cadence the periodic
        # broadcast will deliver.
        from ...services.metrics_personalize import personalize_readings
        s_app = app.settings.app
        try:
            sensors = personalize_readings(
                app.platform.sensors().read_all(),
                temp_unit=s_app.temp_unit,
                hdd_enabled=s_app.hdd_enabled,
            )
        except Exception as e:
            log.warning(
                "LoadTheme: sensors.read_all() raised %s — first frame "
                "will paint with empty sensors; tick observer will recover",
                e,
            )
            sensors = {}
        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
            sent = app.send(self.key, frame)
        except (TransportError, Exception) as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="render",
                                             key=self.key))
            _publish_if_disconnect(app, self.key, e)
            return ThemeResult(
                ok=False, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Render/send failed: {e}",
            )

        if sent:
            # Carry the rendered surface so the preview shows the loaded
            # theme's first frame without re-rendering it.
            app.events.publish(FrameSent(
                key=self.key, bytes_sent=len(frame),
                surface=app.display.rendered_surface(self.key),
            ))
            log.info(
                "LoadTheme: %s rendered + sent (%d bytes) from %s",
                theme.name, len(frame), theme_path_str,
            )
        return ThemeResult(
            ok=sent, key=self.key, theme_name=theme.name,
            theme_path=theme_path_str,
            message=(f"Theme '{theme.name}' loaded and sent ({len(frame)} bytes)"
                     if sent else f"Theme '{theme.name}' rendered but send failed"),
        )

@dataclass(frozen=True, slots=True)
class SaveTheme(Command[ThemeResult]):
    """Save the device's CURRENT rendered state as a new theme directory.

    Distinct from "duplicate the source theme" — the cloud background,
    cloud/user mask, user overlay edits, and mask-DC layout all live
    in :class:`DeviceSettings`, not in the active theme's directory.
    A pure ``shutil.copytree`` of the source dir would lose every one
    of those.  Instead the saved theme is a **reference manifest** —
    granular assets live once in the user library and the theme points
    at them by path, so identical backgrounds/masks dedup across themes:

      * ``trcc.json`` — the manifest.  ``elements`` inlines the final
        baked overlay layout (mask layout REPLACES the theme's elements
        when a mask override carries one, then user overlay edits are
        appended — same precedence the old ``config1.dc`` bake used).
        ``background`` / ``mask`` carry library refs
        (``web/{w}{h}/<hash>.png`` / ``web/zt{w}{h}/<hash>``) when the
        background is an image / a mask is present.  Render flags
        (``overlay_enabled`` / ``rotation`` / ``mask_visible`` / …) carry
        over from the source.
      * image background → stored in the library (deduped); video
        background → bundled verbatim as ``Theme.<ext>`` (videos dedup
        poorly and the in-dir convention already reloads them).
      * mask → stored image-only in the library (no ``config1.dc``); the
        manifest's inline ``elements`` own the layout, so ``ApplyMask`` on
        reload applies the mask image + position WITHOUT clobbering them.
      * ``Theme.png`` — a **snapshot of the live preview composite** (the
        GUI theme-chooser grid tile, just like shipped local themes have).
        Never sent to the device, so baking the full composite is safe.
      * no ``config1.dc`` — ``load()`` reads ``trcc.json`` directly.

    After a successful save the per-device overrides
    (``background_path`` / ``mask_path`` / ``mask_overlay_elements`` /
    ``user_overlay_elements``) are cleared and ``current_theme`` is
    re-pointed to the new directory.  Without this the next render
    would re-stack the (now-baked-in) overrides on top of the saved
    theme — double-mask, double-bg, doubled overlay text.

    On any failure after ``target.mkdir`` the partially-written
    target is removed and the original DeviceSettings are left
    untouched so the user can retry without losing state.
    """
    key: str
    name: str
    overwrite: bool = False

    def execute(self, app: App) -> ThemeResult:
        log.info("SaveTheme: key=%s name=%s overwrite=%s",
                 self.key, self.name, self.overwrite)
        if not is_safe_user_name(self.name):
            log.warning("SaveTheme: rejected unsafe name %r", self.name)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"invalid theme name {self.name!r} "
                         "(no path separators, '..', leading '.', or NUL bytes)"),
            )

        theme = app.active_themes.get(self.key)
        if theme is None:
            log.warning("SaveTheme: no active theme for %s — refusing to save",
                        self.key)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"no active theme for {self.key} — load one first"),
            )

        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "SaveTheme: cannot resolve resolution for %s "
                "— device must be connected with a known profile",
                self.key,
            )
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        import shutil
        target = app.platform.paths().user_theme_dir(w, h) / self.name
        device_settings = app.settings.for_device(self.key)
        log.info(
            "SaveTheme: source=%s target=%s resolution=%dx%d "
            "bg_override=%r mask_override=%r user_elements=%d "
            "mask_elements=%d",
            theme.path, target, w, h,
            device_settings.background_path,
            device_settings.mask_path,
            len(device_settings.user_overlay_elements),
            len(device_settings.mask_overlay_elements or []),
        )
        if target.exists() and not self.overwrite:
            log.info("SaveTheme: target %s exists — overwrite confirmation "
                     "needed", target)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                target_exists=True,
                message=f"A theme named {self.name!r} already exists.",
            )

        # Build into a staging dir, THEN atomically swap it over the target.
        # The theme being saved is usually the target ITSELF (re-saving the
        # loaded theme — a prior save re-points the active theme at the saved
        # dir), so deleting the target up-front would destroy the source's own
        # background/mask BEFORE _build_manifest reads them — the cause of the
        # "source theme has no background" data loss.  Staging keeps the source
        # intact until every asset is captured, and makes overwrite crash-safe.
        staging = target.parent / f".{self.name}.saving"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(exist_ok=False)
        except OSError as e:
            log.warning("SaveTheme: mkdir failed for staging %s: %s", staging, e)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=f"failed to create staging directory: {e}",
            )

        try:
            manifest = self._build_manifest(
                app, staging, theme, device_settings, w, h,
            )
            self._write_manifest(staging, manifest)
            self._write_thumbnail(app, staging, theme)
        except (OSError, ThemeError, TrccError) as e:
            log.exception("SaveTheme: assembly failed; discarding staging %s",
                          staging)
            shutil.rmtree(staging, ignore_errors=True)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=f"failed to assemble saved theme: {e}",
            )

        # Swap staging → target.  Every source asset is now captured in staging,
        # so removing the (possibly ==source) target is safe.
        try:
            if target.exists():
                log.warning("SaveTheme: overwriting existing theme %s", target)
                shutil.rmtree(target)
            staging.replace(target)
        except OSError as e:
            log.exception("SaveTheme: finalize failed; discarding staging %s",
                          staging)
            shutil.rmtree(staging, ignore_errors=True)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=f"failed to finalize saved theme: {e}",
            )

        # Saved theme is now fully self-contained — drop the device's
        # overrides + user edits, then re-point BOTH the live active
        # theme (what the renderer reads via app.active_themes) and the
        # persisted path (what RestoreLastTheme reads) at the new base.
        # Re-pointing the live object is what stops the next render from
        # reverting to the SOURCE theme's bundled mask/background after a
        # save — same switch LoadTheme performs.
        app.settings.set_user_overlay_elements(self.key, [])
        app.settings.set_mask_overlay_elements(self.key, None)
        app.settings.set_mask_path(self.key, None)
        app.settings.set_background_path(self.key, None)
        app.active_themes[self.key] = app.themes.load(target)
        app.settings.set_current_theme(self.key, str(target.resolve()))
        # Headless callers (CLI/API one-shots, tests) may run without
        # a Renderer attached — no scene cache to invalidate then.
        try:
            app.display.invalidate(self.key)
        except RuntimeError:
            pass
        log.info(
            "SaveTheme: %s — cleared overrides + re-pointed active theme "
            "+ current_theme to %s", self.name, target,
        )

        app.events.publish(ThemeSaved(
            key=self.key, theme_name=self.name, path=str(target),
        ))
        return ThemeResult(
            ok=True, key=self.key, theme_name=self.name,
            message=f"theme saved as '{self.name}' at {target}",
        )

    # ── Manifest assembly (called by execute, error-rolled-back as a unit) ──

    def _build_manifest(
        self,
        app: App,
        target: Path,
        theme: Theme,
        s: DeviceSettings,
        width: int,
        height: int,
    ) -> dict:
        """Assemble the reference-manifest dict written as ``trcc.json``.

        Carries the source's render flags, the baked overlay layout (see
        :meth:`_combine_elements`), and library refs for the background
        image / mask (see :meth:`_store_background` / :meth:`_store_mask`).
        A video background is bundled verbatim by :meth:`_store_background`
        and produces no ref.
        """
        manifest: dict = {"name": self.name, "width": width, "height": height}
        for field in (
            "overlay_enabled", "rotation", "background_display",
            "transparent_display", "mask_visible", "mask_position",
        ):
            if field in theme.config:
                manifest[field] = theme.config[field]
        manifest.setdefault("overlay_enabled", True)
        manifest["elements"] = self._combine_elements(theme, s)

        bg_ref = self._store_background(app, target, theme, s, width, height)
        if bg_ref is not None:
            manifest["background"] = bg_ref
        mask_ref = self._store_mask(app, target, theme, s, width, height)
        if mask_ref is not None:
            manifest["mask"] = mask_ref
        return manifest

    @staticmethod
    def _combine_elements(theme: Theme, s: DeviceSettings) -> list[dict]:
        """Bake the final overlay layout the saved theme should render.

        Uses the SAME single-layout resolver as the renderer
        (``resolve_overlay_elements``: user edits > applied mask > theme),
        so the saved theme inlines EXACTLY what was on screen — one layout,
        never theme + user stacked.  Inlined into ``trcc.json`` so ``load()``
        reads it directly, no binary DC round-trip.  (Legacy baked the
        single ``self.config`` it was rendering, for the same reason.)
        """
        from ...services.overlay import resolve_overlay_elements

        elements = resolve_overlay_elements(
            theme.config, s.mask_overlay_elements, s.user_overlay_elements,
        )
        log.info(
            "SaveTheme: baking %d overlay element(s) [source=%s]",
            len(elements),
            "user" if s.user_overlay_elements
            else "mask" if s.mask_overlay_elements is not None else "theme",
        )
        return elements

    @staticmethod
    def _pick_asset(
        override: str | None, source: Path | None, kind: str,
    ) -> Path | None:
        """The asset to persist: the device *override* when it's a real
        file, else the source theme's own *source* asset.

        Shared by :meth:`_store_background` / :meth:`_store_mask` — the
        cloud/user override (set in ``DeviceSettings``) always wins over
        the source theme's bundled asset.  *kind* labels the warning.
        """
        if override:
            cand = Path(override)
            if cand.is_file():
                return cand
            log.warning("SaveTheme: %s override %s does not exist; falling "
                        "back to source theme %s", kind, cand, kind)
        return source

    def _store_background(
        self,
        app: App,
        target: Path,
        theme: Theme,
        s: DeviceSettings,
        width: int,
        height: int,
    ) -> str | None:
        """Store the resolved current background; return its manifest ref.

        Precedence, all returning a ``web/{w}{h}/…`` ref (or ``None``):

        * **Catalog asset** — already under the cloud (``cloud_theme_dir``)
          or user (``user_background_dir``) ``web/{w}{h}`` tree → REFERENCED
          by ``web/{w}{h}/<name>``, never copied (cloud/user assets are
          shared; a re-save re-emits the ref so the bg can't be lost).
        * **Loose image** → canonical PNG bytes into the user library
          (deduped), returning the library ref.
        * **Loose video** → bundled verbatim as ``Theme.<ext>`` in the theme
          dir (returns ``None``; videos reload via the in-dir convention).

        ``DeviceSettings.background_path`` (cloud/user override) wins over the
        source theme's bundled background.
        """
        import shutil

        src = self._pick_asset(
            s.background_path, app.themes.background_path(theme), "background",
        )
        if src is None:
            log.warning("SaveTheme: source theme %r has no background",
                        theme.name)
            return None

        # Asset already in a data catalog (a cloud download or the user
        # background library) → REFERENCE it, never copy.  Mirrors
        # _store_mask's catalog handling + the data-ownership model: cloud /
        # user ``web/{w}{h}`` assets are shared, not bundled per-theme.  The
        # ref resolves back via _resolve_asset_ref (user root → cloud root), so
        # a saved theme just points at the existing download — and a re-save
        # re-emits the same ref instead of copying, so the background can't be
        # lost on overwrite. (#bg-save)
        src_resolved = src.resolve()
        paths = app.platform.paths()
        for root in (paths.user_background_dir(width, height),
                     paths.cloud_theme_dir(width, height)):
            if src_resolved.is_relative_to(root.resolve()):
                ref = f"web/{width}{height}/{src.name}"
                log.info("SaveTheme: background %s is a catalog asset → "
                         "reference %s (no copy)", src.name, ref)
                return ref

        ext = src.suffix.lower()
        if ext in _VIDEO_EXTS_FOR_SAVE:
            dst = target / f"Theme{ext}"
            shutil.copy2(src, dst)
            log.info("SaveTheme: video bg bundled → %s", dst)
            return None

        surface = app.renderer.open_image(src)
        ref = app.themes.store_background(
            app.renderer.encode_png(surface), ".png", width, height,
        )
        log.info("SaveTheme: image bg %s → library ref %s", src.name, ref)
        return ref

    def _store_mask(
        self,
        app: App,
        target: Path,
        theme: Theme,
        s: DeviceSettings,
        width: int,
        height: int,
    ) -> str | None:
        """Make the saved theme reference (not duplicate) its mask.

        A mask already in a catalog — cloud (``cloud_mask_dir``) or a prior
        user upload (``user_mask_dir``) — is REFERENCED by its existing ref
        ``web/zt{w}{h}/<id>`` and never copied into the user catalog
        (copying a cloud mask would duplicate it into the user's "my masks"
        browser).  A cloud mask already ships its own ``config1.dc``, and
        the user's edited metrics live inline in the saved manifest's
        ``elements``, so no DC is written here.

        A theme's OWN bundled mask (not in any catalog) is copied
        theme-local into the saved dir as ``01.png`` — self-contained, yet
        still never in the catalog.  Returns the ref for a catalog mask, or
        ``None`` for a theme-local one (``mask_path`` then falls back to the
        in-dir ``01.png``) and for no-mask.  ``DeviceSettings.mask_path``
        (user/cloud override) wins over the source theme's mask.
        """
        src = self._pick_asset(
            s.mask_path, app.themes.mask_path(theme), "mask",
        )
        if src is None:
            log.info("SaveTheme: no mask to save (neither override nor source)")
            return None

        # Catalog mask (cloud or prior upload) → reference it, never copy.
        src_resolved = src.resolve()
        paths = app.platform.paths()
        for root in (paths.cloud_mask_dir(width, height),
                     paths.user_mask_dir(width, height)):
            if src_resolved.is_relative_to(root.resolve()):
                ref = f"web/zt{width}{height}/{src.parent.name}"
                log.info("SaveTheme: mask %s is a catalog mask → reference "
                         "%s (no copy)", src.parent.name, ref)
                return ref

        # Theme's own bundled mask → copy theme-local so the saved theme is
        # self-contained, without adding to the user mask catalog.
        import shutil
        dst = target / _LEGACY_MASK_FILENAME
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            log.warning("SaveTheme: theme-local mask copy failed (%s) — mask "
                        "omitted", e)
            return None
        log.info("SaveTheme: bundled mask %s → theme-local %s", src.name, dst)
        return None

    @staticmethod
    def _write_manifest(target: Path, manifest: dict) -> None:
        """Write the reference manifest as ``target/trcc.json``."""
        import json

        (target / "trcc.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info(
            "SaveTheme: wrote manifest → %s (bg=%s mask=%s elements=%d)",
            target / "trcc.json", manifest.get("background"),
            manifest.get("mask"), len(manifest.get("elements") or []),
        )

    def _write_thumbnail(self, app: App, target: Path, theme: Theme) -> None:
        """Snapshot the live preview composite as ``target/Theme.png`` — the
        GUI theme-chooser grid tile, so a saved theme shows a real preview
        to pick from, just like shipped local themes.

        ``Theme.png`` is the chooser's preferred tile and is NEVER rendered
        to the device, so baking the full composite (background + mask +
        overlay) is safe.  Best-effort: needs a connected device + renderer
        to snapshot; a headless save falls back to the source theme's
        thumbnail.  Never raises — a thumbnail miss must not fail the save.
        """
        import shutil

        device = app.devices.get(self.key)
        if device is not None:
            try:
                from ...services.metrics_personalize import personalize_readings
                s_app = app.settings.app
                sensors = personalize_readings(
                    app.platform.sensors().read_all(),
                    temp_unit=s_app.temp_unit, hdd_enabled=s_app.hdd_enabled,
                )
                surface = app.display.build_preview_surface(
                    info=device.info, theme=theme, sensors=sensors,
                    profile=device.profile,
                )
                (target / "Theme.png").write_bytes(
                    app.renderer.encode_png(surface),
                )
                log.info("SaveTheme: preview snapshot → %s", target / "Theme.png")
                return
            except Exception as e:
                log.warning("SaveTheme: preview snapshot failed (%s) — "
                            "falling back to source thumbnail", e)

        src = theme.path / "Theme.png"
        if src.is_file():
            shutil.copy2(src, target / "Theme.png")
            log.info("SaveTheme: copied source thumbnail → %s",
                     target / "Theme.png")
        else:
            log.info("SaveTheme: no device + no source thumbnail — "
                     "saved without a grid tile")

@dataclass(frozen=True, slots=True)
class ExportConfig(Command[ExportConfigResult]):
    """Write one device's ``DeviceSettings`` to a JSON file.

    Distinct from :class:`ExportTheme`:
      * ``ExportTheme`` zips a theme directory (background + DC +
        masks) for sharing the theme assets.
      * ``ExportConfig`` snapshots the user's per-device prefs (active
        theme path, brightness, overlay edits, mask choice, fit mode,
        format prefs, etc.) — for backup / migration between hosts.

    Restore with :class:`ImportConfig`.  File format is the same shape
    Settings persists internally; the JSON is intentionally human-
    inspectable so reporters can paste it into issues.
    """
    key: str
    output_path: Path

    def execute(self, app: App) -> ExportConfigResult:
        import json
        log.info("ExportConfig: key=%s output=%s", self.key, self.output_path)
        try:
            snapshot = app.settings.snapshot_device(self.key)
        except Exception as e:
            log.warning("ExportConfig: snapshot failed for %s: %s", self.key, e)
            return ExportConfigResult(
                ok=False, key=self.key, output_path=str(self.output_path),
                message=f"snapshot failed: {e}",
            )
        payload = {
            "version": 1,
            "key": self.key,
            "device": snapshot,
        }
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=_json_default_tuple)
        except OSError as e:
            return ExportConfigResult(
                ok=False, key=self.key, output_path=str(self.output_path),
                message=f"write failed: {e}",
            )
        return ExportConfigResult(
            ok=True, key=self.key, output_path=str(self.output_path),
            message=(f"exported {self.key} config to {self.output_path}"),
        )

@dataclass(frozen=True, slots=True)
class ImportConfig(Command[ImportConfigResult]):
    """Restore one device's ``DeviceSettings`` from an :class:`ExportConfig` JSON.

    Atomic — replaces the named device's entire settings dataclass.
    Tolerant of older snapshot versions (unknown fields ignored;
    missing fields fall back to dataclass defaults).

    Refuses to import when the file's ``key`` field doesn't match the
    target key — prevents accidental cross-device clobber.  Pass the
    explicit ``--force`` at the caller edge if migration across keys
    is intentional (caller responsibility, not Command).
    """
    key: str
    input_path: Path

    def execute(self, app: App) -> ImportConfigResult:
        import json
        log.info("ImportConfig: key=%s input=%s", self.key, self.input_path)
        if not self.input_path.is_file():
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message=f"file not found: {self.input_path}",
            )
        try:
            with self.input_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as e:
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message=f"failed to parse JSON: {e}",
            )
        if not isinstance(payload, dict):
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message="config root must be an object",
            )
        snapshot_key = payload.get("key")
        if snapshot_key and snapshot_key != self.key:
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message=(f"key mismatch: snapshot is for {snapshot_key!r}, "
                         f"target is {self.key!r}"),
            )
        device_snapshot = payload.get("device")
        if not isinstance(device_snapshot, dict):
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message="missing 'device' object in snapshot",
            )
        try:
            app.settings.restore_device(self.key, device_snapshot)
        except Exception as e:
            log.warning("ImportConfig: restore failed for %s: %s", self.key, e)
            return ImportConfigResult(
                ok=False, key=self.key, input_path=str(self.input_path),
                message=f"restore failed: {e}",
            )
        return ImportConfigResult(
            ok=True, key=self.key, input_path=str(self.input_path),
            message=f"restored {self.key} config from {self.input_path}",
        )

@dataclass(frozen=True, slots=True)
class ExportTheme(Command[ThemeExportResult]):
    """Zip a theme under ``user_theme_dir(w, h) / theme_name`` to an archive path.

    Device-scoped — resolution comes from the device the caller named via
    ``key``, matching legacy's ``dev.export_config(path)`` shape where the
    device's own ``lcd_size`` supplied the per-resolution directory.

    The archive_path is written wherever the caller specifies (CLI / API
    are responsible for sanitizing that path at their edge).
    """
    key: str
    theme_name: str
    archive_path: Path

    def execute(self, app: App) -> ThemeExportResult:
        log.info("ExportTheme: key=%s theme=%s archive=%s",
                 self.key, self.theme_name, self.archive_path)
        if not is_safe_user_name(self.theme_name):
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportTheme: cannot resolve resolution for %s "
                "— device must be connected with a known profile",
                self.key,
            )
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        source = app.platform.paths().user_theme_dir(w, h) / self.theme_name
        if not source.is_dir():
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=f"theme {self.theme_name!r} not found at {source}",
            )

        try:
            app.themes.export(source, self.archive_path)
        except ThemeError as e:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=str(e),
            )

        app.events.publish(ThemeExported(
            theme_name=self.theme_name, archive_path=str(self.archive_path),
        ))
        return ThemeExportResult(
            ok=True, theme_name=self.theme_name,
            archive_path=str(self.archive_path),
            message=f"theme '{self.theme_name}' exported to {self.archive_path}",
        )

@dataclass(frozen=True, slots=True)
class ExportOverlay(Command[ThemeExportResult]):
    """Copy a theme's overlay config file to ``output_path``.

    Legacy ``export_config(lcd, path)`` exported the OVERLAY CONFIG
    ONLY — a single ``config1.dc`` (legacy binary) or
    ``trcc.json`` (next/-native).  Next/'s :class:`ExportTheme`
    zips the WHOLE directory (00.png + 01.png + Theme.png + …),
    which is heavy when a user just wants to share their metric-
    grid layout.

    Pick the source file in this order:
      1. ``theme_dir/config1.dc`` (legacy binary — most compatible
         with Windows TRCC users sharing layouts).
      2. ``theme_dir/trcc.json`` (next/-native JSON).
      3. Error if neither exists.

    Reuses :class:`ThemeExportResult` — the shape (theme_name +
    archive_path) fits.  Publishes :class:`ThemeExported` for
    consistency with the whole-theme path.

    Device-scoped — resolution comes from the device the caller named
    via ``key``, matching legacy's ``dev.export_config(path)`` shape.
    """
    key: str
    theme_name: str
    output_path: Path

    def execute(self, app: App) -> ThemeExportResult:
        log.info("ExportOverlay: key=%s theme=%s out=%s",
                 self.key, self.theme_name, self.output_path)
        if not is_safe_user_name(self.theme_name):
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportOverlay: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        source_dir = app.platform.paths().user_theme_dir(w, h) / self.theme_name
        if not source_dir.is_dir():
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"theme {self.theme_name!r} not found at {source_dir}",
            )

        # Prefer the legacy binary config so Windows TRCC users can
        # import the overlay layout without next/ around.
        candidates = (
            source_dir / "config1.dc",
            source_dir / "trcc.json",
        )
        source = next((c for c in candidates if c.is_file()), None)
        if source is None:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=(f"theme {self.theme_name!r} has no overlay config "
                         f"(no config1.dc or trcc.json in {source_dir})"),
            )

        try:
            import shutil
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, self.output_path)
        except OSError as e:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"failed to copy overlay config: {e}",
            )

        app.events.publish(ThemeExported(
            theme_name=self.theme_name,
            archive_path=str(self.output_path),
        ))
        return ThemeExportResult(
            ok=True, theme_name=self.theme_name,
            archive_path=str(self.output_path),
            message=(f"overlay config for '{self.theme_name}' exported to "
                     f"{self.output_path} (source: {source.name})"),
        )

@dataclass(frozen=True, slots=True)
class ImportTheme(Command[ThemeImportResult]):
    """Unpack a theme archive into ``user_theme_dir(w, h) / name``.

    Device-scoped — resolution comes from the device the caller named
    via ``key``, matching legacy's ``dev.import_config(path, data_dir)``
    shape where the device's ``lcd_size`` selected the per-resolution
    directory.

    ``name`` defaults to the archive filename's stem when blank.
    Zip-slip is filtered server-side by ``ThemeService.import_``.
    """
    key: str
    archive_path: Path
    name: str = ""

    def execute(self, app: App) -> ThemeImportResult:
        log.info("ImportTheme: key=%s archive=%s name=%r",
                 self.key, self.archive_path, self.name)
        chosen_name = self.name.strip() or self.archive_path.stem
        if not is_safe_user_name(chosen_name):
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path="",
                message=f"invalid theme name {chosen_name!r}",
            )

        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ImportTheme: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path="",
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        target = app.platform.paths().user_theme_dir(w, h) / chosen_name

        try:
            theme = app.themes.import_(self.archive_path, target)
        except ThemeError as e:
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path=str(target),
                message=str(e),
            )

        app.events.publish(ThemeImported(
            theme_name=chosen_name, path=str(theme.path),
        ))
        return ThemeImportResult(
            ok=True, theme_name=chosen_name, path=str(theme.path),
            message=f"theme imported as '{chosen_name}' at {theme.path}",
        )

@dataclass(frozen=True, slots=True)
class ListThemes(Command[ThemesListResult]):
    """Enumerate themes for a device resolution.

    With ``resolution=(w, h)`` (the GUI/CLI default), walks both
    ``paths.theme_dir(w, h)`` (pkg + cloud-downloaded) and
    ``paths.user_theme_dir(w, h)`` (legacy user-saved location) so
    in-place users see every theme without a migration step.

    With ``directory=``, scans that exact dir (escape hatch for tests
    and ad-hoc browsing).
    """
    resolution: tuple[int, int] | None = None
    directory: Path | None = None

    def execute(self, app: App) -> ThemesListResult:
        paths = app.platform.paths()
        if self.directory is not None:
            roots = [self.directory]
        elif self.resolution is not None:
            w, h = self.resolution
            # User dir FIRST so a user-saved theme WINS over a shipped theme of
            # the same name (dedupe by name below) — a saved "Theme1" shadows
            # the placeholder "Theme1", never the reverse.  Universal: every UI
            # dispatches this Command, so CLI / API / GUI agree on which theme a
            # name resolves to. (#theme-collision)
            roots = [paths.user_theme_dir(w, h), paths.theme_dir(w, h)]
        else:
            return ThemesListResult(
                ok=False, directory="", themes=[],
                message="ListThemes requires resolution=(w,h) or directory=...",
            )

        # Origin is location-derived (theme under user_data_dir → "user"), so
        # it is correct in directory mode too and replaces name heuristics.
        user_root = paths.user_data_dir().resolve()
        seen_names: set[str] = set()
        entries: list[ThemeListEntry] = []
        for root in roots:
            for theme in app.themes.list(root):
                if theme.name in seen_names:
                    log.info("ListThemes: %r in %s shadowed by a same-named "
                             "user theme — skipping", theme.name, root)
                    continue
                seen_names.add(theme.name)
                is_user = theme.path.resolve().is_relative_to(user_root)
                entries.append(ThemeListEntry(
                    name=theme.name, resolution=theme.resolution,
                    path=str(theme.path),
                    preview=str(_theme_preview(theme.path)),
                    origin="user" if is_user else "shipped",
                ))
        target_str = "; ".join(str(r) for r in roots)
        return ThemesListResult(
            ok=True, directory=target_str, themes=entries,
            message=f"{len(entries)} theme(s) under {target_str}",
        )

@dataclass(frozen=True, slots=True)
class ListWebThemes(Command[WebThemesListResult]):
    """List the downloaded cloud-theme previews for a resolution.

    Pure disk read of ``paths.cloud_theme_dir(w, h)`` — works with no
    device connected.  Returns domain entries (id / category / has_video);
    the API layer adds the preview/download URLs.  Empty until the data is
    fetched (``EnsureDataDownload``).
    """
    width: int
    height: int

    def execute(self, app: App) -> WebThemesListResult:
        log.info("ListWebThemes: %dx%d", self.width, self.height)
        web_dir = app.platform.paths().cloud_theme_dir(self.width, self.height)
        entries = app.themes.list_web_previews(web_dir)
        return WebThemesListResult(
            ok=True, width=self.width, height=self.height, entries=entries,
            message=f"{len(entries)} cloud preview(s) for "
                    f"{self.width}x{self.height}",
        )

@dataclass(frozen=True, slots=True)
class ExportDcTheme(Command[ThemeDcExportResult]):
    """Write a theme out as a legacy-compatible ``config1.dc`` file.

    Reads the named theme under ``user_theme_dir(w, h)``, layers in the
    device's user overlay elements (so the exported DC reflects what the
    user actually sees on screen), and writes ``output_path``.  Device-
    scoped — resolution comes from ``key``, matching legacy's
    device-driven export shape.  Used by anyone sharing a next/-managed
    theme back to Windows TRCC or legacy Linux users.
    """
    key: str
    theme_name: str
    output_path: Path

    def execute(self, app: App) -> ThemeDcExportResult:
        log.info("ExportDcTheme: key=%s theme=%s out=%s",
                 self.key, self.theme_name, self.output_path)
        if not is_safe_user_name(self.theme_name):
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportDcTheme: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        theme_dir = (
            app.platform.paths().user_theme_dir(w, h) / self.theme_name
        )
        if not theme_dir.is_dir():
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"theme not found at {theme_dir}",
            )
        settings = app.settings.for_device(self.key)
        user_overlays = [
            e.to_dict() for e in settings.user_overlay_elements
        ]
        try:
            written = app.themes.export_dc(
                theme_dir, self.output_path,
                user_overlay_elements=user_overlays,
            )
        except ThemeError as e:
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=str(e),
            )
        return ThemeDcExportResult(
            ok=True, theme_name=self.theme_name,
            output_path=str(written),
            message=f"wrote DC theme to {written}",
        )

@dataclass(frozen=True, slots=True)
class DeleteTheme(Command[DeleteThemeResult]):
    """Delete the theme directory at ``path``.

    Path-based — matches legacy's ``delete_theme(lcd, path)`` shape: the
    caller already resolved the theme's location (from the picker / list /
    filesystem walk), so the Command doesn't have to re-derive it from
    name + resolution.  Confined to the user-content tree to keep this
    Command from accidentally wiping system data.
    """
    path: Path

    def execute(self, app: App) -> DeleteThemeResult:
        log.info("DeleteTheme: path=%s", self.path)
        root = app.platform.paths().user_content_dir()
        try:
            target = self.path.resolve()
            root_resolved = root.resolve()
            target.relative_to(root_resolved)
        except (OSError, ValueError) as e:
            log.warning(
                "DeleteTheme: refusing to delete %s — not under %s (%s)",
                self.path, root, e,
            )
            return DeleteThemeResult(
                ok=False, theme_name=self.path.name, path=str(self.path),
                message=(f"refusing to delete {self.path} — "
                         f"not inside {root}"),
            )

        try:
            deleted = app.themes.delete(target.parent, target.name)
        except ThemeError as e:
            return DeleteThemeResult(
                ok=False, theme_name=target.name, path=str(target),
                message=str(e),
            )
        return DeleteThemeResult(
            ok=True, theme_name=deleted.name, path=str(deleted),
            message=f"Deleted theme at {deleted}",
        )

@dataclass(frozen=True, slots=True)
class UploadCustomMask(Command[MaskUploadResult]):
    """Copy a mask image into the user-mask dir for the device's resolution.

    Writes the source under ``paths.user_mask_dir(w, h)/custom_<name>/``
    as both ``01.png`` (the canonical mask renderers consume) and
    ``Theme.png`` (the preview thumbnail Thermalright's cloud masks
    use).  Matches legacy's mask-on-disk shape so cloud + user masks
    coexist and the legacy mask browser picks them both up.

    Then dispatches ApplyMask so the new mask wires onto the device.
    """
    key: str
    source: Path

    def execute(self, app: App) -> MaskUploadResult:
        import shutil

        if not self.source.is_file():
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Source not a file: {self.source}",
            )
        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=(f"Cannot resolve resolution for {self.key} — "
                         "connect the device or register the product first"),
            )
        masks_root = app.platform.paths().user_mask_dir(*resolution)
        mask_dir = masks_root / f"custom_{self.source.stem}"
        try:
            mask_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Failed to ensure masks dir: {e}",
            )
        mask_file = mask_dir / _LEGACY_MASK_FILENAME
        preview_file = mask_dir / "Theme.png"
        try:
            shutil.copy2(self.source, mask_file)
            # Preview = mask itself.  Legacy's cloud catalog ships a
            # distinct Theme.png, but user uploads only have the one
            # image; copying it satisfies legacy-port browsers that
            # gate visibility on Theme.png existence.
            shutil.copy2(self.source, preview_file)
        except OSError as e:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Copy failed: {e}",
            )
        # Every uploaded mask carries an editable config1.dc from the moment
        # of upload (allow_empty=True) — seeded with the device's CURRENT
        # overlay, or an empty placeholder the user then fills.  Written
        # BEFORE ApplyMask so the upload applies with its metrics.
        settings = app.settings.for_device(self.key)
        elements: list[dict] = []
        if settings.mask_overlay_elements is not None:
            elements = [e.to_dict() for e in settings.mask_overlay_elements]
        elements += [e.to_dict() for e in settings.user_overlay_elements]
        dc_bytes = overlay_elements_to_dc(elements, allow_empty=True)
        if dc_bytes is not None:
            try:
                (mask_dir / "config1.dc").write_bytes(dc_bytes)
                log.info("UploadCustomMask: wrote config1.dc (%d byte(s)) → %s",
                         len(dc_bytes), mask_dir.name)
            except OSError as e:
                log.warning("UploadCustomMask: config1.dc write failed (%s)", e)
        apply_result = ApplyMask(key=self.key, path=mask_file).execute(app)
        if not apply_result.ok:
            return MaskUploadResult(
                ok=False, key=self.key, path=str(mask_file),
                message=f"Uploaded but apply failed: {apply_result.message}",
            )
        return MaskUploadResult(
            ok=True, key=self.key, path=str(mask_file),
            message=f"Mask uploaded + applied: {mask_dir.name}",
        )

@dataclass(frozen=True, slots=True)
class ListMasks(Command[MasksListResult]):
    """Enumerate masks for a device resolution.

    With ``resolution=(w, h)`` (default for the GUI), scans both the
    cloud-downloaded mask dir (``paths.cloud_mask_dir``) and the
    user-created mask dir (``paths.user_mask_dir``).

    With ``directory=``, scans that exact dir (escape hatch for tests
    and CLI use).

    Masks come in two on-disk shapes:

    * **Legacy directory** — ``<root>/<id>/01.png`` (cloud catalog +
      ``UploadCustomMask``).  Returned with ``name=<id>``, ``path=01.png``.
    * **Flat image** — ``<root>/<name>.png`` (forward-compat for any
      future flat layout).  Returned with ``name=<filename>``.
    """
    resolution: tuple[int, int] | None = None
    directory: Path | None = None

    def execute(self, app: App) -> MasksListResult:
        if self.directory is not None:
            roots = [self.directory]
        elif self.resolution is not None:
            paths = app.platform.paths()
            w, h = self.resolution
            roots = [paths.cloud_mask_dir(w, h), paths.user_mask_dir(w, h)]
        else:
            return MasksListResult(
                ok=False, directory="", masks=[],
                message="ListMasks requires resolution=(w,h) or directory=...",
            )

        seen: set[Path] = set()
        entries: list[FileEntry] = []
        for root in roots:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                resolved = _resolve_mask_path(entry)
                if resolved is None or resolved in seen:
                    continue
                seen.add(resolved)
                # For legacy subdirs, surface the subdir name (e.g.
                # "000a") so users see the catalog id, not "01.png".
                display_name = entry.name if entry.is_dir() else resolved.name
                entries.append(FileEntry(name=display_name, path=str(resolved)))
        target_str = "; ".join(str(r) for r in roots)
        return MasksListResult(
            ok=True, directory=target_str, masks=entries,
            message=f"{len(entries)} mask(s) under {target_str}",
        )

@dataclass(frozen=True, slots=True)
class RestoreLastTheme(Command[ThemeResult]):
    """Re-load the theme persisted in Settings for *key*.

    Convenience wrapper around LoadTheme — looks up the last
    ``current_theme`` for this device and re-dispatches LoadTheme so
    the render pipeline catches up on connect or after a restart.

    ``current_theme`` is normally the theme's absolute path (written by
    LoadTheme).  Legacy values can be bare theme names like
    ``"image:00"`` or ``"Custom_Theme1"`` — those trigger a heuristic
    search across the device's known theme roots so existing users
    don't lose their last selection on upgrade.
    """
    key: str

    def execute(self, app: App) -> ThemeResult:
        settings = app.settings.for_device(self.key)
        stored = settings.current_theme
        if not stored:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"No persisted theme for {self.key}",
            )

        # Absolute or already-resolvable path → use it directly, BUT re-rooted
        # to the device's current orientation dir: a non-square panel restored
        # at 90°/270° must load the portrait-catalog variant (theme480854), not
        # the stored landscape path (theme854480), or the preview/dirs go
        # portrait while the theme stays landscape (#136).  Orientation is
        # already restored (``_restore_rotation``) before this runs.
        # reset_overrides=False: a reconnect/restart restores the device's
        # persisted overlay edits + last-applied mask, it does not revert to
        # the theme's bundled layout (that's an explicit-switch behavior).
        candidate = Path(stored)
        if candidate.is_dir():
            candidate = oriented_theme_path(app, self.key, candidate)
            # User-wins precedence on restore: a stale pointer at a shipped
            # theme that a same-named user theme now shadows re-resolves to the
            # user one (consistent with ListThemes). (#theme-collision)
            candidate = _prefer_user_theme(app, candidate)
            return LoadTheme(
                key=self.key, path=candidate, reset_overrides=False,
            ).execute(app)

        # Legacy bare-name value — search the known theme roots.
        resolved = _search_theme_by_name(app, self.key, stored)
        if resolved is None:
            return ThemeResult(
                ok=False, key=self.key, theme_name=stored,
                message=(f"Persisted theme {stored!r} not found in any "
                         "known theme root for this device"),
            )
        return LoadTheme(
            key=self.key, path=resolved, reset_overrides=False,
        ).execute(app)

@dataclass(frozen=True, slots=True)
class ListCloudThemes(Command[CloudThemesListResult]):
    """List themes available in Thermalright's hosted catalog.

    Pass ``category="a"`` (or any registered prefix) to scope the list,
    or ``"all"`` (the default) for everything.  Pure read — no network
    until ``LoadCloudTheme`` runs, since the catalog itself is static.
    """
    category: str = "all"

    def execute(self, app: App) -> CloudThemesListResult:
        try:
            themes = app.cloud_themes.list_themes(self.category)
        except ValueError as e:
            return CloudThemesListResult(
                ok=False, category=self.category, message=str(e),
            )
        categories = [
            CloudCategoryEntry(prefix=c.prefix, name=c.name, count=c.count)
            for c in app.cloud_themes.categories()
        ]
        entries = [
            CloudThemeEntryResult(
                id=t.id, category=t.category, category_name=t.category_name,
            )
            for t in themes
        ]
        return CloudThemesListResult(
            ok=True, category=self.category,
            categories=categories, themes=entries,
            message=f"{len(entries)} cloud theme(s) in {self.category!r}",
        )

@dataclass(frozen=True, slots=True)
class EnsureDataDownload(Command[EnsureDataDownloadResult]):
    """Force-install the theme + cloud + mask archives for a resolution.

    The DataInstaller is normally invoked implicitly by
    :class:`DiscoverDevices` (it ensures every attached device's
    resolution).  This Command exposes the same machinery for users
    who want to **pre-fetch** before connecting — e.g. populating a
    laptop's cache while still online so a headless display setup
    works offline later.

    Idempotent: archives already on disk are skipped.  Non-square
    resolutions also install the rotated counterpart for portrait /
    landscape switching.
    """
    width: int
    height: int

    def execute(self, app: App) -> EnsureDataDownloadResult:
        log.info("EnsureDataDownload: %dx%d", self.width, self.height)
        if self.width <= 0 or self.height <= 0:
            return EnsureDataDownloadResult(
                ok=False, width=self.width, height=self.height,
                message=(f"invalid resolution {self.width}x{self.height} "
                         "(both dimensions must be > 0)"),
            )
        result = app.data_install.ensure_all((self.width, self.height))
        return EnsureDataDownloadResult(
            ok=result.ok,
            width=self.width, height=self.height,
            themes_ok=result.themes_ok,
            web_ok=result.web_ok,
            masks_ok=result.masks_ok,
            message=(f"{self.width}x{self.height} ready" if result.ok else
                     f"partial install: themes={result.themes_ok} "
                     f"web={result.web_ok} masks={result.masks_ok}"),
        )

@dataclass(frozen=True, slots=True)
class LoadCloudTheme(Command[CloudThemeLoadResult]):
    """Download a cloud video and apply it as the device's background.

    Despite the name (kept for API compat), a cloud "theme" is just a
    video background — picking one swaps what plays behind the active
    theme's overlay + mask, not the theme itself.  Matches legacy
    ``select_cloud_theme``: it wraps the MP4 in ``ThemeInfo.from_video``
    and starts an animation timer; the active overlay state stays.

    Execution:
      1. Resolve the device's render resolution.
      2. ``CloudThemeService.materialise`` downloads the MP4 flat into
         ``paths.cloud_theme_dir(w, h)/<id>.mp4`` and generates the
         first-frame PNG + animated GIF previews for the GUI.
      3. Persist the new background path on ``DeviceSettings.background_path``
         so it survives an app restart.
      4. Dispatch ``PlayVideo(key, path=<mp4>)`` — that's the path
         MediaService + DisplayService already use to render a video
         background on every tick.
    """
    key: str
    theme_id: str

    def execute(self, app: App) -> CloudThemeLoadResult:
        log.info("LoadCloudTheme: key=%s theme_id=%s", self.key, self.theme_id)
        # Cloud backgrounds are catalogued per ORIENTED resolution (the C#
        # GetWebBackgroundImageDirectory direction split: 854480 ↔ 480854), so
        # at portrait we must materialise from web/480854 — not the native
        # landscape — or the landscape image gets fit-squished into the canvas.
        resolution = _resolve_oriented_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "LoadCloudTheme: cannot resolve resolution for %s — "
                "device not connected and no registry entry",
                self.key,
            )
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=(f"Cannot resolve resolution for {self.key} — "
                         "connect the device or register the product first"),
            )
        log.info("LoadCloudTheme: materialising %s @ %dx%d",
                 self.theme_id, resolution[0], resolution[1])
        try:
            mp4_path = app.cloud_themes.materialise(
                self.theme_id, resolution,
            )
        except ValueError as e:
            log.warning("LoadCloudTheme: ValueError materialising %s: %s",
                        self.theme_id, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=str(e),
            )
        except HttpFetchError as e:
            log.warning("LoadCloudTheme: download failed for %s: %s",
                        self.theme_id, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Cloud download failed: {e}",
            )
        except OSError as e:
            log.warning("LoadCloudTheme: local IO failed for %s: %s: %s",
                        self.theme_id, type(e).__name__, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Local IO failed: {e}",
            )

        log.info(
            "LoadCloudTheme: %s ready at %s — setting background override + "
            "dispatching PlayVideo",
            self.theme_id, mp4_path,
        )
        # Persist the new background on the device — survives restart.
        app.settings.set_background_path(self.key, str(mp4_path))
        # MediaService.load_video populates the playback; DisplayService's
        # ``_resolve_background`` short-circuits to ``playback.current``
        # when a playback exists, so this is the entire "play this video
        # as the bg" wire (overlay + mask stay untouched).
        play_result = PlayVideo(key=self.key, path=mp4_path).execute(app)
        return CloudThemeLoadResult(
            ok=play_result.ok,
            key=self.key,
            theme_id=self.theme_id,
            theme_path=str(mp4_path),
            message=play_result.message,
        )

@dataclass(frozen=True, slots=True)
class LoadImage(Command[ThemeResult]):
    """Show one image on the LCD as the background of a single-image theme.

    Smallest path for "show me this picture" — internally materialises
    a one-file theme directory under
    ``user_content_dir/single-image/<filename>`` then dispatches
    ``LoadTheme``.  Idempotent: re-running with the same image re-uses
    the staged directory rather than re-creating it.

    Errors surface as structured Results.  Acceptable extensions:
    PNG / JPG / JPEG / BMP / WEBP.
    """
    key: str
    path: Path

    def execute(self, app: App) -> ThemeResult:
        if not self.path.is_file():
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Image file not found: {self.path}.  "
                    "Check the path and try again."
                ),
            )
        if self.path.suffix.lower() not in _IMAGE_EXTS:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Unsupported image extension {self.path.suffix!r}.  "
                    f"Supported: {', '.join(sorted(_IMAGE_EXTS))}."
                ),
            )
        import json
        import shutil

        # Stage the image as a minimal next/-shape theme under a stable
        # subdirectory so re-runs are cheap and the theme appears in
        # `theme list` like any other.
        target_root = app.platform.paths().user_content_dir() / "single-image"
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create single-image theme directory: {e}",
            )
        theme_name = self.path.stem
        theme_dir = target_root / theme_name
        if theme_dir.exists() and not theme_dir.is_dir():
            theme_dir = (
                target_root / f"{theme_name}-from-{self.path.parent.name}"
            )
        try:
            theme_dir.mkdir(parents=True, exist_ok=True)
            target_image = theme_dir / self.path.name
            if (
                not target_image.is_file()
                or target_image.stat().st_size != self.path.stat().st_size
            ):
                shutil.copy2(self.path, target_image)
            config_path = theme_dir / "trcc.json"
            if not config_path.is_file():
                config_path.write_text(
                    json.dumps({
                        "name": f"image:{theme_name}",
                        "elements": [],
                    }, indent=2) + "\n",
                    encoding="utf-8",
                )
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Failed to stage image as theme: {e}",
            )
        return LoadTheme(key=self.key, path=theme_dir).execute(app)

@dataclass(frozen=True, slots=True)
class LoadVideo(Command[ThemeResult]):
    """Play a video on the LCD as a single-video theme.

    Conceptually parallel to :class:`LoadImage`: turn an arbitrary file
    into a one-shot theme directory + dispatch :class:`LoadTheme`.

    For ``.zt`` inputs the source is copied straight in.  For real video
    files (``.mp4``, ``.mov``, ``.webm``, etc.) the file is transcoded
    into a ``Theme.zt`` via :class:`VideoExporter`, sized to the
    device's native resolution and optionally clipped to ``start_ms`` →
    ``end_ms``.

    The device must be attached (so we know its native resolution).
    Errors surface as structured Results — never exceptions to UIs.
    """
    key: str
    path: Path
    start_ms: int = 0
    end_ms: int | None = None
    rotation: int = 0

    def execute(self, app: App) -> ThemeResult:
        if not self.path.is_file():
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Video file not found: {self.path}.  "
                    "Check the path and try again."
                ),
            )
        if self.path.suffix.lower() not in _VIDEO_EXTS_FOR_LOAD:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Unsupported video extension {self.path.suffix!r}.  "
                    f"Supported: {', '.join(sorted(_VIDEO_EXTS_FOR_LOAD))}."
                ),
            )

        target_w, target_h = self._resolve_target_size(app)
        if target_w == 0 or target_h == 0:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Don't know the target resolution for {self.key}.  "
                    "Connect the device first (or pass a key that matches "
                    "a row in the product registry)."
                ),
            )

        import json
        import shutil

        target_root = app.platform.paths().user_content_dir() / "single-video"
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create single-video theme directory: {e}",
            )
        theme_name = self.path.stem
        theme_dir = target_root / theme_name
        if theme_dir.exists() and not theme_dir.is_dir():
            theme_dir = (
                target_root / f"{theme_name}-from-{self.path.parent.name}"
            )
        try:
            theme_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create theme directory: {e}",
            )

        # Either copy a .zt straight in or transcode the source.
        zt_target = theme_dir / "Theme.zt"
        if self.path.suffix.lower() == ".zt":
            try:
                if (
                    not zt_target.is_file()
                    or zt_target.stat().st_size != self.path.stat().st_size
                ):
                    shutil.copy2(self.path, zt_target)
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to stage .zt as theme: {e}",
                )
        else:
            from ...services.video_export import (
                VideoExporter,
                VideoExportError,
                VideoExportRequest,
                probe_duration_ms,
            )
            end_ms = self.end_ms
            if end_ms is None:
                probed = probe_duration_ms(self.path)
                end_ms = probed if probed > 0 else self.start_ms + 10_000
            request = VideoExportRequest(
                source=self.path,
                start_ms=self.start_ms,
                end_ms=end_ms,
                target_w=target_w,
                target_h=target_h,
                rotation=self.rotation,
            )
            try:
                produced = VideoExporter().export_zt(request)
            except VideoExportError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Video export failed: {e}",
                )
            try:
                shutil.move(str(produced), str(zt_target))
                # Clean up the now-empty temp dir VideoExporter created.
                temp_parent = produced.parent
                shutil.rmtree(temp_parent, ignore_errors=True)
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to install Theme.zt into theme dir: {e}",
                )

        config_path = theme_dir / "trcc.json"
        if not config_path.is_file():
            try:
                config_path.write_text(
                    json.dumps({
                        "name": f"video:{theme_name}",
                        "elements": [],
                    }, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to write theme config: {e}",
                )
        return LoadTheme(key=self.key, path=theme_dir).execute(app)

    def _resolve_target_size(self, app: App) -> tuple[int, int]:
        """Prefer an attached device's profile, fall back to the registry."""
        device = app.devices.get(self.key)
        if device is not None:
            if device.profile is not None:
                return device.profile.resolution
            if device.info.native_resolution != (0, 0):
                return device.info.native_resolution
        # Pre-attach: parse vid/pid and ask the registry directly so
        # users can stage video themes ahead of plugging in the device.
        try:
            vid_s, pid_s = self.key.split(":")
            vid = int(vid_s, 16)
            pid = int(pid_s, 16)
        except ValueError:
            return (0, 0)
        product = find_product(vid, pid)
        if product is None:
            return (0, 0)
        return product.native_resolution
