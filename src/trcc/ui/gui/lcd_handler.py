"""LCDHandler — one per LCD device, wired to next/ Commands.

Self-contained handler for a single LCD device.  Holds:

* ``_device_key`` — vid:pid; ``app.devices[key]`` is the live Device
* ``_app: App`` — universal command/event hub
* ``_state: _DeviceState`` — cached canvas / mask / theme info,
  refreshed on connect / orientation / theme-load events
* ``_w`` — shared GUI widgets (preview, theme tabs, cuts, etc.)

Every device mutation goes through ``self._app.dispatch(Command(...))``.
Animation state (playing, interval, current frame) comes from
``app.media.playback(key)`` — handler delegates rather than caches.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap

from ...core.commands import (
    ApplyMask,
    EnableOverlay,
    ExportTheme,
    ImportTheme,
    LoadCloudTheme,
    LoadTheme,
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
)
from ._overlay_grid_adapter import dc_as_legacy_overlay_config
from .base_handler import BaseHandler

if TYPE_CHECKING:
    from ...app import App
    from ...core.models import ProductInfo

log = logging.getLogger(__name__)

# Per-resolution split-mode availability (legacy: SPLIT_MODE_RESOLUTIONS).
# Devices with one of these resolutions get the multi-zone "Dynamic
# Island"-style split editor instead of the brightness cycle button.
_SPLIT_MODE_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (480, 1280), (1280, 480),
    (440, 1920), (1920, 440),
    (462, 1920), (1920, 462),
})

# Default brightness % when the user hasn't picked one yet.
_DEFAULT_BRIGHTNESS_LEVEL = 100


class _DataReadyNotifier(QObject):
    """Thread-safe notifier: emits ``ready`` from any thread to the Qt main thread."""
    ready = Signal()


@dataclass(slots=True)
class _DeviceState:
    """Per-handler cache of derived device state.

    Populated/refreshed on ``apply_device_config`` and event-driven
    callbacks (orientation, theme-load, overlay-toggle).  Read locally
    per frame so the hot path doesn't pay per-call dispatch overhead.
    """
    canvas_size: tuple[int, int] = (0, 0)        # pre-rotation (w, h)
    lcd_size: tuple[int, int] = (0, 0)           # post-rotation (w, h)
    is_rotated: bool = False                     # 90° / 270° → True
    overlay_enabled: bool = False
    current_theme_path: Path | None = None
    last_metrics: Any = None                     # cached for video-overlay updates


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

        # UI focus state — multi-display windows share one preview widget;
        # only the active handler writes to it.
        self._ui_active = False

        # Per-device cache + counters
        self._state = _DeviceState()
        self._brightness_level = _DEFAULT_BRIGHTNESS_LEVEL
        self._split_mode = 0
        self._ldd_is_split = False
        self._background_active = False
        self._slideshow_index = 0

        # QPixmap cache keyed by frame index — avoids QImage→QPixmap
        # conversion on every video tick when the surface hasn't changed.
        self._pixmap_cache: dict[int, tuple[int, QPixmap]] = {}
        self._last_render_id: int | None = None

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

    # ── LCDDevice Config (C# ReadSystemConfiguration) ─────────────────

    def apply_device_config(self, info: ProductInfo, w: int, h: int) -> None:
        """First-time device setup + full widget refresh.

        ``info`` is a next/ ``ProductInfo``; its ``key`` ("vid:pid") is
        already the handler's ``_device_key``, set in __init__.
        """
        self.log.info("apply_device_config: %s %dx%d", info.key, w, h)
        self._ui_active = True
        # Per-device child logger — tags handler logs with the key
        self.log = logging.getLogger(f"{__name__}.{info.key}")
        self._refresh(w, h)

    def reactivate(self, w: int, h: int) -> None:
        """Return to known device — device already configured from connect()."""
        self.log.info("reactivate: %dx%d", w, h)
        self._ui_active = True
        self._refresh(w, h)

    def restore_inactive_state(self) -> None:
        """Restore last theme for an inactive LCD without touching shared widgets.

        Multi-display: all LCDs should keep playing their video even
        when not selected.  next/'s RestoreLastTheme Command persists +
        re-loads the previously-active theme; the handler subscribes to
        the resulting frame via the global FrameSent stream.
        """
        self._ui_active = False
        self._pixmap_cache.clear()
        device = self._app.devices.get(self._device_key)
        if device is None or not device.is_connected:
            return
        result = self._app.dispatch(RestoreLastTheme(key=self._device_key))
        if not result.ok:
            return
        playback = self._app.media.playback(self._device_key)
        if playback is not None and not self._animation_timer.isActive():
            interval = self._video_interval_ms()
            self.log.info(
                "restore_inactive_state: starting background video timer "
                "interval=%dms", interval,
            )
            self._animation_timer.start(interval)

    def _refresh(self, w: int, h: int) -> None:
        """Update widgets from the device's current persisted settings."""
        self.log.info("_refresh: device_key=%s resolution=%dx%d",
                      self._device_key, w, h)
        # Cache canvas + lcd size + per-resolution dirs in the shared
        # _DeviceState.  Done here (not in apply_device_config) so
        # reactivate() also refreshes them — reactivate runs every time
        # the user picks the device in the sidebar, and the paths port
        # is the source of truth for theme/mask/web directories.
        self._state.canvas_size = (w, h)
        self._state.lcd_size = (w, h)
        paths = self._app.platform.paths()
        # Theme / web / mask dirs aren't cached on _state any more —
        # ``_update_theme_directories`` derives them per-call so portrait
        # rotation can switch the browser to the rotated dir on demand
        # (auto-rotation portrait).  Log the initial landscape set so
        # the connect-time picture is preserved.
        self.log.info(
            "_refresh: theme_dir=%s web_dir=%s masks_dir(cloud)=%s "
            "user_mask_dir=%s",
            paths.theme_dir(w, h), paths.cloud_theme_dir(w, h),
            paths.cloud_mask_dir(w, h), paths.user_mask_dir(w, h),
        )
        # next/'s per-device settings live in app.settings.for_device(key)
        # — DeviceSettings dataclass.  Build a dict-shape view so the
        # legacy _restore_X methods that read cfg.get(...) keep working.
        ds = self._app.settings.for_device(self._device_key)
        cfg: dict = {
            'brightness_level': ds.brightness,
            'rotation': ds.orientation,
            'split_mode': ds.split_mode,
            'carousel': None,  # SlideshowService owns this now
        }

        self._w['preview'].set_resolution(w, h)
        self._w['preview'].set_image(None)
        self._w['image_cut'].set_resolution(w, h)
        self._w['video_cut'].set_resolution(w, h)
        self._w['theme_setting'].set_resolution(w, h)

        auto_loaded = self._update_theme_directories()

        self._restore_brightness(cfg)
        self._restore_rotation(cfg)
        self._restore_split_mode(cfg, w, h)
        self._restore_carousel(cfg)

        if auto_loaded:
            return
        self._restore_theme_and_preview(cfg)

    def _on_data_ready(self) -> None:
        """Background data extraction finished — re-probe dirs and update UI."""
        self.log.info("_on_data_ready: refreshing dirs and theme lists")
        auto_loaded = self._update_theme_directories()
        self.log.info("_on_data_ready: done, auto_loaded=%s", auto_loaded)

    def _restore_brightness(self, cfg: dict) -> None:
        self._brightness_level = cfg.get('brightness_level', _DEFAULT_BRIGHTNESS_LEVEL)
        self.log.info("Restoring brightness: %d%%", self._brightness_level)
        self._app.dispatch(SetBrightness(
            key=self._device_key, percent=self._brightness_level,
        ))

    def _restore_rotation(self, cfg: dict) -> None:
        rotation_index = cfg.get('rotation', 0) // 90
        rotation = rotation_index * 90
        self.log.debug("_restore_rotation: rotation=%d", rotation)
        self._app.dispatch(SetOrientation(
            key=self._device_key, degrees=rotation,
        ))
        self._w['rotation_combo'].blockSignals(True)
        self._w['rotation_combo'].setCurrentIndex(rotation_index)
        self._w['rotation_combo'].blockSignals(False)
        ow, oh = self._state.canvas_size
        self._w['preview'].set_resolution(ow, oh)
        self._update_theme_directories()

    def _restore_split_mode(self, cfg: dict, w: int, h: int) -> None:
        self._split_mode = cfg.get('split_mode', 2)
        self._ldd_is_split = (w, h) in _SPLIT_MODE_RESOLUTIONS
        self.log.debug("_restore_split_mode: split_mode=%d ldd_is_split=%s",
                       self._split_mode, self._ldd_is_split)
        if self._ldd_is_split:
            if not self._split_mode:
                self._split_mode = 2
            self._app.dispatch(SetSplitMode(
                key=self._device_key, mode=self._split_mode,
            ))
        else:
            self._app.dispatch(SetSplitMode(key=self._device_key, mode=0))

    def _restore_carousel(self, cfg: dict) -> None:
        carousel = cfg.get('carousel')
        local = self._w['theme_local']
        if carousel and isinstance(carousel, dict):
            local._lunbo_array = carousel.get('themes', [])
            local._slideshow = carousel.get('enabled', False)
            local._slideshow_interval = carousel.get('interval', 3)
            local.timer_input.setText(str(carousel.get('interval', 3)))
            px = local._lunbo_on if carousel.get('enabled') else local._lunbo_off
            if not px.isNull():
                local.slideshow_btn.setIcon(QIcon(px))
                local.slideshow_btn.setIconSize(local.slideshow_btn.size())
            local._apply_decorations()
            self._update_slideshow_state()
        else:
            self._slideshow_timer.stop()
            local._lunbo_array = []
            local._slideshow = False
            local._apply_decorations()

    def _restore_theme_and_preview(self, cfg: dict) -> None:
        """Restore last theme + overlay, or clear preview if none.

        Dispatches RestoreLastTheme (which re-runs LoadTheme for the
        persisted theme name). Playback / animation state comes from
        :class:`MediaService`; preview redraws via ``rebuild_preview``.
        """
        self.log.debug("_restore_theme_and_preview: cfg keys=%s", list(cfg.keys()))
        result = self._app.dispatch(RestoreLastTheme(key=self._device_key))
        overlay_cfg = cfg.get('overlay', {})
        overlay_config = overlay_cfg.get('config')
        overlay_enabled = overlay_cfg.get('enabled', False)
        if overlay_config:
            self._w['theme_setting'].load_from_overlay_config(overlay_config)
        self._w['theme_setting'].set_overlay_enabled(overlay_enabled)

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
            self._load_theme_overlay_config(
                Path(result.theme_path), persist=False,
            )
        playback = self._app.media.playback(self._device_key)
        if playback is not None and playback.frames:
            self._state.current_theme_path = (
                Path(result.theme_path) if result.theme_path else None
            )
            self._animation_timer.start(self._video_interval_ms())
            if self._ui_active:
                self._w['preview'].set_playing(True)
                self._w['preview'].show_video_controls(True)
        else:
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

        # Reset mode toggles (C# ReadSystemConfiguration override)
        self._background_active = False
        self._animation_timer.stop()
        self._app.dispatch(StopVideo(key=self._device_key))
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
        self._state.current_theme_path = path if result.ok else None
        if overlay_config:
            self._load_theme_overlay_config(path, persist=persist)

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
        self._background_active = False
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
        # Drive frame advancement — LoadCloudTheme dispatches PlayVideo
        # which loads MediaService playback, but the per-frame
        # ``playback.advance()`` call lives on a Qt timer the handler
        # owns.  Without starting it here the LCD freezes on frame 0;
        # legacy starts a 33ms timer at the equivalent point in
        # ``_select_theme()``.
        if not self._animation_timer.isActive():
            interval = self._video_interval_ms()
            self.log.info(
                "select_cloud_theme: starting animation timer %dms (%s)",
                interval, theme_id,
            )
            self._animation_timer.start(interval)

    def apply_mask(self, mask_info: Any) -> None:
        """Apply mask overlay on top of current content."""
        self.log.info("apply_mask: %s path=%s", mask_info.name, mask_info.path)
        if not mask_info.path:
            self._w['preview'].set_status(f"Mask: {mask_info.name}")
            return
        mask_dir = Path(mask_info.path)
        # DC first — sets overlay resolution + element positions for this mask
        self._load_theme_overlay_config(mask_dir, persist=False)
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
        self._app.dispatch(SetMaskPosition(
            key=self._device_key, x=x, y=y,
        ))
        self._render_and_send()

    def save_theme(self, name: str) -> None:
        r = self._app.dispatch(SaveTheme(
            key=self._device_key, name=name,
        ))
        self._w['preview'].set_status(r.message)
        if r.ok:
            # Reload local theme list so the new theme shows up
            self._w['theme_local'].load_themes()

    def export_config(self, path: Path) -> None:
        r = self._app.dispatch(ExportTheme(
            theme_name=path.stem,
            archive_path=path,
        ))
        self._w['preview'].set_status(r.message)

    def import_config(self, path: Path) -> None:
        r = self._app.dispatch(ImportTheme(archive_path=path))
        self._w['preview'].set_status(r.message)
        if r.ok:
            self._w['theme_local'].load_themes()

    # ── DC File Loading ────────────────────────────────────────────

    def _load_theme_overlay_config(self, theme_dir: Path,
                                    *, persist: bool = True) -> None:
        """Load overlay config from the theme's ``config1.dc``.

        Wires the legacy GUI grid (overlay_grid) to the theme's persisted
        layout.  The DC file is the source of truth — `RestoreLastTheme`
        re-reads it on every device connect, so we don't replay through
        `SetOverlayConfig` here (that Command takes next/-shape elements
        with ids, used by the GUI editor when the user drops a new
        element, not by automatic restore).
        """
        self.log.info("_load_theme_overlay_config: dir=%s persist=%s",
                      theme_dir, persist)
        overlay_config = dc_as_legacy_overlay_config(theme_dir)

        if not overlay_config:
            self.log.info("_load_theme_overlay_config: no DC found → overlay disabled")
            self._w['theme_setting'].set_overlay_enabled(False)
            self._app.dispatch(EnableOverlay(
                key=self._device_key, enabled=False,
            ))
            self._state.overlay_enabled = False
            self._render_and_send()
            return

        self.log.info(
            "_load_theme_overlay_config: DC loaded, %d elements → overlay enabled",
            len(overlay_config),
        )
        self._w['theme_setting'].set_overlay_enabled(True)
        self._w['theme_setting'].load_from_overlay_config(overlay_config)
        self._app.dispatch(EnableOverlay(key=self._device_key, enabled=True))
        self._state.overlay_enabled = True
        self._render_and_send()

    # ── Video (C# ucBoFangQiKongZhi1) ─────────────────────────────

    def play_pause(self) -> None:
        self.log.debug("play_pause")
        playback = self._app.media.playback(self._device_key)
        if playback is None:
            return
        # Toggle pause state.  next/'s Playback exposes pause(bool).
        was_paused = playback.paused
        playback.pause(not was_paused)
        playing = not playback.paused
        self._w['preview'].set_playing(playing)
        if playing:
            interval_ms = self._video_interval_ms()
            self._animation_timer.start(interval_ms)
        else:
            self._animation_timer.stop()

    def stop_video(self) -> None:
        self.log.debug("stop_video")
        from ...core.commands import StopVideo
        self._app.dispatch(StopVideo(key=self._device_key))
        self._animation_timer.stop()
        self._w['preview'].set_playing(False)
        self._w['preview'].show_video_controls(False)

    def seek(self, percent: float) -> None:
        """Jump playback to ``percent`` (0.0-1.0) of total frames."""
        from ...core.commands import SeekVideo
        playback = self._app.media.playback(self._device_key)
        if playback is None:
            return
        total = playback.frame_count
        frame = max(0, min(total - 1, int(percent * total)))
        self._app.dispatch(SeekVideo(key=self._device_key, frame=frame))

    def set_video_fit_mode(self, mode: str) -> None:
        self._app.dispatch(SetFitMode(key=self._device_key, mode=mode))
        # Re-render preview on the next FrameSent / tick

    def _video_interval_ms(self) -> int:
        """Return ms between frames for the active video, or 33 (≈30 fps)."""
        playback = self._app.media.playback(self._device_key)
        if playback is None:
            return 33
        fps = getattr(playback, 'fps', 0) or 30
        return max(1, int(1000 / fps))

    def _on_video_tick(self) -> None:
        """Timer callback: advance one video frame.

        next/ owns playback in :class:`MediaService`; ``RenderAndSend``
        builds + encodes + sends the current cursor's frame.  Preview
        widget refreshes via the ``FrameSent`` → ``rebuild_preview``
        bridge — no per-tick image plumbing here.
        """
        from ...core.commands import RenderAndSend
        playback = self._app.media.playback(self._device_key)
        if playback is None or not playback.frames:
            return
        playback.advance()

        if self._ui_active:
            total = playback.frame_count
            cursor = playback.cursor
            percent = (cursor / total) if total else 0.0
            self._w['preview'].set_progress(percent, cursor, total)

        device = self._app.devices.get(self._device_key)
        if device is None or not device.is_connected:
            return

        result = self._app.dispatch(RenderAndSend(key=self._device_key))
        if not result.ok:
            self.log.debug("_on_video_tick: render failed — %s", result.message)

    # ── Overlay (C# ucXiTongXianShi1) ─────────────────────────────

    def on_overlay_changed(self, element_data: dict) -> None:
        """Forward overlay config change from settings panel."""
        self.log.debug("on_overlay_changed: %d elements", len(element_data) if element_data else 0)
        if not element_data:
            return
        # Apply overlay change via the Command bus.  EnableOverlay
        # persists the toggle; SetOverlayConfig persists the element
        # list.  next/ skips the legacy "is video playing" cache-update
        # branch — the render service handles overlay refresh next tick.
        if not self._state.overlay_enabled:
            self._app.dispatch(EnableOverlay(
                key=self._device_key, enabled=True,
            ))
            self._state.overlay_enabled = True
        self._app.dispatch(SetOverlayConfig(
            key=self._device_key,
            elements=tuple(element_data.values())
                if isinstance(element_data, dict) else tuple(element_data),
        ))
        self._render_and_send()

    def handle_frame(self, image: Any) -> None:
        """Receive rendered frame from tick loop — update preview widget."""
        if self._ui_active:
            self._w['preview'].set_image(image)

    def rebuild_preview(self) -> None:
        """Re-render the preview from current state (FrameSent observer).

        Builds a preview surface from the App's render pipeline using
        the live theme + sensors; updates the preview widget when the
        handler owns the UI.  Idempotent — safe to call from FrameSent.
        """
        image = self._build_preview_surface()
        if image is not None and self._ui_active:
            self._w['preview'].set_image(image, fast=self._animation_timer.isActive())

    def update_preview(self, image: Any) -> None:
        """Display a frame that was already rendered and sent to the device."""
        if self._ui_active:
            self._w['preview'].set_image(image)

    def update_metrics(self, metrics: Any) -> None:
        """Metrics tick: cache for video-overlay redraws on next frame."""
        self._state.last_metrics = metrics

    def flash_element(self, index: int) -> None:
        """Flash/blink selected overlay element on preview."""
        from ...core.commands import FlashOverlayElement
        self._app.dispatch(FlashOverlayElement(
            key=self._device_key, element_id=str(index), duration_ms=980,
        ))
        self._flash_timer.start(980)
        self._render_and_send()

    def _on_flash_timeout(self) -> None:
        self._render_and_send()

    # ── Display Settings ───────────────────────────────────────────

    def set_brightness(self, percent: int) -> None:
        self.log.debug("set_brightness: %d%%", percent)
        self._brightness_level = percent
        self._app.dispatch(SetBrightness(
            key=self._device_key, percent=percent,
        ))

    def set_rotation(self, degrees: int) -> None:
        self.log.debug("set_rotation: degrees=%d", degrees)
        self._app.dispatch(SetOrientation(
            key=self._device_key, degrees=degrees,
        ))
        # Refresh cached state — rotation changes lcd_size + is_rotated.
        self._state.is_rotated = degrees in (90, 270)
        cw, ch = self._state.canvas_size
        if self._state.is_rotated:
            self._state.lcd_size = (ch, cw)
        else:
            self._state.lcd_size = (cw, ch)
        ow, oh = self._state.lcd_size
        self.log.info(
            "set_rotation: rotation=%d output=%dx%d rotated=%s",
            degrees, ow, oh, self._state.is_rotated,
        )
        self._w['preview'].set_resolution(ow, oh)
        self._update_theme_directories()

    def set_split_mode(self, mode: int) -> None:
        self.log.debug("set_split_mode: mode=%d", mode)
        self._split_mode = mode
        self._app.dispatch(SetSplitMode(
            key=self._device_key, mode=mode,
        ))

    # ── Background / Screencast Toggles ────────────────────────────

    def on_background_toggle(self, enabled: bool) -> None:
        """Handle background display toggle."""
        self.log.debug("on_background_toggle: enabled=%s", enabled)
        self._background_active = enabled
        if enabled:
            self._animation_timer.stop()
            from ...core.commands import StopVideo
            self._app.dispatch(StopVideo(key=self._device_key))
            self._w['preview'].set_playing(False)
            self._w['preview'].show_video_controls(False)
        self._render_and_send()
        playback = self._app.media.playback(self._device_key)
        kind = "video" if playback is not None else "image"
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
        if self._ui_active:
            self._w['preview'].set_image(image)
        device = self._app.devices.get(self._device_key)
        if device is None:
            return
        try:
            data = self._app.display.build_screencast_frame(
                info=device.info, frame=image,
            )
        except Exception as e:
            self.log.debug("screencast encode failed: %s", e)
            return
        from ...core.commands import SendFrame
        self._app.dispatch(SendFrame(key=self._device_key, data=data))

    # ── Slideshow / Carousel ───────────────────────────────────────

    def _update_slideshow_state(self) -> None:
        self.log.debug("_update_slideshow_state")
        local = self._w['theme_local']
        enabled = local.is_slideshow()
        interval_s = local.get_slideshow_interval()
        themes = local.get_slideshow_themes()

        if enabled and themes:
            self._slideshow_index = 0
            self._slideshow_timer.start(interval_s * 1000)
        else:
            self._slideshow_timer.stop()

        # ConfigureSlideshow + SetSlideshow own carousel persistence
        # through next/'s SlideshowService.
        from ...core.commands import ConfigureSlideshow, SetSlideshow
        self._app.dispatch(ConfigureSlideshow(
            key=self._device_key,
            themes=tuple(t.name for t in themes),
            interval_s=float(interval_s),
        ))
        self._app.dispatch(SetSlideshow(
            key=self._device_key, enabled=enabled,
        ))

    def on_slideshow_delegate(self) -> None:
        """Handle slideshow toggle from local theme panel."""
        self._update_slideshow_state()

    def _on_slideshow_tick(self) -> None:
        """Auto-rotate to next theme in slideshow."""
        themes = self._w['theme_local'].get_slideshow_themes()
        if not themes:
            self._slideshow_timer.stop()
            return
        self._slideshow_index = (self._slideshow_index + 1) % len(themes)
        theme_info = themes[self._slideshow_index]
        path = Path(theme_info.path)
        if path.exists():
            self._app.dispatch(LoadTheme(
                key=self._device_key, path=path,
            ))
            self._state.current_theme_path = path
            self._load_theme_overlay_config(path)

    # ── Rendering ──────────────────────────────────────────────────

    def _render_and_send(self) -> None:
        """Render overlay + send to LCD, update preview.

        Skipped while video playback owns the wire (the animation timer
        loop dispatches its own ``RenderAndSend``).  Preview refresh
        happens via the ``FrameSent`` → ``rebuild_preview`` bridge.
        """
        from ...core.commands import RenderAndSend
        if self._animation_timer.isActive():
            return
        device = self._app.devices.get(self._device_key)
        if device is None or not device.is_connected:
            return
        result = self._app.dispatch(RenderAndSend(key=self._device_key))
        if not result.ok:
            self.log.debug("_render_and_send: failed — %s", result.message)

    def render_and_preview(self) -> Any:
        """Render overlay and update preview (no send)."""
        image = self._build_preview_surface()
        if image is not None and self._ui_active:
            self._w['preview'].set_image(image)
        return image

    def _build_preview_surface(self) -> Any:
        """Build a preview surface from the App's render pipeline.

        Returns None when the device has no active theme yet (pre-load)
        or the device key no longer points at a live Device.
        """
        device = self._app.devices.get(self._device_key)
        if device is None:
            return None
        theme = self._app.active_themes.get(self._device_key)
        if theme is None:
            return None
        sensors = self._app.platform.sensors().read_all()
        try:
            return self._app.display.build_preview_surface(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
        except Exception as e:
            self.log.debug("_build_preview_surface: %s", e)
            return None

    # ── Helpers ─────────────────────────────────────────────────────

    def _update_theme_directories(self) -> bool:
        """Reload theme browser directories for the current resolution.

        Returns True if a first-install auto-load happened (caller should
        skip restore_last_theme to avoid a redundant double-load).

        Reads come from ``_DeviceState`` (cached at connect / rotation),
        not the legacy ``self._device.X`` properties which next/'s
        Device port doesn't expose.

        Auto-rotation portrait: when the device is rotated 90/270 AND a
        portrait-native theme dir exists on disk (legacy convention:
        ``data/theme{H}x{W}/``), point the browser at it.  When no
        portrait dir is present, stay on the landscape dir — the render
        pipeline pixel-rotates landscape art at encode time so the
        device still gets a correctly-oriented frame.
        """
        paths = self._app.platform.paths()
        cw, ch = self._state.canvas_size

        # Pick browse dims: prefer portrait when rotated AND the
        # portrait dir actually exists.
        ow, oh = cw, ch
        if self._state.is_rotated:
            rw, rh = self._state.lcd_size
            rotated_theme_dir = paths.theme_dir(rw, rh)
            if rotated_theme_dir and rotated_theme_dir.exists():
                self.log.info(
                    "_update_theme_directories: portrait theme dir "
                    "%s exists — switching browser to %dx%d",
                    rotated_theme_dir, rw, rh,
                )
                ow, oh = rw, rh
            else:
                self.log.info(
                    "_update_theme_directories: rotated %dx%d but no "
                    "portrait theme dir at %s — staying landscape "
                    "(%dx%d); render pipeline will pixel-rotate",
                    rw, rh, rotated_theme_dir, cw, ch,
                )

        theme_dir = paths.theme_dir(ow, oh)
        web_dir = paths.cloud_theme_dir(ow, oh)
        masks_dir = paths.cloud_mask_dir(ow, oh)

        # Also expose the legacy user-saved theme location so the
        # browser picks up Custom_* themes from
        # ``~/.trcc-user/data/theme{w}{h}/`` alongside the pkg/cloud
        # themes from ``~/.trcc/data/theme{w}{h}/``.
        user_theme_dir = paths.user_theme_dir(ow, oh)

        self.log.info(
            "_update_theme_directories: output=%dx%d theme_dir=%s "
            "user_theme_dir=%s web_dir=%s masks_dir=%s rotated=%s",
            ow, oh, theme_dir, user_theme_dir, web_dir, masks_dir,
            self._state.is_rotated,
        )

        if theme_dir and theme_dir.exists():
            self._w['theme_local'].set_theme_directory(theme_dir, user_theme_dir)
        elif user_theme_dir and user_theme_dir.exists():
            self._w['theme_local'].set_theme_directory(user_theme_dir)
        if web_dir:
            self._w['theme_web'].set_web_directory(web_dir)
        self._w['theme_web'].set_resolution(f'{ow}x{oh}')
        if masks_dir:
            self._w['theme_mask'].set_mask_directory(masks_dir)
        self._w['theme_mask'].set_resolution(f'{ow}x{oh}')
        self._w['image_cut'].set_resolution(ow, oh)
        self._w['video_cut'].set_resolution(ow, oh)

        # First-install auto-load: pick the first theme in the dir if
        # the device has nothing rendered yet AND no saved theme name.
        ds = self._app.settings.for_device(self._device_key)
        if (self._state.current_theme_path is None
                and theme_dir and theme_dir.exists()
                and not ds.current_theme):
            for item in sorted(theme_dir.iterdir()):
                if item.is_dir() and (item / '00.png').exists():
                    self.log.info("Data ready: auto-loading first theme: %s", item)
                    self._select_theme_from_path(item, persist=True,
                                                  overlay_config=True)
                    return True
            self.log.debug(
                "_update_theme_directories: no valid theme found for auto-load in %s",
                theme_dir,
            )
        return False

    @property
    def is_background_active(self) -> bool:
        return self._background_active

    @is_background_active.setter
    def is_background_active(self, value: bool) -> None:
        self._background_active = value

    @property
    def brightness_level(self) -> int:
        return self._brightness_level

    @property
    def split_mode(self) -> int:
        return self._split_mode

    @property
    def ldd_is_split(self) -> bool:
        return self._ldd_is_split

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
        self._ui_active = False
        self._slideshow_timer.stop()
        self._flash_timer.stop()

    def _cleanup_device(self) -> None:
        """Release LCD resources via Commands."""
        from ...core.commands import SendColor, StopVideo
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
