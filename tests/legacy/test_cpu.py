"""CPU performance regression tests for TRCC Linux hot paths.

Measures actual CPU time (not wall time) for critical render, overlay,
LED, encoding, and config operations. Asserts that per-iteration CPU
cost stays under thresholds — regressions in algorithmic complexity or
accidental O(n²) loops will trip these bounds.

Uses time.process_time() for CPU-only measurement (unaffected by I/O
waits or system load). Reports measurements to the PerfReport collector
for Valgrind-style summary output.
"""
from __future__ import annotations

import gc
import os
import time
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import make_test_surface

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
def display_svc():
    """DisplayService with mock device service and real overlay/media."""
    mock_devices = MagicMock()
    mock_devices.selected = None
    overlay = OverlayService(320, 320, renderer=ImageService.renderer())
    media = MediaService()
    from trcc.legacy.services.display import DisplayService
    return DisplayService(devices=mock_devices, overlay=overlay, media=media)


@pytest.fixture()
def led_svc():
    """LEDService with 64-segment breathing red."""
    state = LEDState()
    state.global_on = True
    state.brightness = 100
    state.color = (255, 0, 0)
    state.segment_count = 64
    state.led_count = 64
    return LEDService(state=state)


@pytest.fixture()
def perf(request):
    """Shortcut to the PerfReport collector."""
    return request.config._perf_report


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _cpu_per_iter(fn, iterations: int = 100) -> float:
    """Run fn() N times and return average CPU seconds per iteration."""
    gc.collect()
    # Warm up
    fn()
    start = time.process_time()
    for _ in range(iterations):
        fn()
    elapsed = time.process_time() - start
    return elapsed / iterations


# ═══════════════════════════════════════════════════════════════════════
# 1. Image Operations
# ═══════════════════════════════════════════════════════════════════════

class TestImageCPU:
    """CPU bounds for ImageService operations (open, resize, encode)."""

    def test_open_and_resize_cpu(self, tmp_path, perf):
        """open_and_resize 320x320 PNG stays under 5ms/iter."""
        p = tmp_path / "test.png"
        make_test_surface(320, 320, (128, 0, 64)).save(str(p), "PNG")

        limit = 0.005
        avg = _cpu_per_iter(lambda: ImageService.open_and_resize(str(p), 320, 320))
        perf.record_cpu("open_and_resize 320x320", avg, limit)
        assert avg < limit, f"open_and_resize: {avg*1000:.1f}ms/iter (limit 5ms)"

    def test_resize_cpu(self, perf):
        """Resize 640->320 stays under 3ms/iter."""
        src = make_test_surface(640, 640, (200, 100, 50))
        limit = 0.003
        avg = _cpu_per_iter(lambda: ImageService.resize(src, 320, 320))
        perf.record_cpu("resize 640->320", avg, limit)
        assert avg < limit, f"resize: {avg*1000:.1f}ms/iter (limit 3ms)"

    def test_apply_brightness_cpu(self, perf):
        """Brightness adjustment stays under 2ms/iter."""
        img = make_test_surface(320, 320, (100, 100, 100))
        limit = 0.002
        avg = _cpu_per_iter(lambda: ImageService.apply_brightness(img, 50))
        perf.record_cpu("apply_brightness 50%", avg, limit)
        assert avg < limit, f"brightness: {avg*1000:.1f}ms/iter (limit 2ms)"

    def test_apply_rotation_cpu(self, perf):
        """90 degree rotation stays under 3ms/iter."""
        img = make_test_surface(320, 320, (100, 100, 100))
        limit = 0.003
        avg = _cpu_per_iter(lambda: ImageService.apply_rotation(img, 90))
        perf.record_cpu("apply_rotation 90deg", avg, limit)
        assert avg < limit, f"rotation: {avg*1000:.1f}ms/iter (limit 3ms)"


# ═══════════════════════════════════════════════════════════════════════
# 2. RGB565 Encoding
# ═══════════════════════════════════════════════════════════════════════

class TestEncodingCPU:
    """CPU bounds for device frame encoding."""

    def test_encode_rgb565_320(self, perf):
        """320x320 RGB565 encode stays under 5ms/iter."""
        r = ImageService.renderer()
        surface = make_test_surface(320, 320, (255, 128, 0))
        limit = 0.005
        avg = _cpu_per_iter(lambda: r.encode_rgb565(surface, '>'))
        perf.record_cpu("encode_rgb565 320x320", avg, limit)
        assert avg < limit, f"rgb565 320: {avg*1000:.1f}ms/iter (limit 5ms)"

    def test_encode_rgb565_480(self, perf):
        """480x480 RGB565 encode stays under 10ms/iter."""
        r = ImageService.renderer()
        surface = make_test_surface(480, 480, (0, 128, 255))
        limit = 0.010
        avg = _cpu_per_iter(lambda: r.encode_rgb565(surface, '>'))
        perf.record_cpu("encode_rgb565 480x480", avg, limit)
        assert avg < limit, f"rgb565 480: {avg*1000:.1f}ms/iter (limit 10ms)"


# ═══════════════════════════════════════════════════════════════════════
# 3. Overlay Rendering
# ═══════════════════════════════════════════════════════════════════════

class TestOverlayCPU:
    """CPU bounds for overlay compositing pipeline."""

    def test_overlay_render_no_config(self, overlay_svc, perf):
        """Overlay render with no config (fast path) under 0.5ms/iter."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        overlay_svc.set_background(bg)
        overlay_svc.enabled = True

        limit = 0.0005
        avg = _cpu_per_iter(lambda: overlay_svc.render(bg))
        perf.record_cpu("overlay render (no config)", avg, limit)
        assert avg < limit, f"overlay no-config: {avg*1000:.2f}ms/iter (limit 0.5ms)"

    def test_overlay_render_with_config(self, overlay_svc, perf):
        """Overlay render with text config under 5ms/iter."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        overlay_svc.set_background(bg)
        overlay_svc.enabled = True
        overlay_svc.config = {
            'cpu_temp': {'x': 10, 'y': 10, 'color': '#ff0000', 'font_size': 20,
                         'text': '65\u00b0C'},
            'gpu_temp': {'x': 10, 'y': 40, 'color': '#00ff00', 'font_size': 20,
                         'text': '70\u00b0C'},
        }

        metrics = HardwareMetrics(cpu_temp=65.0, gpu_temp=70.0)
        limit = 0.005
        avg = _cpu_per_iter(lambda: overlay_svc.render(bg, metrics=metrics))
        perf.record_cpu("overlay render (2 elements)", avg, limit)
        assert avg < limit, f"overlay with config: {avg*1000:.1f}ms/iter (limit 5ms)"

    def test_overlay_cache_hit(self, overlay_svc, perf):
        """Repeated render with same metrics hits cache — under 0.1ms/iter."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        overlay_svc.set_background(bg)
        overlay_svc.enabled = True
        overlay_svc.config = {
            'cpu_temp': {'x': 10, 'y': 10, 'color': '#ff0000', 'font_size': 20,
                         'text': '65\u00b0C'},
        }
        metrics = HardwareMetrics(cpu_temp=65.0)
        # Prime the cache
        overlay_svc.render(bg, metrics=metrics)

        limit = 0.0001
        avg = _cpu_per_iter(
            lambda: overlay_svc.render(bg, metrics=metrics), iterations=500)
        perf.record_cpu("overlay cache hit", avg, limit)
        assert avg < limit, f"overlay cache hit: {avg*1000:.3f}ms/iter (limit 0.1ms)"


# ═══════════════════════════════════════════════════════════════════════
# 4. DisplayService Render Pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestDisplayServiceCPU:
    """CPU bounds for the full display render pipeline."""

    def test_render_and_process_cpu(self, display_svc, perf):
        """_render_and_process() under 3ms/iter (no overlay)."""
        bg = make_test_surface(320, 320, (100, 100, 100))
        display_svc.current_image = bg
        display_svc._clean_background = bg

        limit = 0.003
        avg = _cpu_per_iter(lambda: display_svc._render_and_process())
        perf.record_cpu("render_and_process (plain)", avg, limit)
        assert avg < limit, f"render_and_process: {avg*1000:.1f}ms/iter (limit 3ms)"

    def test_render_and_process_with_overlay_cpu(self, display_svc, perf):
        """_render_and_process() with overlay under 5ms/iter."""
        bg = make_test_surface(320, 320, (100, 100, 100))
        display_svc.current_image = bg
        display_svc._clean_background = bg
        display_svc.overlay.enabled = True
        display_svc.overlay.set_background(bg)

        limit = 0.005
        avg = _cpu_per_iter(lambda: display_svc._render_and_process())
        perf.record_cpu("render_and_process + overlay", avg, limit)
        assert avg < limit, f"render+overlay: {avg*1000:.1f}ms/iter (limit 5ms)"

    def test_render_and_process_with_adjustments_cpu(self, display_svc, perf):
        """_render_and_process() with brightness+rotation under 5ms/iter."""
        bg = make_test_surface(320, 320, (100, 100, 100))
        display_svc.current_image = bg
        display_svc._clean_background = bg
        display_svc.brightness = 70
        display_svc.rotation = 90

        limit = 0.005
        avg = _cpu_per_iter(lambda: display_svc._render_and_process())
        perf.record_cpu("render_and_process + adjust", avg, limit)
        assert avg < limit, f"render+adjust: {avg*1000:.1f}ms/iter (limit 5ms)"

    def test_video_tick_cpu(self, display_svc, perf):
        """video_tick() with injected frames under 3ms/iter."""
        frames = [make_test_surface(320, 320, (i * 25, 0, 0)) for i in range(10)]
        display_svc.media._frames = frames
        display_svc.media._state.total_frames = 10
        display_svc.media._state.fps = 30
        display_svc.media._state.state = PlaybackState.PLAYING

        limit = 0.003
        avg = _cpu_per_iter(lambda: display_svc.video_tick())
        perf.record_cpu("video_tick", avg, limit)
        assert avg < limit, f"video_tick: {avg*1000:.1f}ms/iter (limit 3ms)"


# ═══════════════════════════════════════════════════════════════════════
# 5. LED Service
# ═══════════════════════════════════════════════════════════════════════

class TestLEDCPU:
    """CPU bounds for LED animation tick."""

    def test_led_tick_breathing(self, led_svc, perf):
        """LED breathing tick (64 segments) under 0.5ms/iter."""
        led_svc.state.mode = LEDMode.BREATHING
        limit = 0.0005
        avg = _cpu_per_iter(lambda: led_svc.tick(), iterations=500)
        perf.record_cpu("LED tick breathing (64 seg)", avg, limit)
        assert avg < limit, f"LED breathing: {avg*1000:.2f}ms/iter (limit 0.5ms)"

    def test_led_tick_rainbow(self, led_svc, perf):
        """LED rainbow tick (64 segments) under 0.5ms/iter."""
        led_svc.state.mode = LEDMode.RAINBOW
        limit = 0.0005
        avg = _cpu_per_iter(lambda: led_svc.tick(), iterations=500)
        perf.record_cpu("LED tick rainbow (64 seg)", avg, limit)
        assert avg < limit, f"LED rainbow: {avg*1000:.2f}ms/iter (limit 0.5ms)"

    def test_led_tick_static(self, led_svc, perf):
        """LED static tick (64 segments) under 0.2ms/iter."""
        led_svc.state.mode = LEDMode.STATIC
        limit = 0.0002
        avg = _cpu_per_iter(lambda: led_svc.tick(), iterations=500)
        perf.record_cpu("LED tick static (64 seg)", avg, limit)
        assert avg < limit, f"LED static: {avg*1000:.3f}ms/iter (limit 0.2ms)"

    def test_led_tick_128_segments(self, perf):
        """LED breathing with 128 segments under 1ms/iter."""
        state = LEDState()
        state.global_on = True
        state.brightness = 100
        state.color = (0, 255, 0)
        state.segment_count = 128
        state.led_count = 128
        state.mode = LEDMode.BREATHING
        svc = LEDService(state=state)
        limit = 0.001
        avg = _cpu_per_iter(lambda: svc.tick(), iterations=500)
        perf.record_cpu("LED tick breathing (128 seg)", avg, limit)
        assert avg < limit, f"LED 128seg: {avg*1000:.2f}ms/iter (limit 1ms)"


# ═══════════════════════════════════════════════════════════════════════
# 6. Config Load/Save
# ═══════════════════════════════════════════════════════════════════════

class TestConfigCPU:
    """CPU bounds for config file operations."""

    def test_load_config_cpu(self, tmp_config, perf):
        """load_config() under 1ms/iter."""
        from trcc.legacy.conf import load_config, save_config
        save_config({"devices": {"0": {"vid_pid": "0402_3922"}}})

        limit = 0.001
        avg = _cpu_per_iter(lambda: load_config(), iterations=200)
        perf.record_cpu("load_config", avg, limit)
        assert avg < limit, f"load_config: {avg*1000:.2f}ms/iter (limit 1ms)"

    def test_save_config_cpu(self, tmp_config, perf):
        """save_config() under 1ms/iter."""
        from trcc.legacy.conf import save_config
        data = {"devices": {"0": {"vid_pid": "0402_3922", "brightness": 80}}}

        limit = 0.001
        avg = _cpu_per_iter(lambda: save_config(data), iterations=200)
        perf.record_cpu("save_config", avg, limit)
        assert avg < limit, f"save_config: {avg*1000:.2f}ms/iter (limit 1ms)"


# ═══════════════════════════════════════════════════════════════════════
# 7. Scaling Regression — O(n) not O(n²)
# ═══════════════════════════════════════════════════════════════════════

class TestScalingRegression:
    """Verify operations scale linearly, not quadratically."""

    def test_led_tick_scales_linearly(self, perf):
        """LED tick CPU scales ~linearly with segment count."""
        times = {}
        for n in (32, 64, 128):
            state = LEDState()
            state.global_on = True
            state.brightness = 100
            state.color = (255, 0, 0)
            state.segment_count = n
            state.led_count = n
            state.mode = LEDMode.RAINBOW
            svc = LEDService(state=state)
            times[n] = _cpu_per_iter(lambda: svc.tick(), iterations=500)

        # 128 segments should cost <=4x the 32-segment time (linear = 4x)
        # O(n^2) would be 16x
        limit = 8.0
        ratio = times[128] / max(times[32], 1e-9)
        perf.record_scale("LED tick 128/32 seg ratio", ratio, limit)
        assert ratio < limit, (
            f"LED tick scaling: 128/32 ratio = {ratio:.1f}x (limit 8x, "
            f"32seg={times[32]*1000:.3f}ms, 128seg={times[128]*1000:.3f}ms)")

    def test_overlay_render_scales_with_elements(self, overlay_svc, perf):
        """Overlay render scales reasonably with element count."""
        bg = make_test_surface(320, 320, (50, 50, 50))
        overlay_svc.set_background(bg)
        overlay_svc.enabled = True
        metrics = HardwareMetrics(cpu_temp=65.0, gpu_temp=70.0)

        # 2 elements
        overlay_svc.config = {
            'cpu_temp': {'x': 10, 'y': 10, 'color': '#ff0000',
                         'font_size': 20, 'text': '65\u00b0C'},
            'gpu_temp': {'x': 10, 'y': 40, 'color': '#00ff00',
                         'font_size': 20, 'text': '70\u00b0C'},
        }
        overlay_svc._cache_key = None
        overlay_svc._overlay_cache = None
        t2 = _cpu_per_iter(
            lambda: overlay_svc.render(bg, metrics=metrics), iterations=50)

        # 6 elements
        overlay_svc.config = {
            f'el_{i}': {'x': 10, 'y': 10 + i * 30, 'color': '#ffffff',
                        'font_size': 20, 'text': f'val{i}'}
            for i in range(6)
        }
        overlay_svc._cache_key = None
        overlay_svc._overlay_cache = None
        t6 = _cpu_per_iter(
            lambda: overlay_svc.render(bg, metrics=metrics), iterations=50)

        # 6 elements should cost <=6x the 2-element time (linear = 3x)
        limit = 6.0
        ratio = t6 / max(t2, 1e-9)
        perf.record_scale("overlay 6/2 element ratio", ratio, limit)
        assert ratio < limit, (
            f"Overlay scaling: 6el/2el ratio = {ratio:.1f}x (limit 6x, "
            f"2el={t2*1000:.2f}ms, 6el={t6*1000:.2f}ms)")
