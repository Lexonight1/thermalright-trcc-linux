"""ScreencastPanel — mirror a region of the desktop to a connected LCD.

Workflow:

1.  Pick a device (the existing :class:`DevicePickerWidget`).
2.  Click "Choose region…" — opens :class:`RegionSelectOverlay` which
    freezes the screen and lets the user drag a rectangle.
3.  Pick an update interval (frames per second).
4.  Click Start — every tick the panel grabs the chosen region,
    encodes it for the device, and dispatches :class:`SendFrame`.
5.  Stop ends the loop; the device keeps the last frame until the
    user picks a new theme.

Honest scope:

* X11 + Wayland (via ``grim`` / ``scrot``) work today.  PipeWire-
  native capture lands later as a second :class:`ScreenCapture`
  adapter — the port is in place.
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

from PySide6.QtCore import Qt, QTimer
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

from ....adapters.screencast import QtScreenCapture
from ....core.commands import SendFrame
from ....core.registry import find_product
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    from ....core.models import ProductInfo

log = logging.getLogger(__name__)

_MIN_FPS = 1
_MAX_FPS = 30
_DEFAULT_FPS = 6


class ScreencastPanel(BasePanel):
    """Drive a captured screen region into the device on a timer."""

    def _setup_ui(self) -> None:
        self._capture = QtScreenCapture()
        self._region: tuple[int, int, int, int] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        # ── Device picker ─────────────────────────────────────────────
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
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
        self._fps.valueChanged.connect(
            lambda v: self._fps_label.setText(f"{v} fps"),
        )
        self._fps.valueChanged.connect(self._on_fps_changed)
        fps_row = QHBoxLayout()
        fps_row.addWidget(self._fps, stretch=1)
        fps_row.addWidget(self._fps_label)

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
        self._region = (x, y, w, h)
        self._region_label.setText(
            f"{w} × {h} at ({x}, {y})",
        )

    def _on_region_cancelled(self) -> None:
        log.info("_on_region_cancelled")
        self._status.setText("Region selection cancelled.")

    # ── FPS plumbing ─────────────────────────────────────────────────

    def _on_fps_changed(self, value: int) -> None:
        log.info("_on_fps_changed: value=%s", value)
        if self._timer.isActive():
            self._timer.setInterval(self._tick_interval_ms(value))

    def _tick_interval_ms(self, fps: int) -> int:
        return max(33, int(1000 / max(_MIN_FPS, fps)))

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
        if self._product_for(key) is None:
            self._status.setText(
                f"No product info for {key} — that key isn't in the "
                "registry, so we don't know the target resolution.",
            )
            return
        self._timer.setInterval(self._tick_interval_ms(self._fps.value()))
        self._timer.start()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pick_btn.setEnabled(False)
        self._status.setText(
            f"Mirroring region to {key} at {self._fps.value()} fps.  "
            "Click Stop to end.",
        )

    def _on_stop(self) -> None:
        log.info("_on_stop")
        self._timer.stop()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pick_btn.setEnabled(True)
        self._status.setText("Screencast stopped.")

    def _on_key_changed(self, _key: str) -> None:
        log.info("_on_key_changed: _key=%s", _key)
        if self._timer.isActive():
            # Changing device mid-screencast: stop cleanly to avoid
            # sending to whichever device the user just deselected.
            self._on_stop()

    # ── Tick ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        key = self._picker.current_key()
        if not key or self._region is None:
            self._on_stop()
            return
        product = self._product_for(key)
        if product is None:
            self._on_stop()
            return

        x, y, w, h = self._region
        try:
            frame = self._capture.grab_region(x, y, w, h)
        except OSError as e:
            log.warning("Screencast capture failed: %s", e)
            self._status.setText(f"Capture failed: {e}")
            self._on_stop()
            return

        try:
            payload = self.app.display.build_screencast_frame(
                info=product, frame=frame,
            )
        except Exception as e:  # last-ditch — keep the GUI alive
            log.exception("Screencast encode failed")
            self._status.setText(f"Encode failed: {e}")
            self._on_stop()
            return

        result = self.dispatch(SendFrame(key=key, data=payload))
        if not result.ok:
            self._status.setText(f"Send failed: {result.message}")
            self._on_stop()

    # ── Helpers ──────────────────────────────────────────────────────

    def _product_for(self, key: str) -> ProductInfo | None:
        device = self.app.devices.get(key)
        if device is not None:
            return device.info
        try:
            vid_s, pid_s = key.split(":")
            vid = int(vid_s, 16)
            pid = int(pid_s, 16)
        except ValueError:
            return None
        return find_product(vid, pid)
