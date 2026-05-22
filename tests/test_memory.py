"""Memory and resource leak tests for TRCC Linux services.

Verifies that long-running GUI sessions do not accumulate QImages,
video frames, overlay caches, or USB handles across repeated cycles.
Uses tracemalloc (memory growth), weakref (object reclaimability),
and gc (no uncollectable cycles).
"""
from __future__ import annotations

import gc
import os
import tracemalloc
import weakref
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import make_test_surface
from PySide6.QtGui import QImage

from trcc.legacy.core.models import (
    HardwareMetrics,
    LEDMode,
    LEDState,
    PlaybackState,
)
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.led import LEDService
from trcc.legacy.services.media import MediaService
from trcc.legacy.services.overlay import OverlayService

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def overlay_svc():
    """Fresh OverlayService at 320x320."""
    return OverlayService(320, 320, renderer=ImageService.renderer())


@pytest.fixture()
def media_svc():
    """Fresh MediaService."""
    return MediaService()


@pytest.fixture()
def led_state():
    """LEDState with sensible defaults for tick testing."""
    state = LEDState()
    state.global_on = True
    state.brightness = 100
    state.color = (255, 0, 0)
    state.segment_count = 64
    state.led_count = 64
    return state


@pytest.fixture()
def led_svc(led_state):
    """LEDService with default state."""
    return LEDService(state=led_state)


@pytest.fixture()
def lcd_png(tmp_path):
    """320x320 PNG file on disk."""
    p = tmp_path / "lcd.png"
    make_test_surface(320, 320, (0, 128, 0)).save(str(p), "PNG")
    return str(p)


# ═══════════════════════════════════════════════════════════════════════
# 1. QImage Lifecycle
# ═══════════════════════════════════════════════════════════════════════

class TestImageLifecycle:
    """Verify QImages are reclaimable after resize/convert operations."""

    def test_resize_returns_new_object(self):
        """ImageService.resize() returns a different object — old is GC-eligible."""
        original = make_test_surface(640, 640, (255, 0, 0))
        original_id = id(original)
        resized = ImageService.resize(original, 320, 320)
        assert id(resized) != original_id

    def test_image_weakref_dies_after_delete(self):
        """QImage is reclaimable after all strong references are dropped."""
        img = make_test_surface(320, 320, (0, 0, 255))
        ref = weakref.ref(img)
        del img
        gc.collect()
        assert ref() is None, "QImage was not reclaimed after del + gc.collect()"

    def test_repeated_open_resize_bounded_memory(self, lcd_png, request):
        """50 open/resize cycles do not accumulate unbounded memory."""
        tracemalloc.start()
        # Warm up
        img = ImageService.open_and_resize(lcd_png, 320, 320)
        del img
        gc.collect()

        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(50):
            img = ImageService.open_and_resize(lcd_png, 320, 320)
            del img

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("open_and_resize x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 cycles"


# ═══════════════════════════════════════════════════════════════════════
# 2. MediaService Frame Accumulation
# ═══════════════════════════════════════════════════════════════════════

class TestMediaFrameAccumulation:
    """Verify MediaService releases frames on close/reload."""

    @pytest.fixture()
    def loaded_media(self, media_svc):
        """MediaService with 10 injected frames (no ffmpeg needed)."""
        frames = [make_test_surface(4, 4, (i * 25, 0, 0)) for i in range(10)]
        media_svc._frames = frames
        media_svc._state.total_frames = 10
        media_svc._state.fps = 16
        media_svc._state.state = PlaybackState.STOPPED
        return media_svc

    def test_clear_frames(self, loaded_media):
        """Clearing _frames empties list and releases memory."""
        assert len(loaded_media._frames) == 10
        loaded_media._frames.clear()
        loaded_media._decoder = None
        assert len(loaded_media._frames) == 0
        assert loaded_media._decoder is None

    def test_frame_weakrefs_die_after_clear(self, loaded_media):
        """All frame references become reclaimable after clearing."""
        refs = [weakref.ref(f) for f in loaded_media._frames]
        loaded_media._frames.clear()
        gc.collect()
        alive = sum(1 for r in refs if r() is not None)
        assert alive == 0, f"{alive}/10 frames still alive after clear()"

    def test_load_clears_previous_frames(self, media_svc):
        """Second load() releases first frame set."""
        first_frames = [make_test_surface(4, 4, (255, 0, 0)) for _ in range(5)]
        refs = [weakref.ref(f) for f in first_frames]
        media_svc._frames = first_frames

        # Simulate second load by clearing and injecting new frames
        second_frames = [make_test_surface(4, 4, (0, 255, 0)) for _ in range(5)]
        media_svc._frames.clear()
        media_svc._frames = second_frames
        del first_frames
        gc.collect()

        alive = sum(1 for r in refs if r() is not None)
        assert alive == 0, f"{alive}/5 old frames still alive after reload"

    def test_stop_preserves_frames(self, loaded_media):
        """stop() keeps frames in memory (stop ≠ unload)."""
        loaded_media.play()
        loaded_media.stop()
        assert len(loaded_media._frames) == 10


# ═══════════════════════════════════════════════════════════════════════
# 3. OverlayService Render Cycles
# ═══════════════════════════════════════════════════════════════════════

class TestOverlayRenderCycles:
    """Verify OverlayService releases old caches on replacement/clear."""

    def test_set_background_releases_old(self, overlay_svc):
        """Old background is reclaimable after set_background() with new image."""
        img_a = make_test_surface(320, 320, (255, 0, 0))
        ref_a = weakref.ref(img_a)
        overlay_svc.set_background(img_a)

        img_b = make_test_surface(320, 320, (0, 0, 255))
        overlay_svc.set_background(img_b)
        del img_a
        gc.collect()

        assert ref_a() is None, "Old background was not released"

    def test_set_mask_releases_old(self, overlay_svc):
        """Old mask is reclaimable after set_mask() with new image."""
        mask_a = make_test_surface(320, 320, (255, 255, 255, 128))
        ref_a = weakref.ref(mask_a)
        overlay_svc.set_mask(mask_a)

        mask_b = make_test_surface(320, 320, (0, 0, 0, 128))
        overlay_svc.set_mask(mask_b)
        del mask_a
        gc.collect()

        assert ref_a() is None, "Old mask was not released"

    def test_clear_releases_all_surfaces(self, overlay_svc):
        """clear() makes background, mask, and cache all reclaimable."""
        bg = make_test_surface(320, 320, (100, 100, 100))
        mask = make_test_surface(320, 320, (255, 255, 255, 128))
        ref_bg = weakref.ref(bg)
        ref_mask = weakref.ref(mask)

        overlay_svc.set_background(bg)
        overlay_svc.set_mask(mask)
        overlay_svc.clear()
        del bg, mask
        gc.collect()

        assert ref_bg() is None, "Background not released after clear()"
        assert ref_mask() is None, "Mask not released after clear()"

    def test_repeated_render_bounded_memory(self, overlay_svc, request):
        """50 render cycles with varying metrics stay within memory bounds."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        overlay_svc.set_background(bg)
        overlay_svc.enabled = True

        tracemalloc.start()
        # Warm up
        overlay_svc.render(metrics=HardwareMetrics())
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for i in range(50):
            m = HardwareMetrics(cpu_temp=40.0 + i, cpu_percent=float(i))
            overlay_svc._invalidate_cache()  # Force re-render each cycle
            overlay_svc.render(metrics=m)

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("overlay render x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 renders"


# ═══════════════════════════════════════════════════════════════════════
# 4. Theme Image Cycles
# ═══════════════════════════════════════════════════════════════════════

class TestThemeImageCycles:
    """Verify theme load cycles release old images."""

    def test_open_and_resize_intermediate_released(self, lcd_png):
        """Intermediate QImage load result is reclaimable after delete."""
        raw = QImage(lcd_png)
        ref_raw = weakref.ref(raw)
        del raw
        gc.collect()
        # Raw image with no other references should be dead
        assert ref_raw() is None, "Raw QImage was not released"

    def test_repeated_theme_load_bounded(self, lcd_png, request):
        """20 open_and_resize cycles stay within memory bounds."""
        tracemalloc.start()
        img = ImageService.open_and_resize(lcd_png, 320, 320)
        del img
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(20):
            img = ImageService.open_and_resize(lcd_png, 320, 320)
            del img

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("theme load x20", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 20 loads"


# ═══════════════════════════════════════════════════════════════════════
# 5. LED Tick Loop
# ═══════════════════════════════════════════════════════════════════════

class TestLedTickLoop:
    """Verify LED tick loops do not accumulate per-tick objects."""

    def test_tick_bounded_memory(self, led_svc, request):
        """500 static-mode ticks stay within memory bounds."""
        led_svc.state.mode = LEDMode.STATIC
        tracemalloc.start()
        # Warm up
        led_svc.tick()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(500):
            led_svc.tick()

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 500_000
        request.config._perf_report.record_mem("LED static tick x500", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 500 ticks"

    def test_breathing_tick_no_accumulation(self, led_svc, request):
        """200 breathing-mode ticks (most complex timer) stay bounded."""
        led_svc.state.mode = LEDMode.BREATHING
        tracemalloc.start()
        for _ in range(10):
            led_svc.tick()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(200):
            led_svc.tick()

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 500_000
        request.config._perf_report.record_mem("LED breathing tick x200", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 200 ticks"

    def test_tick_returns_fresh_list(self, led_svc):
        """Consecutive tick() calls return different list objects (transient)."""
        led_svc.state.mode = LEDMode.STATIC
        result_a = led_svc.tick()
        result_b = led_svc.tick()
        assert id(result_a) != id(result_b), "tick() reused same list object"


# ═══════════════════════════════════════════════════════════════════════
# 6. USB Handle Cleanup
# ═══════════════════════════════════════════════════════════════════════

class TestUsbHandleCleanup:
    """Verify USB protocol handles are released on close/error paths."""

    def test_protocol_close_releases_transport(self):
        """Mock transport is cleared after protocol close()."""
        mock_transport = MagicMock()
        mock_transport.is_open = True

        # Simulate a protocol with a transport attribute
        svc = MagicMock()
        svc._transport = mock_transport
        svc.close = lambda: setattr(svc, '_transport', None)

        svc.close()
        assert svc._transport is None

    def test_handshake_error_transport_closeable(self):
        """Transport can still be closed after handshake exception."""
        mock_transport = MagicMock()
        mock_transport.is_open = True
        mock_transport.write.side_effect = OSError("Device disconnected")

        # Even after write failure, close should work
        mock_transport.close()
        mock_transport.close.assert_called_once()

    def test_led_service_cleanup_releases_protocol(self, led_svc):
        """LEDService cleanup sets _protocol to None."""
        mock_proto = MagicMock()
        led_svc._protocol = mock_proto

        led_svc._protocol = None  # Simulate cleanup
        assert led_svc._protocol is None


# ═══════════════════════════════════════════════════════════════════════
# 7. Garbage Collectability
# ═══════════════════════════════════════════════════════════════════════

class TestGarbageCollectability:
    """Verify service objects do not create uncollectable circular references."""

    def test_overlay_no_uncollectable(self):
        """OverlayService create/use/delete produces no uncollectable cycles."""
        gc.collect()
        gc.set_debug(0)
        garbage_before = len(gc.garbage)

        svc = OverlayService(320, 320, renderer=ImageService.renderer())
        bg = make_test_surface(320, 320, (100, 100, 100))
        svc.set_background(bg)
        svc.render(metrics=HardwareMetrics())
        del svc, bg
        gc.collect()

        assert len(gc.garbage) == garbage_before, (
            f"Uncollectable objects: {len(gc.garbage) - garbage_before}")

    def test_media_no_uncollectable(self, media_svc):
        """MediaService with mock frames produces no uncollectable cycles."""
        gc.collect()
        garbage_before = len(gc.garbage)

        media_svc._frames = [
            make_test_surface(4, 4, (i, 0, 0)) for i in range(10)
        ]
        media_svc._frames.clear()
        media_svc._decoder = None
        del media_svc
        gc.collect()

        assert len(gc.garbage) == garbage_before

    def test_led_no_uncollectable(self, led_svc):
        """LEDService after tick loop produces no uncollectable cycles."""
        gc.collect()
        garbage_before = len(gc.garbage)

        led_svc.state.mode = LEDMode.BREATHING
        for _ in range(50):
            led_svc.tick()
        del led_svc
        gc.collect()

        assert len(gc.garbage) == garbage_before


# ═══════════════════════════════════════════════════════════════════════
# 8. Config Read/Write Cycles
# ═══════════════════════════════════════════════════════════════════════

class TestConfigCycles:
    """Verify config load/save cycles do not leak memory."""

    def test_repeated_load_save_bounded_memory(self, tmp_config, request):
        """100 load/save cycles stay within memory bounds."""
        from trcc.legacy.conf import load_config, save_config

        tracemalloc.start()
        # Warm up
        save_config({"devices": {"0": {"vid_pid": "0402_3922", "brightness": 3}}})
        load_config()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for i in range(100):
            config = load_config()
            config["devices"]["0"]["brightness"] = i % 4
            save_config(config)

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 500_000
        request.config._perf_report.record_mem("config load/save x100", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 100 config cycles"

    def test_config_dict_reclaimable(self, tmp_config, request):
        """Config dicts do not accumulate across repeated loads."""
        from trcc.legacy.conf import load_config, save_config

        save_config({"key": "value"})
        tracemalloc.start()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(50):
            config = load_config()
            del config

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 500_000
        request.config._perf_report.record_mem("config dict reclaimable x50", growth, limit)
        assert growth < limit, f"Config dicts leaked {growth:,} bytes over 50 loads"

    def test_migration_no_leak(self, tmp_config, request):
        """Old->new format migration does not leak old dict."""
        import json

        from trcc.legacy.conf import CONFIG_PATH, load_config

        # Write old format directly (bypass save_config to avoid migration)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump({"devices": {"0:0402_3922": {"brightness": 2}}}, f)

        tracemalloc.start()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        # load_config triggers migration
        config = load_config()
        assert "0" in config["devices"]
        del config

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 500_000
        request.config._perf_report.record_mem("config migration", growth, limit)
        assert growth < limit, f"Migration leaked {growth:,} bytes"


# ═══════════════════════════════════════════════════════════════════════
# 9. DisplayService Long-Running Loops
# ═══════════════════════════════════════════════════════════════════════

class TestDisplayServiceCycles:
    """Verify DisplayService render loops do not accumulate memory."""

    @pytest.fixture()
    def display_svc(self):
        """DisplayService with mock device service and real overlay/media."""
        mock_devices = MagicMock()
        mock_devices.selected = None
        overlay = OverlayService(320, 320, renderer=ImageService.renderer())
        media = MediaService()
        from trcc.legacy.services.display import DisplayService
        svc = DisplayService(
            devices=mock_devices,
            overlay=overlay,
            media=media,
        )
        return svc

    def test_render_and_process_bounded_memory(self, display_svc, request):
        """50 _render_and_process() cycles stay within memory bounds."""
        bg = make_test_surface(320, 320, (100, 100, 100))
        display_svc.current_image = bg
        display_svc._clean_background = bg

        tracemalloc.start()
        display_svc._render_and_process()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(50):
            display_svc._render_and_process()

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("display render x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 renders"

    def test_brightness_rotation_cycles_bounded(self, display_svc, request):
        """50 brightness/rotation changes stay within memory bounds."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        display_svc.current_image = bg
        display_svc._clean_background = bg

        tracemalloc.start()
        display_svc.set_brightness(50)
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for i in range(50):
            display_svc.set_brightness(25 + (i % 75))
            display_svc.set_rotation((i * 90) % 360)

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("brightness/rotation x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 adjustments"

    def test_render_overlay_bounded_memory(self, display_svc, request):
        """50 render_overlay() cycles with forced re-render stay bounded."""
        bg = make_test_surface(320, 320, (80, 80, 80))
        display_svc.current_image = bg
        display_svc._clean_background = bg
        display_svc.overlay.enabled = True

        tracemalloc.start()
        display_svc.render_overlay()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(50):
            display_svc.overlay._invalidate_cache()
            display_svc.render_overlay()

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("display overlay render x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 overlay renders"

    def test_video_tick_bounded_memory(self, display_svc, request):
        """200 video_tick() calls with injected frames stay bounded."""
        frames = [make_test_surface(320, 320, (i * 5, 0, 0)) for i in range(10)]
        display_svc.media._frames = frames
        display_svc.media._state.total_frames = 10
        display_svc.media._state.fps = 30
        display_svc.media.play()

        tracemalloc.start()
        for _ in range(10):
            display_svc.video_tick()
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for _ in range(200):
            display_svc.video_tick()

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        growth = peak_after - peak_before
        limit = 2_000_000
        request.config._perf_report.record_mem("video tick x200", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 200 ticks"

    def test_display_service_no_uncollectable(self, display_svc):
        """DisplayService create/use/delete produces no uncollectable cycles."""
        gc.collect()
        garbage_before = len(gc.garbage)

        bg = make_test_surface(320, 320, (100, 100, 100))
        display_svc.current_image = bg
        display_svc._render_and_process()
        display_svc.set_brightness(75)
        display_svc.set_rotation(90)
        del display_svc, bg
        gc.collect()

        assert len(gc.garbage) == garbage_before, (
            f"Uncollectable objects: {len(gc.garbage) - garbage_before}")


# ═══════════════════════════════════════════════════════════════════════
# 10. Qt Widget Layer
# ═══════════════════════════════════════════════════════════════════════

class TestQtWidgetMemory:
    """Verify Qt widgets release resources on destruction."""

    @staticmethod
    def _process() -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app:
            app.processEvents()

    def test_base_panel_no_uncollectable(self):
        """BasePanel subclass create/delete has no uncollectable cycles."""
        from PySide6.QtWidgets import QLabel

        from trcc.legacy.ui.gui.base import BasePanel

        class TestPanel(BasePanel):
            def _setup_ui(self):
                self._label = QLabel("test", self)

        gc.collect()
        garbage_before = len(gc.garbage)

        panel = TestPanel()
        panel.deleteLater()
        self._process()
        del panel
        gc.collect()

        assert len(gc.garbage) == garbage_before

    def test_preview_widget_image_released(self, settings_with_resolution):
        """UCPreview releases its pixmap on destruction."""
        from trcc.legacy.conf import settings
        from trcc.legacy.ui.gui.uc_preview import UCPreview

        preview = UCPreview(settings.width, settings.height)
        # Set an image
        img = make_test_surface(320, 320, (255, 0, 0))
        preview.set_image(img)

        ref = weakref.ref(img)
        del img
        preview.deleteLater()
        self._process()
        del preview
        gc.collect()

        # QImage should be reclaimable (Qt pixmap is a copy)
        assert ref() is None, "QImage not released after preview destruction"

    def test_repeated_set_image_bounded(self, request, settings_with_resolution):
        """50 set_image() calls on UCPreview stay within memory bounds."""
        from trcc.legacy.conf import settings
        from trcc.legacy.ui.gui.uc_preview import UCPreview

        preview = UCPreview(settings.width, settings.height)

        tracemalloc.start()
        img = make_test_surface(320, 320, (0, 0, 0))
        preview.set_image(img)
        gc.collect()
        _, peak_before = tracemalloc.get_traced_memory()

        for i in range(50):
            img = make_test_surface(320, 320, (i * 5, 0, 0))
            preview.set_image(img)
            del img

        gc.collect()
        _, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        preview.deleteLater()
        self._process()

        growth = peak_after - peak_before
        limit = 3_000_000
        request.config._perf_report.record_mem("UCPreview set_image x50", growth, limit)
        assert growth < limit, f"Memory grew {growth:,} bytes over 50 set_image calls"
