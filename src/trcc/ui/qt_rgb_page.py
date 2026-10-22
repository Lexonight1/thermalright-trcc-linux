"""The RGB page both windows show -- the view, not the Commands.

The devices on the left (Memory, Motherboard, OpenRGB, and "Find lights");
the one picked on the right -- its lights, its source (Leave alone /
Built-in effect / Follow a device) with only the controls that source and that
effect take, and Apply, which changes that device only.  ``presentation.rgb_page.RgbPage`` makes every choice; this draws it.

Template Method, as ``qt_ram_access.RamAccessRow``: each skin's subclass names
the Commands (``_lights`` .. ``_apply``) and builds its RAM-access row, so the
Commands are each UI's own and the parity gates see both windows reach them.
Find and Apply run off the UI thread -- the RAM probe reads the chipset bus,
and an effect waits on the stick -- and the window stays usable meanwhile.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QImage,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.commands import Command
from ..core.events import RgbFollowChanged
from ..core.led_models import linked_preview, stretch
from ..core.logs import per_frame
from ..core.models import (
    MAX_ARGB_LEDS,
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    LinkedEffect,
    RamEffect,
    RamEffectSettings,
)
from ..core.ports import CommandBus
from ..core.results import DeviceEntry, Result, RgbFollowResult, RgbLightsResult
from ..services.effect_clock import LIGHT_GRADIENTS
from ..services.effect_frames import APPROXIMATE, LEDS, effect_frame
from ..services.follow_colors import Rgb, strip_spans
from .presentation.rgb_page import (
    BLINK_TIP,
    BOARD_NOTE,
    COLORS_LABELS,
    DEVICE_KINDS,
    DEVICE_LABELS,
    DIRECTION_LABELS,
    EFFECT_LABELS,
    LEDS_TIP,
    LINKED_CHOICES,
    MAPPING_LABELS,
    SOURCE_LABELS,
    SPEED_LABELS,
    ApplyPlan,
    EffectPicks,
    FindLights,
    HeaderLeds,
    LightRow,
    RgbDevice,
    RgbPage,
    RgbSource,
    Strip,
    effect_label,
    strip_legend,
)
from .qt_background import dispatch_in_background
from .qt_ram_access import RamAccessRow

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

ACCESS_OFF_TEXT = (
    "TRCC needs permission to reach the memory's lighting chips.  Nothing is "
    "changed until you turn it on, and you can turn it off here again.")
WORKING = "Working..."
#: The devices column: "Motherboard (3) · on", the longest button, and no
#: wider -- 240 left a wide empty band (his note, 2026-10-21).
DEVICES_WIDTH = 180
#: The follow picker: room for a device's name, not the whole panel.
FOLLOW_PICKER_WIDTH = 300
#: The effect picker: "Rainbow Wave", the longest name, with room to spare.
EFFECT_PICKER_WIDTH = 220
#: How wide the line explaining the picked choice runs -- the form's width.
HINT_WIDTH = 520
#: The most a device's lights take of the panel before they scroll.
LIGHTS_HEIGHT = 180
#: How often the follow status is re-read while frames reach the preview.
STATUS_EVERY_S = 2.0


def _clear(layout: QVBoxLayout) -> None:
    """Drop every widget *layout* holds."""
    log.debug("_clear: %d item(s)", layout.count())
    while (item := layout.takeAt(0)) is not None:
        if (widget := item.widget()) is not None:
            widget.hide()              # gone now, not when Qt deletes it
            widget.deleteLater()


def _muted(text: str = "") -> QLabel:
    """A wrapped grey note."""
    log.debug("_muted: %r", text[:40])
    label = QLabel(text)
    label.setObjectName("rgb-muted")
    label.setWordWrap(True)
    return label


def _title(text: str) -> QLabel:
    """A box's title -- OpenRGB's, the motherboard's."""
    log.debug("_title: %s", text)
    label = QLabel(text)
    label.setObjectName("rgb-title")
    return label


def _heading(text: str) -> QLabel:
    log.debug("_heading: %s", text)
    label = QLabel(text)
    label.setObjectName("rgb-heading")
    return label


class _LightCard(QFrame):
    """One light: its tick (does it take part?), its name and where it is."""

    def __init__(self, row: LightRow, toggled: Callable[[str, bool], None],
                 leds_set: Callable[[int, int], None] | None = None,
                 blink: Callable[[int], None] | None = None) -> None:
        super().__init__()
        log.debug("_LightCard.__init__: %s checked=%s", row.ref, row.checked)
        self.setObjectName("rgb-card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        if row.checkable:
            self.check = QCheckBox(row.title, self)
            self.check.setObjectName("rgb-light")
            self.check.setChecked(row.checked)
            self.check.toggled.connect(partial(toggled, row.ref))
            layout.addWidget(self.check)
        else:
            title = QLabel(row.title, self)
            title.setObjectName("rgb-light")
            layout.addWidget(title)
        layout.addWidget(_muted(row.detail))
        if row.header is not None and leds_set is not None:
            layout.addLayout(self._leds_box(row, leds_set, blink))

    def _leds_box(self, row: LightRow, leds_set: Callable[[int, int], None],
                  blink: Callable[[int], None] | None) -> QHBoxLayout:
        """How many LEDs the header drives -- the board cannot say."""
        log.debug("_LightCard._leds_box: header %s, %d LED(s)", row.header,
                  row.leds)
        header = row.header or 0
        box = QSpinBox(self)
        box.setObjectName("rgb-leds")
        box.setRange(0, MAX_ARGB_LEDS)
        box.setValue(row.leds)
        box.setToolTip(LEDS_TIP)
        box.editingFinished.connect(
            partial(self._on_leds, box, header, row.leds, leds_set))
        line = QHBoxLayout()
        line.addWidget(_muted("LEDs on this header"))
        line.addWidget(box)
        if blink is not None:
            button = QPushButton("Blink", self)
            button.setObjectName("rgb-blink")
            button.setToolTip(BLINK_TIP)
            button.clicked.connect(partial(blink, header))
            line.addWidget(button)
        line.addStretch(1)
        return line

    @staticmethod
    def _on_leds(box: QSpinBox, header: int, was: int,
                 leds_set: Callable[[int, int], None]) -> None:
        """An edit finished: a new count goes to the App, an unchanged one
        does not."""
        log.info("_LightCard._on_leds: header %d %d -> %d", header, was,
                 box.value())
        if box.value() != was:
            leds_set(header, box.value())


def _scrolling(parent: QWidget, column: QWidget) -> QScrollArea:
    """*column* in a frameless scroll area: a device's lights outgrow the
    panel -- more sticks, a board's headers -- and must not push the
    controls below off the page."""
    log.debug("_scrolling")
    area = QScrollArea(parent)
    area.setObjectName("rgb-lights-scroll")
    area.setWidget(column)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # As tall as its lights, up to the cap: a fixed small box hid the second
    # stick behind a scroll bar with half the page empty below.
    area.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
    area.setMaximumHeight(LIGHTS_HEIGHT)
    return area


class _DeviceList(QWidget):
    """The left column: the devices, and how to find their lights."""

    picked = Signal(object)          # RgbDevice
    find_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        log.debug("_DeviceList.__init__")
        self._group = QButtonGroup(self)
        self.buttons: dict[RgbDevice, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(_heading("Devices"))
        for device, label in DEVICE_LABELS.items():
            button = QPushButton(label, self)
            button.setObjectName("rgb-device")
            button.setCheckable(True)
            button.clicked.connect(partial(self._on_picked, device))
            self._group.addButton(button)
            self.buttons[device] = button
            layout.addWidget(button)
        layout.addSpacing(12)
        self.find_button = QPushButton("Find lights", self)
        self.find_button.setObjectName("rgb-button")
        self.find_button.clicked.connect(self.find_clicked)
        layout.addWidget(self.find_button)
        layout.addStretch(1)

    def _on_picked(self, device: RgbDevice, _checked: bool = False) -> None:
        """A device button: ``clicked`` passes its checked state, which is
        not the device."""
        log.info("_DeviceList._on_picked: %s", device.value)
        self.picked.emit(device)

    def show_page(self, page: RgbPage) -> None:
        """Which device is shown, and how many lights each has."""
        log.debug("_DeviceList.show_page: %s", page.device.value)
        for device, button in self.buttons.items():
            count = len(page.rows(DEVICE_KINDS[device][0]))
            following = " · on" if page.follower is device else ""
            button.setText(f"{DEVICE_LABELS[device]} ({count}){following}")
            button.setChecked(device is page.device)

    def set_busy(self, busy: bool) -> None:
        log.debug("_DeviceList.set_busy: %s", busy)
        self.find_button.setEnabled(not busy)


class _LightsColumn(QWidget):
    """The selected device's lights: a card each, with what that device
    needs beside them -- the RAM-access switch, OpenRGB's address."""

    leds_set = Signal(int, int)
    blinked = Signal(int)

    def __init__(self, page: RgbPage, access: RamAccessRow,
                 changed: Callable[[], None]) -> None:
        super().__init__()
        log.debug("_LightsColumn.__init__")
        self._page, self._changed = page, changed
        self._access_card = QFrame(self)
        self._access_card.setObjectName("rgb-card")
        card = QVBoxLayout(self._access_card)
        self._access_title = QLabel("RAM lighting is off")
        self._access_title.setObjectName("rgb-title")
        self._access_text = _muted(ACCESS_OFF_TEXT)
        card.addWidget(self._access_title)
        card.addWidget(self._access_text)
        card.addWidget(access)
        self._address_row = QWidget(self)
        address = QHBoxLayout(self._address_row)
        address.setContentsMargins(0, 0, 0, 0)
        self._address = QLineEdit(self)
        self._address.setObjectName("rgb-address")
        self._address.setToolTip("OpenRGB's SDK server, host:port")
        self._address.textEdited.connect(self._on_address_edited)
        address.addWidget(_muted("SDK server"))
        address.addWidget(self._address, 1)
        self._board_note = _muted(BOARD_NOTE)
        self._note = _muted()
        self._cards = QVBoxLayout()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._address_row)
        layout.addWidget(self._board_note)
        layout.addLayout(self._cards)
        layout.addWidget(self._note)
        layout.addWidget(self._access_card)
        layout.addStretch(1)

    def show_page(self) -> None:
        """Lay out the shown device's lights."""
        page = self._page
        device = page.device
        log.debug("_LightsColumn.show_page: %s reachable=%s", device.value,
                  page.ram_reachable)
        _clear(self._cards)
        board = device is RgbDevice.MOTHERBOARD
        for kind in DEVICE_KINDS[device]:
            for row in page.rows(kind):
                card = _LightCard(row, self._on_light_toggled,
                                  self.leds_set.emit if board else None,
                                  self.blinked.emit if board else None)
                self._cards.addWidget(card)
                # Shown now, not on Qt's next pass: the box measures its
                # lights straight after this, and a card not yet shown is
                # not counted.
                card.show()
        self._note.setText(page.note)
        self._note.setVisible(bool(page.note))
        memory = device is RgbDevice.MEMORY
        reachable = page.ram_reachable
        self._access_card.setVisible(memory)
        # Off: the card explains and offers the switch.  On: just the switch.
        self._access_title.setVisible(not reachable)
        self._access_text.setVisible(not reachable)
        self._address_row.setVisible(device is RgbDevice.OPENRGB)
        self._address.setText(page.address)
        self._board_note.setVisible(board)

    def _on_light_toggled(self, ref: str, checked: bool) -> None:
        log.info("_LightsColumn._on_light_toggled: %s %s", ref, checked)
        self._page.set_checked(ref, checked)
        self._changed()

    def _on_address_edited(self, text: str) -> None:
        log.info("_LightsColumn._on_address_edited: %r", text)
        self._page.address = text
        self._changed()


class _ChoiceButtons(QWidget):
    """A row of exclusive buttons, one per value -- speed, direction."""

    def __init__(self, labels: Mapping[Any, str],
                 picked: Callable[[Any], None]) -> None:
        super().__init__()
        log.debug("_ChoiceButtons.__init__: %s", list(labels))
        self._group = QButtonGroup(self)
        self._buttons: dict[object, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for value, text in labels.items():
            button = QPushButton(text, self)
            button.setObjectName("rgb-choice")
            button.setCheckable(True)
            button.clicked.connect(partial(picked, value))
            self._group.addButton(button)
            self._buttons[value] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def show_values(self, values: Sequence[object], picked: object) -> None:
        """Show only *values*, with *picked* pressed."""
        log.debug("_ChoiceButtons.show_values: %s picked=%s", values, picked)
        for value, button in self._buttons.items():
            button.setVisible(value in values)
            button.setChecked(value == picked)


class _EffectForm(QWidget):
    """The controls of one built-in effect -- only those it takes."""

    def __init__(self, picks: EffectPicks,
                 changed: Callable[[], None]) -> None:
        super().__init__()
        log.debug("_EffectForm.__init__")
        self._picks, self._changed = picks, changed
        self._effect = QComboBox(self)
        # Wide enough for the longest name, not the whole form (his note).
        self._effect.setFixedWidth(EFFECT_PICKER_WIDTH)
        self._effect.activated.connect(self._on_effect)
        self._swatches = [QPushButton(self) for _ in picks.colors]
        for index, swatch in enumerate(self._swatches):
            swatch.setObjectName("rgb-swatch")
            swatch.setFixedSize(34, 26)
            swatch.clicked.connect(partial(self._on_swatch, index))
        self._random = QCheckBox("random colours", self)
        self._random.toggled.connect(self._on_random)
        self._takes = _muted()
        self._speed = _ChoiceButtons(SPEED_LABELS, self._on_speed)
        self._direction = _ChoiceButtons(DIRECTION_LABELS, self._on_direction)
        self._brightness = QSlider(Qt.Orientation.Horizontal, self)
        self._brightness.setRange(0, 255)
        self._brightness.setFixedWidth(300)
        self._brightness.valueChanged.connect(self._on_brightness)
        colors = QHBoxLayout()
        for swatch in self._swatches:
            colors.addWidget(swatch)
        colors.addWidget(self._random)
        colors.addWidget(self._takes)
        colors.addStretch(1)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)    # controls close to their labels
        self._grid.setVerticalSpacing(14)
        self._rows: dict[str, tuple[QLabel, QWidget]] = {}
        for name, widget in (("Effect", self._effect), ("Colours", colors),
                             ("Speed", self._speed),
                             ("Direction", self._direction),
                             ("Brightness", self._brightness)):
            self._add_row(name, widget)
        self._grid.setColumnStretch(1, 1)

    def _add_row(self, name: str, widget: QWidget | QHBoxLayout) -> None:
        log.debug("_EffectForm._add_row: %s", name)
        row = self._grid.rowCount()
        label = QLabel(name, self)
        self._grid.addWidget(label, row, 0)
        if isinstance(widget, QHBoxLayout):
            holder = QWidget(self)
            holder.setLayout(widget)
            widget.setContentsMargins(0, 0, 0, 0)
            widget = holder
        self._grid.addWidget(widget, row, 1)
        self._rows[name] = (label, widget)

    def _show_row(self, name: str, shown: bool) -> None:
        log.debug("_EffectForm._show_row: %s %s", name, shown)
        for widget in self._rows[name]:
            widget.setVisible(shown)

    def show_page(self) -> None:
        """The picked effect's controls, set to the page's values."""
        page = self._picks
        traits = page.traits
        log.debug("_EffectForm.show_page: %s %s linked=%s", page.effect.value,
                  traits, page.linked)
        self._fill_picker()
        effect = page.light_effect()
        linked = next((key for key, choice in LINKED_CHOICES.items()
                       if choice == effect), None)
        self._effect.setCurrentIndex(self._effect.findData(linked or page.effect))
        if linked is not None:           # a reading picks the colour
            for name in ("Colours", "Speed", "Direction", "Brightness"):
                self._show_row(name, False)
            return
        for swatch, (r, g, b) in zip(self._swatches, page.colors, strict=True):
            swatch.setStyleSheet(f"background-color: rgb({r}, {g}, {b});")
        for index, swatch in enumerate(self._swatches):
            swatch.setVisible(index < traits.colors)
        self._random.setVisible(traits.random)
        self._random.blockSignals(True)
        self._random.setChecked(page.random_colors)
        self._random.blockSignals(False)
        self._takes.setText(f"(this effect takes {traits.colors})"
                            if traits.colors > 1 else "")
        self._show_row("Colours", traits.colors > 0)
        self._show_row("Speed", traits.speed)
        self._speed.show_values(tuple(EffectSpeed), page.speed)
        self._show_row("Direction", bool(traits.directions))
        self._direction.show_values(traits.directions, page.shown_direction)
        self._show_row("Brightness", traits.brightness)
        self._brightness.blockSignals(True)
        self._brightness.setValue(page.brightness)
        self._brightness.blockSignals(False)

    def _fill_picker(self) -> None:
        """The effects, and the linked colours where the device takes them."""
        items = [(label, effect) for effect, label in EFFECT_LABELS.items()]
        if self._picks.linked_allowed:
            items += [(effect_label(choice), key)
                      for key, choice in LINKED_CHOICES.items()]
        log.debug("_EffectForm._fill_picker: %d", len(items))
        if [self._effect.itemData(i) for i in range(self._effect.count())] == [
                data for _label, data in items]:
            return
        self._effect.clear()
        for label, data in items:
            self._effect.addItem(label, data)

    def _on_effect(self, index: int) -> None:
        # Qt hands item data back as a plain string, not the enum stored.
        data = str(self._effect.itemData(index))
        log.info("_EffectForm._on_effect: %s", data)
        if (linked := LINKED_CHOICES.get(data)) is not None:
            self._picks.take(linked)
        else:
            self._picks.set_effect(RamEffect(data))
        self.show_page()
        self._changed()

    def _on_swatch(self, index: int) -> None:
        r, g, b = self._picks.colors[index]
        log.info("_EffectForm._on_swatch: %d from %s", index, (r, g, b))
        color = QColorDialog.getColor(QColor(r, g, b), self, "Pick a colour")
        if not color.isValid():
            log.info("_EffectForm._on_swatch: cancelled")
            return
        self._picks.set_color(index, (color.red(), color.green(), color.blue()))
        self.show_page()
        self._changed()

    def _on_random(self, checked: bool) -> None:
        log.info("_EffectForm._on_random: %s", checked)
        self._picks.random_colors = checked
        self._changed()

    def _on_speed(self, speed: object) -> None:
        log.info("_EffectForm._on_speed: %s", speed)
        self._picks.speed = EffectSpeed(speed)
        self._changed()

    def _on_direction(self, direction: object) -> None:
        log.info("_EffectForm._on_direction: %s", direction)
        self._picks.direction = EffectDirection(direction)
        self._changed()

    def _on_brightness(self, value: int) -> None:
        log.debug("_EffectForm._on_brightness: %d", value)
        self._picks.brightness = value
        self._changed()


class _FollowPreview(QWidget):
    """What the lights follow, and each light's LEDs in the colours the App
    last sent it: an LCD's live picture with the strips the lights take
    outlined, or an LED cooler's own colours.

    The strips are what the App reports sending (``RgbFollowSent``), never
    sampled here: the App samples the picture under the overlay, which a
    window never sees.  Paints only what it was handed -- a frame asks the
    page nothing.
    """

    PICTURE = 220
    CELL = (22, 16)
    STRIP_GAP = 40
    COOLER_ROW = 10              # the cooler's LEDs drawn ten to a row

    def __init__(self) -> None:
        super().__init__()
        log.debug("_FollowPreview.__init__")
        self._image: QImage | None = None
        self._colors: tuple[Rgb, ...] = ()
        self._sent: tuple[tuple[Rgb, ...], ...] = ()
        self._lights: tuple[str, ...] = ()
        self._columns = 1
        self.follows_picture = True
        self.setFixedHeight(self.PICTURE + 26)

    def set_layout(self, lights: tuple[str, ...], columns: int,
                   picture: bool) -> None:
        """The lights drawn, the columns the picture is cut into, and whether
        a picture leads (else a cooler)."""
        log.debug("_FollowPreview.set_layout: %s %d picture=%s", lights,
                  columns, picture)
        self._lights, self._columns = lights, columns
        self.follows_picture = picture
        self.update()

    def show_frame(self, image: QImage) -> None:
        frame_log.debug("_FollowPreview.show_frame: %dx%d", image.width(),
                        image.height())
        self._image = image
        self.update()

    def show_colors(self, colors: Sequence[Rgb]) -> None:
        frame_log.debug("_FollowPreview.show_colors: %d", len(colors))
        self._colors = tuple(colors)
        self.update()

    def show_sent(self, columns: Sequence[Sequence[Rgb]]) -> None:
        frame_log.debug("_FollowPreview.show_sent: %d column(s)", len(columns))
        self._sent = tuple(tuple(column) for column in columns)
        self.update()

    def clear_sent(self) -> None:
        log.debug("_FollowPreview.clear_sent")
        self._sent = ()
        self.update()

    def clear(self) -> None:
        log.debug("_FollowPreview.clear")
        self._image, self._colors, self._sent = None, (), ()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        frame_log.debug("_FollowPreview.paintEvent: picture=%s sent=%d",
                        self.follows_picture, len(self._sent))
        p = QPainter(self)
        right = (self._paint_picture(p) if self.follows_picture
                 else self._paint_cooler(p))
        self._paint_strips(p, right + self.STRIP_GAP)
        p.end()

    def _paint_waiting(self, p: QPainter, text: str) -> None:
        frame_log.debug("_FollowPreview._paint_waiting: %s", text)
        frame = QRect(0, 0, self.PICTURE, self.PICTURE)
        p.setPen(QPen(QColor("#5a5a62"), 1, Qt.PenStyle.DashLine))
        p.drawRect(frame.adjusted(0, 0, -1, -1))
        p.setPen(QColor("#9a9aa2"))
        p.drawText(frame, Qt.AlignmentFlag.AlignCenter
                   | Qt.TextFlag.TextWordWrap, text)

    def _paint_picture(self, p: QPainter) -> int:
        """The LCD's frame with the strips the lights take outlined; its
        right edge."""
        frame_log.debug("_FollowPreview._paint_picture: %d column(s)",
                        self._columns)
        if self._image is None or self._image.isNull():
            self._paint_waiting(p, "Waiting for the device's\nnext frame...")
            return self.PICTURE
        picture = self._image.scaled(
            self.PICTURE, self.PICTURE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        p.drawImage(QRect(0, 0, picture.width(), picture.height()), picture)
        p.setPen(QPen(QColor("#d9b45a"), 1, Qt.PenStyle.DashLine))
        for x, width in strip_spans(picture.width(), self._columns):
            p.drawRect(QRect(x, 0, width, picture.height()).adjusted(0, 0, -1, -1))
        return picture.width()

    def _paint_cooler(self, p: QPainter) -> int:
        """The cooler's LEDs, ten to a row; the right edge."""
        frame_log.debug("_FollowPreview._paint_cooler: %d LED(s)",
                        len(self._colors))
        if not self._colors:
            self._paint_waiting(p, "Waiting for the cooler's\ncolours...")
            return self.PICTURE
        size = self.PICTURE // self.COOLER_ROW
        for i, rgb in enumerate(self._colors):
            row, col = divmod(i, self.COOLER_ROW)
            p.fillRect(QRect(col * size + 2, row * size + 2, size - 4,
                             size - 4), QColor(*rgb))
        return self.PICTURE

    def _paint_strips(self, p: QPainter, left: int) -> None:
        """One strip per light: light *i* in the column the App sent it --
        dark until the App has sent something."""
        frame_log.debug("_FollowPreview._paint_strips: %d", len(self._lights))
        width, height = self.CELL
        sent = self._sent
        for i, _name in enumerate(self._lights):
            x = left + i * (width + 24)
            leds = (stretch(list(sent[i % len(sent)]), RgbPage.PREVIEW_ROWS)
                    if sent else [None] * RgbPage.PREVIEW_ROWS)
            for row, rgb in enumerate(leds):
                p.fillRect(QRect(x, 4 + row * (height + 4), width, height),
                           QColor(*rgb) if rgb else QColor("#2b2b31"))
            p.setPen(self.palette().windowText().color())   # either skin
            p.drawText(QRect(x - 12, self.PICTURE + 4, width + 24, 20),
                       Qt.AlignmentFlag.AlignCenter, chr(ord("A") + i))


Strips = Sequence[Strip]


class _EffectStrips(QWidget):
    """Each stick's LEDs running its effect -- TRCC's drawing of it.

    Paints the strips it was last given and asks the page nothing, so a
    frame costs only the drawing.  Animates only while on screen with an
    effect to run; a stick whose effect is not known stays grey and still.
    """

    CELL = (22, 14)
    GAP = 24                     # between strips: room for each letter
    TICK_MS = 50                 # 20 frames a second: smooth, and cheap

    def __init__(self) -> None:
        super().__init__()
        log.debug("_EffectStrips.__init__")
        self._strips: Strips = ()
        self._start = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(LEDS * (self.CELL[1] + 4) + 26)

    @property
    def animating(self) -> bool:
        running = self._timer.isActive()
        log.debug("_EffectStrips.animating: %s", running)
        return running

    def set_strips(self, strips: Strips) -> None:
        log.debug("_EffectStrips.set_strips: %d -> %d", len(self._strips),
                  len(strips))
        self._strips = tuple(strips)
        # As wide as the strips: the legend sits beside them, not past a gap.
        self.setFixedWidth(max(1, len(self._strips)) * (self.CELL[0] + self.GAP))
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        # Only what moves: a Static or linked strip is drawn once, not 20
        # times a second.
        run = self.isVisible() and any(
            isinstance(drawn, RamEffectSettings) and drawn.effect is not RamEffect.STATIC
            for drawn in (strip.drawn for strip in self._strips))
        log.debug("_EffectStrips._sync_timer: %s", "run" if run else "still")
        if run and not self._timer.isActive():
            self._timer.start()
        elif not run:
            self._timer.stop()

    def showEvent(self, event: QShowEvent) -> None:
        log.debug("_EffectStrips.showEvent")
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event: QHideEvent) -> None:
        log.debug("_EffectStrips.hideEvent")
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        frame_log.debug("_EffectStrips._tick")     # per frame: the trace rung
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        strips = self._strips
        frame_log.debug("_EffectStrips.paintEvent: %d strip(s)", len(strips))
        t = time.monotonic() - self._start
        width, height = self.CELL
        p = QPainter(self)
        for i, strip in enumerate(strips):
            settings = strip.drawn
            x = self.GAP // 2 + i * (width + self.GAP)
            leds = (None if settings is None
                    else linked_preview(LIGHT_GRADIENTS[settings.metric], LEDS)
                    if isinstance(settings, LinkedEffect)
                    else effect_frame(settings, t, i, len(strips)))
            for led in range(LEDS):
                color = QColor(*leds[led]) if leds else QColor("#3a3a40")
                p.fillRect(QRect(x, 4 + led * (height + 4), width, height),
                           color)
            p.setPen(self.palette().windowText().color())   # either skin
            p.drawText(QRect(x - self.GAP // 2, LEDS * (height + 4) + 6,
                             width + self.GAP, 20),
                       Qt.AlignmentFlag.AlignCenter,
                       chr(ord("A") + i) + ("" if leds else "?"))
        p.end()


class _EffectPreview(QWidget):
    """The animated sticks, what each runs, and that TRCC drew them --
    hidden when there is no stick to show."""

    def __init__(self) -> None:
        super().__init__()
        log.debug("_EffectPreview.__init__")
        self.strips = _EffectStrips()
        self.legend = _muted()
        self._approximate = _muted(APPROXIMATE)
        # What each strip runs, beside the strips (his ask, 2026-10-21).
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self.strips)
        row.addWidget(self.legend, 1, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(row)
        layout.addWidget(self._approximate)

    def show_strips(self, strips: Strips, *, approximate: bool) -> None:
        """*approximate*: the lights run the effect themselves (RAM), so the
        drawing is TRCC's guess; a board light shows exactly what TRCC sends."""
        log.debug("_EffectPreview.show_strips: %d approximate=%s", len(strips),
                  approximate)
        self.strips.set_strips(strips)
        self.legend.setText(strip_legend(strips))
        self._approximate.setVisible(approximate)
        self.setVisible(bool(strips))


class _FollowForm(QWidget):
    """Which device the lights follow, and how its picture is spread."""

    def __init__(self, page: RgbPage, changed: Callable[[], None],
                 following: Callable[[], RgbFollowResult]) -> None:
        super().__init__()
        log.debug("_FollowForm.__init__")
        self._page, self._changed = page, changed
        self._following = following
        self._status_at = 0.0
        self.lead = ""                    # the first cooler heard from
        self._source = QComboBox(self)
        self._source.setFixedWidth(FOLLOW_PICKER_WIDTH)
        self._source.activated.connect(self._on_source)
        self._mapping_label = QLabel("Mapping", self)
        self._mapping = _ChoiceButtons(MAPPING_LABELS, self._on_mapping)
        self._colors_label = QLabel("Colours", self)
        self._colors = _ChoiceButtons(COLORS_LABELS, self._on_colors)
        self.preview = _FollowPreview()
        self._legend = _muted()
        self._status = _muted()
        # The controls, and beside them the preview -- under them it ran off
        # the page and the legend sat on the picture (his note, 2026-10-21).
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(14)
        grid.addWidget(QLabel("Follow", self), 0, 0)
        grid.addWidget(self._source, 0, 1)
        note = _muted("Any connected LCD, or an LED cooler -- which sends its "
                      "own colours.")
        note.setMaximumWidth(FOLLOW_PICKER_WIDTH)
        grid.addWidget(note, 1, 1)
        grid.addWidget(self._mapping_label, 2, 0)
        grid.addWidget(self._mapping, 2, 1)
        grid.addWidget(self._colors_label, 3, 0)
        grid.addWidget(self._colors, 3, 1)
        self._status.setMaximumWidth(FOLLOW_PICKER_WIDTH)
        grid.addWidget(self._status, 4, 1)
        grid.setRowStretch(5, 1)
        # No legend: it only named each strip's light, and the cards above
        # list them in the same A, B order (his call on the effect legend).
        self._legend.hide()
        shown = QVBoxLayout()
        shown.setSpacing(8)
        shown.addWidget(self.preview)
        shown.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(40)
        row.addLayout(grid, 0)
        row.addLayout(shown, 1)

    def show_page(self) -> None:
        page = self._page
        picks = page.follow_picks
        log.debug("_FollowForm.show_page: %r %s", picks.source, picks.mapping.value)
        self._source.clear()
        for choice in picks.choices():
            self._source.addItem(choice.label, choice.key)
        self._source.setCurrentIndex(self._source.findData(picks.source))
        applies = picks.mapping_applies
        self._mapping_label.setVisible(applies)
        self._mapping.setVisible(applies)
        self._mapping.show_values(tuple(FollowMapping), picks.mapping)
        self._colors_label.setVisible(applies)
        self._colors.setVisible(applies)
        self._colors.show_values(tuple(FollowColors), picks.colors)
        self._legend.setText("   ".join(
            f"{chr(ord('A') + i)}: {name}"
            for i, name in enumerate(page.preview_lights)))
        self.show_layout()
        self._status.setText(page.follow_status)

    def show_layout(self) -> None:
        """The preview's lights and columns, as the page has them now."""
        page = self._page
        log.debug("_FollowForm.show_layout")
        picks = page.follow_picks
        lights = page.preview_lights
        self.preview.set_layout(lights, picks.columns(len(lights)),
                                picks.mapping_applies)

    def show_status(self) -> None:
        log.debug("_FollowForm.show_status")
        self._status.setText(self._page.follow_status)

    def _on_source(self, index: int) -> None:
        key = str(self._source.itemData(index))
        log.info("_FollowForm._on_source: %r", key)
        if key != self._page.follow_picks.source:
            self.preview.clear()
        self._page.follow_picks.source = key
        self.show_page()
        self._changed()

    # ── The live feed: what the windows hand the preview ──────────────

    def on_frame(self, event: object) -> None:
        """``FrameSent``: the followed LCD's frame goes to the preview."""
        key = getattr(event, "key", "")
        surface = getattr(event, "surface", None)
        if (key != self._page.follow_picks.source or not isinstance(surface, QImage)
                or not self.preview.isVisible()):
            return
        frame_log.debug("_FollowForm.on_frame: %s", key)
        self.preview.show_frame(surface)
        self._status_due()

    def on_led_colors(self, event: object) -> None:
        """``LedColorsChanged``: a followed cooler's colours -- the very ones
        the follower takes, before the cooler's segment mask."""
        key = getattr(event, "key", "")
        colors = getattr(event, "colors", ())
        source = self._page.follow_picks.source
        if (self.preview.follows_picture or not colors
                or not self.preview.isVisible()
                or key != (source or self.lead or key)):
            return
        frame_log.debug("_FollowForm.on_led_colors: %s %d", key, len(colors))
        self.lead = self.lead or key       # "the first cooler": the first heard
        self.preview.show_colors(colors)
        self._status_due()

    def on_follow_sent(self, event: object) -> None:
        """``RgbFollowSent``: the colours the App just sent the lights --
        drawn on the strips when they come from what this page follows."""
        source = getattr(event, "source", "")
        columns = getattr(event, "columns", ())
        followed = self._page.follow_picks.source
        if (not self.preview.isVisible()
                or source != (followed or self.lead or source)):
            return
        frame_log.debug("_FollowForm.on_follow_sent: %s %d", source,
                        len(columns))
        self.preview.show_sent(columns)

    def _status_due(self) -> None:
        """Re-read what following is doing, every few seconds while shown."""
        if (now := time.monotonic()) - self._status_at >= STATUS_EVERY_S:
            log.debug("_FollowForm._status_due")
            self._status_at = now
            self._page.follow_now(self._following())
            self.show_status()

    def _on_colors(self, colors: object) -> None:
        log.info("_FollowForm._on_colors: %s", colors)
        self._page.follow_picks.colors = FollowColors(colors)
        self._changed()

    def _on_mapping(self, mapping: object) -> None:
        log.info("_FollowForm._on_mapping: %s", mapping)
        self._page.follow_picks.mapping = FollowMapping(mapping)
        self._changed()


class RgbPageView(QWidget):
    """The page; a skin's subclass names the Commands and the access row."""


    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("RgbPageView.__init__: %s", type(self).__name__)
        self._app = app
        self.setObjectName("rgb-page")
        self.page = RgbPage()
        self._busy = False
        self._reload = False            # the running job: a scan reloads
        self._access = self._access_row()
        self._devices_column = _DeviceList()
        self._devices_column.setFixedWidth(DEVICES_WIDTH)
        self._devices_column.picked.connect(self._on_device)
        self._devices_column.find_clicked.connect(self._on_find)
        self._lights_column = _LightsColumn(self.page, self._access,
                                            self._on_changed)
        self._lights_column.leds_set.connect(self._on_leds)
        self._lights_column.blinked.connect(self._on_blink)
        self._title = _heading("")
        self._sources = QButtonGroup(self)
        self._source_buttons: dict[RgbSource, QRadioButton] = {}
        self._effect = _EffectForm(self.page.effect_picks, self._on_changed)
        self.follow = _FollowForm(self.page, self._on_changed,
                                  self._following)
        self._previews: list[_EffectPreview] = []
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._with_strips(None))
        self._stack.addWidget(self._with_strips(self._effect))
        self._stack.addWidget(self.follow)
        self._hint = _muted()
        self._problem = QLabel("", self)
        self._problem.setObjectName("rgb-problem")
        self._problem.setWordWrap(True)
        self._message = _muted()
        self._apply = QPushButton("Apply", self)
        self._apply.setObjectName("rgb-apply")
        self._apply.setFixedSize(120, 34)
        self._apply.clicked.connect(self._on_apply)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Spaced by hand: a nested layout inherits its parent's spacing.
        layout.setSpacing(0)
        layout.addWidget(self._devices_column)
        layout.addSpacing(40)
        layout.addLayout(self._source_column(), 1)

    def _with_strips(self, form: QWidget | None) -> QWidget:
        """*form* (if any) with the animated lights and what they run beside
        it -- below it, the page outgrew the window (2026-10-21)."""
        log.debug("RgbPageView._with_strips: %s", type(form).__name__)
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(40)
        # The form at its own width, the preview right after it, the spare
        # room past both -- a share of the width left a wide gap (his note).
        if form is not None:
            layout.addWidget(form, 0, Qt.AlignmentFlag.AlignTop)
        preview = _EffectPreview()
        self._previews.append(preview)
        layout.addWidget(preview, 1, Qt.AlignmentFlag.AlignTop)
        return holder

    def _source_column(self) -> QVBoxLayout:
        log.debug("RgbPageView._source_column")
        radios = QHBoxLayout()
        radios.setSpacing(36)
        for source, label in SOURCE_LABELS.items():
            button = QRadioButton(label, self)
            button.clicked.connect(partial(self._on_source, source))
            self._sources.addButton(button)
            self._source_buttons[source] = button
            radios.addWidget(button)
        radios.addStretch(1)
        apply_row = QHBoxLayout()
        apply_row.addWidget(self._message, 1)
        apply_row.addWidget(self._apply)
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(self._title)
        self._lights_area = _scrolling(self, self._lights_column)
        column.addWidget(self._lights_area)
        column.addSpacing(8)
        column.addLayout(radios)
        # What the picked choice means, right under it and no wider than the
        # form -- alone at the foot of the page, full width, it read as stray
        # (his note, 2026-10-21).
        self._hint.setMaximumWidth(HINT_WIDTH)
        column.addWidget(self._hint)
        column.addSpacing(12)
        column.addWidget(self._stack)
        column.addWidget(self._problem)
        column.addStretch(1)
        column.addLayout(apply_row)
        return column

    # ── What a skin supplies ─────────────────────────────────────────

    def _access_row(self) -> RamAccessRow:
        """This skin's RAM-access row."""
        log.error("RgbPageView._access_row: %s builds none", type(self).__name__)
        raise NotImplementedError

    def _lights(self) -> RgbLightsResult:
        """The ``RgbLights`` Query's answer."""
        log.error("RgbPageView._lights: %s names no Query", type(self).__name__)
        raise NotImplementedError

    def _following(self) -> RgbFollowResult:
        """The ``RgbFollow`` Query's answer."""
        log.error("RgbPageView._following: %s names no Query",
                  type(self).__name__)
        raise NotImplementedError

    def _devices(self) -> Sequence[DeviceEntry]:
        """The ``ListDevices`` Query's devices."""
        log.error("RgbPageView._devices: %s names no Query", type(self).__name__)
        raise NotImplementedError

    def _find(self, plan: FindLights) -> Command[Any]:
        """``ScanRgbLights`` for *plan* -- named, not dispatched."""
        log.error("RgbPageView._find: %s names no Command (%s)",
                  type(self).__name__, plan)
        raise NotImplementedError

    def _send(self, plan: ApplyPlan) -> Command[Any]:
        """The Command *plan* stands for -- named, not dispatched."""
        log.error("RgbPageView._send: %s names no Command (%s)",
                  type(self).__name__, plan)
        raise NotImplementedError

    def _leds(self, plan: HeaderLeds) -> Command[Any]:
        """``SetAuraLeds`` for *plan* -- named, not dispatched."""
        log.error("RgbPageView._leds: %s names no Command (%s)",
                  type(self).__name__, plan)
        raise NotImplementedError

    def _blink(self, header: int) -> Command[Any]:
        """``BlinkAuraHeader`` for ARGB *header* -- named, not dispatched."""
        log.error("RgbPageView._blink: %s names no Command (header %d)",
                  type(self).__name__, header)
        raise NotImplementedError

    # ── The page ─────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:
        """On screen: what the App says now -- devices come and go meanwhile."""
        log.debug("RgbPageView.showEvent")
        super().showEvent(event)
        self.refresh()

    def on_app_event(self, event: object) -> None:
        """An RGB event from any UI: reload, if anyone can see it."""
        log.debug("RgbPageView.on_app_event: %s visible=%s",
                  type(event).__name__, self.isVisible())
        if isinstance(event, RgbFollowChanged):
            self.follow.preview.clear_sent()     # new colours are coming
        if self.isVisible() and not self._busy:
            self.refresh()

    def refresh(self) -> None:
        """Start again from the App -- three reads, no bus traffic."""
        log.info("RgbPageView.refresh")
        self.follow.lead = ""
        self._access.refresh()
        self.page.load(self._lights(), self._following(), self._devices())
        self._show()

    def _show(self) -> None:
        page = self.page
        log.debug("RgbPageView._show: %s source=%s", page.device.value,
                  page.source.value)
        self._title.setText(DEVICE_LABELS[page.device])
        self._devices_column.show_page(page)
        self._lights_column.show_page()
        self._lights_area.setMinimumHeight(
            min(LIGHTS_HEIGHT, self._lights_column.sizeHint().height()))
        for source, button in self._source_buttons.items():
            button.setEnabled(page.source_enabled(source))
            button.setChecked(source is page.source)
        self._stack.setCurrentIndex(list(RgbSource).index(page.source))
        self._effect.show_page()
        self.follow.show_page()
        self._on_changed()

    def _on_changed(self) -> None:
        """A pick changed: say what Apply would do, or why it cannot."""
        page = self.page
        problem = page.problem
        log.debug("RgbPageView._on_changed: problem=%s busy=%s", problem,
                  self._busy)
        self._hint.setText(page.hint)
        self.follow.show_layout()
        strips = page.effect_strips()
        for preview in self._previews:
            preview.show_strips(strips, approximate=page.preview_approximate)
        self._problem.setText(problem or "")
        self._problem.setVisible(problem is not None)
        self._message.setText(WORKING if self._busy else page.message)
        self._apply.setEnabled(problem is None and not self._busy)
        self._devices_column.set_busy(self._busy)

    def _on_device(self, device: RgbDevice) -> None:
        """Show another device's lights and source."""
        log.info("RgbPageView._on_device: %s", device.value)
        self.page.set_device(device)
        self._show()

    def _on_source(self, source: RgbSource) -> None:
        log.info("RgbPageView._on_source: %s", source.value)
        self.page.set_source(source)
        self._stack.setCurrentIndex(list(RgbSource).index(source))
        self._on_changed()

    def _on_find(self) -> None:
        log.info("RgbPageView._on_find: %r", self.page.address)
        if (plan := self.page.find()) is None:
            self._on_changed()
            return
        self._run(self._find(plan), reload=True)

    def _on_leds(self, header: int, leds: int) -> None:
        """A header's LED count: saved, the board read again, the page
        reloaded -- off the GUI thread, like Find."""
        log.info("RgbPageView._on_leds: header %d -> %d", header, leds)
        self._run(self._leds(HeaderLeds(header, leds)), reload=True)

    def _on_blink(self, header: int) -> None:
        """Blink a header -- two seconds, off the GUI thread; the page is
        busy until it is done."""
        log.info("RgbPageView._on_blink: header %d", header)
        self._run(self._blink(header), reload=False)

    def _on_apply(self) -> None:
        plan = self.page.apply()
        log.info("RgbPageView._on_apply: %s", plan)
        if plan is not None:
            self._run(self._send(plan), reload=False)

    def _run(self, command: Command[Any], *, reload: bool) -> None:
        """*command* off the GUI thread -- a scan or a password prompt can
        wait -- its answer back to ``_on_answered``.  One at a time: the page
        is busy until it comes."""
        log.info("RgbPageView._run: %s reload=%s", type(command).__name__,
                 reload)
        self._busy, self._reload = True, reload
        self._on_changed()
        dispatch_in_background(self._app, command, self._on_answered)

    def _on_answered(self, result: Result) -> None:
        """Show the answer; a found light, or a change made, reloads -- a
        refusal keeps the user's picks to correct."""
        reload = self._reload
        log.info("RgbPageView._on_answered: ok=%s reload=%s", result.ok, reload)
        self._busy = False
        self.page.answered(result)
        if reload or result.ok:
            self.refresh()
        else:
            self._on_changed()
