"""SettingsBinding — the Settings panel wired to the App, for any GUI.

The panel draws (``UCThemeSetting``, in the skin it was given); this connects
what the user does on it to ``ThemeSettings`` -- every Command over the bus --
and puts what the App holds back on it (``ThemeSettings.read``).  Some actions
need a UI-only step first: a file to pick, an address to type, a colour off the
screen, a sensor to choose.  Each is a method here with a plain default, and a
GUI that does it its own way overrides that one method (the gui crops a picture
in its cutter) -- base + override, so no GUI re-wires the panel.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QWidget

from ...core.commands import GetSensorDashboard, LcdSnapshot, ListModules, ReadSensors
from ...core.logs import per_frame
from ...core.models import InsetCorner, InsetSize
from ...core.ports import CommandBus
from ..presentation.sensor_display import activity_rows
from ..presentation.theme_settings import ElementDrag, ThemeSettings
from .display_mode_panels import InsetDialog
from .uc_theme_setting import UCThemeSetting

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_IMAGES = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)"
_VIDEOS = "Videos (*.mp4 *.avi *.mkv *.mov *.webm *.gif *.zt);;All Files (*)"


class SettingsBinding(QObject):
    """One Settings panel, bound to the device *key* names, over *app*."""

    def __init__(self, panel: UCThemeSetting, app: CommandBus, key: Callable[[], str],
                 steps: SettingsSteps) -> None:
        super().__init__(panel)
        log.info("SettingsBinding: bound")
        self._panel, self._key, self.steps = panel, key, steps
        self._settings = ThemeSettings(app)
        #: The overlay switch as last read -- an edit to a dark overlay lights it.
        self._overlay_on = False
        #: Moving an element on the preview.
        self.drag = PanelDrag(self)
        panel.overlay_grid.toggle_changed.connect(self._on_overlay)
        panel.delegate.connect(self._on_delegate)
        panel.background_changed.connect(self._on_background)
        panel.screencast_changed.connect(self._on_screencast)
        panel.temp_unit_changed.connect(self._settings.set_temp_unit)
        panel.eyedropper_requested.connect(self._pick_colour)
        panel.add_panel.hardware_requested.connect(self._add_hardware)
        cast = panel.screencast_panel
        cast.region_edited.connect(self._on_region)
        cast.border_toggled.connect(self._on_border)
        cast.audio_toggled.connect(self._on_audio)

    # ── What the panel shows ───────────────────────────────────────────

    def refresh(self, *_event: object) -> None:
        """Put what the App holds for the device on the panel.  Sends nothing."""
        key = self._key()
        view = self._settings.read(key) if key else None
        log.debug("SettingsBinding.refresh: %s -> %s", key, view is not None)
        if view is None:
            return
        self._panel.show(view)
        self._overlay_on = view.overlay_on

    def size_to_canvas(self, *_event: object) -> None:
        """X / Y bounded by where elements sit: the device, its theme or its
        orientation changed -- never per edit."""
        key = self._key()
        canvas = self._settings.canvas(key) if key else None
        log.debug("SettingsBinding.size_to_canvas: %s -> %s", key, canvas)
        if canvas is not None:
            self._panel.set_canvas(*canvas)

    # ── What the user does ─────────────────────────────────────────────

    def _on_overlay(self, on: bool) -> None:
        log.info("SettingsBinding._on_overlay: %s", on)
        self._settings.set_overlay(self._key(), on)

    def _on_background(self, on: bool) -> None:
        log.info("SettingsBinding._on_background: %s", on)
        self._settings.show_background(self._key(), on)

    def _on_screencast(self, on: bool) -> None:
        log.info("SettingsBinding._on_screencast: %s", on)
        cast = self._panel.screencast_panel
        result = (self._settings.start_cast(self._key(), cast.values(), cast.audio_on)
                  if on else self._settings.stop_cast(self._key()))
        if not result.ok:
            self.steps.say(f"Screencast: {result.message}")

    def _on_region(self, x: int, y: int, w: int, h: int) -> None:
        log.info("SettingsBinding._on_region: (%d,%d %dx%d)", x, y, w, h)
        result = self._settings.set_region(self._key(), (x, y, w, h))
        if not result.ok:
            self.steps.say(f"Screencast: {result.message}")
            self.refresh()

    def _on_border(self, hide: bool) -> None:
        log.info("SettingsBinding._on_border: hide=%s", hide)
        self._settings.set_hide_border(self._key(), hide)

    def _on_audio(self, on: bool) -> None:
        log.info("SettingsBinding._on_audio: %s", on)
        self._settings.set_cast_audio(self._key(), on)

    def _on_delegate(self, cmd: int, info: Any, _data: Any) -> None:
        """The panel's actions that are not signals of their own -- by table."""
        action = self._ACTIONS.get(cmd)
        log.info("SettingsBinding._on_delegate: %s -> %s", cmd,
                 action.__name__ if action is not None else "(not ours)")
        if action is not None:
            action(self, info)

    def _edit(self, edit: Any) -> None:
        """One element added, changed or deleted -- the key added here."""
        log.debug("SettingsBinding._edit: overlay on %s", self._overlay_on)
        self._overlay_on = self._settings.edit_element(self._key(), edit,
                                                       self._overlay_on).overlay_on

    def _mask_visible(self, on: Any) -> None:
        log.info("SettingsBinding._mask_visible: %s", on)
        self._settings.set_mask_visible(self._key(), bool(on))

    def _mask_position(self, where: Any) -> None:
        log.info("SettingsBinding._mask_position: %s", where)
        if where:
            self._settings.set_mask_position(self._key(), where[0], where[1])

    def _upload_mask(self, _info: Any) -> None:
        log.info("SettingsBinding._upload_mask")
        if (path := self.steps.choose_file("Mask", _IMAGES)) is not None:
            self._answer(self._settings.apply_mask(self._key(), path, custom=True))

    def _cloud_masks(self, _info: Any) -> None:
        log.info("SettingsBinding._cloud_masks")
        self.steps.show_cloud_masks()

    def _load_background(self, _info: Any, *, what: str, filter_: str) -> None:
        """Load Image / Load Video: a file the user picks becomes the background."""
        log.info("SettingsBinding._load_background: %s", what)
        if (path := self.steps.choose_file(what, filter_)) is not None:
            self._answer(self._settings.set_background(self._key(), path))

    def _media_file(self, _info: Any) -> None:
        log.info("SettingsBinding._media_file")
        if (path := self.steps.choose_file("Video", _VIDEOS)) is not None:
            self._answer(self._settings.play_media(self._key(), str(path)))

    def _media_url(self, _info: Any) -> None:
        log.info("SettingsBinding._media_url")
        if (url := self.steps.ask_url()):
            self._answer(self._settings.play_media(self._key(), url))

    def _inset(self, _info: Any) -> None:
        log.info("SettingsBinding._inset")
        if (choice := self.steps.choose_inset(self._key())) is not None:
            self._answer(self._settings.set_inset(self._key(), *choice))

    def _media_switch(self, on: Any) -> None:
        """The media player switch: off stops it; on needs a file or address."""
        log.info("SettingsBinding._media_switch: %s", on)
        if not on:
            self._settings.stop_media(self._key())

    def _remember(self, rgb: Any) -> None:
        log.info("SettingsBinding._remember: %s", rgb)
        if (colours := self._settings.remember_colour(self._key(), rgb)) is not None:
            self._panel.color_panel.set_recent_colors(colours)

    #: The panel's action codes, each to the method that answers it.
    _ACTIONS: ClassVar[dict[int, Callable[[SettingsBinding, Any], None]]] = {
        UCThemeSetting.CMD_OVERLAY_CHANGED: _edit,
        UCThemeSetting.CMD_MASK_TOGGLE: _mask_visible,
        UCThemeSetting.CMD_MASK_VISIBILITY: _mask_visible,
        UCThemeSetting.CMD_MASK_POSITION: _mask_position,
        UCThemeSetting.CMD_MASK_UPLOAD: _upload_mask,
        UCThemeSetting.CMD_MASK_LOAD: _cloud_masks,
        UCThemeSetting.CMD_MASK_CLOUD: _cloud_masks,
        UCThemeSetting.CMD_BACKGROUND_LOAD_IMAGE: partial(_load_background, what="Image",
                                                          filter_=_IMAGES),
        UCThemeSetting.CMD_BACKGROUND_LOAD_VIDEO: partial(_load_background, what="Video",
                                                          filter_=_VIDEOS),
        UCThemeSetting.CMD_VIDEO_LOAD: _media_file,
        UCThemeSetting.CMD_VIDEO_URL: _media_url,
        UCThemeSetting.CMD_INSET: _inset,
        UCThemeSetting.CMD_VIDEO_TOGGLE: _media_switch,
        UCThemeSetting.CMD_COLOR_REMEMBER: _remember,
    }

    def _answer(self, result: Any) -> None:
        """A Command's answer, said where the user is looking when refused."""
        log.debug("SettingsBinding._answer: ok=%s %s", result.ok, result.message)
        if not result.ok:
            self.steps.say(result.message)

    def _pick_colour(self) -> None:
        """The eyedropper: a colour off the screen onto the selected element."""
        log.info("SettingsBinding._pick_colour")
        self.steps.pick_colour(self._panel.color_panel._apply_color)

    def _add_hardware(self) -> None:
        """Hardware Data: the reading the user picks, as a new element."""
        config = self.steps.choose_hardware()
        log.info("SettingsBinding._add_hardware: %s", getattr(config, "metric", None))
        if config is not None:
            self._panel.overlay_grid.add_element(config)


class PanelDrag:
    """An overlay element moved by hand on the preview -- the gui's way: a
    press picks up the selected element or the nearest, it moves as far as
    the pointer, kept on the canvas, and the panel shows the App's layout
    again when it is let go."""

    def __init__(self, binding: SettingsBinding) -> None:
        log.debug("PanelDrag: built")
        self._b = binding
        self._drag: ElementDrag | None = None


    def grab_at(self, x: int, y: int) -> None:
        """A press on the preview: the selected element, else the nearest."""
        grid = self._b._panel.overlay_grid
        if grid.get_selected_config() is None and (index := grid.find_nearest_element(x, y)) >= 0:
            grid.select_element(index)
        config = grid.get_selected_config()
        canvas = self._b._settings.canvas(self._b._key()) if config is not None else None
        log.info("PanelDrag.grab_at: (%d, %d) -> %s", x, y, config and config.id)
        self._drag = (ElementDrag(origin=(x, y), start=(config.x, config.y), canvas=canvas)
                      if config is not None and canvas is not None else None)

    def drag_to(self, x: int, y: int) -> None:
        """The pointer moved: the element moves as far, kept on the canvas."""
        if self._drag is None:
            return
        new_x, new_y = self._drag.to(x, y)
        frame_log.debug("PanelDrag.drag_to: (%d, %d)", new_x, new_y)
        self._b._panel.color_panel.set_position(new_x, new_y)
        self._b._panel._on_position_changed(new_x, new_y)

    def let_go(self) -> None:
        """Let go: the panel shows the App's layout again."""
        log.info("PanelDrag.let_go")
        self._drag = None
        self._b.refresh()


class SettingsSteps:
    """The UI-only steps before a Settings Command -- a plain default each.
    A GUI that does one its own way subclasses and overrides that method (the
    gui crops a picture in its cutter); the panel's wiring never changes."""

    def __init__(self, window: QWidget, app: CommandBus) -> None:
        log.debug("SettingsSteps: built")
        self._window, self._app = window, app
        self._eyedropper: QWidget | None = None
        self._apply: Callable[[int, int, int], None] | None = None


    def say(self, text: str) -> None:
        """Tell the user something -- the log by default."""
        log.warning("SettingsSteps.say: %s", text)

    def choose_file(self, what: str, filter_: str) -> Path | None:
        """A file the user picks, or None."""
        path, _ = QFileDialog.getOpenFileName(self._window, f"Open {what}", "", filter_)
        log.info("SettingsSteps.choose_file: %s -> %r", what, path)
        return Path(path) if path else None

    def ask_url(self) -> str | None:
        """A web address for the media player, or None."""
        url, ok = QInputDialog.getText(
            self._window, "Web Video",
            "An http, https or rtsp address (a video file or a live stream):")
        log.info("SettingsSteps.ask_url: ok=%s", ok)
        return url.strip() if ok and url.strip() else None

    def choose_inset(self, key: str) -> tuple[str, str, str] | None:
        """A module's picture in a box: (module, corner, size); "" module =
        no box.  None when cancelled."""
        snap = self._app.dispatch(LcdSnapshot(key=key))
        choices = [(m.key, m.title) for m in self._app.dispatch(ListModules()).modules
                   if m.draws_pictures]
        if snap.inset_module and snap.inset_module not in dict(choices):
            choices.append((snap.inset_module, snap.inset_title or snap.inset_module))
        dialog = InsetDialog(choices, snap.inset_module,
                             snap.inset_corner or InsetCorner.BOTTOM_RIGHT.value,
                             snap.inset_size or InsetSize.MEDIUM.value, self._window)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        log.info("SettingsSteps.choose_inset: %s accepted=%s", key, accepted)
        return dialog.choice() if accepted else None

    def pick_colour(self, apply: Callable[[int, int, int], None]) -> None:
        """A colour off the screen, handed to *apply*."""
        from ..eyedropper import EyedropperOverlay
        log.info("SettingsSteps.pick_colour")
        self._apply = apply
        overlay = EyedropperOverlay()
        overlay.color_picked.connect(self._on_colour_picked)
        overlay.cancelled.connect(self._on_colour_cancelled)
        self._eyedropper = overlay
        overlay.show()

    def _on_colour_picked(self, r: int, g: int, b: int) -> None:
        log.info("SettingsSteps._on_colour_picked: (%d,%d,%d)", r, g, b)
        self._eyedropper = None
        if self._apply is not None:
            self._apply(r, g, b)

    def _on_colour_cancelled(self) -> None:
        log.debug("SettingsSteps._on_colour_cancelled")
        self._eyedropper = None

    def show_cloud_masks(self) -> None:
        """The panel's Masks button: where the downloadable masks are."""
        log.info("SettingsSteps.show_cloud_masks")
        self.say("Pick a mask on the Cloud Masks tab.")

    def choose_hardware(self) -> Any:
        """Hardware Data: a reading to show, from the sensor dashboard and the
        modules' own -- the gui's Activity list, as a menu.  The new element's
        config, or None."""
        rows = activity_rows(
            list(self._app.dispatch(GetSensorDashboard()).panels),
            self._app.dispatch(ReadSensors()).readings,
            {m.key: m.title for m in self._app.dispatch(ListModules()).modules})
        log.info("SettingsSteps.choose_hardware: %d group(s)", len(rows))
        menu = QMenu(self._window)
        for name, _category, group in rows:
            sub = menu.addMenu(name)
            for label, _sensor_id, _unit, config, _explanation in group:
                action = QAction(label, sub)
                action.setData(config)
                sub.addAction(action)
        chosen = menu.exec(QCursor.pos())
        return chosen.data() if chosen is not None else None
