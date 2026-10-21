"""The playing audio visual's controls, for either window.

Under the gallery: one slider per adjustment the visual declares (its
``VisualInfo.adjustments`` -- TRCC Pipes has Growth, Thickness, Centre
picture and Fire), and for a visual that shows a centre picture, a button to
switch it on and off, one to choose another, and its Fire while it is on.
A slider changes the
visual LIVE when it is let go (``SetVisualSettings``): the visual keeps
playing.  What the controls show comes from ``ListVisuals(key=...)``, so
they show the same truth as every other UI, and follow a change another UI
made.

The window builds the Commands, as the RGB page's skins do: *sliders* turns
``{name: value}`` into its panel's ``SetVisualSettings``, *center* turns a
picture path into ``SetVisualCenter``.  Each window styles the widget.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from trcc.core.commands import Command
from trcc.core.ports import CommandBus
from trcc.core.results import Result
from trcc.ui.qt_background import dispatch_in_background

from ..core.models import VisualAdjustment, VisualInfo
from ..core.results import VisualsResult

log = logging.getLogger(__name__)

#: Slider steps between an adjustment's minimum and maximum.
_STEPS = 100
#: The picture button's thumbnail, pixels a side, and how far it fades
#: while the picture is off.
_THUMB = 40
_FADED = 0.3
#: What the picture dialog offers.
_PICTURES = "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


def _thumbnail(path: str, on: bool) -> QIcon:
    """The picture on its button -- faded while it is off, so the state
    shows at a glance (greying it showed nothing on a black-and-white
    picture)."""
    image = QImage(path)
    log.debug("_thumbnail: %s on=%s null=%s", path, on, image.isNull())
    if image.isNull():
        return QIcon()
    image = image.scaled(_THUMB, _THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    if not on:
        faded = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
        faded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(faded)
        painter.setOpacity(_FADED)
        painter.drawImage(0, 0, image)
        painter.end()
        image = faded
    return QIcon(QPixmap.fromImage(image))


class VisualControls(QWidget):
    """Sliders and the centre picture for the visual a panel plays."""

    def __init__(self, bus: CommandBus,
                 sliders: Callable[[dict[str, float]], Command[Any]],
                 center: Callable[[str], Command[Any]],
                 picture: Callable[[bool | None, float | None], Command[Any]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("VisualControls.__init__")
        self._bus, self._sliders, self._center = bus, sliders, center
        self._picture_command = picture
        self._adjustments: dict[str, VisualAdjustment] = {}
        self._picture = ""
        self._values: dict[str, QLabel] = {}
        self._rows = QGridLayout()
        self._rows.setColumnStretch(1, 1)
        self._hint = QLabel(self)
        self._hint.setWordWrap(True)
        # The picture: a button showing it that switches it on and off (the
        # user's design), one to choose another -- and, while it is on, its
        # Fire.
        self._toggle = QPushButton(self)
        self._toggle.setCheckable(True)
        self._toggle.setFixedSize(_THUMB + 8, _THUMB + 8)
        self._toggle.setIconSize(QSize(_THUMB, _THUMB))
        self._toggle.setToolTip("The centre picture: on or off")
        self._toggle.clicked.connect(self._on_toggle_picture)
        self._pick = QPushButton("Choose picture…", self)
        self._pick.setSizePolicy(QSizePolicy.Policy.Maximum,
                                 QSizePolicy.Policy.Fixed)
        self._pick.clicked.connect(self._on_pick_picture)
        self._fire_label = QLabel("Fire", self)
        self._fire = QSlider(Qt.Orientation.Horizontal, self)
        self._fire.setRange(0, _STEPS)
        self._fire.setTracking(False)
        self._fire.sliderMoved.connect(self._on_fire_moved)
        self._fire.valueChanged.connect(self._on_fire_let_go)
        self._fire_value = QLabel(self)
        pictures = QHBoxLayout()
        pictures.addWidget(self._toggle)
        pictures.addWidget(self._pick)
        pictures.addWidget(self._fire_label)
        pictures.addWidget(self._fire, stretch=1)
        pictures.addWidget(self._fire_value)
        pictures.addStretch(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._rows)
        root.addLayout(pictures)
        root.addWidget(self._hint)
        self.show_for(VisualsResult(ok=True))

    def show_for(self, listed: VisualsResult) -> None:
        """The controls of the visual *listed* says the panel plays."""
        info: VisualInfo | None = next(
            (v for v in listed.visuals if v.kind == listed.playing), None)
        log.info("VisualControls.show_for: %s %s", listed.playing or "-",
                 dict(listed.sliders))
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.deleteLater()
        self._adjustments, self._values = {}, {}
        self._picture = listed.center
        for row, adjustment in enumerate(info.adjustments if info else ()):
            self._add_slider(row, adjustment, listed.sliders.get(
                adjustment.name, adjustment.default))
        self._show_picture(listed, info is not None and info.uses_center)
        self._hint.setText(
            "Pick an audio visual above -- its sliders show here."
            if info is None else
            "Hearing nothing -- play some music.  If music IS playing, TRCC "
            "cannot hear this computer's sound output."
            if not listed.hearing else
            "Let go of a slider and the visual changes as it plays.")

    def _show_picture(self, listed: VisualsResult, centred: bool) -> None:
        """The picture row: its button (with the picture on it, pressed while
        it is on) when there is one, and its Fire while it is on."""
        has = centred and bool(listed.center)
        on = has and listed.center_shown
        log.debug("VisualControls._show_picture: has=%s on=%s fire=%.2f",
                  has, on, listed.center_fire)
        if has:
            self._toggle.setIcon(_thumbnail(listed.center, on))
        self._toggle.blockSignals(True)
        self._toggle.setChecked(on)
        self._toggle.blockSignals(False)
        self._fire.blockSignals(True)
        self._fire.setValue(round(listed.center_fire * _STEPS))
        self._fire.blockSignals(False)
        self._fire_value.setText(f"{listed.center_fire:.2f}")
        self._toggle.setVisible(has)
        self._pick.setVisible(centred)
        for widget in (self._fire_label, self._fire, self._fire_value):
            widget.setVisible(on)

    def _add_slider(self, row: int, adjustment: VisualAdjustment,
                    value: float) -> None:
        log.debug("VisualControls._add_slider: %s = %s", adjustment.name, value)
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(0, _STEPS)
        slider.setTracking(False)
        slider.setValue(round((value - adjustment.minimum)
                              / (adjustment.maximum - adjustment.minimum)
                              * _STEPS))
        slider.setProperty("adjustment", adjustment.name)
        slider.sliderMoved.connect(self._on_moved)
        slider.valueChanged.connect(self._on_let_go)
        shown = QLabel(f"{value:.2f}", self)
        self._adjustments[adjustment.name] = adjustment
        self._values[adjustment.name] = shown
        name = QLabel(adjustment.label, self)
        for column, widget in enumerate((name, slider, shown)):
            self._rows.addWidget(widget, row, column)
            # Shown now: Qt would show a new child on the next event-loop
            # pass, and a layout leaves hidden widgets out of its height --
            # the window sized the settings at 70 px for 214 px of sliders.
            widget.show()

    def _value(self, name: str, step: int) -> float:
        adjustment = self._adjustments[name]
        value = (adjustment.minimum
                 + (adjustment.maximum - adjustment.minimum) * step / _STEPS)
        log.debug("VisualControls._value: %s step %d -> %.3f", name, step, value)
        return value

    def _on_moved(self, step: int) -> None:
        """While dragging: the number follows the thumb."""
        name = str(self.sender().property("adjustment"))
        log.debug("VisualControls._on_moved: %s %d", name, step)
        self._values[name].setText(f"{self._value(name, step):.2f}")

    def _on_let_go(self, step: int) -> None:
        """Let go: the visual takes the value, live."""
        name = str(self.sender().property("adjustment"))
        value = self._value(name, step)
        log.info("VisualControls._on_let_go: %s = %.3f", name, value)
        self._values[name].setText(f"{value:.2f}")
        dispatch_in_background(self._bus, self._sliders({name: value}),
                               self._on_answer)

    def _on_pick_picture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Centre picture",
                                              self._picture_folder(), _PICTURES)
        log.info("VisualControls._on_pick_picture: %r", path)
        if path:
            dispatch_in_background(self._bus, self._center(path),
                                   self._on_answer)

    def _picture_folder(self) -> str:
        """Where the picker opens: the current picture's folder, else the
        user's Pictures -- never wherever TRCC happened to be started."""
        from PySide6.QtCore import QStandardPaths
        where = QStandardPaths.StandardLocation
        folder = (str(Path(self._picture).parent) if self._picture else
                  QStandardPaths.writableLocation(where.PicturesLocation)
                  or QStandardPaths.writableLocation(where.HomeLocation))
        log.debug("VisualControls._picture_folder: %s", folder)
        return folder

    def _on_toggle_picture(self, on: bool) -> None:
        """The picture's button: on or off -- the App keeps the picture."""
        log.info("VisualControls._on_toggle_picture: %s", on)
        dispatch_in_background(self._bus, self._picture_command(on, None),
                               self._on_answer)

    def _on_fire_moved(self, step: int) -> None:
        """While dragging: the number follows the thumb."""
        log.debug("VisualControls._on_fire_moved: %d", step)
        self._fire_value.setText(f"{step / _STEPS:.2f}")

    def _on_fire_let_go(self, step: int) -> None:
        """Let go: the picture burns that hot."""
        log.info("VisualControls._on_fire_let_go: %.2f", step / _STEPS)
        self._fire_value.setText(f"{step / _STEPS:.2f}")
        dispatch_in_background(self._bus,
                               self._picture_command(None, step / _STEPS),
                               self._on_answer)

    def _on_answer(self, result: Result) -> None:
        """A refusal shows here; success shows on the panel itself."""
        log.info("VisualControls._on_answer: ok=%s %s", result.ok,
                 result.message)
        if not result.ok:
            self._hint.setText(result.message)
