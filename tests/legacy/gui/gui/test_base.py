"""
Tests for gui.base – base widget classes and utility functions.

Uses QT_QPA_PLATFORM=offscreen for headless testing.

Tests cover:
- BasePanel: init, fixed size, delegate signal, resource loading
- ImageLabel: init, set_image, click signal
- ClickableFrame: click signal
- BaseThumbnail: init, selection state, style updates
- BaseThemeBrowser: grid population, empty state, item selection
- create_image_button: button creation with fallback text
- set_background_pixmap: palette-based background
"""

import os
import sys
import unittest

# Must set before ANY Qt import
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication

# Create app once for all tests
_app = QApplication.instance() or QApplication(sys.argv)

from PySide6.QtGui import QImage, QPixmap  # noqa: E402

from trcc.legacy.core.models import ThemeItem  # noqa: E402
from trcc.legacy.ui.gui.base import (  # noqa: E402
    BasePanel,
    BaseThumbnail,
    ClickableFrame,
    ImageLabel,
    create_image_button,
    set_background_pixmap,
)
from trcc.legacy.ui.gui.constants import Sizes  # noqa: E402


class TestBasePanel(unittest.TestCase):
    """Test BasePanel base class."""

    def test_init_no_size(self):
        panel = BasePanel()
        self.assertIsNotNone(panel)

    def test_init_with_size(self):
        panel = BasePanel(width=200, height=100)
        self.assertEqual(panel.width(), 200)
        self.assertEqual(panel.height(), 100)

    def test_init_width_only(self):
        panel = BasePanel(width=300)
        self.assertEqual(panel.width(), 300)

    def test_init_height_only(self):
        panel = BasePanel(height=400)
        self.assertEqual(panel.height(), 400)

    def test_delegate_signal(self):
        """delegate signal emits (cmd, info, data)."""
        panel = BasePanel()
        received = []
        panel.delegate.connect(lambda c, i, d: received.append((c, i, d)))
        panel.invoke_delegate(42, 'info', 'data')
        self.assertEqual(received, [(42, 'info', 'data')])

    def test_load_pixmap_no_resource_dir(self):
        panel = BasePanel()
        self.assertIsNone(panel.load_pixmap('test.png'))

    def test_set_resource_dir(self):
        panel = BasePanel()
        panel.set_resource_dir('/tmp')
        self.assertIsNotNone(panel._resource_dir)


class TestImageLabel(unittest.TestCase):
    """Test ImageLabel widget."""

    def test_init(self):
        label = ImageLabel(320, 320)
        self.assertEqual(label.width(), 320)
        self.assertEqual(label.height(), 320)

    def test_set_image(self):
        label = ImageLabel(100, 100)
        img = QImage(100, 100, QImage.Format.Format_RGB32)
        label.set_image(img)
        self.assertFalse(label.pixmap().isNull())

    def test_set_image_resizes(self):
        """Image is resized to fit label dimensions."""
        label = ImageLabel(50, 50)
        img = QImage(200, 200, QImage.Format.Format_RGB32)
        label.set_image(img)
        self.assertEqual(label.pixmap().width(), 50)

    def test_set_none_clears(self):
        label = ImageLabel(100, 100)
        img = QImage(100, 100, QImage.Format.Format_RGB32)
        label.set_image(img)
        label.set_image(None)
        # After clear, pixmap should be null or label text empty
        pix = label.pixmap()
        self.assertTrue(pix is None or pix.isNull())

    def test_clicked_signal(self):
        label = ImageLabel(100, 100)
        fired = []
        label.clicked.connect(lambda: fired.append(True))
        # Simulate click via mousePressEvent
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(50, 50),
            QPointF(50, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        label.mousePressEvent(event)
        self.assertTrue(fired)


class TestClickableFrame(unittest.TestCase):
    """Test ClickableFrame signal."""

    def test_click_emits_signal(self):
        frame = ClickableFrame()
        fired = []
        frame.clicked.connect(lambda: fired.append(True))

        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        frame.mousePressEvent(event)
        self.assertTrue(fired)


class TestBaseThumbnail(unittest.TestCase):
    """Test BaseThumbnail widget."""

    def test_init(self):
        thumb = BaseThumbnail(ThemeItem(name='TestTheme'))
        self.assertEqual(thumb.width(), Sizes.THUMB_W)
        self.assertEqual(thumb.height(), Sizes.THUMB_H)
        self.assertFalse(thumb.selected)

    def test_display_name(self):
        thumb = BaseThumbnail(ThemeItem(name='MyTheme'))
        self.assertEqual(thumb.name_label.text(), 'MyTheme')

    def test_long_name_truncated(self):
        """Names > 15 chars are truncated to 12 + '...'."""
        thumb = BaseThumbnail(ThemeItem(name='VeryLongThemeNameHere'))
        self.assertTrue(thumb.name_label.text().endswith('...'))
        self.assertLessEqual(len(thumb.name_label.text()), Sizes.THUMB_NAME_MAX)

    def test_set_selected(self):
        thumb = BaseThumbnail(ThemeItem(name='T'))
        thumb.set_selected(True)
        self.assertTrue(thumb.selected)
        thumb.set_selected(False)
        self.assertFalse(thumb.selected)

    def test_clicked_signal(self):
        info = ThemeItem(name='Clicked')
        thumb = BaseThumbnail(info)
        received = []
        thumb.clicked.connect(lambda d: received.append(d))

        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(60, 60),
            QPointF(60, 60),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        thumb.mousePressEvent(event)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].name, 'Clicked')


class TestCreateImageButton(unittest.TestCase):
    """Test create_image_button factory."""

    def test_fallback_text(self):
        """When images don't exist, shows fallback text."""
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        btn = create_image_button(
            parent, 10, 20, 80, 30,
            'nonexistent.png', 'nonexistent_a.png',
            fallback_text='Test'
        )
        self.assertEqual(btn.text(), 'Test')
        self.assertEqual(btn.x(), 10)
        self.assertEqual(btn.y(), 20)

    def test_checkable(self):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        btn = create_image_button(
            parent, 0, 0, 50, 50,
            None, None, checkable=True, fallback_text='Check'
        )
        self.assertTrue(btn.isCheckable())


class TestSetBackgroundPixmap(unittest.TestCase):
    """Test set_background_pixmap utility."""

    def test_fallback_style(self):
        """When asset doesn't exist, applies fallback stylesheet."""
        from PySide6.QtWidgets import QWidget
        widget = QWidget()
        widget.setFixedSize(100, 100)
        result = set_background_pixmap(
            widget, 'nonexistent_bg.png',
            fallback_style='background: red;'
        )
        self.assertIsNone(result)

    def test_with_qpixmap_directly(self):
        """Passing QPixmap directly installs paint event filter."""
        from PySide6.QtWidgets import QWidget
        widget = QWidget()
        widget.setFixedSize(50, 50)
        pix = QPixmap(50, 50)
        pix.fill()
        result = set_background_pixmap(widget, pix)
        self.assertIsNotNone(result)
        # Uses paint event filter (no tiling), not QPalette auto-fill
        self.assertFalse(widget.autoFillBackground())


# Import Qt here for the mouse event helper
from PySide6.QtCore import Qt  # noqa: E402

if __name__ == '__main__':
    unittest.main()
