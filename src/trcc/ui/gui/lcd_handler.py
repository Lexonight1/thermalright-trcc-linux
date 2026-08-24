"""LCDHandler — one per LCD device, wired to next/ Commands.

Self-contained handler for a single LCD device.  Holds:

* ``_device_key`` — vid:pid; ``app.devices[key]`` is the live Device
* ``_app: App`` — universal command/event hub
* ``_pm.state: DeviceState`` — cached canvas / mask / theme info,
  refreshed on connect / orientation / theme-load events
* ``_w`` — shared GUI widgets (preview, theme tabs, cuts, etc.)

Every device mutation goes through ``self._app.dispatch(Command(...))``.
Animation state (playing, interval, current frame) comes from
``VideoStatus`` / ``TickDisplay`` on the same bus — handler delegates
rather than caches, and never reaches into ``app.media`` (which does not
exist on the ``AppProxy`` a daemon-mode UI holds, #249).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QPixmap

from ...core.commands import (
    ApplyMask,
    BuildPreview,
    EnableOverlay,
    ExportTheme,
    ImportTheme,
    ListThemes,
    LoadCloudTheme,
    LoadTheme,
    RestoreDeviceState,
    RestoreLastTheme,
    SaveTheme,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetOrientation,
    SetOverlayConfig,
    SetSplitMode,
    StopVideo,
    UploadCustomMask,
    VideoStatus,
)
from ..presentation.lcd_presentation_model import LcdPresentationModel
from ..presentation.overlay_serialization import dc_as_legacy_overlay_config
from ..presentation.theme_directories import (
    oriented_theme_reload_target,
    resolve_theme_directories,
)
from .base_handler import BaseHandler

if TYPE_CHECKING:
    from ...app import App
    from ...core.models import DeviceSettings, ProductInfo
    from ...core.results import ThemeResult, VideoStatusResult

log = logging.getLogger(__name__)


class _DataReadyNotifier(QObject):
    """Thread-safe notifier: emits ``ready`` from any thread to the Qt main thread."""
    ready = Signal()


class LCDHandler(BaseHandler):
    """Per-LCD-device GUI handler, dispatching through next/'s App.

    Each LCD device gets its own handler.  The constructor signature
    keeps the legacy positional shape so the window's ``LCDHandler(
    device, widgets, make_timer, data_dir, is_visible_fn=…, app=…,
    lcd_idx=…)`` call works unchanged.
    """

    def __init__(
        self,
        device: Any,
        widgets: dict[str, Any],
        make_timer: Any,
        data_dir: Path,
        is_visible_fn: Any = None,
        app: App | None = None,
        lcd_idx: Any = '',
    ) -> None:
        super().__init__(device, 'form')
        if app is None:
            raise RuntimeError(
                "LCDHandler requires an App handle — composition root must pass one"
            )
        self._app: App = app
        # ``lcd_idx`` carries the device key in the next/ port (legacy
        # passed an int index into Trcc._lcd_devices).
        self._device_key: str = str(lcd_idx) if lcd_idx else device.info.key
        self._w = widgets
        self._data_dir = data_dir
        self._is_visible = is_visible_fn or (lambda: True)
        self.log: logging.Logger = log

        # Qt-free coordination model — owns the per-device DeviceState cache
        # AND the activation/view-lifecycle flags (ui_active gate, configured
        # first-load gate, brightness/split/background state).  PM-refactor
        # increment 5 grows the decisions onto it; the handler keeps the Qt.
        self._pm = LcdPresentationModel(self._device_key)
        # Slideshow cursor lives on ``app.slideshow`` (SlideshowService);
        # don't duplicate state here (S1.1 audit).

        # QPixmap cache keyed by frame index — avoids QImage→QPixmap
        # conversion on every video tick when the surface hasn't changed.
        self._pixmap_cache: dict[int, tuple[int, QPixmap]] = {}
        self._last_render_id: int | None = None

        # Animation observability: log one INFO line at first tick (proves
        # the QTimer fires) and on each silent-skip TRANSITION (so we see
        # "device disconnected" once, not every 66 ms).  Per-tick stays
        # DEBUG — flipping these on info-level when the timer starts is
        # how we tell a "didn't fire" bug apart from a "fired but skipped"
        # bug without reading the code.
        self._animation_first_tick_logged: bool = False
        self._animation_last_skip_reason: str | None = None

        # Thread-safe notifier for background data extraction → UI refresh
        self._data_notifier = _DataReadyNotifier()
        self._data_notifier.ready.connect(self._on_data_ready)

        # Timers (parent factory + signal wiring; lifetime owned here)
        self._animation_timer: QTimer = make_timer(self._on_video_tick)
        self._slideshow_timer: QTimer = make_timer(self._on_slideshow_tick)
        self._flash_timer: QTimer = make_timer(
            self._on_flash_timeout, single_shot=True,
        )

    # ── Public API ───────────────────────────────────────────────────

    @property
    def display(self) -> Any:
        """Legacy alias for the underlying device.

        Typed as ``Any`` so legacy attribute reads (``display.lcd_size``,
        ``display.connected``, etc.) used elsewhere in the window keep
        type-checking.  Phase 7 verification surfaces runtime gaps where
        the next/ Device doesn't expose the legacy attribute name.
        """
        return self._device

    @property
    def device_key(self) -> str:
        return self._device_key

    @property
    def is_configured(self) -> bool:
        """True once ``apply_device_config`` has loaded the persisted theme.

        Distinguishes first activation (must LOAD) from re-selection (must
        only READ).  ``device_key`` is set at construction, so it can't
        serve as this flag.
        """
        return self._pm.configured

    @property
    def current_theme_path(self) -> Path | None:
        """Active theme directory, or ``None`` if no theme is loaded.

        Tracked on ``_pm.state`` by the load + restore flows; exposed read-
        only so the window can query without reaching into private
        state (DIP boundary at the handler).
        """
        return self._pm.state.current_theme_path

    @property
    def lcd_size(self) -> tuple[int, int]:
        """Active resolution for this device.

        Cached on ``_pm.state`` by ``_refresh`` from
        ``device.profile.resolution`` (post-handshake) or the registry
        fallback.  Window-layer code that needs the canvas dims for
        image cutters / drag math reads this — never reaches into the
        protocol adapter directly.
        """
        return self._pm.state.lcd_size

    def _video_status(self) -> VideoStatusResult:
        """Ask the bus what this device's playback is doing.

        One dispatch instead of six reaches into ``app.media`` — which is a
        crash under ``TRCC_DAEMON=1``, where the handler holds an ``AppProxy``
        exposing ``dispatch`` and nothing else (#249).
        """
        status = self._app.dispatch(VideoStatus(key=self._device_key))
        self.log.debug("_video_status: playing=%s frame=%s/%s fps=%s",
                       status.playing, status.cursor, status.frame_count,
                       status.fps)
        return status

    def has_video_playback(self) -> bool:
        """True iff MediaService has frames bound for this device.

        ``playing`` alone is not enough: a bound playback that decoded zero
        frames is not something to animate, which is why this tests the count
        as well — and why ``frame_count`` is ``None`` rather than ``0`` when
        there is no playback at all.
        """
        status = self._video_status()
        answer = status.playing and bool(status.frame_count)
        self.log.debug("has_video_playback: %s (playing=%s frames=%s)",
                       answer, status.playing, status.frame_count)
        return answer

    # ── LCDDevice Config (C# ReadSystemConfiguration) ─────────────────

    def apply_device_config(self, info: ProductInfo, w: int, h: int) -> None:
        """First-time device setup + full widget refresh.

        ``info`` is a next/ ``ProductInfo``; its ``key`` ("vid:pid") is
        already the handler's ``_device_key``, set in __init__.
        """
        self.log.info("apply_device_config: %s %dx%d", info.key, w, h)
        self._pm.ui_active = True
        self._pm.configured = True
        # Per-device child logger — tags handler logs with the key
        self.log = logging.getLogger(f"{__name__}.{info.key}")
        # First connect: load the persisted theme onto the device.
        self._refresh(w, h, first_load=True)

    def reactivate(self, w: int, h: int) -> None:
        """Return to known device — device already configured from connect()."""
        self.log.info("reactivate: %dx%d", w, h)
        self._pm.ui_active = True
        # Re-select: read what the device is already showing; do NOT re-load.
        self._refresh(w, h, first_load=False)

    def restore_inactive_state(self) -> None:
        """Restore display state for an inactive LCD without touching shared widgets.

        Multi-display: all LCDs should keep playing their video even
        when not selected.  The unified ``RestoreDeviceState`` Command
        rehydrates the device's persisted theme + background; the handler
        subscribes to the resulting frame via the global FrameSent stream.

        The animation timer is started by the ``VideoStarted`` observer
        when ``RestoreDeviceState`` → ``LoadTheme`` → ``PlayVideo`` fires,
        so no explicit timer start here (DRY: one start site).
        """
        self._pm.ui_active = False
        self._pixmap_cache.clear()
        device = self._app.devices.get(self._device_key)
        if device is None or not device.is_connected:
            return
        self._app.dispatch(RestoreDeviceState(key=self._device_key))

    def _refresh(self, w: int, h: int, *, first_load: bool = False) -> None:
        """Update widgets from the device's current persisted settings.

        ``first_load`` distinguishes the two callers: first connect
        (``apply_device_config``) must LOAD the persisted theme onto the
        device; a re-select (``reactivate``) must only READ what the device
        is already showing — re-loading would clear the user's overrides
        (cloud background, overlay edits) and disturb the running device.
        """
        log.debug("_refresh: w=%s h=%s", w, h)
        self.log.info("_refresh: device_key=%s resolution=%dx%d",
                      self._device_key, w, h)
        # Cache canvas + lcd size + per-resolution dirs in the shared
        # DeviceState.  Done here (not in apply_device_config) so
        # reactivate() also refreshes them — reactivate runs every time
        # the user picks the device in the sidebar, and the paths port
        # is the source of truth for theme/mask/web directories.
        self._pm.set_canvas(w, h)
        paths = self._app.platform.paths()
        # Theme / web / mask dirs aren't cached on _state any more —
        # ``_update_theme_directories`` derives them per-call so portrait
        # rotation can switch the browser to the rotated dir on demand
        # (auto-rotation portrait).  Log the initial landscape set so
        # the connect-time picture is preserved.
        libs = self._app.libraries(self._device_key)
        self.log.info(
            "_refresh: theme_dir=%s web_dir=%s masks_dir(cloud)=%s "
            "user_mask_dir=%s",
            libs.theme_dir(w, h), libs.cloud_theme_dir(w, h),
            libs.cloud_mask_dir(w, h), paths.user_mask_dir(w, h),
        )
        # Typed source: every _restore_* below reads DeviceSettings
        # directly.  Pre-S1.2 this slot built an intermediate
        # ``cfg: dict`` shim so the methods could keep their legacy
        # ``cfg.get(field, default)`` shape — the shim has been removed
        # in favour of dataclass attribute access (typed by pyright,
        # defaults baked into DeviceSettings itself).
        ds = self._app.settings.for_device(self._device_key)

        self._w['preview'].set_resolution(w, h)
        self._w['preview'].set_image(None)
        self._w['image_cut'].set_resolution(w, h)
        self._w['video_cut'].set_resolution(w, h)
        self._w['theme_setting'].set_resolution(w, h)

        auto_loaded = self._update_theme_directories()

        self._restore_brightness(ds)
        self._restore_rotation(ds)
        self._restore_split_mode(ds, w, h)
        self._restore_slideshow(ds)
        self._update_device_info()

        if auto_loaded:
            return
        self._restore_theme_and_preview(first_load=first_load)

    def notify_data_ready(self) -> None:
        """Background install finished — re-list this device's grids.

        Safe from ANY thread: the refresh is emitted through a QObject
        signal, so it always runs on the Qt main thread no matter who calls
        (the install worker, the bus bridge, a test).  Until #275 nothing
        ever called this — the install ran inline before the window existed,
        so ``_on_data_ready`` sat connected and unreachable.
        """
        log.info("notify_data_ready: %s", self._device_key)
        self._data_notifier.ready.emit()

    def _on_data_ready(self) -> None:
        """Background data extraction finished — re-probe dirs and update UI."""
        log.info("_on_data_ready")
        self.log.info("_on_data_ready: refreshing dirs and theme lists")
        auto_loaded = self._update_theme_directories()
        self.log.info("_on_data_ready: done, auto_loaded=%s", auto_loaded)

    def _update_device_info(self) -> None:
        """Populate the selectable fingerprint line for the active device.

        Name · vid:pid · FBL/PM/SUB — the handshake bytes read straight from
        ``device.handshake`` (same source as the qtgui device inspector), so a
        user can copy the line into a bug report / porting note.  LED devices
        have no LCD handshake; the profile-only fields still identify them.
        """
        from ...core.protocol import pm_to_fbl

        info = self._device.info
        parts = [info.product, info.key]
        hs = self._device.handshake
        if hs is not None:
            fbl = hs.fbl if hs.fbl is not None else pm_to_fbl(
                hs.pm_byte, hs.sub_byte)
            parts += [f"FBL {fbl}", f"PM {hs.pm_byte}", f"SUB {hs.sub_byte}"]
        text = "  ·  ".join(parts)
        self.log.info("_update_device_info: %s", text)
        self._w['device_info_label'].setText(text)


    def _restore_brightness(self, ds: DeviceSettings) -> None:
        self._pm.brightness_level = ds.brightness
        self.log.info("Restoring brightness: %d%%", self._pm.brightness_level)
        self._app.dispatch(SetBrightness(
            key=self._device_key, percent=self._pm.brightness_level,
        ))

    def _restore_rotation(self, ds: DeviceSettings) -> None:
        rotation_index = ds.orientation // 90
        rotation = rotation_index * 90
        self.log.debug("_restore_rotation: rotation=%d", rotation)
        self._app.dispatch(SetOrientation(
            key=self._device_key, degrees=rotation,
        ))
        self._sync_rotation_state(rotation)
        self._w['rotation_combo'].blockSignals(True)
        self._w['rotation_combo'].setCurrentIndex(rotation_index)
        self._w['rotation_combo'].blockSignals(False)
        self._sync_preview_size()   # composed orientation, not pre-rotation (#136)
        self._update_theme_directories()

    def _restore_split_mode(self, ds: DeviceSettings, w: int, h: int) -> None:
        mode = self._pm.apply_split_mode(ds.split_mode, (w, h))
        self.log.debug("_restore_split_mode: split_mode=%d ldd_is_split=%s mode=%d",
                       self._pm.split_mode, self._pm.ldd_is_split, mode)
        self._app.dispatch(SetSplitMode(key=self._device_key, mode=mode))

    def _restore_slideshow(self, ds: DeviceSettings) -> None:
        """Restore slideshow UI state from typed DeviceSettings.

        ``SlideshowService`` owns the transient rotation cursor;
        ``DeviceSettings.slideshow_*`` owns the persisted config
        (themes / interval / enabled flag).  This slot just pushes
        the persisted state into the legacy local-theme panel widgets
        so the next ``_update_slideshow_state`` reads back what
        ``ConfigureSlideshow`` / ``SetSlideshow`` saved.
        """
        local = self._w['theme_local']
        if ds.slideshow_themes or ds.slideshow_enabled:
            interval = max(1, int(ds.slideshow_interval_s))
            # Public API on the panel (backed by its SlideshowModel) — no
            # reaching into private attrs.
            local.set_slideshow_state(
                list(ds.slideshow_themes), ds.slideshow_enabled, interval,
            )
            self._update_slideshow_state()
        else:
            self._slideshow_timer.stop()
            local.set_slideshow_state([], False, local.get_slideshow_interval())

    def _restore_theme_and_preview(self, *, first_load: bool = False) -> None:
        """Show the device's theme + overlay in the GUI.

        On a re-select (``first_load=False``) this READS what the device is
        already rendering — the cached current frame via
        ``display.rendered_surface`` (``rebuild_preview``) plus the overlay
        editor repopulated from the active theme — and dispatches NOTHING to
        the device.  Re-loading there (the old behaviour) ran RestoreLastTheme
        → LoadTheme → StopVideo, which cleared the user's cloud background +
        overlay overrides and disturbed the running device just because the
        GUI changed tabs.  Only first connect (``first_load=True``) loads the
        persisted theme onto the device.
        """
        if not first_load:
            current = self._app.settings.for_device(
                self._device_key,
            ).current_theme
            if not current:
                self._w['preview'].set_image(None)
                return
            self.log.info(
                "_restore_theme_and_preview: re-select — reading current "
                "frame for %s (theme=%s), no re-load", self._device_key,
                current,
            )
            self._pm.state.current_theme_path = Path(current)
            # Repopulate the overlay editor from the active theme (GUI only —
            # no EnableOverlay / render / send to the device).  The toggle
            # shows the DEVICE's persisted state, not "does this theme carry
            # elements" — a sidebar switch must report what is on screen.
            self._restore_overlay_editor(Path(current))
            # Show what the device is already rendering — no re-render/send.
            self.rebuild_preview()
            return

        result = self._app.dispatch(RestoreLastTheme(key=self._device_key))
        if not result.ok:
            self.log.info("_restore_theme_and_preview: no saved theme — %s",
                          result.message)
            self._w['preview'].set_image(None)
            return

        self.log.info(
            "_restore_theme_and_preview: loaded %s from %s",
            result.theme_name, result.theme_path,
        )
        # Restore the overlay grid from the theme's persisted config1.dc
        # (or trcc.json).  Without this the overlay UI is empty on every
        # restart even though the theme renders correctly on the device.
        if result.theme_path:
            self._restore_overlay_editor(Path(result.theme_path))
        # Track the restored theme directory so deletion / re-renders
        # can reference it.  For video-backed themes the VideoStarted
        # observer (``on_video_started``) takes over animating; for
        # static themes we refresh the preview here.
        self._pm.state.current_theme_path = (
            Path(result.theme_path) if result.theme_path else None
        )
        if not self._video_status().playing:
            self.rebuild_preview()

    # ── Theme (C# Theme_Click_Event) ───────────────────────────────
    # _select_theme is gone — next/'s LoadTheme Command owns the whole
    # build/cache/render/persist cycle.  Callers dispatch LoadTheme
    # directly through _select_theme_from_path / select_cloud_theme /
    # _on_slideshow_tick.

    def select_theme_from_path(self, path: Path, persist: bool = True) -> None:
        """Public entry for theme selection by path (local theme clicks)."""
        self._select_theme_from_path(path, persist=persist)

    def _select_theme_from_path(self, path: Path, persist: bool = True,
                                overlay_config: bool = True) -> None:
        """Load a local/mask theme by directory path.

        Direct port of legacy ``LCDHandler._select_theme_from_path``:
        the orchestration order matters — every step is here because
        the legacy sequence relies on the state being reset BEFORE the
        new theme + overlay load runs.  Re-ordering any step risks
        leaking the previous mask / animation / video onto the device.
        """
        self.log.info("_select_theme_from_path: %s persist=%s overlay_config=%s",
                 path, persist, overlay_config)
        if not path.exists():
            self.log.warning("_select_theme_from_path: path does not exist: %s", path)
            return
        self._slideshow_timer.stop()
        self._app.dispatch(EnableOverlay(key=self._device_key, enabled=False))

        self._pm.background_active = False
        # LoadTheme internally dispatches StopVideo (clears the previous
        # playback + cloud-bg override + publishes VideoStopped which
        # stops the timer via the bus_bridge observer) and, if the new
        # theme has a Theme.{mp4,mov,webm,zt}, dispatches PlayVideo
        # which publishes VideoStarted to restart the timer.  The
        # handler does not have to coordinate that here.

        # Picking a new theme clears the previous mask — legacy persists
        # ``mask_id=''`` here so a follow-up render doesn't keep the old
        # mask layered on top of the new theme's bg.  Direct settings
        # write (no Command) mirrors legacy's
        # ``Settings.save_device_settings(mask_id='')`` — the upcoming
        # LoadTheme below triggers the render via its own publish chain.
        self._app.settings.set_mask_path(self._device_key, None)
        self._w['theme_setting'].background_panel.set_enabled(False)
        self._w['theme_setting'].screencast_panel.set_enabled(False)
        self._w['theme_setting'].video_panel.set_enabled(False)

        # LoadTheme dispatches through the App — the Command owns the
        # theme info build, scene cache invalidation, and (if persist)
        # the per-device current_theme update in app.settings.
        result = self._app.dispatch(LoadTheme(
            key=self._device_key, path=path,
        ))
        self._pm.state.current_theme_path = path if result.ok else None
        if result.ok:
            self._sync_preview_size()   # bezel matches portrait/landscape theme (#136)
        if overlay_config:
            self._load_theme_overlay_config(path)

        if not persist or not self._device_key:
            self.log.warning("_select_theme_from_path: not persisting (persist=%s, key=%s)",
                             persist, self._device_key)

    def select_cloud_theme(self, theme_info: Any) -> None:
        """Handle cloud theme selection — a BACKGROUND swap, not a
        theme load.

        Picking a cloud item:
          * Swaps the video that plays behind the active theme's
            overlay + mask (legacy ``select_cloud_theme`` behaviour).
          * Does NOT replace the active theme.  The user's mask layout,
            metrics, brightness, rotation all stay.

        ``LoadCloudTheme`` is the command that owns the flow:
          1. materialise the MP4 (idempotent — skip if already cached)
          2. set ``DeviceSettings.background_path`` so the override
             survives an app restart
          3. dispatch ``PlayVideo`` to load MediaService playback —
             DisplayService renders the video on every tick
        """
        self.log.info("select_cloud_theme: %s (video=%s)", theme_info.name,
                      getattr(theme_info, 'video', None))
        self._slideshow_timer.stop()
        self._pm.background_active = False
        self._w['theme_setting'].background_panel.set_enabled(False)
        self._w['theme_setting'].screencast_panel.set_enabled(False)

        theme_id = getattr(theme_info, 'id', None) or theme_info.name
        if not theme_id:
            self.log.warning(
                "select_cloud_theme: cloud item has no id/name — refusing",
            )
            return
        result = self._app.dispatch(LoadCloudTheme(
            key=self._device_key, theme_id=theme_id,
        ))
        if not result.ok:
            self.log.warning(
                "select_cloud_theme: LoadCloudTheme failed for %s: %s",
                theme_id, result.message,
            )
            return
        # ``LoadCloudTheme`` → ``PlayVideo`` publishes ``VideoStarted``;
        # the bus_bridge observer routes it back to this handler's
        # ``on_video_started`` which starts the per-frame Qt timer.
        # One start site, one stop site — the same way restoring a
        # local video theme works.

    def apply_mask(self, mask_info: Any) -> None:
        """Apply mask overlay on top of current content."""
        self.log.info("apply_mask: %s path=%s", mask_info.name, mask_info.path)
        if not mask_info.path:
            self._w['preview'].set_status(f"Mask: {mask_info.name}")
            return
        mask_dir = Path(mask_info.path)
        # DC first — sets overlay resolution + element positions for this mask
        self._load_theme_overlay_config(mask_dir)
        is_custom = getattr(mask_info, 'is_custom', False)
        if is_custom:
            r = self._app.dispatch(UploadCustomMask(
                key=self._device_key, source=mask_dir,
            ))
        else:
            r = self._app.dispatch(ApplyMask(
                key=self._device_key, path=mask_dir,
            ))
        if r.ok:
            self._w['preview'].set_status(r.message)
        else:
            self._w['preview'].set_status(f"Mask failed: {r.message}")

    def update_mask_position(self, x: int, y: int) -> None:
        """Update mask overlay position and re-render."""
        log.debug("update_mask_position: x=%s y=%s", x, y)
        self._app.dispatch(SetMaskPosition(
            key=self._device_key, x=x, y=y,
        ))
        self._render_and_send()

    def save_theme(self, name: str, *, overwrite: bool = False) -> ThemeResult:
        self.log.info("save_theme: name=%s overwrite=%s", name, overwrite)
        r = self._app.dispatch(SaveTheme(
            key=self._device_key, name=name, overwrite=overwrite,
        ))
        self._w['preview'].set_status(r.message)
        if r.ok:
            # Re-list via ListThemes so the new theme appears with user-
            # precedence (same universal path as the initial listing).
            self._update_theme_directories()
        return r

    def export_config(self, path: Path) -> None:
        r = self._app.dispatch(ExportTheme(
            key=self._device_key,
            theme_name=path.stem,
            archive_path=path,
        ))
        self._w['preview'].set_status(r.message)

    def import_config(self, path: Path) -> None:
        r = self._app.dispatch(ImportTheme(
            key=self._device_key, archive_path=path,
        ))
        self._w['preview'].set_status(r.message)
        if r.ok:
            self._update_theme_directories()   # re-list via ListThemes

    # ── DC File Loading ────────────────────────────────────────────

    def _read_overlay_layout(self, theme_dir: Path) -> dict[str, dict[str, Any]]:
        """The theme's persisted overlay layout, in the grid's shape.

        Reads only — ``trcc.json`` ``elements`` for saved themes,
        ``config1.dc`` / legacy ``config.json`` for older/packaged ones (see
        ``dc_as_legacy_overlay_config``).  Touches no widget and dispatches
        nothing, so a caller that must NOT write device state can still ask
        what the theme carries.
        """
        layout = dc_as_legacy_overlay_config(theme_dir)
        self.log.info("_read_overlay_layout: dir=%s → %d element(s)",
                      theme_dir, len(layout))
        return layout

    def _show_overlay_layout(
        self, layout: dict[str, dict[str, Any]], enabled: bool,
    ) -> None:
        """Put a layout + toggle state on the grid.  GUI only.

        Dispatches nothing and persists nothing — the two callers that DO
        own device state (``_load_theme_overlay_config`` for a user-initiated
        load, ``_restore_overlay_editor`` for a reconnect) decide that for
        themselves.  Order matches the original: enable first, then load, so
        the grid's model sees the toggle before the elements.
        """
        self.log.info("_show_overlay_layout: %d element(s) enabled=%s",
                      len(layout), enabled)
        self._w['theme_setting'].set_overlay_enabled(enabled)
        if layout:
            self._w['theme_setting'].load_from_overlay_config(layout)

    def _restore_overlay_editor(self, theme_dir: Path) -> None:
        """Show the persisted overlay state on reconnect — never write it.

        An automatic restore must not DECIDE overlay-enabled.
        ``DeviceSettings.overlay_enabled`` is the single authority (see the
        ``build_overlay`` comment in ``services/display.py``) and the user set
        it deliberately; deriving it from "does this theme carry elements"
        overwrote that on every boot, so an overlay switched off came back on
        at the next launch (#276).  ``_load_theme_overlay_config`` used to
        take a ``persist`` flag meant to express exactly this, but the body
        never read it — the restore path passed ``persist=False`` and got a
        persisted ``EnableOverlay`` anyway.
        """
        self.log.info("_restore_overlay_editor: dir=%s", theme_dir)
        enabled = self._app.settings.for_device(
            self._device_key,
        ).overlay_enabled
        self._show_overlay_layout(self._read_overlay_layout(theme_dir), enabled)
        self._pm.state.overlay_enabled = enabled

    def _load_theme_overlay_config(self, theme_dir: Path) -> None:
        """Adopt a theme's overlay layout as the device's live overlay.

        For USER-INITIATED loads only — a theme click, a mask apply, a
        slideshow advance.  The theme establishes both the layout and the
        toggle (a theme with no layout switches the overlay off), which is
        legacy's behaviour and the one the GUI standards document.  A
        reconnect goes through ``_restore_overlay_editor`` instead, which
        honours the persisted toggle rather than replacing it.

        Not replayed through ``SetOverlayConfig``: that Command takes
        next/-shape elements with ids, used by the editor when the user drops
        a new element, not by a load.
        """
        layout = self._read_overlay_layout(theme_dir)
        enabled = bool(layout)
        self.log.info(
            "_load_theme_overlay_config: dir=%s → %d element(s), "
            "overlay %s", theme_dir, len(layout),
            "enabled" if enabled else "disabled (theme carries no layout)",
        )
        self._show_overlay_layout(layout, enabled)
        self._app.dispatch(EnableOverlay(
            key=self._device_key, enabled=enabled,
        ))
        self._pm.state.overlay_enabled = enabled
        self._render_and_send()

    # ── Video lifecycle (bus_bridge observers) ─────────────────────

    def on_video_started(self, event: Any) -> None:
        """Domain event ``VideoStarted`` arrived for this device.

        Single entry point for "start animating".  Anything that wants
        a video to play — local theme load, cloud-bg select,
        play-pause-resume on a paused playback, slideshow tick, future
        Commands — publishes ``VideoStarted`` and lands here.

        ``event.path`` is the VIDEO FILE (not the theme directory), so
        we don't touch ``_state.current_theme_path`` here — the Command
        that initiated the load (``LoadTheme`` / ``LoadCloudTheme``)
        owns that field's lifecycle.
        """
        if event.key != self._device_key:
            return
        self.log.info(
            "on_video_started: %s frames=%d interval=%dms",
            event.path, event.frame_count, event.interval_ms,
        )
        self._start_animation_timer(
            event.interval_ms, reason="video-started",
        )
        if self._pm.ui_active:
            self._w['preview'].set_playing(True)
            self._w['preview'].show_video_controls(True)

    def on_video_stopped(self, event: Any) -> None:
        """Domain event ``VideoStopped`` arrived for this device.

        Single entry point for "stop animating".  Mirrors
        ``on_video_started`` — every stop path (StopVideo Command,
        device disconnect cleanup, theme switch) lands here.
        """
        if event.key != self._device_key:
            return
        self.log.info("on_video_stopped: device=%s", self._device_key)
        self._stop_animation_timer(reason="video-stopped")
        if self._pm.ui_active:
            self._w['preview'].set_playing(False)
            self._w['preview'].show_video_controls(False)

    # ── Video (C# ucBoFangQiKongZhi1) ─────────────────────────────

    def play_pause(self) -> None:
        self.log.info("play_pause: device=%s", self._device_key)
        playback = self._app.media.playback(self._device_key)
        if playback is None:
            self.log.warning(
                "play_pause: no playback bound for %s — toggle dropped",
                self._device_key,
            )
            return
        # Toggle pause state.  next/'s Playback exposes pause(bool).
        was_paused = playback.paused
        playback.pause(not was_paused)
        playing = not playback.paused
        self.log.info("play_pause: was_paused=%s → playing=%s",
                      was_paused, playing)
        self._w['preview'].set_playing(playing)
        # Pause is a transient toggle on an EXISTING playback — no
        # VideoStarted / VideoStopped is published.  Drive the Qt timer
        # directly here through the same start/stop helpers so the
        # observability hooks fire (entry log + first-tick reset).
        if playing:
            self._start_animation_timer(
                self._video_interval_ms(), reason="play_pause-resume",
            )
        else:
            self._stop_animation_timer(reason="play_pause-pause")

    def stop_video(self) -> None:
        self.log.info("stop_video: device=%s", self._device_key)
        # StopVideo publishes VideoStopped → the bus_bridge observer
        # routes back to ``on_video_stopped`` which stops the timer.
        self._app.dispatch(StopVideo(key=self._device_key))
        self._w['preview'].set_playing(False)
        self._w['preview'].show_video_controls(False)

    def seek(self, percent: float) -> None:
        """Jump playback to ``percent`` (0.0-1.0) of total frames."""
        from ...core.commands import SeekVideo
        playback = self._app.media.playback(self._device_key)
        if playback is None:
            self.log.warning(
                "seek(%.3f): no playback bound for %s — dropped",
                percent, self._device_key,
            )
            return
        total = playback.frame_count
        frame = self._pm.seek_frame(percent, total)
        self.log.info("seek: percent=%.3f frame=%d/%d", percent, frame, total)
        self._app.dispatch(SeekVideo(key=self._device_key, frame=frame))

    def set_video_fit_mode(self, mode: str) -> None:
        self.log.info("set_video_fit_mode: mode=%r", mode)
        self._app.dispatch(SetFitMode(key=self._device_key, mode=mode))
        # Re-render preview on the next FrameSent / tick

    def _video_interval_ms(self) -> int:
        """Return ms-per-frame for the active playback, 33 as fallback.

        Only used by ``play_pause`` to resume an EXISTING paused
        playback — load-new-video paths get the interval from the
        ``VideoStarted`` event payload instead (DIP: don't query the
        service if the event already carries the answer).
        """
        return self._pm.video_interval_ms(self._video_status().fps)

    def _start_animation_timer(self, interval_ms: int, reason: str) -> None:
        """Single entry point for starting the per-frame video timer.

        Phase 4 collapses the previous three call sites (cloud theme
        select, restore-last-theme, inactive-restore) onto a single
        VideoStarted observer that routes here.  Centralising the start
        site is the SRP win — it also lets the first-tick diagnostic be
        reset in exactly one place.
        """
        self._animation_first_tick_logged = False
        self._animation_last_skip_reason = None
        self.log.info(
            "_start_animation_timer: %dms (reason=%s) device=%s",
            interval_ms, reason, self._device_key,
        )
        self._animation_timer.start(max(1, interval_ms))

    def _stop_animation_timer(self, reason: str) -> None:
        """Single entry point for stopping the per-frame video timer.

        Idempotent: no log when already stopped.  Phase 4 routes the
        VideoStopped observer here.
        """
        if not self._animation_timer.isActive():
            return
        self.log.info(
            "_stop_animation_timer: reason=%s device=%s",
            reason, self._device_key,
        )
        self._animation_timer.stop()
        self._animation_first_tick_logged = False
        self._animation_last_skip_reason = None

    def _on_video_tick(self) -> None:
        """Timer callback: advance one video frame.

        next/ owns playback in :class:`MediaService`; ``RenderAndSend``
        builds + encodes + sends the current cursor's frame.  Preview
        widget refreshes via the ``FrameSent`` → ``rebuild_preview``
        bridge — no per-tick image plumbing here.

        Observability rule (CLAUDE.md "Logging coverage is mandatory"):
        first tick of every animation logs at INFO; subsequent ticks at
        DEBUG.  Silent-skip branches log at INFO on STATE TRANSITION
        only — same skip reason in a row stays DEBUG so a disconnected
        device doesn't spam 15 lines/s.
        """
        from ...core.commands import TickDisplay

        if not self._animation_first_tick_logged:
            self.log.info(
                "_on_video_tick: first tick fired for %s",
                self._device_key,
            )
            self._animation_first_tick_logged = True

        # ONE Command does advance + render + send.  The cursor deliberately
        # advances before the connected-check inside the Command, preserving
        # the old order: an unplugged device's video keeps running so it
        # resumes in sync rather than frozen where it dropped.
        result = self._app.dispatch(TickDisplay(key=self._device_key))

        if result.frame_count is None or result.cursor is None:
            # Animation timer is firing but the playback was cleared —
            # WARN once (state-transition); the timer should have been
            # stopped when the playback was cleared.  TickDisplay sets the
            # video fields as a set, so either being None means "no playback";
            # testing both is what lets the type checker prove it too.
            self.log.warning(
                "_on_video_tick: timer firing but device %s has no playback — "
                "stopping animation timer",
                self._device_key,
            )
            self._stop_animation_timer(reason="playback-cleared")
            return

        # MULTI-DISPLAY GATE — load-bearing.  Every LCDHandler shares ONE
        # preview/progress widget set, so only the handler that owns the panel
        # may write to it.  The DISPATCH above is deliberately NOT gated: a
        # background device must keep ticking or its LCD freezes while another
        # device owns the GUI (see ``set_inactive``).
        if self._pm.ui_active:
            percent = self._pm.progress_fraction(
                result.cursor, result.frame_count,
            )
            self._w['preview'].set_progress(
                percent, result.cursor, result.frame_count,
            )

        if not result.ok:
            device = self._app.devices.get(self._device_key)
            if device is None or not device.is_connected:
                self._log_tick_skip(
                    reason="device-not-connected",
                    detail=f"device {self._device_key} not connected — skip send",
                )
                return

        # Cleared on the happy path so a subsequent disconnect re-logs.
        self._animation_last_skip_reason = None

        if not result.ok:
            # Render failure during animation playback is a real user-
            # visible bug (frozen / stuttering LCD).  WARN, not DEBUG.
            self.log.warning(
                "_on_video_tick: render failed cursor=%d/%d — %s",
                result.cursor, result.frame_count, result.message,
            )

    def _log_tick_skip(self, *, reason: str, detail: str) -> None:
        """Log a per-tick skip at INFO on first occurrence of *reason*,
        DEBUG on repeats — preserves the diagnostic value while keeping
        per-frame noise out of the log."""
        if reason != self._animation_last_skip_reason:
            self.log.info("_on_video_tick: %s", detail)
            self._animation_last_skip_reason = reason
        else:
            self.log.debug("_on_video_tick: %s", detail)

    # ── Overlay (C# ucXiTongXianShi1) ─────────────────────────────

    def on_overlay_changed(self, element_data: dict | list) -> None:
        """Forward overlay config change from settings panel.

        Accepts the next/ element LIST (the grid's current dispatch shape) or
        the legacy keyed dict; the body normalizes both below.
        """
        self.log.info("on_overlay_changed: %d elements",
                      len(element_data) if element_data else 0)
        if element_data is None:
            self.log.warning("on_overlay_changed: no payload — nothing to do")
            return
        # An EMPTY list is a real edit: the user deleted the last element.
        # This used to be dropped by a falsiness guard, so the deletion never
        # reached the bus, the previous list stayed persisted, and the element
        # came back — the second half of #276 ("whichever one I delete last
        # still appears").  The sole dispatcher, ``_on_elements_changed``,
        # always sends a real list, so the guard protected nothing.
        #
        # The same guard shape, one hop up in ``trcc_app``, had already caused
        # exactly this bug once: gating on dict-only silently dropped every
        # colour/drag edit.
        # Apply overlay change via the Command bus.  EnableOverlay
        # persists the toggle; SetOverlayConfig persists the element
        # list.  next/ skips the legacy "is video playing" cache-update
        # branch — the render service handles overlay refresh next tick.
        # Editing an element implies wanting to see it, so an edit against a
        # switched-off overlay switches it on.  Deleting the LAST one implies
        # the opposite, so an empty payload must not — that would answer a
        # "remove everything" by turning the overlay on.
        if element_data and not self._pm.state.overlay_enabled:
            self._app.dispatch(EnableOverlay(
                key=self._device_key, enabled=True,
            ))
            self._pm.state.overlay_enabled = True
        self._app.dispatch(SetOverlayConfig(
            key=self._device_key,
            elements=tuple(element_data.values())
                if isinstance(element_data, dict) else tuple(element_data),
        ))
        self._render_and_send()

    def handle_frame(self, image: Any) -> None:
        """Receive the rendered frame from ``FrameSent`` — show it directly.

        The primary preview path (legacy's ``handler.handle_frame(image)``):
        the surface that ``build_frame`` produced + sent is the preview
        image, so it goes straight to the widget — no second render.
        ``fast`` follows the animation timer so video uses the fast paint.
        """
        # Per-tick; DEBUG.  Note when UI is gated so a frozen preview
        # while LCD still updates is visible in the log.
        if image is None:
            self.log.debug("handle_frame: None surface — skip")
            return
        if self._pm.ui_active:
            self._w['preview'].set_image(
                image, fast=self._animation_timer.isActive(),
            )
        else:
            self.log.debug(
                "handle_frame: dropped (ui_active=False, %s)", self._device_key,
            )

    def _composed_preview_size(self) -> tuple[int, int]:
        """Preview bezel/label dims for the active theme.

        Portrait when a portrait theme is composed on a non-square rotate=True
        panel, else the device size swapped for user rotation — so the preview
        frame asset + label match what the panel shows.  Falls back to the
        cached canvas size (pre-handshake / no theme). (#136 phase 3)

        The compose-vs-fallback rule + the cached canvas live on the model;
        this View just gathers the device/theme primitives to feed it.
        """
        device = self._app.devices.get(self._device_key)
        ds = self._app.settings.for_device(self._device_key)
        return self._pm.preview_size(
            self._app.display,
            info=device.info if device is not None else None,
            theme=self._app.active_themes.get(self._device_key),
            profile=device.profile if device is not None else None,
            orientation=ds.orientation,
        )

    def _sync_preview_size(self) -> None:
        """Resize the preview bezel/label to the active theme's composed
        orientation.  Cheap arithmetic; only the asset reload inside
        ``set_resolution`` is real work, and that only matters on change. (#136)"""
        ow, oh = self._composed_preview_size()
        ds = self._app.settings.for_device(self._device_key)
        self.log.info("_sync_preview_size: orientation=%d → preview %dx%d",
                      ds.orientation, ow, oh)
        self._w['preview'].set_resolution(ow, oh)

    def rebuild_preview(self) -> None:
        """Fallback preview refresh for sends that carry no surface.

        The hot path (RenderAndSend / LoadTheme) now ships the rendered
        surface in ``FrameSent`` and the bridge calls :meth:`handle_frame`
        directly — no re-render.  This is only reached when the event has
        no surface (SendFrame / SendColor / SendImage / keepalive): reuse
        the last cached frame if one exists, else build a one-off surface.
        Idempotent.
        """
        if not self._pm.ui_active:
            self.log.debug(
                "rebuild_preview: ui_active=False for %s — skip",
                self._device_key,
            )
            return
        image = self._app.display.rendered_surface(self._device_key)
        if image is None:
            # No frame rendered yet (pre-load) — build a one-off surface.
            self.log.debug(
                "rebuild_preview: no cached frame for %s — building once",
                self._device_key,
            )
            image = self._build_preview_surface()
        if image is None:
            self.log.debug(
                "rebuild_preview: no surface built (theme/device pre-load?)",
            )
            return
        self._w['preview'].set_image(image, fast=self._animation_timer.isActive())

    def update_preview(self, image: Any) -> None:
        """Display a frame that was already rendered and sent to the device."""
        log.debug("update_preview")
        if self._pm.ui_active:
            self._w['preview'].set_image(image)
        else:
            self.log.debug(
                "update_preview: dropped (ui_active=False, %s)", self._device_key,
            )

    def update_metrics(self, metrics: Any) -> None:
        """Metrics tick: cache for video-overlay redraws on next frame."""
        # Per-tick on every metrics broadcast; DEBUG only.
        log.debug("update_metrics")
        self._pm.state.last_metrics = metrics
        readings = getattr(metrics, 'readings', None) or {}
        self.log.debug(
            "update_metrics: %s readings=%d", self._device_key, len(readings),
        )

    def flash_element(self, index: int) -> None:
        """Flash/blink selected overlay element on preview."""
        from ...core.commands import FlashOverlayElement, ResolveOverlay
        # The overlay-element widgets are 1:1 (in order) with the EFFECTIVE
        # layout on screen (user > mask > theme), so map the clicked index →
        # that element's real id.  Every entry carries one: ResolveOverlay
        # mints positional ids for a theme's own elements, which come from a
        # DC parse and have none — the old bare-index fallback then named
        # something FlashOverlayElement could never match (#150/#203).
        elements = self._app.dispatch(
            ResolveOverlay(key=self._device_key),
        ).elements
        # Out of range means the widget list and the layout have desynced —
        # keep a name so the failure is one findable WARNING, not a crash.
        element_id = (
            elements[index].id if 0 <= index < len(elements) else str(index)
        )
        self.log.info("flash_element: index=%d → element_id=%s",
                      index, element_id)
        self._app.dispatch(FlashOverlayElement(
            key=self._device_key, element_id=element_id, duration_ms=980,
        ))
        self._flash_timer.start(980)
        self._render_and_send()

    def _on_flash_timeout(self) -> None:
        log.info("_on_flash_timeout")
        self.log.debug("_on_flash_timeout: re-rendering")
        self._render_and_send()

    # ── Display Settings ───────────────────────────────────────────

    def set_brightness(self, percent: int) -> None:
        self.log.info("set_brightness: %d%% -> %d%% device=%s",
                      self._pm.brightness_level, percent, self._device_key)
        self._pm.brightness_level = percent
        self._app.dispatch(SetBrightness(
            key=self._device_key, percent=percent,
        ))

    def _sync_rotation_state(self, degrees: int) -> None:
        """Reflect a rotation in the cached ``_state`` (is_rotated + lcd_size).

        BOTH the interactive ``set_rotation`` and the startup
        ``_restore_rotation`` must call this — dispatching ``SetOrientation``
        alone only rotates the DEVICE, not the GUI's cached geometry that
        ``_update_theme_directories`` / ``_sync_preview_size`` read.  Without
        it a persisted portrait orientation restores on the device but the
        catalogs + preview stay landscape.
        """
        self._pm.apply_rotation(degrees)

    def set_rotation(self, degrees: int) -> None:
        self.log.info("set_rotation: degrees=%d device=%s",
                      degrees, self._device_key)
        self._app.dispatch(SetOrientation(
            key=self._device_key, degrees=degrees,
        ))
        self._sync_rotation_state(degrees)
        ow, oh = self._pm.state.lcd_size
        self.log.info(
            "set_rotation: rotation=%d output=%dx%d rotated=%s",
            degrees, ow, oh, self._pm.state.is_rotated,
        )
        self._sync_preview_size()   # composed orientation, portrait-theme aware (#136)
        # _update_theme_directories switches the browser catalog to the new
        # orientation dims + re-lists it, and auto-loads the first theme ONLY on
        # first install (nothing active yet).  When a theme IS already active it
        # returns False and leaves the OLD-orientation theme rendering — so reload
        # the active theme's oriented variant here, or the device keeps a
        # landscape bg letterboxed into the portrait buffer (#169 "not filling").
        if not self._update_theme_directories():
            self._reload_theme_for_orientation()

    def _reload_theme_for_orientation(self) -> None:
        """Reload the ACTIVE theme from the just-switched orientation catalog.

        A rotation swaps the browser catalog (``theme1600720`` ↔ ``theme7201600``)
        but not the rendered theme; the C# re-authors the theme per orientation,
        so the portrait catalog ships a genuine portrait ``00.png`` that fills the
        720×1600 buffer.  Reloading the same-name variant from the new catalog
        gives the same result — a filled, upright frame (#169).

        Falls back to keeping the current theme when the new catalog has no
        same-name variant (a user's custom theme, or the #136 portrait-fallback
        where the local dir resolves back to landscape); there the render pipeline
        pixel-rotates the landscape art so it is at least correctly oriented.
        """
        active = self._pm.state.current_theme_path
        if active is None:
            return
        dirs = resolve_theme_directories(
            self._app.libraries(self._device_key),
            canvas_size=self._pm.state.canvas_size,
            lcd_size=self._pm.state.lcd_size,
            is_rotated=self._pm.state.is_rotated,
        )
        target = oriented_theme_reload_target(active, dirs)
        if target is None:
            self.log.info(
                "_reload_theme_for_orientation: no oriented variant of '%s' in the "
                "%dx%d catalog — keeping current theme (pixel-rotate fallback)",
                active.name, *dirs.catalog_size,
            )
            return
        self.log.info(
            "_reload_theme_for_orientation: reloading '%s' from the %dx%d catalog "
            "→ %s", active.name, *dirs.catalog_size, target,
        )
        self._select_theme_from_path(target, persist=True, overlay_config=True)

    def set_split_mode(self, mode: int) -> None:
        self.log.info("set_split_mode: %d -> %d device=%s",
                      self._pm.split_mode, mode, self._device_key)
        self._pm.split_mode = mode
        self._app.dispatch(SetSplitMode(
            key=self._device_key, mode=mode,
        ))

    # ── Background / Screencast Toggles ────────────────────────────

    def on_background_toggle(self, enabled: bool) -> None:
        """Handle background display toggle.

        Enabling "background" mode means "show the theme's static bg,
        not the override video".  StopVideo handles the timer through
        VideoStopped — no direct ``_animation_timer.stop()`` here.
        """
        self.log.info("on_background_toggle: enabled=%s device=%s",
                      enabled, self._device_key)
        self._pm.background_active = enabled
        if enabled:
            self._app.dispatch(StopVideo(key=self._device_key))
            self._w['preview'].set_playing(False)
            self._w['preview'].show_video_controls(False)
        self._render_and_send()
        kind = "video" if self._video_status().playing else "image"
        self._w['preview'].set_status(
            f"Background: {'On' if enabled else 'Off'} ({kind})",
        )

    def on_screencast_frame(self, image: Any) -> None:
        """Handle captured screencast frame — preview + send to LCD.

        Encoding to wire bytes runs through ``app.display.build_screencast_frame``
        before the SendFrame dispatch.  Best-effort: if the device isn't
        currently registered, drop silently — screencast outlives device
        churn.
        """
        # Per-tick path; entry stays DEBUG.
        if self._pm.ui_active:
            self._w['preview'].set_image(image)
        device = self._app.devices.get(self._device_key)
        if device is None:
            self.log.debug(
                "on_screencast_frame: device %s not registered — skip send",
                self._device_key,
            )
            return
        try:
            data = self._app.display.build_screencast_frame(
                info=device.info, frame=image,
            )
        except Exception as e:
            self.log.warning(
                "on_screencast_frame: encode failed for %s: %s: %s",
                self._device_key, type(e).__name__, e,
            )
            return
        from ...core.commands import SendFrame
        self._app.dispatch(SendFrame(key=self._device_key, data=data))

    # ── Slideshow / Carousel ───────────────────────────────────────

    def _update_slideshow_state(self) -> None:
        local = self._w['theme_local']
        enabled = local.is_slideshow()
        interval_s = local.get_slideshow_interval()
        themes = local.get_slideshow_themes()
        self.log.info(
            "_update_slideshow_state: enabled=%s themes=%d interval=%ss",
            enabled, len(themes), interval_s,
        )

        # ConfigureSlideshow + SetSlideshow own persistence AND reset
        # the SlideshowService cursor.  Dispatch BEFORE starting the
        # Qt timer so the first tick reads a freshly-reset cursor.
        from ...core.commands import ConfigureSlideshow, SetSlideshow
        self._app.dispatch(ConfigureSlideshow(
            key=self._device_key,
            themes=tuple(t.name for t in themes),
            interval_s=float(interval_s),
        ))
        self._app.dispatch(SetSlideshow(
            key=self._device_key, enabled=enabled,
        ))

        if enabled and themes:
            self._slideshow_timer.start(interval_s * 1000)
        else:
            self._slideshow_timer.stop()

    def on_slideshow_delegate(self) -> None:
        """Handle slideshow toggle from local theme panel."""
        self.log.info("on_slideshow_delegate: device=%s", self._device_key)
        self._update_slideshow_state()

    def _on_slideshow_tick(self) -> None:
        """Auto-rotate to next theme — SlideshowService owns the cursor.

        Pre-S1.1 this maintained a local ``self._slideshow_index``
        counter that duplicated ``SlideshowService._state[key].cursor``
        — two sources of truth for the same rotation position.  The
        service cursor was reset by ConfigureSlideshow but never
        advanced by anything, leaving daemon-mode / CLI / API rotation
        broken: only the GUI's local counter moved.

        Post-S1.1 the GUI calls ``app.slideshow.advance(key, config)``
        which returns the next theme NAME (or None when not yet due).
        Single cursor across all surfaces.
        """
        from ...services.slideshow import SlideshowConfig
        local = self._w['theme_local']
        themes = local.get_slideshow_themes()
        if not themes:
            self.log.warning(
                "_on_slideshow_tick: themes list empty — stopping timer",
            )
            self._slideshow_timer.stop()
            return
        # Build the canonical config from the user's panel state.
        # Could read from app.settings.for_device(key).slideshow_*
        # but the panel is the live UI source — these two should
        # match post-S1.2 (which restored the panel state from
        # DeviceSettings on startup).
        config = SlideshowConfig(
            enabled=True,
            interval_s=float(local.get_slideshow_interval()),
            themes=[t.name for t in themes],
        )
        next_name = self._app.slideshow.advance(self._device_key, config)
        if next_name is None:
            # Within the interval window — service decided not to
            # rotate yet.  Qt timer will fire again at next interval.
            return
        # Resolve name → theme_info.  The slideshow stores names; the
        # panel knows the path for each name.
        theme_info = next(
            (t for t in themes if t.name == next_name), None,
        )
        if theme_info is None:
            self.log.warning(
                "_on_slideshow_tick: service returned name %r but "
                "the panel has no theme by that name — skipping",
                next_name,
            )
            return
        path = Path(theme_info.path)
        self.log.info(
            "_on_slideshow_tick: SlideshowService → %s (%s)",
            next_name, path,
        )
        if path.exists():
            self._app.dispatch(LoadTheme(
                key=self._device_key, path=path,
            ))
            self._pm.state.current_theme_path = path
            self._load_theme_overlay_config(path)
        else:
            self.log.warning(
                "_on_slideshow_tick: theme path missing %s — skipping",
                path,
            )

    # ── Rendering ──────────────────────────────────────────────────

    def _render_and_send(self) -> None:
        """Render overlay + send to LCD, update preview.

        Skipped while video playback owns the wire (the animation timer
        loop dispatches its own ``RenderAndSend``).  Preview refresh
        happens via the ``FrameSent`` → ``rebuild_preview`` bridge.
        """
        from ...core.commands import RenderAndSend
        if self._animation_timer.isActive():
            self.log.debug(
                "_render_and_send: skipped — animation timer owns the wire",
            )
            return
        device = self._app.devices.get(self._device_key)
        if device is None or not device.is_connected:
            self.log.debug(
                "_render_and_send: device %s not connected — skip",
                self._device_key,
            )
            return
        result = self._app.dispatch(RenderAndSend(key=self._device_key))
        if not result.ok:
            # Static-theme render failure is user-visible.  WARN, not DEBUG.
            self.log.warning(
                "_render_and_send: %s render failed — %s",
                self._device_key, result.message,
            )

    def render_and_preview(self) -> Any:
        """Render overlay and update preview (no send)."""
        self.log.info("render_and_preview: device=%s", self._device_key)
        image = self._build_preview_surface()
        if image is not None and self._pm.ui_active:
            self._w['preview'].set_image(image)
        return image

    def _build_preview_surface(self) -> Any:
        """Ask the bus for a preview surface — one Command, every UI.

        Returns None when the device has no active theme yet (pre-load) or
        the key no longer points at a live Device.  The lookups, the sensor
        read and the render guard all moved into :class:`BuildPreview`, which
        also personalizes the readings the way the wire path does — this GUI
        used to draw °C numbers under a °F glyph.
        """
        result = self._app.dispatch(BuildPreview(key=self._device_key))
        if not result.ok:
            # Blank preview is user-visible; the Command already logged why.
            self.log.warning("_build_preview_surface: %s — %s",
                             self._device_key, result.message)
            return None
        if result.surface is None:
            self.log.debug("_build_preview_surface: %s — %s",
                           self._device_key, result.message)
        return result.surface

    # ── Helpers ─────────────────────────────────────────────────────

    def refresh_themes(self) -> None:
        """Public re-list of the local theme browser (after save / import /
        delete) — re-dispatches ListThemes through the dir-resolution refresh."""
        self._update_theme_directories()

    def _update_theme_directories(self) -> bool:
        """Reload theme browser directories for the current resolution.

        Returns True if a first-install auto-load happened (caller should
        skip restore_last_theme to avoid a redundant double-load).

        Reads come from ``DeviceState`` (cached at connect / rotation),
        not the legacy ``self._device.X`` properties which next/'s
        Device port doesn't expose.

        Auto-rotation portrait: when the device is rotated 90/270 the
        cloud-theme / mask / cutter catalogs + the preview resolution ALL
        follow the rotated (portrait) dims — unconditionally, the way
        legacy let the device own dir resolution.  The portrait cloud/mask
        dirs are fetched at handshake regardless of whether a LOCAL portrait
        theme dir was shipped, so gating the whole switch on the local dir
        (the cutover bug) left every catalog stuck in landscape.  Only the
        LOCAL theme browser falls back to the landscape dir when no portrait
        theme dir is on disk — the render pipeline pixel-rotates that
        landscape art at encode time so the device still gets a correctly
        oriented frame.
        """
        # Pure geometry → directories: the catalog-dims selection + #136
        # portrait-fallback rule live in the Qt-free presentation layer; this
        # View only pokes the resulting paths into the browser widgets.
        dirs = resolve_theme_directories(
            self._app.libraries(self._device_key),
            canvas_size=self._pm.state.canvas_size,
            lcd_size=self._pm.state.lcd_size,
            is_rotated=self._pm.state.is_rotated,
        )
        bw, bh = dirs.catalog_size
        theme_dir = dirs.theme_dir
        user_theme_dir = dirs.user_theme_dir
        web_dir = dirs.web_dir
        masks_dir = dirs.masks_dir

        self.log.info(
            "_update_theme_directories: catalog=%dx%d theme_dir=%s "
            "user_theme_dir=%s web_dir=%s masks_dir=%s rotated=%s",
            bw, bh, theme_dir, user_theme_dir, web_dir, masks_dir,
            self._pm.state.is_rotated,
        )

        # Local theme browser: dispatch the universal ListThemes Command
        # (user-precedence + origin + preview) and render its entries — no disk
        # walk in the View.  The browse resolution is the theme dirs' dims: the
        # canvas (landscape) when the #136 portrait-fallback applied, else the
        # catalog (portrait) dims. (#theme-collision)
        theme_res = (self._pm.state.canvas_size if dirs.portrait_fallback
                     else dirs.catalog_size)
        themes = self._app.dispatch(ListThemes(resolution=theme_res)).themes
        # SHARED widgets — only the active handler may write them.  The theme
        # browser is one widget set for every LCD, so an inactive handler
        # refreshing it leaves ITS catalog on screen while a DIFFERENT device
        # is selected; the next click then dispatches LoadTheme with the wrong
        # device's path.  It fails silently because the stock catalogs all
        # contain "Theme1".."Theme5", so the path resolves to a real theme of
        # the wrong SIZE — the background then fails bg_fit's width test and
        # the panel goes black with no error.
        #
        # Same rule the preview and progress widgets already follow.  Both
        # activation paths (apply_device_config / reactivate) set ui_active
        # before _refresh, so the handler taking the panel always repopulates
        # and no stale catalog survives a device switch.
        if self._pm.ui_active:
            self._w['theme_local'].set_themes(themes)
            if web_dir:
                self._w['theme_web'].set_web_directory(web_dir)
            self._w['theme_web'].set_resolution(f'{bw}x{bh}')
            if masks_dir:
                self._w['theme_mask'].set_mask_directory(masks_dir)
            self._w['theme_mask'].set_resolution(f'{bw}x{bh}')
            self._w['image_cut'].set_resolution(bw, bh)
            self._w['video_cut'].set_resolution(bw, bh)
        else:
            self.log.info(
                "_update_theme_directories: %s is not the active panel — "
                "leaving the shared theme browser alone (writing it would "
                "offer this device's %dx%d catalog to whichever panel IS "
                "selected)", self._device_key, bw, bh)

        # First-install auto-load: nothing rendered yet AND no saved theme →
        # load the first listed theme (user-precedence already applied by
        # ListThemes, so a user theme wins the auto-load too).
        ds = self._app.settings.for_device(self._device_key)
        if (self._pm.state.current_theme_path is None
                and not ds.current_theme and themes):
            first = themes[0]
            self.log.info("Data ready: auto-loading first theme: %s", first.path)
            self._select_theme_from_path(Path(first.path), persist=True,
                                          overlay_config=True)
            return True
        return False

    @property
    def is_background_active(self) -> bool:
        return self._pm.background_active

    @is_background_active.setter
    def is_background_active(self, value: bool) -> None:
        self._pm.background_active = value

    @property
    def brightness_level(self) -> int:
        return self._pm.brightness_level

    @property
    def split_mode(self) -> int:
        return self._pm.split_mode

    @property
    def ldd_is_split(self) -> bool:
        return self._pm.ldd_is_split

    # ── Lifecycle ──────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Stop timers and release device resources."""
        self.deactivate()
        self._pixmap_cache.clear()
        self._last_render_id = None
        self._cleanup_device()

    def deactivate(self) -> None:
        """Full pause — stop all timers (called from cleanup)."""
        self._animation_timer.stop()
        self._slideshow_timer.stop()
        self._flash_timer.stop()

    def set_inactive(self) -> None:
        """Soft pause for sidebar switch — keep video playing in background.

        Multi-display: dropping `_ui_active` stops shared-widget writes
        without killing the per-device animation timer, so the LCD keeps
        showing its theme while another device owns the GUI panel.
        """
        self._pm.ui_active = False
        self._slideshow_timer.stop()
        self._flash_timer.stop()

    def _cleanup_device(self) -> None:
        """Release LCD resources via Commands."""
        from ...core.commands import SendColor
        self._app.dispatch(StopVideo(key=self._device_key))
        try:
            # Best-effort black-frame so the screen visibly goes blank.
            self._app.dispatch(SendColor(
                key=self._device_key, r=0, g=0, b=0,
            ))
        except (OSError, RuntimeError) as e:
            # USB I/O during teardown — log + move on.
            self.log.debug("LCD teardown black-frame send failed: %s", e)
        # App.detach is owned by app.close() in the window's closeEvent;
        # individual handler cleanup just releases timers + state.
