"""The gui's widescreen preview pops out into a window of its own, as the C# does.

The C# (``FormScreenImage``): double-clicking a widescreen (``isBiliPingmu``)
panel's preview moves the ONE preview control into a frameless always-on-top
window; its power button moves it back; a rotation swaps in the portrait art;
editing keeps working inside.  The window is its art: preview at (10, 50), art
= preview + (20, 60).  We shipped all 16 arts and no code used them.

Every case here drives the real ``UCPreview`` and ``PreviewPopup``.

MUTATION CHECK -- five ways, MEASURED 2026-10-01; failures in THIS file:

  1. drags mapped by the DOCKED size  →  **1** (the drag case).
  2. a resize that does not redraw the last image  →  **1**.
  3. a rotation while popped docks instead of re-laying  →  **1**.
  4. every panel gets a pop-out, not only widescreen  →  **2**.
  5. closing the window destroys it instead of docking  →  **1**.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtGui import QImage

from trcc.ui.gui.uc_preview import UCPreview


@pytest.fixture
def make(qtbot: Any) -> Iterator[Any]:
    built: list[UCPreview] = []

    def build(width: int, height: int) -> UCPreview:
        preview = UCPreview(width, height)
        qtbot.addWidget(preview)
        built.append(preview)
        return preview

    yield build
    for preview in built:
        if preview.popout.window is not None:
            preview.popout.window.hide()
            preview.popout.window.deleteLater()


def _pop(preview: UCPreview, qtbot: Any) -> None:
    preview.preview_label.double_clicked.emit()
    qtbot.waitUntil(lambda: preview.popout.popped, timeout=1000)


def _area(preview: UCPreview) -> tuple[int, int]:
    return preview.preview_label._width, preview.preview_label._height


def test_a_widescreen_preview_pops_out_at_the_c_sharp_size(make, qtbot) -> None:
    """1920x462 pops at half, 960x231, in its 980x291 art at (10, 50)."""
    preview = make(1920, 462)
    _pop(preview, qtbot)
    popup = preview.popout.window
    assert popup is not None and popup.isVisible()
    assert preview.preview_label.parent() is popup
    assert _area(preview) == (960, 231)
    assert (preview.preview_label.x(), preview.preview_label.y()) == (10, 50)
    assert (popup.width(), popup.height()) == (980, 291)


def test_the_1280x480_pops_at_half_now_its_art_is_the_2_1_8_one(make, qtbot) -> None:
    preview = make(1280, 480)
    _pop(preview, qtbot)
    assert _area(preview) == (640, 240)


def test_a_square_preview_does_not_pop_out(make, qtbot) -> None:
    preview = make(320, 320)
    preview.preview_label.double_clicked.emit()
    qtbot.wait(50)
    assert not preview.popout.popped
    assert preview.popout.window is None


def test_a_rotation_while_popped_swaps_in_the_portrait_art(make, qtbot) -> None:
    preview = make(1920, 462)
    _pop(preview, qtbot)
    preview.set_resolution(462, 1920)
    popup = preview.popout.window
    assert popup is not None
    assert preview.popout.popped
    assert _area(preview) == (231, 960)
    assert (popup.width(), popup.height()) == (251, 1020)


def test_switching_to_a_square_device_docks_it(make, qtbot) -> None:
    preview = make(1920, 462)
    _pop(preview, qtbot)
    preview.set_resolution(320, 320)
    assert not preview.popout.popped
    assert preview.preview_label.parent() is preview.frame_container
    assert _area(preview) == (320, 320)
    assert preview.popout.window is not None and not preview.popout.window.isVisible()


def test_the_dock_button_and_closing_the_window_both_dock(make, qtbot) -> None:
    preview = make(854, 480)
    _pop(preview, qtbot)
    popup = preview.popout.window
    assert popup is not None
    popup._dock.click()
    assert not preview.popout.popped
    assert _area(preview) == (427, 240)          # the docked bezel area

    _pop(preview, qtbot)
    popup.close()                                # the window's own close
    assert not preview.popout.popped
    assert preview.preview_label.parent() is preview.frame_container


def test_a_drag_in_the_pop_out_lands_on_the_same_lcd_pixel(make, qtbot) -> None:
    """The middle of the popped 960x231 preview is the middle of the panel.

    The mapping used the DOCKED size (480x116), so a drag in the pop-out would
    have landed twice as far across.
    """
    preview = make(1920, 462)
    _pop(preview, qtbot)
    assert preview._widget_to_lcd(480, 115) == (960, 230)


def test_the_last_image_is_redrawn_at_the_new_size(make, qtbot) -> None:
    """A static theme sends no new frame, so popping out must not leave the
    preview blank or at the docked size until one arrives."""
    preview = make(1920, 462)
    image = QImage(1920, 462, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    preview.set_image(image)
    _pop(preview, qtbot)
    pixmap = preview.preview_label.pixmap()
    assert (pixmap.width(), pixmap.height()) == (960, 231)
