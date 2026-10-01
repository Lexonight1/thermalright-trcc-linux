"""ScreencastPanel — mirror a region of the desktop to a connected LCD.

Workflow:

1.  Pick a device (the existing :class:`DevicePickerWidget`).
2.  The region shown is the device's own (``LcdSnapshot.screencast_rect``:
    the loaded theme's, the last edit from any UI, or the C#'s default).
    Edit it in the X / Y / W / H fields (their arrows are the C#'s +/-
    nudges), or "Choose region…" to drag one with
    :class:`RegionSelectOverlay`.  :class:`SetScreencastRegion` stores
    either -- a running cast follows it.
3.  Pick an update interval (frames per second), and optionally tick
    "Draw a spectrum from the microphone" — it applies at the next Start
    and re-issues the session when toggled mid-cast.
4.  Click Start — :class:`StartScreencast` casts the stored region; the
    App's capture driver grabs and sends every tick.
5.  Stop ends the cast; the device keeps the last frame until the
    user picks a new theme.

Honest scope:

* X11 and Wayland both capture through ``Platform.screen_capture()``:
  Qt's grab and the X11 grabbers on X11, the xdg-portal PipeWire stream
  with the desktop's own tool behind it on Wayland.
* Background mode is left to the user — the configuration panel
  exposes ``transparent`` which makes the captured frame the only
  visible layer.  Picking ``theme`` keeps the theme's background and
  composites the screencast on top, which is rarely what people
  want.
"""
from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from ....core.commands import (
    LcdSnapshot,
    SetScreencastRegion,
    StartScreencast,
    StopScreencast,
)
from ....core.events import Event
from ....core.geometry import lock_region_to_panel
from ....core.models import SCREENCAST_TICK_S
from ...viewfinder import frames_supported, open_picker, store_picked_region
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    from ....core.results import ScreencastResult

log = logging.getLogger(__name__)

_MIN_FPS = 1
_MAX_FPS = 30
#: Where the slider starts: the app's own cadence, DERIVED rather than
#: restated.  This was a literal 6, which matched the invented 0.15 s tick
#: the rest of the tree used until 2026-09-18 -- so once that moved to the
#: C# oracle's rate, qtgui would have gone on casting at 6 fps while gui,
#: cli and api ran at 16.7.  See ``SCREENCAST_TICK_S`` for the derivation
#: from ``FormCZTV.Timer_event``.
_DEFAULT_FPS = round(1.0 / SCREENCAST_TICK_S)
#: The region's fields, in the order the C# lays them out and the Command takes.
_AXES = ("x", "y", "w", "h")
#: The C#'s clamp on every field and nudge (``UCTouPingXianShi.cs:215-310``).
_FIELD_MAX = 9999


class ScreencastPanel(BasePanel):
    """Drive a captured screen region into the device on a timer."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        #: The selected device's region as the App last showed it, re-read on
        #: every event -- what Start and the border flag send back.
        self._rect: tuple[int, int, int, int] | None = None
        #: The canvas the cast fills, as the App last showed it -- what a
        #: W or H edit locks the other edge to.
        self._canvas: tuple[int, int] | None = None
        #: Whether the SELECTED device is casting, as the App says
        #: (``LcdSnapshot.screencast_region``).  It was a local
        #: ``_casting_key``: another UI's start or stop never showed here, and
        #: after picking another device the buttons still described the first.
        self._casting = False

        # ── Device picker ─────────────────────────────────────────────
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )

        # ── Region: typed fields + the drag picker ────────────────────
        # Keyboard tracking off: a field signals on Enter, on focus leaving it
        # and on each arrow click -- a finished edit, as gui sends -- never on
        # the half-typed "3" of "320".  An unchanged focus-out signals nothing.
        self._pick_btn = QPushButton("Choose region…", self)
        self._pick_btn.clicked.connect(self._on_pick_region)
        region_row = QHBoxLayout()
        region_row.addWidget(self._pick_btn)
        self._fields: dict[str, QSpinBox] = {}
        for axis in _AXES:
            field = QSpinBox(self)
            field.setRange(0, _FIELD_MAX)
            field.setKeyboardTracking(False)
            field.setPrefix(f"{axis.upper()} ")
            field.valueChanged.connect(partial(self._on_field_edited, axis))
            region_row.addWidget(field)
            self._fields[axis] = field
        region_row.addStretch(1)

        # The C#'s border button (``myYcbk``): hide the frame drawn round the
        # region on screen.  Stored with the region and saved into themes.
        self._hide_border = QCheckBox("Hide the frame around the region", self)
        self._hide_border.toggled.connect(self._on_hide_border_toggled)
        if not frames_supported(self.app):
            # Stored and saved into themes all the same, so a theme moved to
            # an X11 or Windows desktop keeps it -- but nothing draws here.
            self._hide_border.setToolTip(
                "No frame can be shown on Wayland: a window cannot place "
                "itself there.  Use \"Choose region…\" to set the region.")

        # ── Interval slider ───────────────────────────────────────────
        self._fps = QSlider(Qt.Orientation.Horizontal, self)
        self._fps.setRange(_MIN_FPS, _MAX_FPS)
        self._fps.setValue(_DEFAULT_FPS)
        self._fps.setTickInterval(5)
        self._fps.setToolTip(
            "How many frames to push per second.  Higher = smoother + "
            "more CPU; the LCD's refresh limit is usually 25–30 fps.",
        )
        self._fps_label = QLabel(f"{_DEFAULT_FPS} fps", self)
        self._fps.valueChanged.connect(self._on_fps_slid)
        self._fps.valueChanged.connect(self._on_fps_changed)
        fps_row = QHBoxLayout()
        fps_row.addWidget(self._fps, stretch=1)
        fps_row.addWidget(self._fps_label)

        # ── Microphone ────────────────────────────────────────────────
        # The last face that could not reach ``StartScreencast.audio``:
        # gui has a mic button, cli has ``--audio``, api has ``body.audio``,
        # and qtgui dispatched the field's default forever.
        self._audio = QCheckBox("Draw a spectrum from the microphone", self)
        self._audio.setToolTip(
            "Overlays audio bars on the cast frame.  Needs "
            "``sounddevice``; without it the cast runs without bars.",
        )
        self._audio.toggled.connect(self._on_audio_toggled)

        # ── Tips group ────────────────────────────────────────────────
        # A tip, not a checkbox: the checkbox this was is read by nothing, so
        # "recommended" was ticked and did not do anything.
        tips_box = QGroupBox("Tips", self)
        tips_layout = QVBoxLayout(tips_box)
        tip = QLabel(
            "Set the background mode to 'Transparent' on the Configuration "
            "page before starting; otherwise the theme's background "
            "composites under the screencast.",
            tips_box,
        )
        tip.setWordWrap(True)
        tips_layout.addWidget(tip)

        # ── Start / Stop ──────────────────────────────────────────────
        self._start_btn = QPushButton("Start screencast", self)
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn = QPushButton("Stop", self)
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self._start_btn)
        button_row.addWidget(self._stop_btn)
        button_row.addStretch(1)

        # ── Status ────────────────────────────────────────────────────
        self._status = QLabel(
            "Pick a device + a region, then Start to mirror the region "
            "to the device.",
            self,
        )
        self._status.setWordWrap(True)

        # ── Compose ───────────────────────────────────────────────────
        form = QFormLayout()
        form.addRow("Device:", self._picker)
        form.addRow("Region:", region_row)
        form.addRow("", self._hide_border)
        form.addRow("Update rate:", fps_row)
        form.addRow("", self._audio)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(form)
        root.addWidget(tips_box)
        root.addLayout(button_row)
        root.addWidget(self._status)
        root.addStretch(1)

        qconn = Qt.ConnectionType.QueuedConnection
        self._picker.key_changed.connect(self._on_key_changed)
        for signal in (self._bus.screencast_started, self._bus.screencast_stopped,
                       self._bus.settings_changed):
            signal.connect(self._on_cast_event, type=qconn)
        self._show_state()

    # ── Region picking ───────────────────────────────────────────────

    def _on_pick_region(self) -> None:
        log.info("_on_pick_region")
        overlay = open_picker(self)
        overlay.region_selected.connect(self._on_region_selected)
        overlay.cancelled.connect(self._on_region_cancelled)

    def _on_region_selected(
        self, x: int, y: int, w: int, h: int,
    ) -> None:
        log.info("_on_region_selected: x=%s y=%s w=%s h=%s", x, y, w, h)
        # Fit the drag to the canvas the cast fills.  The C# oracle's
        # viewfinder is PRE-SIZED from the panel and the user picks position
        # only, so a free-form rectangle loses the guarantee the original
        # gave: what you framed is what appears.  The canvas turns with the
        # orientation; the native resolution this used to read does not.
        key = self._picker.current_key()
        if not key:
            self._status.setText("Pick a device first.  Open the Devices panel to scan.")
            return
        result = store_picked_region(self.app, key, x, y, w, h)
        if not result.ok:
            self._status.setText(result.message)
        self._show_state()

    def _on_field_edited(self, axis: str, value: int) -> None:
        """A field finished an edit -- the App stores the region it shows.

        A W or H edit leads and the other edge locks to the canvas, through
        the same helper gui uses (the C# locks both ways too,
        ``UCTouPingXianShi.cs:362-395``).  X and Y move the region and lock
        nothing.
        """
        key = self._picker.current_key()
        region = tuple(self._fields[a].value() for a in _AXES)
        log.info("_on_field_edited: %s %s=%d -> %s", key, axis, value, region)
        if not key:
            return
        if axis in ("w", "h"):
            region = lock_region_to_panel(
                self._canvas, *region, keep="width" if axis == "w" else "height")
        self._send_region(key, region)

    def _send_region(self, key: str, region: tuple[int, ...]) -> None:
        """Store a region edit in the App; a refusal shows, and the panel re-reads."""
        x, y, w, h = region
        result = self.dispatch(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h))
        log.info("_send_region: %s (%d,%d %dx%d) -> %s", key, x, y, w, h,
                 result.message)
        if not result.ok:
            self._status.setText(result.message)
        self._show_state()

    def _on_hide_border_toggled(self, hide: bool) -> None:
        """The border flag, sent with the region the App holds."""
        key = self._picker.current_key()
        log.info("_on_hide_border_toggled: %s hide=%s rect=%s", key, hide, self._rect)
        if key and self._rect is not None:
            x, y, w, h = self._rect
            self.dispatch(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h,
                                              hide_border=hide))

    def _on_region_cancelled(self) -> None:
        log.info("_on_region_cancelled")
        self._status.setText("Region selection cancelled.")

    # ── FPS plumbing ─────────────────────────────────────────────────

    def _on_fps_slid(self, value: int) -> None:
        """Echo the slider position beside it.  ``_on_fps_changed`` applies."""
        log.debug("_on_fps_slid: value=%s", value)
        self._fps_label.setText(f"{value} fps")

    def _on_fps_changed(self, value: int) -> None:
        log.info("_on_fps_changed: value=%s", value)
        key = self._picker.current_key()
        if self._casting and key:
            # Re-issuing replaces the driver, so this is how the cadence
            # changes mid-cast.
            self._issue(key)

    def _issue(self, key: str) -> ScreencastResult:
        """Start (or re-issue) the session with everything the panel holds.

        One dispatch for Start, the fps slider and the mic checkbox, so a
        re-issue for one of them cannot reset the others.  The region is the
        one shown -- the App's, re-read on every event; 0x0 before a snapshot
        has answered, which the App reads as the stored one.
        """
        x, y, w, h = self._rect or (0, 0, 0, 0)
        log.info("_issue: key=%s region=%s audio=%s fps=%s", key, self._rect,
                 self._audio.isChecked(), self._fps.value())
        return self.dispatch(StartScreencast(
            key=key, x=x, y=y, w=w, h=h, audio=self._audio.isChecked(),
            interval_s=self._fps_interval_s(),
        ))

    def _fps_interval_s(self) -> float:
        """The slider's fps as the driver's tick interval, in seconds."""
        log.debug("_fps_interval_s")
        return max(0.033, 1.0 / max(_MIN_FPS, self._fps.value()))

    # ── Microphone ───────────────────────────────────────────────────

    def _on_audio_toggled(self, enabled: bool) -> None:
        """Mic on/off, mid-cast included.

        Re-issuing the session with the new flag is how the flag changes,
        exactly as re-issuing it is how the cadence changes
        (:meth:`_on_fps_changed`).  The flag lives in ``screencast_region``'s
        fifth element — the one persisted truth — so there is no second piece
        of state to keep in step, and ``_sync_audio`` in the Command holds the
        microphone open for exactly as long as some session wants it.

        Off-session it is just the checkbox; :meth:`_on_start` reads it.
        """
        log.info("_on_audio_toggled: enabled=%s", enabled)
        key = self._picker.current_key()
        if not self._casting or not key:
            log.debug("_on_audio_toggled: no live session — the checkbox "
                      "applies at the next Start")
            return
        result = self._issue(key)
        if not result.ok:
            log.warning("_on_audio_toggled: re-issue failed: %s",
                        result.message)
            self._status.setText(result.message)

    # ── Lifecycle ────────────────────────────────────────────────────

    def _on_start(self) -> None:
        log.info("_on_start")
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan.",
            )
            return
        started = self._issue(key)
        if not started.ok:
            self._status.setText(started.message)
            return
        self._show_state()

    def _on_stop(self) -> None:
        key = self._picker.current_key()
        log.info("_on_stop: key=%s", key)
        if key:
            self.dispatch(StopScreencast(key=key))
        self._show_state()

    # ── Showing what the App holds ───────────────────────────────────

    def _on_key_changed(self, key: str) -> None:
        log.info("_on_key_changed: %s", key)
        self._show_state()

    def _on_cast_event(self, event: Event) -> None:
        """A cast started or stopped, or a device setting changed -- the
        region among them -- in any UI."""
        key = getattr(event, "key", None)
        log.debug("_on_cast_event: %s for %s", type(event).__name__, key)
        if key == self._picker.current_key():
            self._show_state()

    def _show_state(self) -> None:
        """The selected device's screencast, from any UI: region, border,
        buttons, mic.  Sends nothing -- checkboxes are set under blocked
        signals."""
        key = self._picker.current_key()
        snap = self.dispatch(LcdSnapshot(key=key)) if key else None
        if snap is not None and not snap.ok:
            snap = None
        region = snap.screencast_region if snap is not None else None
        self._rect = snap.screencast_rect if snap is not None else None
        self._canvas = snap.screencast_canvas if snap is not None else None
        self._casting = region is not None
        log.info("_show_state: %s casting=%s region=%s rect=%s", key,
                 self._casting, region, self._rect)
        for field, value in zip(self._fields.values(),
                                self._rect or (0, 0, 0, 0), strict=True):
            with QSignalBlocker(field):
                field.setValue(value)
            field.setEnabled(self._rect is not None)
        if snap is not None:
            _set_quietly(self._hide_border, snap.screencast_hide_border)
        if region is not None:
            _set_quietly(self._audio, region[4])
            self._status.setText(f"Mirroring a region to {key}.  Click Stop to end.")
        elif key:
            self._status.setText("Not casting.  Start mirrors the region shown.")
        self._start_btn.setEnabled(not self._casting)
        self._stop_btn.setEnabled(self._casting)
        self._pick_btn.setEnabled(bool(key))
        self._hide_border.setEnabled(self._rect is not None)


def _set_quietly(box: QCheckBox, checked: bool) -> None:
    """Show a value from the App without emitting ``toggled``."""
    log.debug("_set_quietly: %s -> %s", box.text(), checked)
    box.blockSignals(True)
    box.setChecked(bool(checked))
    box.blockSignals(False)
