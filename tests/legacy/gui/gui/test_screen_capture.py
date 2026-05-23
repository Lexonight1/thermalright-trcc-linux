"""Tests for gui/screen_capture.py — screen capture utilities.

Covers:
- is_wayland() — env var detection
- BaseScreenOverlay — construction, keyPressEvent (ESC cancel)
- ScreenCaptureOverlay — construction, _selection_rect normalization,
  _emit_cancel signal, MIN_SELECTION guard
"""
from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"


from PySide6.QtCore import QPoint

from trcc.legacy.ui.gui.screen_capture import (
    ScreenCaptureOverlay,
    is_wayland,
)

# =========================================================================
# is_wayland()
# =========================================================================


class TestIsWayland:
    """is_wayland() — detects session type from environment."""

    def setup_method(self):
        is_wayland.cache_clear()

    def teardown_method(self):
        is_wayland.cache_clear()

    def test_wayland_session_type(self, monkeypatch):
        monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        assert is_wayland() is True

    def test_wayland_display_set(self, monkeypatch):
        is_wayland.cache_clear()
        monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
        assert is_wayland() is True

    def test_x11_session(self, monkeypatch):
        is_wayland.cache_clear()
        monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        assert is_wayland() is False

    def test_unset_vars(self, monkeypatch):
        is_wayland.cache_clear()
        monkeypatch.delenv('XDG_SESSION_TYPE', raising=False)
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        assert is_wayland() is False

    def test_cached(self, monkeypatch):
        is_wayland.cache_clear()
        monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        result1 = is_wayland()
        # Change env — should still return cached value
        monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
        result2 = is_wayland()
        assert result1 == result2


# =========================================================================
# ScreenCaptureOverlay
# =========================================================================


class TestScreenCaptureOverlay:
    """ScreenCaptureOverlay — selection rect, cancel signal."""

    def test_construction(self):
        overlay = ScreenCaptureOverlay()
        assert overlay._selecting is False
        assert overlay._start == QPoint()

    def test_selection_rect_normalized(self):
        overlay = ScreenCaptureOverlay()
        # Top-left to bottom-right — QRect is inclusive on both ends
        overlay._start = QPoint(10, 20)
        overlay._end = QPoint(100, 200)
        rect = overlay._selection_rect()
        assert rect.left() == 10
        assert rect.top() == 20
        assert rect.right() == 100
        assert rect.bottom() == 200

    def test_selection_rect_inverted(self):
        """Drawing bottom-right to top-left normalizes to valid rect."""
        overlay = ScreenCaptureOverlay()
        overlay._start = QPoint(100, 200)
        overlay._end = QPoint(10, 20)
        rect = overlay._selection_rect()
        # Normalized rect has positive width/height
        assert rect.width() > 0
        assert rect.height() > 0
        # Contains both original points
        assert rect.contains(QPoint(50, 100))

    def test_emit_cancel_emits_none(self):
        overlay = ScreenCaptureOverlay()
        received = []
        overlay.captured.connect(lambda x: received.append(x))
        overlay._emit_cancel()
        assert len(received) == 1
        assert received[0] is None

    def test_min_selection_constant(self):
        assert ScreenCaptureOverlay._MIN_SELECTION == 10

    def test_dim_color_has_alpha(self):
        assert ScreenCaptureOverlay._DIM_COLOR.alpha() > 0
        assert ScreenCaptureOverlay._DIM_COLOR.alpha() < 255


# =========================================================================
# EyedropperOverlay
# =========================================================================


class TestEyedropperOverlay:
    """EyedropperOverlay — construction, cancel, constants."""

    def test_construction(self):
        from trcc.legacy.ui.gui.eyedropper import EyedropperOverlay
        overlay = EyedropperOverlay()
        assert overlay._current_color.red() == 0
        assert overlay._cursor_pos == QPoint()

    def test_emit_cancel_emits_signal(self):
        from trcc.legacy.ui.gui.eyedropper import EyedropperOverlay
        overlay = EyedropperOverlay()
        received = []
        overlay.cancelled.connect(lambda: received.append(True))
        overlay._emit_cancel()
        assert len(received) == 1

    def test_magnify_constants(self):
        from trcc.legacy.ui.gui.eyedropper import EyedropperOverlay
        assert EyedropperOverlay.MAGNIFY_SIZE == 12
        assert EyedropperOverlay.MAGNIFY_SCALE == 10
        assert EyedropperOverlay.PREVIEW_OFFSET == 25
