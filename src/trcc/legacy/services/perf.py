"""Performance benchmarking service — CPU time + memory leak detection.

Runs benchmarks against core services and returns a PerfReport.
No pytest dependency — pure Python with stdlib tracemalloc + time.
"""
from __future__ import annotations

import gc
import logging
import time
import tracemalloc
from collections.abc import Callable
from functools import partial
from typing import Any
from unittest.mock import MagicMock

from ..core.models import FBL_PROFILES, HardwareMetrics, LEDMode, LEDState, PlaybackState
from ..core.perf import PerfReport

log = logging.getLogger(__name__)


def _noop() -> None:
    """No-op setup hook for ``_mem_growth`` — strong-ref alternative to ``lambda: None``."""


def _cpu_per_iter(fn: Callable[[], Any], iterations: int = 100) -> float:
    """Run fn() N times and return average CPU seconds per iteration."""
    gc.collect()
    fn()  # warm up
    start = time.process_time()
    for _ in range(iterations):
        fn()
    return (time.process_time() - start) / iterations


def _mem_growth(setup: Callable[[], Any], body: Callable[[], Any],
                iterations: int) -> int:
    """Run body() N times and return peak memory growth in bytes."""
    setup()
    gc.collect()
    tracemalloc.start()
    body()  # warm up
    gc.collect()
    _, peak_before = tracemalloc.get_traced_memory()

    for _ in range(iterations):
        body()

    gc.collect()
    _, peak_after = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak_after - peak_before


def run_benchmarks() -> PerfReport:
    """Run all performance benchmarks and return a PerfReport."""
    log.debug("starting CPU/memory benchmarks")
    from ..services.image import ImageService
    from ..services.led import LEDService
    from ..services.media import MediaService
    from ..services.overlay import OverlayService

    report = PerfReport()
    r = ImageService.renderer()

    # Benchmark profiles — representative device resolutions from FBL_PROFILES
    _p320 = FBL_PROFILES[100]   # 320×320 RGB565 big-endian
    _p480 = FBL_PROFILES[72]    # 480×480 RGB565
    _w320, _h320 = _p320.width, _p320.height
    _w480, _h480 = _p480.width, _p480.height

    def _surface(w: int, h: int, color: tuple[int, ...] = (128, 0, 0)) -> Any:
        return r.create_surface(w, h, color)

    # ── CPU benchmarks ────────────────────────────────────────────

    # Image operations
    big_src = _surface(640, 640)
    avg = _cpu_per_iter(partial(ImageService.resize, big_src, _w320, _h320))
    report.record_cpu(f"resize 640->{_w320}", avg, 0.003)

    img = _surface(_w320, _h320, (100, 100, 100))
    avg = _cpu_per_iter(partial(ImageService.apply_brightness, img, 50))
    report.record_cpu("apply_brightness 50%", avg, 0.002)

    avg = _cpu_per_iter(partial(ImageService.apply_rotation, img, 90))
    report.record_cpu("apply_rotation 90deg", avg, 0.003)

    # RGB565 encoding
    s320 = _surface(_w320, _h320, (255, 128, 0))
    avg = _cpu_per_iter(partial(r.encode_rgb565, s320, '>'))
    report.record_cpu(f"encode_rgb565 {_w320}x{_h320}", avg, 0.005)

    s480 = _surface(_w480, _h480, (0, 128, 255))
    avg = _cpu_per_iter(partial(r.encode_rgb565, s480, '>'))
    report.record_cpu(f"encode_rgb565 {_w480}x{_h480}", avg, 0.010)

    # Overlay rendering
    overlay = OverlayService(_w320, _h320, renderer=r)
    bg = _surface(320, 320, (50, 50, 50))
    overlay.set_background(bg)
    overlay.enabled = True

    avg = _cpu_per_iter(partial(overlay.render, bg))
    report.record_cpu("overlay render (no config)", avg, 0.0005)

    overlay.config = {
        'cpu_temp': {'x': 10, 'y': 10, 'color': '#ff0000', 'font_size': 20,
                     'text': '65\u00b0C'},
        'gpu_temp': {'x': 10, 'y': 40, 'color': '#00ff00', 'font_size': 20,
                     'text': '70\u00b0C'},
    }
    metrics = HardwareMetrics(cpu_temp=65.0, gpu_temp=70.0)
    avg = _cpu_per_iter(partial(overlay.render, bg, metrics=metrics))
    report.record_cpu("overlay render (2 elements)", avg, 0.005)

    # Cache hit
    overlay.render(bg, metrics=metrics)  # prime
    avg = _cpu_per_iter(
        partial(overlay.render, bg, metrics=metrics), iterations=500)
    report.record_cpu("overlay cache hit", avg, 0.0001)

    # DisplayService pipeline
    from ..services.display import DisplayService
    mock_devices = MagicMock()
    mock_devices.selected = None
    dsvc = DisplayService(
        devices=mock_devices,
        overlay=OverlayService(320, 320, renderer=r),
        media=MediaService(),
    )
    dbg = _surface(320, 320, (100, 100, 100))
    dsvc.current_image = dbg
    dsvc._clean_background = dbg

    avg = _cpu_per_iter(dsvc._render_and_process)
    report.record_cpu("render_and_process (plain)", avg, 0.003)

    dsvc.overlay.enabled = True
    dsvc.overlay.set_background(dbg)
    avg = _cpu_per_iter(dsvc._render_and_process)
    report.record_cpu("render_and_process + overlay", avg, 0.005)

    dsvc.overlay.enabled = False
    dsvc.brightness = 70
    dsvc.rotation = 90
    avg = _cpu_per_iter(dsvc._render_and_process)
    report.record_cpu("render_and_process + adjust", avg, 0.005)

    # Video tick
    dsvc2 = DisplayService(
        devices=mock_devices,
        overlay=OverlayService(320, 320, renderer=r),
        media=MediaService(),
    )
    frames = [r.create_surface(320, 320, (i * 25, 0, 0)) for i in range(10)]
    dsvc2.media._frames = frames
    dsvc2.media._state.total_frames = 10
    dsvc2.media._state.fps = 30
    dsvc2.media._state.state = PlaybackState.PLAYING
    avg = _cpu_per_iter(dsvc2.video_tick)
    report.record_cpu("video_tick", avg, 0.003)

    # LED tick
    for mode_name, mode, limit in [
        ("breathing", LEDMode.BREATHING, 0.0005),
        ("rainbow", LEDMode.RAINBOW, 0.0005),
        ("static", LEDMode.STATIC, 0.0002),
    ]:
        state = LEDState()
        state.global_on = True
        state.brightness = 100
        state.color = (255, 0, 0)
        state.segment_count = 64
        state.led_count = 64
        state.mode = mode
        svc = LEDService(state=state)
        avg = _cpu_per_iter(svc.tick, iterations=500)
        report.record_cpu(f"LED tick {mode_name} (64 seg)", avg, limit)

    # LED 128 segments
    state128 = LEDState()
    state128.global_on = True
    state128.brightness = 100
    state128.color = (0, 255, 0)
    state128.segment_count = 128
    state128.led_count = 128
    state128.mode = LEDMode.BREATHING
    svc128 = LEDService(state=state128)
    avg = _cpu_per_iter(svc128.tick, iterations=500)
    report.record_cpu("LED tick breathing (128 seg)", avg, 0.001)

    # ── Memory benchmarks ─────────────────────────────────────────

    # Overlay render x50
    def _overlay_mem_setup():
        overlay.config = {}
        overlay._cache_key = None
        overlay._overlay_cache = None

    def _overlay_mem_body():
        overlay._invalidate_cache()
        overlay.render(bg, metrics=HardwareMetrics(cpu_temp=65.0))

    growth = _mem_growth(_overlay_mem_setup, _overlay_mem_body, 50)
    report.record_mem("overlay render x50", growth, 2_000_000)

    # Display render x50
    dsvc3 = DisplayService(
        devices=mock_devices,
        overlay=OverlayService(320, 320, renderer=r),
        media=MediaService(),
    )
    dsvc3.current_image = dbg
    dsvc3._clean_background = dbg
    growth = _mem_growth(_noop, dsvc3._render_and_process, 50)
    report.record_mem("display render x50", growth, 2_000_000)

    # LED tick x500
    led_static = LEDState()
    led_static.global_on = True
    led_static.brightness = 100
    led_static.color = (255, 0, 0)
    led_static.segment_count = 64
    led_static.led_count = 64
    led_static.mode = LEDMode.STATIC
    led_svc = LEDService(state=led_static)
    growth = _mem_growth(_noop, led_svc.tick, 500)
    report.record_mem("LED static tick x500", growth, 500_000)

    # LED breathing tick x200
    led_breath = LEDState()
    led_breath.global_on = True
    led_breath.brightness = 100
    led_breath.color = (255, 0, 0)
    led_breath.segment_count = 64
    led_breath.led_count = 64
    led_breath.mode = LEDMode.BREATHING
    led_bsvc = LEDService(state=led_breath)
    growth = _mem_growth(_noop, led_bsvc.tick, 200)
    report.record_mem("LED breathing tick x200", growth, 500_000)

    # ── Scaling benchmarks ────────────────────────────────────────

    # LED segment scaling
    times: dict[int, float] = {}
    for n in (32, 64, 128):
        st = LEDState()
        st.global_on = True
        st.brightness = 100
        st.color = (255, 0, 0)
        st.segment_count = n
        st.led_count = n
        st.mode = LEDMode.RAINBOW
        sv = LEDService(state=st)
        times[n] = _cpu_per_iter(sv.tick, iterations=500)

    ratio = times[128] / max(times[32], 1e-9)
    report.record_scale("LED tick 128/32 seg ratio", ratio, 8.0)

    # Overlay element scaling
    overlay2 = OverlayService(320, 320, renderer=r)
    overlay2.set_background(bg)
    overlay2.enabled = True
    m = HardwareMetrics(cpu_temp=65.0, gpu_temp=70.0)

    overlay2.config = {
        'cpu_temp': {'x': 10, 'y': 10, 'color': '#ff0000',
                     'font_size': 20, 'text': '65\u00b0C'},
        'gpu_temp': {'x': 10, 'y': 40, 'color': '#00ff00',
                     'font_size': 20, 'text': '70\u00b0C'},
    }
    overlay2._cache_key = None
    overlay2._overlay_cache = None
    t2 = _cpu_per_iter(partial(overlay2.render, bg, metrics=m), iterations=50)

    overlay2.config = {
        f'el_{i}': {'x': 10, 'y': 10 + i * 30, 'color': '#ffffff',
                    'font_size': 20, 'text': f'val{i}'}
        for i in range(6)
    }
    overlay2._cache_key = None
    overlay2._overlay_cache = None
    t6 = _cpu_per_iter(partial(overlay2.render, bg, metrics=m), iterations=50)

    ratio = t6 / max(t2, 1e-9)
    report.record_scale("overlay 6/2 element ratio", ratio, 6.0)

    return report


def run_device_benchmarks(
    *,
    detect_fn: Callable,
    get_protocol: Callable,
    get_protocol_info: Callable,
    probe_led_fn: Callable,
) -> PerfReport:
    """Benchmark the connected hardware device (LCD or LED).

    Measures real wall-clock I/O latencies:
    - USB handshake time
    - Frame encode + send latency
    - Sustained FPS over 3 seconds

    Uses time.perf_counter() (wall clock) because USB I/O is the bottleneck.
    Adapter dependencies are injected by the caller (CLI/API composition root).
    """
    log.debug("starting device benchmarks")
    report = PerfReport()
    return _run_device_benchmarks_inner(
        report,
        detect_fn=detect_fn,
        get_protocol=get_protocol,
        get_protocol_info=get_protocol_info,
        probe_led_fn=probe_led_fn,
    )


def _run_device_benchmarks_inner(
    report: PerfReport,
    *,
    detect_fn: Callable,
    get_protocol: Callable,
    get_protocol_info: Callable,
    probe_led_fn: Callable,
) -> PerfReport:
    """Inner device benchmark logic."""
    from ..services import DeviceService
    from ..services.image import ImageService

    # ── Detect + handshake ─────────────────────────────────────────
    svc = DeviceService(
        detect_fn=detect_fn,
        probe_led_fn=probe_led_fn,
        get_protocol=get_protocol,
        get_protocol_info=get_protocol_info,
    )
    svc.scan_and_select()  # detect + handshake (populates resolution/fbl)

    if not svc.devices:
        return report  # no devices — empty report

    # ── LCD benchmarks ─────────────────────────────────────────────
    lcd_dev = next(
        (d for d in svc.devices if d.implementation != 'hid_led'), None)

    if lcd_dev:
        if not svc.selected or svc.selected is not lcd_dev:
            svc.select(lcd_dev)
            svc._discover_resolution(lcd_dev)
        protocol = get_protocol(lcd_dev)
        w, h = lcd_dev.resolution or (320, 320)
        r = ImageService.renderer()

        # Handshake time (reconnect)
        protocol.close()
        t0 = time.perf_counter()
        protocol.handshake()
        handshake_s = time.perf_counter() - t0
        report.record_device("LCD handshake", handshake_s, 2.0)

        # Single frame encode + send
        img = r.create_surface(w, h, (255, 0, 0))
        ep = lcd_dev.encoding_params
        _, resolution, fbl, use_jpeg = ep

        # Encode-only timing
        t0 = time.perf_counter()
        _, data = ImageService.encode_for_device(
            img, ep[0], resolution, fbl, use_jpeg)
        encode_s = time.perf_counter() - t0
        report.record_device(f"LCD encode {w}x{h}", encode_s, 0.010)

        # Send-only timing (warm up first)
        protocol.send_data(data, w, h)
        t0 = time.perf_counter()
        protocol.send_data(data, w, h)
        send_s = time.perf_counter() - t0
        report.record_device("LCD send frame", send_s, 0.150)

        # Full pipeline: encode + send
        img2 = r.create_surface(w, h, (0, 255, 0))
        t0 = time.perf_counter()
        _, data2 = ImageService.encode_for_device(
            img2, ep[0], resolution, fbl, use_jpeg)
        protocol.send_data(data2, w, h)
        pipeline_s = time.perf_counter() - t0
        report.record_device("LCD encode+send pipeline", pipeline_s, 0.200)

        # Sustained FPS over 3 seconds
        colors = [(i * 25 % 256, 128, 0) for i in range(20)]
        frames_data = []
        for color in colors:
            frame_img = r.create_surface(w, h, color)
            _, frame_bytes = ImageService.encode_for_device(
                frame_img, ep[0], resolution, fbl, use_jpeg)
            frames_data.append(frame_bytes)

        frame_count = 0
        duration = 3.0
        t_start = time.perf_counter()
        while time.perf_counter() - t_start < duration:
            protocol.send_data(
                frames_data[frame_count % len(frames_data)], w, h)
            frame_count += 1

        elapsed = time.perf_counter() - t_start
        fps = frame_count / elapsed
        avg_frame_ms = elapsed / frame_count * 1000

        # Avg frame time — limit 150ms = ~7fps minimum (SCSI is ~80-90ms)
        report.record_device(
            f"LCD sustained ({frame_count}f/{elapsed:.1f}s = {fps:.1f}fps)",
            avg_frame_ms / 1000, 0.150)

        protocol.close()

    # ── LED benchmarks ─────────────────────────────────────────────
    led_dev = next(
        (d for d in svc.devices if d.implementation == 'hid_led'), None)

    if led_dev:
        led_protocol: Any = get_protocol(led_dev)

        # Handshake time
        t0 = time.perf_counter()
        led_protocol.handshake()
        handshake_s = time.perf_counter() - t0
        report.record_device("LED handshake", handshake_s, 2.0)

        # Single LED data send
        led_count = 64
        if hasattr(led_protocol, 'handshake_info') and led_protocol.handshake_info:
            info = led_protocol.handshake_info
            if hasattr(info, 'style') and info.style:
                led_count = info.style.led_count

        colors = [(255, 0, 0)] * led_count
        # Warm up
        led_protocol.send_data(colors, brightness=100)

        t0 = time.perf_counter()
        led_protocol.send_data(colors, brightness=100)
        send_s = time.perf_counter() - t0
        report.record_device("LED send data", send_s, 0.050)

        # Sustained LED updates over 3 seconds
        update_count = 0
        t_start = time.perf_counter()
        while time.perf_counter() - t_start < 3.0:
            r_val = (update_count * 4) % 256
            led_colors = [(r_val, 0, 255 - r_val)] * led_count
            led_protocol.send_data(led_colors, brightness=100)
            update_count += 1

        elapsed = time.perf_counter() - t_start
        ups = update_count / elapsed
        avg_update_ms = elapsed / update_count * 1000
        report.record_device(
            f"LED sustained ({update_count}u/{elapsed:.1f}s = {ups:.0f}ups)",
            avg_update_ms / 1000, 0.050)

        led_protocol.close()

    return report
