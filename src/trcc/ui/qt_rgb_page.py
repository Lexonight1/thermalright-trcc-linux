"""The RGB page both windows show -- the view, not the Commands.

Lights on the left (RGB memory, OpenRGB's devices, the RAM-access card and
"Find lights"), the source on the right (Leave alone / Built-in effect /
Follow a device) with only the controls that source and that effect take, and
Apply.  ``presentation.rgb_page.RgbPage`` makes every choice; this draws it.

Template Method, as ``qt_ram_access.RamAccessRow``: each skin's subclass names
the Commands (``_lights`` .. ``_apply``) and builds its RAM-access row, so the
Commands are each UI's own and the parity gates see both windows reach them.
Find and Apply run off the UI thread -- the RAM probe reads the chipset bus,
and an effect waits on the stick -- and the window stays usable meanwhile.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPen, QShowEvent
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
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.led_models import stretch
from ..core.models import (
    EffectDirection,
    EffectSpeed,
    FollowMapping,
    LightKind,
    RamEffect,
)
from ..core.ports import CommandBus
from ..core.results import DeviceEntry, Result, RgbFollowResult, RgbLightsResult
from .presentation.rgb_page import (
    DIRECTION_LABELS,
    EFFECT_LABELS,
    MAPPING_LABELS,
    SOURCE_LABELS,
    SPEED_LABELS,
    ApplyPlan,
    EffectPicks,
    FindLights,
    LightRow,
    RgbPage,
    RgbSource,
)
from .qt_ram_access import RamAccessRow

log = logging.getLogger(__name__)

ACCESS_OFF_TEXT = (
    "TRCC needs permission to reach the memory's lighting chips.  Nothing is "
    "changed until you turn it on, and you can turn it off here again.")
WORKING = "Working..."
#: How often the follow status is re-read while frames reach the preview.
STATUS_EVERY_S = 2.0


def _clear(layout: QVBoxLayout) -> None:
    """Drop every widget *layout* holds."""
    log.debug("_clear: %d item(s)", layout.count())
    while (item := layout.takeAt(0)) is not None:
        if (widget := item.widget()) is not None:
            widget.deleteLater()


def _muted(text: str = "") -> QLabel:
    """A wrapped grey note."""
    log.debug("_muted: %r", text[:40])
    label = QLabel(text)
    label.setObjectName("rgb-muted")
    label.setWordWrap(True)
    return label


def _heading(text: str) -> QLabel:
    log.debug("_heading: %s", text)
    label = QLabel(text)
    label.setObjectName("rgb-heading")
    return label


class _LightCard(QFrame):
    """One light: its tick (does it take part?), its name and where it is."""

    def __init__(self, row: LightRow, toggled: Callable[[str, bool], None]
                 ) -> None:
        super().__init__()
        log.debug("_LightCard.__init__: %s checked=%s", row.ref, row.checked)
        self.setObjectName("rgb-card")
        self.check = QCheckBox(row.title, self)
        self.check.setObjectName("rgb-light")
        self.check.setChecked(row.checked)
        self.check.toggled.connect(partial(toggled, row.ref))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        layout.addWidget(self.check)
        layout.addWidget(_muted(row.detail))


class _LightsColumn(QWidget):
    """The left column: what TRCC can light, and how to find more."""

    find_clicked = Signal()

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
        self._ram = QVBoxLayout()
        self._ram_note = _muted()
        self._address = QLineEdit(self)
        self._address.setObjectName("rgb-address")
        self._address.setToolTip("OpenRGB's SDK server, host:port")
        self._address.textEdited.connect(self._on_address_edited)
        self._openrgb_note = _muted()
        self._openrgb = QVBoxLayout()
        self.find_button = QPushButton("Find lights", self)
        self.find_button.setObjectName("rgb-button")
        self.find_button.clicked.connect(self.find_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_heading("Lights"))
        layout.addLayout(self._ram)
        layout.addWidget(self._ram_note)
        layout.addWidget(self._access_card)
        layout.addWidget(self._openrgb_box())
        layout.addWidget(self.find_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _openrgb_box(self) -> QFrame:
        log.debug("_LightsColumn._openrgb_box")
        box = QFrame(self)
        box.setObjectName("rgb-openrgb")
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        title = QLabel("OpenRGB")
        title.setObjectName("rgb-title")
        top.addWidget(title)
        top.addWidget(self._address, 1)
        layout.addLayout(top)
        layout.addWidget(self._openrgb_note)
        layout.addLayout(self._openrgb)
        return box

    def show_page(self) -> None:
        """Lay out the lights the page lists now."""
        page = self._page
        log.debug("_LightsColumn.show_page: reachable=%s", page.ram_reachable)
        for layout, kind in ((self._ram, LightKind.RAM),
                             (self._openrgb, LightKind.OPENRGB)):
            _clear(layout)
            for row in page.rows(kind):
                layout.addWidget(_LightCard(row, self._on_light_toggled))
        reachable = page.ram_reachable
        self._ram_note.setText(page.ram_note if reachable else "")
        self._ram_note.setVisible(reachable and bool(page.ram_note))
        # Off: the card explains and offers the switch.  On: just the switch.
        self._access_title.setVisible(not reachable)
        self._access_text.setVisible(not reachable)
        self._address.setText(page.address)
        self._openrgb_note.setText(page.openrgb_note)

    def set_busy(self, busy: bool) -> None:
        log.debug("_LightsColumn.set_busy: %s", busy)
        self.find_button.setEnabled(not busy)

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
        for effect, label in EFFECT_LABELS.items():
            self._effect.addItem(label, effect)
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
        self._grid.setHorizontalSpacing(24)
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
        log.debug("_EffectForm.show_page: %s %s", page.effect.value, traits)
        self._effect.setCurrentIndex(self._effect.findData(page.effect))
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

    def _on_effect(self, index: int) -> None:
        effect = RamEffect(self._effect.itemData(index))
        log.info("_EffectForm._on_effect: %s", effect.value)
        self._picks.set_effect(effect)
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


def sample_grid(image: QImage, columns: int, rows: int
                ) -> list[list[tuple[int, int, int]]]:
    """*image* as ``rows`` x ``columns`` colours, each the average of its cell.

    The App samples a followed frame with ``QtRenderer.get_pixels_rgb``, which
    this repeats: the window cannot reach the App's renderer, and the preview
    must show the colours the lights get.  ``tests/test_rgb_page_view.py``
    checks that the two agree.
    """
    log.debug("sample_grid: %dx%d", columns, rows)
    scaled = image.scaled(columns, rows, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    scaled = scaled.convertToFormat(QImage.Format.Format_RGB32)
    # QRgb is 0xAARRGGBB on every platform, as the renderer reads it.
    return [[((pixel := scaled.pixel(x, y)) >> 16 & 0xFF, pixel >> 8 & 0xFF,
              pixel & 0xFF) for x in range(columns)] for y in range(rows)]


class _FollowPreview(QWidget):
    """What the lights follow, and each light's LEDs in the colours it gets
    now: an LCD's live picture cut into the columns the lights take, or an
    LED cooler's own colours, the same on every light."""

    PICTURE = 220
    CELL = (22, 16)
    STRIP_GAP = 40
    COOLER_ROW = 10              # the cooler's LEDs drawn ten to a row

    def __init__(self, page: RgbPage) -> None:
        super().__init__()
        log.debug("_FollowPreview.__init__")
        self._page = page
        self._image: QImage | None = None
        self._colors: tuple[tuple[int, int, int], ...] = ()
        self.setFixedHeight(self.PICTURE + 26)

    def show_frame(self, image: QImage) -> None:
        log.debug("_FollowPreview.show_frame: %dx%d", image.width(),
                  image.height())
        self._image = image
        self.update()

    def show_colors(self, colors: Sequence[tuple[int, int, int]]) -> None:
        log.debug("_FollowPreview.show_colors: %d", len(colors))
        self._colors = tuple(colors)
        self.update()

    def clear(self) -> None:
        log.debug("_FollowPreview.clear")
        self._image, self._colors = None, ()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        page = self._page
        log.debug("_FollowPreview.paintEvent: picture=%s",
                  page.mapping_applies)
        p = QPainter(self)
        if page.mapping_applies:
            columns, grid, right = self._paint_picture(p)
        else:
            columns, grid, right = 1, self._paint_cooler(p), self.PICTURE
        self._paint_strips(p, right + self.STRIP_GAP, page.preview_lights,
                           columns, grid)
        p.end()

    def _paint_waiting(self, p: QPainter, text: str) -> None:
        log.debug("_FollowPreview._paint_waiting: %s", text)
        frame = QRect(0, 0, self.PICTURE, self.PICTURE)
        p.setPen(QPen(QColor("#5a5a62"), 1, Qt.PenStyle.DashLine))
        p.drawRect(frame.adjusted(0, 0, -1, -1))
        p.setPen(QColor("#9a9aa2"))
        p.drawText(frame, Qt.AlignmentFlag.AlignCenter
                   | Qt.TextFlag.TextWordWrap, text)

    def _paint_picture(self, p: QPainter
                       ) -> tuple[int, list[list[tuple[int, int, int]]] | None,
                                  int]:
        """The LCD's frame and the column lines: (columns, grid, right edge)."""
        columns = self._page.preview_columns
        log.debug("_FollowPreview._paint_picture: %d column(s)", columns)
        if self._image is None or self._image.isNull():
            self._paint_waiting(p, "Waiting for the device's\nnext frame...")
            return columns, None, self.PICTURE
        picture = self._image.scaled(
            self.PICTURE, self.PICTURE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        frame = QRect(0, 0, picture.width(), picture.height())
        p.drawImage(frame, picture)
        p.setPen(QPen(QColor("#d9b45a"), 1, Qt.PenStyle.DashLine))
        for i in range(1, columns):
            x = frame.left() + frame.width() * i // columns
            p.drawLine(x, frame.top(), x, frame.bottom())
        return (columns, sample_grid(self._image, columns,
                                     self._page.PREVIEW_ROWS), frame.right())

    def _paint_cooler(self, p: QPainter
                      ) -> list[list[tuple[int, int, int]]] | None:
        """The cooler's LEDs, ten to a row; the one column every light gets
        -- stretched over its LEDs as the drivers stretch it."""
        log.debug("_FollowPreview._paint_cooler: %d LED(s)", len(self._colors))
        if not self._colors:
            self._paint_waiting(p, "Waiting for the cooler's\ncolours...")
            return None
        size = self.PICTURE // self.COOLER_ROW
        for i, rgb in enumerate(self._colors):
            row, col = divmod(i, self.COOLER_ROW)
            p.fillRect(QRect(col * size + 2, row * size + 2, size - 4,
                             size - 4), QColor(*rgb))
        return [[rgb] for rgb in stretch(self._colors,
                                         self._page.PREVIEW_ROWS)]

    def _paint_strips(self, p: QPainter, left: int, lights: Sequence[str],
                      columns: int,
                      grid: list[list[tuple[int, int, int]]] | None) -> None:
        """One strip per light: light *i* shows column *i* of the picture."""
        log.debug("_FollowPreview._paint_strips: %d", len(lights))
        width, height = self.CELL
        for i, name in enumerate(lights):
            x = left + i * (width + 24)
            for row in range(self._page.PREVIEW_ROWS):
                color = (QColor(*grid[row][i % columns]) if grid
                         else QColor("#2b2b31"))
                p.fillRect(QRect(x, 4 + row * (height + 4), width, height),
                           color)
            p.setPen(self.palette().windowText().color())   # either skin
            p.drawText(QRect(x - 12, self.PICTURE + 4, width + 24, 20),
                       Qt.AlignmentFlag.AlignCenter, chr(ord("A") + i))
            log.debug("_FollowPreview: strip %s is %s", chr(ord("A") + i), name)


class _FollowForm(QWidget):
    """Which device the lights follow, and how its picture is spread."""

    def __init__(self, page: RgbPage, changed: Callable[[], None]) -> None:
        super().__init__()
        log.debug("_FollowForm.__init__")
        self._page, self._changed = page, changed
        self._source = QComboBox(self)
        self._source.setMinimumWidth(340)
        self._source.activated.connect(self._on_source)
        self._mapping_label = QLabel("Mapping", self)
        self._mapping = _ChoiceButtons(MAPPING_LABELS, self._on_mapping)
        self.preview = _FollowPreview(page)
        self._legend = _muted()
        self._status = _muted()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(14)
        grid.addWidget(QLabel("Follow", self), 0, 0)
        grid.addWidget(self._source, 0, 1)
        grid.addWidget(_muted("Any connected LCD, or an LED cooler -- which "
                              "sends its own colours."), 1, 1)
        grid.addWidget(self._mapping_label, 2, 0)
        grid.addWidget(self._mapping, 2, 1)
        grid.addWidget(self.preview, 3, 1)
        grid.addWidget(self._legend, 4, 1)
        grid.addWidget(self._status, 5, 1)
        grid.setColumnStretch(1, 1)

    def show_page(self) -> None:
        page = self._page
        log.debug("_FollowForm.show_page: %r %s", page.follow_source,
                  page.mapping.value)
        self._source.clear()
        for choice in page.follow_choices():
            self._source.addItem(choice.label, choice.key)
        self._source.setCurrentIndex(self._source.findData(page.follow_source))
        applies = page.mapping_applies
        self._mapping_label.setVisible(applies)
        self._mapping.setVisible(applies)
        self._mapping.show_values(tuple(FollowMapping), page.mapping)
        self._legend.setText("   ".join(
            f"{chr(ord('A') + i)}: {name}"
            for i, name in enumerate(page.preview_lights)))
        self.preview.update()
        self._status.setText(page.follow_status)

    def show_status(self) -> None:
        log.debug("_FollowForm.show_status")
        self._status.setText(self._page.follow_status)

    def _on_source(self, index: int) -> None:
        key = str(self._source.itemData(index))
        log.info("_FollowForm._on_source: %r", key)
        if key != self._page.follow_source:
            self.preview.clear()
        self._page.follow_source = key
        self.show_page()
        self._changed()

    def _on_mapping(self, mapping: object) -> None:
        log.info("_FollowForm._on_mapping: %s", mapping)
        self._page.mapping = FollowMapping(mapping)
        self.preview.update()
        self._changed()


class RgbPageView(QWidget):
    """The page; a skin's subclass names the Commands and the access row."""

    _answered = Signal(object, bool)        # Result, from the worker; reload?

    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("RgbPageView.__init__: %s", type(self).__name__)
        self._app = app
        self.setObjectName("rgb-page")
        self.page = RgbPage()
        self._busy = False
        self._status_at = 0.0
        self._lead = ""                   # the first cooler heard from
        self._access = self._access_row()
        self._lights_column = _LightsColumn(self.page, self._access,
                                            self._on_changed)
        self._lights_column.setFixedWidth(400)
        self._lights_column.find_clicked.connect(self._on_find)
        self._sources = QButtonGroup(self)
        self._source_buttons: dict[RgbSource, QRadioButton] = {}
        self._effect = _EffectForm(self.page.effect_picks, self._on_changed)
        self._follow = _FollowForm(self.page, self._on_changed)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(QWidget(self))
        self._stack.addWidget(self._effect)
        self._stack.addWidget(self._follow)
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
        layout.addWidget(self._lights_column)
        layout.addSpacing(70)
        layout.addLayout(self._source_column(), 1)
        self._answered.connect(self._on_answered)

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
        column.addWidget(_heading("Source"))
        column.addLayout(radios)
        column.addSpacing(18)
        column.addWidget(self._stack)
        column.addWidget(self._hint)
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

    def _find(self, plan: FindLights) -> Result:
        """``ScanRgbLights`` for *plan*."""
        log.error("RgbPageView._find: %s names no Command (%s)",
                  type(self).__name__, plan)
        raise NotImplementedError

    def _send(self, plan: ApplyPlan) -> Result:
        """The Command *plan* stands for."""
        log.error("RgbPageView._send: %s names no Command (%s)",
                  type(self).__name__, plan)
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
        if self.isVisible() and not self._busy:
            self.refresh()

    @property
    def follow_preview(self) -> QWidget:
        """The live picture -- frames are wanted while it is on screen."""
        log.debug("RgbPageView.follow_preview")
        return self._follow.preview

    def on_frame(self, event: object) -> None:
        """``FrameSent``: the followed LCD's frame goes to the preview."""
        key = getattr(event, "key", "")
        surface = getattr(event, "surface", None)
        if (key != self.page.follow_source or not isinstance(surface, QImage)
                or not self._follow.preview.isVisible()):
            return
        log.debug("RgbPageView.on_frame: %s", key)
        self._follow.preview.show_frame(surface)
        self._status_due()

    def on_led_colors(self, event: object) -> None:
        """``LedColorsChanged``: a followed cooler's colours -- the very ones
        the follower takes, before the cooler's segment mask."""
        key = getattr(event, "key", "")
        colors = getattr(event, "colors", ())
        source = self.page.follow_source
        if (self.page.mapping_applies or not colors
                or not self._follow.preview.isVisible()
                or key != (source or self._lead or key)):
            return
        log.debug("RgbPageView.on_led_colors: %s %d", key, len(colors))
        self._lead = self._lead or key     # "the first cooler": the first heard
        self._follow.preview.show_colors(colors)
        self._status_due()

    def _status_due(self) -> None:
        """Re-read what following is doing, every few seconds while shown."""
        if (now := time.monotonic()) - self._status_at >= STATUS_EVERY_S:
            log.debug("RgbPageView._status_due")
            self._status_at = now
            self.page.follow_now(self._following())
            self._follow.show_status()

    def refresh(self) -> None:
        """Start again from the App -- three reads, no bus traffic."""
        log.info("RgbPageView.refresh")
        self._lead = ""
        self._access.refresh()
        self.page.load(self._lights(), self._following(), self._devices())
        self._show()

    def _show(self) -> None:
        page = self.page
        log.debug("RgbPageView._show: source=%s", page.source.value)
        self._lights_column.show_page()
        for source, button in self._source_buttons.items():
            button.setEnabled(page.source_enabled(source))
            button.setChecked(source is page.source)
        self._stack.setCurrentIndex(list(RgbSource).index(page.source))
        self._effect.show_page()
        self._follow.show_page()
        self._on_changed()

    def _on_changed(self) -> None:
        """A pick changed: say what Apply would do, or why it cannot."""
        page = self.page
        problem = page.problem
        log.debug("RgbPageView._on_changed: problem=%s busy=%s", problem,
                  self._busy)
        self._hint.setText(page.hint)
        self._problem.setText(problem or "")
        self._problem.setVisible(problem is not None)
        self._message.setText(WORKING if self._busy else page.message)
        self._apply.setEnabled(problem is None and not self._busy)
        self._lights_column.set_busy(self._busy)

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
        self._run(partial(self._find, plan), reload=True)

    def _on_apply(self) -> None:
        plan = self.page.apply()
        log.info("RgbPageView._on_apply: %s", plan)
        if plan is not None:
            self._run(partial(self._send, plan), reload=False)

    def _run(self, work: Callable[[], Result], *, reload: bool) -> None:
        """*work* on a worker thread; its answer comes back to the UI."""
        log.info("RgbPageView._run: reload=%s", reload)
        self._busy = True
        self._on_changed()
        threading.Thread(target=self._work, args=(work, reload), daemon=True,
                         name="trcc-rgb-page").start()

    def _work(self, work: Callable[[], Result], reload: bool) -> None:
        log.info("RgbPageView._work: started")
        try:
            result = work()
        except Exception as e:            # the App went away meanwhile
            log.warning("RgbPageView._work: failed -- %s: %s",
                        type(e).__name__, e)
            result = Result(ok=False, message=f"Could not reach TRCC: {e}")
        try:
            self._answered.emit(result, reload)
        except RuntimeError:              # the window closed while it ran
            log.info("RgbPageView._work: answered after the window closed")

    def _on_answered(self, result: Result, reload: bool) -> None:
        """Show the answer; a found light, or a change made, reloads -- a
        refusal keeps the user's picks to correct."""
        log.info("RgbPageView._on_answered: ok=%s reload=%s", result.ok, reload)
        self._busy = False
        self.page.answered(result)
        if reload or result.ok:
            self.refresh()
        else:
            self._on_changed()
