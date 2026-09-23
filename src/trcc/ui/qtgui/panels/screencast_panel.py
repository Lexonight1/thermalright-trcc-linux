"""ScreencastPanel — mirror a region of the desktop to a connected LCD.

Workflow:

1.  Pick a device (the existing :class:`DevicePickerWidget`).
2.  Click "Choose region…" — opens :class:`RegionSelectOverlay` which
    freezes the screen and lets the user drag a rectangle.
3.  Pick an update interval (frames per second), and optionally tick
    "Draw a spectrum from the microphone" — it applies at the next Start
    and re-issues the session when toggled mid-cast.
4.  Click Start — every tick the panel grabs the chosen region,
    encodes it for the device, and dispatches :class:`SendFrame`.
5.  Stop ends the loop; the device keeps the last frame until the
    user picks a new theme.

Honest scope:

* X11 and Wayland both capture through ``Platform.screen_capture()``:
  Qt's grab and the X11 grabbers on X11, the xdg-portal PipeWire stream
  with the desktop's own tool behind it on Wayland.
* The panel runs the timer locally; it does not persist across
  restarts.  Screencast state is intentionally transient — users
  who want a permanent mirror are an unusual case.
* Background mode is left to the user — the configuration panel
  exposes ``transparent`` which makes the captured frame the only
  visible layer.  Picking ``theme`` keeps the theme's background and
  composites the screencast on top, which is rarely what people
  want.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from ....core.commands import (
    DeviceState,
    StartScreencast,
    StartScreencastDriver,
    StopScreencast,
    StopScreencastDriver,
)
from ....core.geometry import lock_region_to_panel
from ....core.models import SCREENCAST_TICK_S
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    pass

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


class ScreencastPanel(BasePanel):
    """Drive a captured screen region into the device on a timer."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._region: tuple[int, int, int, int] | None = None
        #: The device currently being cast, or None.  Replaces
        #: ``self._timer.isActive()`` now that the cadence lives in the
        #: driver rather than in this panel.
        self._casting_key: str | None = None

        # ── Device picker ─────────────────────────────────────────────
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )
        self._picker.key_changed.connect(self._on_key_changed)

        # ── Region picker ─────────────────────────────────────────────
        self._region_label = QLabel("No region selected.", self)
        self._region_label.setStyleSheet("color: #aaa;")
        self._pick_btn = QPushButton("Choose region…", self)
        self._pick_btn.clicked.connect(self._on_pick_region)

        region_row = QHBoxLayout()
        region_row.addWidget(self._pick_btn)
        region_row.addWidget(self._region_label, stretch=1)

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
        tips_box = QGroupBox("Tips", self)
        tips_layout = QVBoxLayout(tips_box)
        self._transparent_hint = QCheckBox(
            "Switch background mode to 'transparent' before starting "
            "(recommended).",
            tips_box,
        )
        self._transparent_hint.setChecked(True)
        self._transparent_hint.setToolTip(
            "Otherwise the theme's background composites under the "
            "screencast and the result is muddy.  Change this on the "
            "Configuration panel.",
        )
        tips_layout.addWidget(self._transparent_hint)

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

    # ── Region picking ───────────────────────────────────────────────

    def _on_pick_region(self) -> None:
        log.info("_on_pick_region")
        from ..region_overlay import RegionSelectOverlay

        overlay = RegionSelectOverlay(self)
        overlay.region_selected.connect(self._on_region_selected)
        overlay.cancelled.connect(self._on_region_cancelled)
        overlay.show()

    def _on_region_selected(
        self, x: int, y: int, w: int, h: int,
    ) -> None:
        log.info("_on_region_selected: x=%s y=%s w=%s h=%s", x, y, w, h)
        # Fit the drag to the panel's shape.  The C# oracle's viewfinder is
        # PRE-SIZED from the panel and the user picks position only, so a
        # free-form rectangle loses the guarantee the original gave: what you
        # framed is what appears.  Before this, qtgui dragged unconstrained and
        # the render pipeline squashed the result.
        key = self._picker.current_key()
        resolution = None
        if key:
            state = self.dispatch(DeviceState(key=key))
            resolution = state.resolution if state.ok else None
        x, y, w, h = lock_region_to_panel(resolution, x, y, w, h)
        self._region = (x, y, w, h)
        suffix = "" if resolution else "  (no device picked — not fitted)"
        self._region_label.setText(
            f"{w} × {h} at ({x}, {y}){suffix}",
        )

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
        if self._casting_key:
            # Re-registering replaces the task under the same key, so this is
            # how the cadence changes mid-cast.
            self.dispatch(StartScreencastDriver(
                key=self._casting_key, interval_s=self._fps_interval_s(),
            ))

    def _fps_interval_s(self) -> float:
        """The slider's fps as the driver's tick interval, in seconds."""
        log.debug("_fps_interval_s")
        return max(0.033, 1.0 / max(_MIN_FPS, self._fps.value()))

    # ── Microphone ───────────────────────────────────────────────────

    def _on_audio_toggled(self, enabled: bool) -> None:
        """Mic on/off, mid-cast included.

        Re-issuing the session with the new flag is how the flag changes,
        exactly as re-registering the driver is how the cadence changes
        (:meth:`_on_fps_changed`).  The flag lives in ``screencast_region``'s
        fifth element — the one persisted truth — so there is no second piece
        of state to keep in step, and ``_sync_audio`` in the Command holds the
        microphone open for exactly as long as some session wants it.

        Off-session it is just the checkbox; :meth:`_on_start` reads it.
        """
        log.info("_on_audio_toggled: enabled=%s", enabled)
        key = self._casting_key
        if not key or self._region is None:
            log.debug("_on_audio_toggled: no live session — the checkbox "
                      "applies at the next Start")
            return
        x, y, w, h = self._region
        result = self.dispatch(StartScreencast(
            key=key, x=x, y=y, w=w, h=h, audio=enabled,
        ))
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
        if self._region is None:
            self._status.setText(
                "Choose a region first — click 'Choose region…' above.",
            )
            return
        x, y, w, h = self._region
        started = self.dispatch(StartScreencast(
            key=key, x=x, y=y, w=w, h=h, audio=self._audio.isChecked(),
        ))
        if not started.ok:
            self._status.setText(started.message)
            return
        # The driver replaces this panel's own QTimer + capture chain.  It
        # was a THIRD screencast driver in the tree, beside the gui skin's and
        # the one core grew for headless clients, and the only one that never
        # persisted its region — so nothing else could tell a cast was running.
        driving = self.dispatch(StartScreencastDriver(
            key=key, interval_s=self._fps_interval_s(),
        ))
        if not driving.ok:
            self._status.setText(driving.message)
            self.dispatch(StopScreencast(key=key))
            return
        self._casting_key = key
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pick_btn.setEnabled(False)
        self._status.setText(
            f"Mirroring region to {key} at {self._fps.value()} fps.  "
            "Click Stop to end.",
        )

    def _on_stop(self) -> None:
        log.info("_on_stop")
        key = self._casting_key
        if key:
            self.dispatch(StopScreencastDriver(key=key))
            self.dispatch(StopScreencast(key=key))
        self._casting_key = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pick_btn.setEnabled(True)
        self._status.setText("Screencast stopped.")

    def _on_key_changed(self, _key: str) -> None:
        log.info("_on_key_changed: _key=%s", _key)
        if self._casting_key:
            # Changing device mid-screencast: stop cleanly so we never send to
            # whichever device the user just deselected.
            self._on_stop()

    # ── Tick ─────────────────────────────────────────────────────────

    # ── Helpers ──────────────────────────────────────────────────────
