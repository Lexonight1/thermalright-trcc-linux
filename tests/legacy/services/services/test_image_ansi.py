"""Developer visual tests for ANSI terminal preview.

Run directly to see rendered output in your terminal:
    PYTHONPATH=src python3 tests/test_ansi_preview.py

Or via pytest for automated validation:
    PYTHONPATH=src pytest tests/test_ansi_preview.py -v

Tests every metric group, LED mode, and protocol scenario with mock data.
Open source contributors: run this to verify ANSI rendering works on your terminal.
"""
import unittest

from PySide6.QtGui import QColor

from tests.conftest import make_test_surface
from trcc.legacy.core.models import HardwareMetrics, LEDMode, LEDState
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.led import LEDService

# ---------------------------------------------------------------------------
# Simulated user scenarios — any contributor can add their own
# ---------------------------------------------------------------------------

def _hot_gaming_rig() -> HardwareMetrics:
    """User running heavy game — high temps, high usage everywhere."""
    m = HardwareMetrics()
    m.cpu_temp = 88.0
    m.cpu_percent = 94.0
    m.cpu_freq = 5100.0
    m.cpu_power = 142.0
    m.gpu_temp = 81.0
    m.gpu_usage = 99.0
    m.gpu_clock = 2145.0
    m.gpu_power = 310.0
    m.mem_percent = 87.0
    m.mem_available = 4096.0
    m.mem_clock = 3200.0
    m.disk_read = 180.5
    m.disk_write = 95.2
    m.disk_activity = 72.0
    m.disk_temp = 48.0
    m.net_down = 45000.0
    m.net_up = 8500.0
    m.fan_cpu = 2400.0
    m.fan_gpu = 1950.0
    m.fan_ssd = 0.0
    return m


def _idle_desktop() -> HardwareMetrics:
    """User at idle — low everything, just browsing."""
    m = HardwareMetrics()
    m.cpu_temp = 32.0
    m.cpu_percent = 3.0
    m.cpu_freq = 800.0
    m.gpu_temp = 26.0
    m.gpu_usage = 1.0
    m.gpu_clock = 210.0
    m.mem_percent = 22.0
    m.mem_available = 25000.0
    m.fan_gpu = 650.0
    return m


def _server_headless() -> HardwareMetrics:
    """Headless server — no GPU, high network, moderate CPU."""
    m = HardwareMetrics()
    m.cpu_temp = 55.0
    m.cpu_percent = 45.0
    m.cpu_freq = 3400.0
    m.mem_percent = 68.0
    m.mem_available = 16000.0
    m.disk_read = 50.0
    m.disk_write = 120.0
    m.disk_activity = 35.0
    m.net_down = 95000.0
    m.net_up = 92000.0
    m.net_total_down = 850000.0
    m.net_total_up = 720000.0
    return m


def _thermal_throttling() -> HardwareMetrics:
    """CPU thermal throttling — 100°C, freq dropping."""
    m = HardwareMetrics()
    m.cpu_temp = 100.0
    m.cpu_percent = 100.0
    m.cpu_freq = 2800.0  # throttled down from 5GHz
    m.cpu_power = 65.0   # power limited
    m.gpu_temp = 90.0
    m.gpu_usage = 95.0
    m.fan_cpu = 3200.0
    m.fan_gpu = 2800.0
    m.mem_percent = 95.0
    m.mem_available = 1500.0
    return m


SCENARIOS = {
    'hot_gaming': _hot_gaming_rig,
    'idle_desktop': _idle_desktop,
    'server_headless': _server_headless,
    'thermal_throttle': _thermal_throttling,
}

METRIC_GROUPS = ['cpu', 'gpu', 'mem', 'disk', 'net', 'fan', 'time']

LED_MODES = {
    'static_red': (LEDMode.STATIC, (255, 0, 0)),
    'static_blue': (LEDMode.STATIC, (0, 0, 255)),
    'static_green': (LEDMode.STATIC, (0, 255, 0)),
    'static_white': (LEDMode.STATIC, (255, 255, 255)),
    'breathing_cyan': (LEDMode.BREATHING, (0, 255, 255)),
    'colorful': (LEDMode.COLORFUL, (255, 0, 0)),
    'rainbow': (LEDMode.RAINBOW, (0, 0, 0)),
}


# ---------------------------------------------------------------------------
# Pytest: automated validation (ANSI output is well-formed)
# ---------------------------------------------------------------------------

class TestAnsiMetricsDashboard(unittest.TestCase):
    """Every metric group × every scenario produces valid ANSI."""

    def test_all_scenarios_all_groups(self):
        for name, factory in SCENARIOS.items():
            m = factory()
            for group in METRIC_GROUPS:
                with self.subTest(scenario=name, group=group):
                    result = ImageService.metrics_to_ansi(m, cols=50, group=group)
                    self.assertIsInstance(result, str)
                    self.assertIn('\033[', result, f'{name}/{group} missing ANSI')
                    self.assertIn('\u2580', result, f'{name}/{group} missing half-block')
                    self.assertIn('\033[0m', result, f'{name}/{group} missing reset')

    def test_all_scenarios_full_dashboard(self):
        for name, factory in SCENARIOS.items():
            m = factory()
            with self.subTest(scenario=name):
                result = ImageService.metrics_to_ansi(m, cols=60)
                self.assertIsInstance(result, str)
                self.assertGreater(len(result), 100)


class TestAnsiLedModes(unittest.TestCase):
    """Every LED mode produces valid ANSI zone output."""

    def _make_svc(self, mode, color, segments=64):
        state = LEDState()
        state.mode = mode
        state.color = color
        state.brightness = 100
        state.segment_count = segments
        state.global_on = True
        return LEDService(state=state)

    def test_all_modes(self):
        for name, (mode, color) in LED_MODES.items():
            with self.subTest(mode=name):
                svc = self._make_svc(mode, color)
                colors = svc.tick()
                result = LEDService.zones_to_ansi(colors)
                self.assertIsInstance(result, str)
                self.assertIn('\033[48;2;', result)
                self.assertIn('\033[0m', result)

    def test_all_modes_small_device(self):
        """Small LED device (10 segments) — all modes."""
        for name, (mode, color) in LED_MODES.items():
            with self.subTest(mode=name):
                svc = self._make_svc(mode, color, segments=10)
                colors = svc.tick()
                result = LEDService.zones_to_ansi(colors)
                self.assertEqual(result.count('\033[0m'), 10)

    def test_all_modes_large_device(self):
        """Large LED device (128 segments) — all modes."""
        for name, (mode, color) in LED_MODES.items():
            with self.subTest(mode=name):
                svc = self._make_svc(mode, color, segments=128)
                colors = svc.tick()
                result = LEDService.zones_to_ansi(colors)
                self.assertEqual(result.count('\033[0m'), 128)


class TestAnsiLcdImages(unittest.TestCase):
    """LCD image preview for each supported resolution."""

    RESOLUTIONS = [
        (240, 240), (320, 320), (480, 480),  # square
        (320, 240), (480, 128), (1920, 480),  # non-square
    ]

    def test_solid_colors_all_resolutions(self):
        """Solid color → ANSI at every resolution."""
        for w, h in self.RESOLUTIONS:
            for color_name, rgb in [('red', (255, 0, 0)), ('green', (0, 255, 0)),
                                     ('blue', (0, 0, 255)), ('white', (255, 255, 255))]:
                with self.subTest(res=f'{w}x{h}', color=color_name):
                    img = make_test_surface(w, h, rgb)
                    result = ImageService.to_ansi(img, cols=30)
                    self.assertIn('\u2580', result)
                    self.assertIn('\033[0m', result)

    def test_gradient_all_resolutions(self):
        """Gradient image → ANSI at every resolution."""
        for w, h in self.RESOLUTIONS:
            with self.subTest(res=f'{w}x{h}'):
                img = make_test_surface(w, h, (0, 0, 0))
                for x in range(w):
                    for y in range(min(h, 4)):  # only fill a few rows for speed
                        img.setPixelColor(x, y, QColor(
                            int(255 * x / w), 0, int(255 * y / max(h, 1))))
                result = ImageService.to_ansi(img, cols=30)
                self.assertIn('\u2580', result)


# ---------------------------------------------------------------------------
# Direct execution: visual output for developers
# ---------------------------------------------------------------------------

def _visual_demo():
    """Run in terminal to SEE the ANSI output. Not automated — for humans."""
    print('=' * 60)
    print('  TRCC ANSI Preview — Developer Visual Test')
    print('=' * 60)

    # 1. Metrics dashboard for each scenario
    for name, factory in SCENARIOS.items():
        m = factory()
        print(f'\n{"─" * 60}')
        print(f'  Scenario: {name}')
        print(f'{"─" * 60}')
        print(ImageService.metrics_to_ansi(m, cols=60))

    # 2. Individual metric groups (using hot gaming rig)
    m = _hot_gaming_rig()
    for group in METRIC_GROUPS:
        print(f'\n  ── {group.upper()} only ──')
        print(ImageService.metrics_to_ansi(m, cols=50, group=group))

    # 3. LED modes
    print(f'\n{"─" * 60}')
    print('  LED Modes (64 segments)')
    print(f'{"─" * 60}')
    for name, (mode, color) in LED_MODES.items():
        state = LEDState()
        state.mode = mode
        state.color = color
        state.segment_count = 64
        state.global_on = True
        svc = LEDService(state=state)
        colors = svc.tick()
        print(f'  {name:20s} {LEDService.zones_to_ansi(colors[:20])}')

    # 4. LCD solid colors at different resolutions
    print(f'\n{"─" * 60}')
    print('  LCD Solid Colors (30 cols)')
    print(f'{"─" * 60}')
    for color_name, rgb in [('Red', (255, 0, 0)), ('Green', (0, 255, 0)),
                             ('Blue', (0, 0, 255)), ('Cyan', (0, 255, 255))]:
        img = make_test_surface(120, 40, rgb)
        print(f'\n  {color_name}:')
        print(ImageService.to_ansi(img, cols=30))

    print(f'\n{"=" * 60}')
    print('  All previews rendered. Check your terminal for colors.')
    print(f'{"=" * 60}\n')


if __name__ == '__main__':
    _visual_demo()
