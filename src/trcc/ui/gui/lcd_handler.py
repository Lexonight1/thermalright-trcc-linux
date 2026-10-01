"""LCDHandler — one per LCD device, wired to next/ Commands.

Self-contained handler for a single LCD device.  Holds:

* ``_device_key`` — vid:pid; ``app.devices[key]`` is the live Device
* ``_app: CommandBus`` — universal command/event hub
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
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from ...core.commands import (
    ApplyMask,
    BuildPreview,
    CurrentFrame,
    DeviceState,
    EnableOverlay,
    ExportTheme,
    GetPaths,
    ImportTheme,
    LcdSnapshot,
    ListThemes,
    LoadCloudTheme,
    LoadTheme,
    PreviewSize,
    ResolveOverlay,
    ResolveThemeDirectories,
    SaveTheme,
    SetBackgroundMode,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetMediaPlayer,
    SetOrientation,
    SetSplitMode,
    StopVideo,
    ToggleVideo,
    UploadCustomMask,
    VideoStatus,
)
from ..presentation.lcd_presentation_model import LcdPresentationModel
from ..presentation.overlay_serialization import entries_to_configs
from .base_handler import BaseHandler

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ...core.results import (
        LcdSnapshotResult,
        ThemeResult,
        VideoStatusResult,
    )

log = logging.getLogger(__name__)


def _always_visible() -> bool:
    """Default visibility predicate — a named function, not a lambda, so a
    traceback through the render gate names something."""
    log.debug("_always_visible: no predicate injected — assuming visible")
    return True


class _DataReadyNotifier(QObject):
    """Thread-safe notifier: emits ``ready`` from any thread to the Qt main thread."""
    ready = Signal()


class LCDHandler(BaseHandler):
    """Per-LCD-device GUI handler, dispatching through next/'s App.

    Each LCD device gets its own handler.  The constructor signature
    takes the device KEY first, not a live ``Device`` — a UI must not
    hold one (CLAUDE.md), and ``app.devices`` is absent under
    TRCC_DAEMON=1.  Everything it needs about the device it asks the bus
    for.
    """

    def __init__(
        self,
        key: str,
        widgets: dict[str, Any],
        data_dir: Path,
        is_visible_fn: Any = None,
        app: CommandBus | None = None,
        lcd_idx: Any = '',
    ) -> None:
        super().__init__(key, 'form')
        if app is None:
            raise RuntimeError(
                "LCDHandler requires an App handle — composition root must pass one"
            )
        self._app: CommandBus = app
        # ``lcd_idx`` carries the device key in the next/ port (legacy
        # passed an int index into Trcc._lcd_devices).
        self._device_key: str = str(lcd_idx) if lcd_idx else key
        self._w = widgets
        self._data_dir = data_dir
        self._is_visible = is_visible_fn or _always_visible
        # "" until a Result proves the live surface cannot reach this process,
        # then "png" for the life of the handler.  An observation, not a
        # configured mode and not a sniffed environment.
        self._preview_encode: Literal["", "png"] = ""
        # The last directory set this handler POPULATED the shared theme
        # browser from.  A cold boot of ten devices called
        # ``_update_theme_directories`` 42 times and built 8,640 thumbnail
        # widgets, because activation, brightness restore, rotation restore
        # and reactivate each call it and none asked whether anything had
        # changed.  Same guard ``UCDevice.update_devices`` already uses for
        # the device list, and the same reason.
        self._dirs_signature: tuple[object, ...] | None = None
        self.log: logging.Logger = log

        # Qt-free coordination model — owns the per-device DeviceState cache
        # AND the activation/view-lifecycle flags (ui_active gate, configured
        # first-load gate, brightness/split/background state).  PM-refactor
        # increment 5 grows the decisions onto it; the handler keeps the Qt.
        self._pm = LcdPresentationModel(self._device_key)
        # The slideshow is the App's: ``SetSlideshow`` drives it and the
        # handler follows ``SlideshowChanged`` / ``ThemeLoaded``.

        # QPixmap cache keyed by frame index — avoids QImage→QPixmap
        # conversion on every video tick when the surface hasn't changed.
        self._pixmap_cache: dict[int, tuple[int, QPixmap]] = {}
        self._last_render_id: int | None = None

        # Whether this device's video is playing (not paused).  The core's
        # VideoLoop does the ticking (#249); this flag is the handler's view
        # of it — "video owns the wire" and the preview's fast mode read it.
        self._video_playing: bool = False

        # Thread-safe notifier for background data extraction → UI refresh
        self._data_notifier = _DataReadyNotifier()
        self._data_notifier.ready.connect(self._on_data_ready)

        #: The orientation the shared widgets show for this panel; None until
        #: it has taken them (``_refresh``), so the first restore always runs.
        self._shown_rotation: int | None = None


    # ── Public API ───────────────────────────────────────────────────

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

    def _lcd_settings(self) -> LcdSnapshotResult:
        """Ask the bus what this device's persisted LCD state is.

        The settings twin of :meth:`_video_status`, and for the same reason:
        ``app.settings`` is absent on the ``AppProxy`` a daemon-mode handler
        holds, so every ``settings.for_device`` reach here raised under
        ``TRCC_DAEMON=1`` (#249).
        """
        snap = self._app.dispatch(LcdSnapshot(key=self._device_key))
        self.log.debug(
            "_lcd_settings: orientation=%s theme=%r overlay=%s slideshow=%s",
            snap.orientation, snap.current_theme, snap.overlay_enabled,
            snap.slideshow_enabled,
        )
        return snap

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

    def apply_device_config(self, key: str, w: int, h: int) -> None:
        """First-time device setup + full widget refresh.

        ``info`` is a next/ ``ProductInfo``; its ``key`` ("vid:pid") is
        already the handler's ``_device_key``, set in __init__.
        """
        self.log.info("apply_device_config: %s %dx%d", key, w, h)
        self._pm.ui_active = True
        self._pm.configured = True
        # Per-device child logger — tags handler logs with the key
        self.log = logging.getLogger(f"{__name__}.{key}")
        self._refresh(w, h)

    def reactivate(self, w: int, h: int) -> None:
        """Return to known device — device already configured from connect()."""
        self.log.info("reactivate: %dx%d", w, h)
        self._pm.ui_active = True
        self._refresh(w, h)

    def _refresh(self, w: int, h: int) -> None:
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
        # Theme / web / mask dirs aren't cached on _state any more —
        # ``_update_theme_directories`` derives them per-call so portrait
        # rotation can switch the browser to the rotated dir on demand
        # (auto-rotation portrait).  Log the initial landscape set so
        # the connect-time picture is preserved.
        # One Query answers all four, and ``key`` resolves them through
        # this cooler's own artwork libraries — what ``libraries(key)``
        # was doing by hand.
        dirs = self._app.dispatch(
            GetPaths(key=self._device_key, resolution=(w, h)),
        )
        self.log.info(
            "_refresh: theme_dir=%s web_dir=%s masks_dir(cloud)=%s "
            "user_mask_dir=%s",
            dirs.theme_dir, dirs.cloud_theme_dir,
            dirs.cloud_mask_dir, dirs.user_mask_dir,
        )
        # Typed source: every _restore_* below reads DeviceSettings
        # directly.  Pre-S1.2 this slot built an intermediate
        # ``cfg: dict`` shim so the methods could keep their legacy
        # ``cfg.get(field, default)`` shape — the shim has been removed
        # in favour of dataclass attribute access (typed by pyright,
        # defaults baked into DeviceSettings itself).
        ds = self._lcd_settings()
        self._shown_rotation = None     # this panel is (re)taking the widgets

        self._w['preview'].set_resolution(w, h)
        self._w['preview'].set_image(None)
        self._w['image_cut'].set_resolution(w, h)
        self._w['video_cut'].set_resolution(w, h)

        self._update_theme_directories()

        self._show_settings(ds)
        self._restore_slideshow(ds)
        self._update_device_info()

        self._restore_theme_and_preview()

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
        self._update_theme_directories(force=True)
        # The session primed this panel as the data landed (``App._prime``
        # runs before any UI hears DataInstalled), so there is now a theme to
        # show.  Only the active handler may write the shared preview.
        self.log.info("_on_data_ready: done, active=%s", self._pm.ui_active)
        if self._pm.ui_active:
            self._restore_theme_and_preview()

    def _update_device_info(self) -> None:
        """Populate the selectable fingerprint line for the active device.

        Name · vid:pid · FBL/PM/SUB — from ``DeviceState``, the same Query the
        qtgui inspector uses, so a user can copy the line into a bug report and
        the two surfaces cannot disagree.  A device with no handshake yet
        reports ``pm_byte=None`` and shows identity only.

        The ``pm_to_fbl`` fallback that used to live here is gone: ``DeviceState``
        now resolves the handshake-derived FBL itself, which is what both
        readers meant all along.
        """
        st = self._app.dispatch(DeviceState(key=self._device_key))
        if not st.ok:
            self.log.warning("_update_device_info: %s", st.message)
            return
        parts = [st.product, st.key]
        if st.pm_byte is not None:
            parts += [f"FBL {st.fbl}", f"PM {st.pm_byte}", f"SUB {st.sub_byte}"]
        text = "  ·  ".join(parts)
        self.log.info("_update_device_info: %s", text)
        self._w['device_info_label'].setText(text)


    # The three ``_restore_*`` below are READ only, like ``_restore_slideshow``.
    # Each used to write the value it had just read back to the App, so merely
    # OPENING the gui sent SetBrightness / SetOrientation / SetSplitMode for
    # every panel (measured: 6 for a 2-panel fleet; qtgui sent none) -- an
    # event to every other UI, and a stale overwrite if one changed it
    # between the read and the write.  Opening a UI changes nothing.

    def follow_app(self) -> None:
        """Show a setting the App now holds, changed here or by another UI.

        READ only, like the ``_restore_*`` it runs.  Not ``_refresh``: that
        blanks the preview and re-lists the theme browser, which is for
        becoming the active panel, not for a brightness change.
        """
        self.log.info("follow_app: %s active=%s",
                      self._device_key, self._pm.ui_active)
        if not self._pm.ui_active:   # the widgets are shared with the other LCDs
            return
        self._show_settings(self._lcd_settings())

    def _show_settings(self, ds: LcdSnapshotResult) -> None:
        """Put the App's settings for this panel on the shared widgets — READ only.

        Opening the gui, switching to this panel and following another UI's
        change all come through here, so the three cannot show different
        things.  The mask fields were set only on a follow, so on open they
        showed whatever the previous panel had left in them.
        """
        self.log.info(
            "_show_settings: brightness=%d orientation=%d split=%d overlay=%s "
            "mask visible=%s at %s", ds.brightness, ds.orientation,
            ds.split_mode, ds.overlay_enabled, ds.mask_visible, ds.mask_position)
        self._restore_brightness(ds)
        self._restore_rotation(ds)
        self._restore_split_mode(ds)
        self._show_overlay_layout()
        settings = self._w['theme_setting']
        settings.set_mask_visible(ds.mask_visible)
        settings.show_sources(ds.display_source, ds.background_mode != "transparent")
        # None is the default place, which the render draws at (0, 0).
        settings.set_mask_position(*(ds.mask_position or (0, 0)))

    def _restore_brightness(self, ds: LcdSnapshotResult) -> None:
        self._pm.brightness_level = ds.brightness
        self.log.info("Showing brightness: %d%%", self._pm.brightness_level)

    def _restore_rotation(self, ds: LcdSnapshotResult) -> None:
        """Show the orientation -- and redo the geometry work only when it
        CHANGED.  ``follow_app`` runs this on every event, a drag move
        included, and each run cost a ResolveThemeDirectories + PreviewSize
        (two socket round trips under a shared App) for nothing."""
        rotation_index = ds.orientation // 90
        rotation = rotation_index * 90
        if rotation == self._shown_rotation:
            self.log.debug("_restore_rotation: still %d° — nothing to redo",
                           rotation)
            return
        self.log.debug("_restore_rotation: rotation=%d", rotation)
        self._shown_rotation = rotation
        self._sync_rotation_state(rotation)
        self._w['rotation_combo'].blockSignals(True)
        self._w['rotation_combo'].setCurrentIndex(rotation_index)
        self._w['rotation_combo'].blockSignals(False)
        self._sync_preview_size()   # composed orientation, not pre-rotation (#136)
        self._update_theme_directories()

    def _restore_split_mode(self, ds: LcdSnapshotResult) -> None:
        self._pm.apply_split_mode(ds.split_mode, self._pm.state.canvas_size)
        self.log.debug("_restore_split_mode: split_mode=%d ldd_is_split=%s",
                       self._pm.split_mode, self._pm.ldd_is_split)

    def _restore_slideshow(self, ds: LcdSnapshotResult) -> None:
        """Show the saved slideshow in the local-theme panel — READ only.

        It used to re-dispatch ``ConfigureSlideshow`` / ``SetSlideshow`` and
        start this window's own timer; the App's session resumes a saved
        slideshow now (``RestoreDeviceState``), for every UI.
        """
        self._show_slideshow(ds.slideshow_themes, ds.slideshow_enabled,
                             ds.slideshow_interval_s)

    def _show_slideshow(self, themes: Sequence[str], enabled: bool,
                        interval_s: float) -> None:
        """Put a slideshow state into the shared local-theme widgets."""
        self.log.info("_show_slideshow: enabled=%s themes=%d interval=%ss",
                      enabled, len(themes), interval_s)
        local = self._w['theme_local']
        interval = max(1, int(interval_s)) if themes or enabled \
            else local.get_slideshow_interval()
        # Public API on the panel (backed by its SlideshowModel) — no reaching
        # into private attrs.
        local.set_slideshow_state(list(themes), enabled, interval)

    def _restore_theme_and_preview(self) -> None:
        """Show what the device is rendering — READ only, dispatching nothing.

        The cached current frame (``rebuild_preview``) plus the overlay editor
        showing what the App holds for the panel.  Loading the saved theme is the
        session's job (``App._prime``, on connect and when data lands), for
        every UI: it used to be done here on first connect, and before that by
        a gui-only first-install auto-load, while qtgui and the API each had
        their own copy.  Re-loading here also ran LoadTheme → StopVideo, which
        cleared the user's cloud background + overlay overrides just because
        the GUI changed tabs.
        """
        current = self._lcd_settings().current_theme
        self._w['theme_local'].show_current_theme(Path(current) if current else None)
        if not current:
            self.log.info("_restore_theme_and_preview: %s has no theme",
                          self._device_key)
            self._w['preview'].set_image(None)
            return
        self.log.info(
            "_restore_theme_and_preview: reading current frame for %s "
            "(theme=%s), no re-load", self._device_key, current,
        )
        self._pm.state.current_theme_path = Path(current)
        # The toggle shows the DEVICE's persisted state, not "does this theme
        # carry elements" — a sidebar switch must report what is on screen.
        self._show_overlay_layout()
        self.rebuild_preview()

    # ── Theme (C# Theme_Click_Event) ───────────────────────────────
    # _select_theme is gone — next/'s LoadTheme Command owns the whole
    # build/cache/render/persist cycle.  Callers dispatch LoadTheme
    # directly through _select_theme_from_path / select_cloud_theme; the
    # App's slideshow driver dispatches it for rotations.

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
        self._app.dispatch(EnableOverlay(key=self._device_key, enabled=False))

        # LoadTheme internally dispatches StopVideo (clears the previous
        # playback + cloud-bg override + publishes VideoStopped which
        # stops the timer via the bus_bridge observer) and, if the new
        # theme has a Theme.{mp4,mov,webm,zt}, dispatches PlayVideo
        # which publishes VideoStarted to restart the timer.  The
        # handler does not have to coordinate that here.

        # The previous theme's mask is dropped by ``LoadTheme`` below, whose
        # ``reset_overrides`` already owns "drop the device's overrides".
        # This panel used to clear it here by hand, which made the behaviour
        # the gui's rather than the Command's — every other UI switched
        # themes with the old mask still layered on.
        # LoadTheme dispatches through the App — the Command owns the
        # theme info build, scene cache invalidation, and (if persist)
        # the per-device current_theme update in app.settings.
        result = self._app.dispatch(LoadTheme(
            key=self._device_key, path=path,
        ))
        self._pm.state.current_theme_path = path if result.ok else None
        if result.ok:
            self._sync_preview_size()   # bezel matches portrait/landscape theme (#136)
        if overlay_config and result.ok:
            self._adopt_loaded_overlay()

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
            self._adopt_loaded_overlay()
        else:
            self._w['preview'].set_status(f"Mask failed: {r.message}")

    def update_mask_position(self, x: int, y: int) -> None:
        """Update mask overlay position and re-render."""
        log.debug("update_mask_position: x=%s y=%s", x, y)
        self._app.dispatch(SetMaskPosition(
            key=self._device_key, x=x, y=y,
        ))

    def save_theme(self, name: str, *, overwrite: bool = False) -> ThemeResult:
        self.log.info("save_theme: name=%s overwrite=%s", name, overwrite)
        r = self._app.dispatch(SaveTheme(
            key=self._device_key, name=name, overwrite=overwrite,
        ))
        self._w['preview'].set_status(r.message)
        if r.ok:
            # Re-list via ListThemes so the new theme appears with user-
            # precedence (same universal path as the initial listing).
            self._update_theme_directories(force=True)
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
            self._update_theme_directories(force=True)   # re-list via ListThemes

    # ── DC File Loading ────────────────────────────────────────────

    def _show_overlay_layout(self) -> None:
        """Put the overlay the App holds for this panel on the editor — READ only.

        The grid and its switch, from ``ResolveOverlay``: the device's working
        layer, which every UI edits, each element with its id.  The grid used
        to be filled from the theme's FILES, so it never showed another UI's
        edit and sent its stale copy back over it on the next change; it also
        preferred ``trcc.json`` where the App reads ``config1.dc``.

        Dispatches no Command, so a reconnect cannot decide the switch (#276):
        ``DeviceSettings.overlay_enabled`` is the single authority.
        """
        layout = self._app.dispatch(ResolveOverlay(key=self._device_key))
        self.log.info(
            "_show_overlay_layout: %s → %d element(s) from the %s layer, "
            "enabled=%s", self._device_key, len(layout.elements),
            layout.source, layout.enabled,
        )
        settings = self._w['theme_setting']
        settings.set_overlay_enabled(layout.enabled)
        settings.load_configs(entries_to_configs(layout.elements))
        self._pm.state.overlay_enabled = layout.enabled

    def _adopt_loaded_overlay(self) -> None:
        """After a USER load (theme click, mask apply) the switch follows it.

        The load has already copied its layout into the device's working
        layer.  A layout switches the overlay on and none switches it off —
        legacy's behaviour and the one the GUI standards document.  A
        reconnect goes through ``_show_overlay_layout`` alone, which honours
        the persisted switch instead (#276).
        """
        enabled = bool(self._app.dispatch(
            ResolveOverlay(key=self._device_key)).elements)
        self.log.info("_adopt_loaded_overlay: %s → overlay %s",
                      self._device_key,
                      "enabled" if enabled else "disabled (no layout)")
        self._app.dispatch(EnableOverlay(
            key=self._device_key, enabled=enabled,
        ))
        self._show_overlay_layout()

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
        self._set_video_playing(True, reason="video-started")
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
        self._set_video_playing(False, reason="video-stopped")
        if self._pm.ui_active:
            self._w['preview'].set_playing(False)
            self._w['preview'].show_video_controls(False)

    # ── Video (C# ucBoFangQiKongZhi1) ─────────────────────────────

    def play_pause(self) -> None:
        self.log.info("play_pause: device=%s", self._device_key)
        # ``ToggleVideo`` reads the pause flag and dispatches its inverse —
        # the read-modify-write this used to do by hand on the Playback
        # object.  Doing it here meant mutating service state from the view
        # AND holding ``app.media``, which a daemon-mode handler does not
        # have.  The Command owns both halves.
        result = self._app.dispatch(ToggleVideo(key=self._device_key))
        if not result.ok:
            self.log.warning(
                "play_pause: no playback bound for %s — toggle dropped (%s)",
                self._device_key, result.message,
            )
            return
        playing = not result.paused
        self.log.info("play_pause: → playing=%s", playing)
        self._w['preview'].set_playing(playing)
        # Pause is a transient toggle on an EXISTING playback — no
        # VideoStarted / VideoStopped is published.  The core's VideoLoop
        # already skips a paused playback; this keeps the handler's view in
        # step, so metric refreshes redraw the paused frame.
        self._set_video_playing(playing, reason="play_pause")

    def stop_video(self) -> None:
        self.log.info("stop_video: device=%s", self._device_key)
        # StopVideo publishes VideoStopped → the bus_bridge observer
        # routes back to ``on_video_stopped`` which stops the timer.
        self._app.dispatch(StopVideo(key=self._device_key))
        self._w['preview'].set_playing(False)
        self._w['preview'].show_video_controls(False)

    def seek(self, frame: int) -> None:
        """Jump playback to *frame* -- the slider counts frames, so no maths."""
        from ...core.commands import SeekVideo
        status = self._video_status()
        if not status.playing or not status.frame_count:
            self.log.warning(
                "seek(%d): no playback bound for %s — dropped",
                frame, self._device_key,
            )
            return
        self.log.info("seek: frame=%d/%d", frame, status.frame_count)
        self._app.dispatch(SeekVideo(key=self._device_key, frame=frame))

    def set_video_fit_mode(self, mode: str) -> None:
        self.log.info("set_video_fit_mode: mode=%r", mode)
        self._app.dispatch(SetFitMode(key=self._device_key, mode=mode))
        # Re-render preview on the next FrameSent / tick

    def _set_video_playing(self, playing: bool, reason: str) -> None:
        """Single entry point for the handler's "video is playing" view."""
        if playing == self._video_playing:
            return
        self.log.info("_set_video_playing: %s -> %s (reason=%s) device=%s",
                      self._video_playing, playing, reason, self._device_key)
        self._video_playing = playing

    def on_video_advanced(self, event: Any) -> None:
        """A frame advanced (``VideoAdvanced``) — move the progress bar.

        Per-frame, so DEBUG.  The core's VideoLoop does the ticking (#249);
        the position used to come back as the result of this handler's own
        timer.  MULTI-DISPLAY GATE — load-bearing: every LCDHandler shares ONE
        preview/progress widget set, so only the active one may write to it.
        """
        if event.key != self._device_key:
            return
        self.log.debug("on_video_advanced: %d/%d", event.cursor, event.frame_count)
        if self._pm.ui_active:
            self._w['preview'].set_progress(
                event.cursor, event.frame_count, event.fps,
            )

    # ── Overlay (C# ucXiTongXianShi1) ─────────────────────────────

    def on_overlay_edit(self, edit: Callable[..., Command]) -> None:
        """Dispatch one element edit from the editor, for this panel.

        *edit* is an Add/Update/DeleteOverlayElement waiting only for the
        device key.  One element by id, never the whole grid: re-sending the
        grid rewrote every other element in the grid's reduced shape and put
        back whatever another UI had changed.

        Editing an element implies wanting to see it, so an edit against a
        switched-off overlay switches it on.  Deleting the LAST one implies
        the opposite, so an edit that leaves nothing must not — that would
        answer a "remove everything" by turning the overlay on.
        """
        command = edit(key=self._device_key)
        self.log.info("on_overlay_edit: %s", command)
        if not self._app.dispatch(command).ok:
            return      # App.dispatch has logged the refusal at WARNING
        if not self._pm.state.overlay_enabled and self._app.dispatch(
                ResolveOverlay(key=self._device_key)).elements:
            self._app.dispatch(EnableOverlay(
                key=self._device_key, enabled=True,
            ))
            self._pm.state.overlay_enabled = True

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
            self._w['preview'].set_image(image, fast=self._video_playing)
        else:
            self.log.debug(
                "handle_frame: dropped (ui_active=False, %s)", self._device_key,
            )

    def _sync_preview_size(self) -> None:
        """Resize the preview bezel/label to the active theme's composed
        orientation.  Cheap arithmetic; only the asset reload inside
        ``set_resolution`` is real work, and that only matters on change. (#136)

        The compose-vs-fallback rule moved to the ``PreviewSize`` Query, which
        owns every input it needs.  Gathering them here meant reaching the
        device, the theme registry and the DisplayService — three
        AttributeErrors in daemon mode, in one expression.
        """
        size = self._app.dispatch(PreviewSize(key=self._device_key))
        if not size.ok:
            # Unknown, not zero.  Resizing to 0x0 would collapse the bezel;
            # keeping the current one is what the cached canvas used to do.
            self.log.debug("_sync_preview_size: %s", size.message)
            return
        self.log.info("_sync_preview_size: composed=%s → preview %dx%d",
                      size.composed, size.width, size.height)
        self._w['preview'].set_resolution(size.width, size.height)

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
        image = self._app.dispatch(CurrentFrame(key=self._device_key)).surface
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
        self._w['preview'].set_image(image, fast=self._video_playing)

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
        # The re-root to the new orientation's catalog (#169 "not filling")
        # already happened INSIDE that dispatch — see _adopt_reoriented_theme.
        self._adopt_reoriented_theme()
        self._sync_rotation_state(degrees)
        ow, oh = self._pm.state.lcd_size
        self.log.info(
            "set_rotation: rotation=%d output=%dx%d rotated=%s",
            degrees, ow, oh, self._pm.state.is_rotated,
        )
        self._sync_preview_size()   # composed orientation, portrait-theme aware (#136)
        # Switches the browser catalog to the new orientation dims + re-lists
        # it, and auto-loads the first theme ONLY on first install.
        self._update_theme_directories()

    def _adopt_reoriented_theme(self) -> None:
        """Catch this View up with the theme the CORE just re-rooted.

        ``SetOrientation`` publishes ``OrientationChanged``, which
        ``App._on_orientation_changed`` handles by reloading the active theme
        — plus the cloud background and the mask — from the new orientation's
        catalog, each through the device's own artwork library.  Publication is
        synchronous, so it has already run by the time ``set_rotation``
        continues; nothing tells the View, so the cached path would keep
        pointing at the old catalog (``trcc_app`` reads it for save/export).

        This panel used to RE-DECIDE that instead, dispatching a second
        ``LoadTheme`` of its own.  Two costs, both measured: the second load
        took the default ``reset_overrides=True`` and so persist-cleared the
        overlay edits the core had just deliberately preserved, and its
        resolver preferred the user tree over the shipped one, silently
        swapping a shipped theme for a same-named saved theme.  It existed
        because July's ``oriented_theme_path`` looked only in the generic
        ``theme{w}{h}``, missing the per-SKU libraries #169's cooler uses;
        ``0709ad5f`` taught the core resolver those libraries on 2026-08-23
        and the workaround has been redundant since.

        ``_show_overlay_layout``, not ``_adopt_loaded_overlay``: a rotation
        re-roots the SAME theme, so the persisted overlay toggle is the
        authority and must be shown, never replaced (#276).
        """
        snap = self._app.dispatch(LcdSnapshot(key=self._device_key))
        current = (
            Path(snap.current_theme) if snap.ok and snap.current_theme else None
        )
        if current == self._pm.state.current_theme_path:
            self.log.debug(
                "_adopt_reoriented_theme: %s unchanged at %s",
                self._device_key, current,
            )
            return
        self.log.info(
            "_adopt_reoriented_theme: core re-rooted %s → %s",
            self._pm.state.current_theme_path, current,
        )
        self._pm.state.current_theme_path = current
        if current is not None:
            self._show_overlay_layout()

    def set_split_mode(self, mode: int) -> None:
        self.log.info("set_split_mode: %d -> %d device=%s",
                      self._pm.split_mode, mode, self._device_key)
        self._pm.split_mode = mode
        self._app.dispatch(SetSplitMode(
            key=self._device_key, mode=mode,
        ))

    # ── Background / Screencast Toggles ────────────────────────────

    def on_background_toggle(self, enabled: bool) -> None:
        """The background switch, as the C#'s: on draws one, off draws none.

        On also closes the media player (the C#'s ``ClosePlayer``); the window
        ends a cast before calling this.  A theme's or a user's background
        VIDEO is the background and keeps playing -- this used to ``StopVideo``
        it.  The switch itself shows what the App then holds (``follow_app``).
        """
        ds = self._lcd_settings()
        self.log.info("on_background_toggle: enabled=%s device=%s source=%s mode=%s",
                      enabled, self._device_key, ds.display_source,
                      ds.background_mode)
        if not enabled:
            self._app.dispatch(SetBackgroundMode(key=self._device_key,
                                                 mode="transparent"))
            return
        if ds.display_source == "media":
            self._app.dispatch(SetMediaPlayer(key=self._device_key, uri=""))
        if ds.background_mode == "transparent":
            self._app.dispatch(SetBackgroundMode(key=self._device_key,
                                                 mode="theme"))

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
        # The panel's edit, saved; ``SetSlideshow`` is what rotates it -- the
        # App's driver, not a timer in this window.
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
        self.log.info("on_slideshow_delegate: device=%s", self._device_key)
        self._update_slideshow_state()

    def on_slideshow_changed(self, event: Any) -> None:
        """The saved slideshow changed — here, in another UI, or the App
        switched it off because another source took the panel."""
        self.log.info("on_slideshow_changed: %s enabled=%s active=%s",
                      event.key, event.enabled, self._pm.ui_active)
        if self._pm.ui_active:   # the local-theme widgets are shared
            self._show_slideshow(event.themes, event.enabled, event.interval_s)

    def on_theme_loaded(self, event: Any) -> None:
        """A theme was loaded on this device — by the slideshow, or any UI.

        Re-reads what the panel shows (``_restore_theme_and_preview``) so the
        current theme and the overlay editor follow; the slideshow's rotations
        used to update them from this window's own tick.
        """
        self.log.info("on_theme_loaded: %s theme=%s active=%s",
                      event.key, event.theme_name, self._pm.ui_active)
        if self._pm.ui_active:
            self._restore_theme_and_preview()

    # ── Rendering ──────────────────────────────────────────────────


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

        **Carrier selection reacts to the RESULT, never to the environment.**
        ``PreviewResult.surface`` is a live ``QImage`` and cannot cross the
        daemon socket — ``_to_wire`` drops it to ``None``.  Asking for
        ``encode="png"`` always would cost a PNG encode per frame that is
        thrown away in-process (measured 2.9 ms at 320x320, 20.2 ms at
        1600x720 — 60% of a core at full rate), and asking "am I remote?" is
        the environment sniffing the architecture forbids.  So: ask for
        nothing, and if a frame WAS rendered yet no carrier arrived, ask again
        for bytes and remember that answer.

        ``width`` is what makes that unambiguous — ``BuildPreview`` sets it
        only on the success path, so ``width == 0`` is "no theme loaded" while
        a non-zero width with no surface and no image means the surface died
        at the wire.
        """
        result = self._app.dispatch(
            BuildPreview(key=self._device_key, encode=self._preview_encode),
        )
        if not result.ok:
            # Blank preview is user-visible; the Command already logged why.
            self.log.warning("_build_preview_surface: %s — %s",
                             self._device_key, result.message)
            return None
        if (not self._preview_encode and result.width
                and result.surface is None and not result.image):
            self.log.info(
                "_build_preview_surface: %s rendered %dx%d but no surface "
                "crossed — switching this panel to PNG bytes (daemon mode)",
                self._device_key, result.width, result.height,
            )
            self._preview_encode = "png"
            result = self._app.dispatch(
                BuildPreview(key=self._device_key, encode="png"),
            )
        if result.surface is not None:
            return result.surface
        if result.image:
            from PySide6.QtGui import QImage
            image = QImage.fromData(result.image)
            if image.isNull():
                self.log.warning(
                    "_build_preview_surface: %s sent %d byte(s) of %s that "
                    "Qt could not decode",
                    self._device_key, len(result.image), result.media_type,
                )
                return None
            return image
        self.log.debug("_build_preview_surface: %s — %s",
                       self._device_key, result.message)
        return None

    # ── Helpers ─────────────────────────────────────────────────────

    def refresh_themes(self) -> None:
        """Public re-list of the local theme browser (after save / import /
        delete) — re-dispatches ListThemes through the dir-resolution refresh."""
        self._update_theme_directories(force=True)

    def _update_theme_directories(self, *, force: bool = False) -> None:
        """Reload theme browser directories for the current resolution.

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
        dirs = self._app.dispatch(
            ResolveThemeDirectories(key=self._device_key))
        if not dirs.ok:
            self.log.warning("_update_theme_directories: %s", dirs.message)
            return
        bw, bh = dirs.catalog_size
        theme_dir = Path(dirs.theme_dir)
        user_theme_dir = Path(dirs.user_theme_dir)
        web_dir = Path(dirs.web_dir)
        masks_dir = Path(dirs.masks_dir)

        # Nothing below changes unless the RESOLVED directories change (or the
        # active panel does — the browser is shared, so becoming active means
        # repopulating it with THIS device's catalog).  Everything after this
        # point costs a ``ListThemes`` dispatch, three directory scans and
        # ~250 Qt widgets, so re-running it for an identical answer is the
        # whole cost.  ``force`` is for the callers where the DIRECTORIES are
        # unchanged but their CONTENT is not: a theme saved, a config
        # imported, the background data download landing.
        signature = (bw, bh, theme_dir, user_theme_dir, masks_dir,
                     web_dir, self._pm.ui_active)
        if not force and signature == self._dirs_signature:
            self.log.debug(
                "_update_theme_directories: %s unchanged (catalog=%dx%d "
                "active=%s) — skipping the browser rebuild",
                self._device_key, bw, bh, self._pm.ui_active,
            )
            return
        self._dirs_signature = signature

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
            # Key and resolution BEFORE the directory, because
            # ``set_mask_directory`` is the call that triggers the refresh and
            # ``ListMasks`` needs both to resolve the per-SKU library.  The old
            # order worked only because the panel read its user-mask dir from
            # whatever resolution it happened to be holding.
            self._w['theme_mask'].set_device_key(self._device_key)
            self._w['theme_mask'].set_resolution(f'{bw}x{bh}')
            if masks_dir:
                self._w['theme_mask'].set_mask_directory(masks_dir)
            self._w['image_cut'].set_resolution(bw, bh)
            self._w['video_cut'].set_resolution(bw, bh)
        else:
            self.log.info(
                "_update_theme_directories: %s is not the active panel — "
                "leaving the shared theme browser alone (writing it would "
                "offer this device's %dx%d catalog to whichever panel IS "
                "selected)", self._device_key, bw, bh)

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
        """Release THIS window's timers and caches — never the panel.

        It used to blank the panel too (``StopVideo`` + ``SleepDevice``).  As a
        daemon client that put every panel to sleep for every other UI when one
        window quit; and on a disconnect ``SleepDevice``, which connects its
        device first, RECONNECTED the panel that had just gone -- measured.
        Blanking belongs to whoever owns the panels: ``App.close()``.
        """
        self.log.info("cleanup: %s — timers and caches only", self._device_key)
        self.deactivate()
        self._pixmap_cache.clear()
        self._last_render_id = None

    def deactivate(self) -> None:
        """Full pause — stop all timers (called from cleanup)."""
        self._set_video_playing(False, reason="deactivate")

    def set_inactive(self) -> None:
        """Soft pause for sidebar switch — keep video playing in background.

        Multi-display: dropping `_ui_active` stops shared-widget writes
        without killing the per-device animation timer, so the LCD keeps
        showing its theme while another device owns the GUI panel.
        """
        self._pm.ui_active = False

