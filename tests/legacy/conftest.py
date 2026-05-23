"""Shared test fixtures for the TRCC Linux test suite.

Tier 0: Environment — disable live IPC daemon (tests must never route through GUI)
Tier 1: Data factories — DeviceInfo, mock devices, native renderer surfaces
Tier 2: Filesystem — isolated config dirs, theme dirs, temp PNGs
Tier 3: Qt — session-scoped QApplication (offscreen)
Tier 4: Performance report — Valgrind-style summary for CPU + memory tests
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from trcc.legacy.adapters.render.qt import QtRenderer
from trcc.legacy.core.models import DeviceInfo

# ═══════════════════════════════════════════════════════════════════════
# Performance report — Valgrind-style summary for test_cpu.py / test_memory.py
# Uses PerfReport from core/perf.py (domain object, hexagonal)
# ═══════════════════════════════════════════════════════════════════════
from trcc.legacy.core.perf import PerfReport
from trcc.legacy.services.image import ImageService


def pytest_configure(config: pytest.Config) -> None:
    """Register the perf report collector."""
    config._perf_report = PerfReport()  # type: ignore[attr-defined]


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config,
) -> None:
    """Print Valgrind-style performance summary after test run."""
    report: PerfReport = getattr(config, '_perf_report', None)  # type: ignore[assignment]
    if report and report.has_data:
        tw = terminalreporter._tw
        for line in report.format_report():
            tw.line(line)

# ── Qt + Renderer initialization (once per test session) ────────────────
# QApplication must exist before QtRenderer — QFontDatabase.addApplicationFont
# segfaults without one. Create it once at module level for all tests.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_app = QApplication.instance() or QApplication([])
ImageService.set_renderer(QtRenderer())

# =========================================================================
# Surface helpers — create/inspect native renderer surfaces in tests
# =========================================================================

def make_test_surface(
    w: int = 320, h: int = 320,
    color: tuple[int, ...] = (128, 0, 0),
) -> Any:
    """Create a native renderer surface (QImage) for testing.

    For RGB: pass 3-tuple. For RGBA: pass 4-tuple.
    """
    r = ImageService.renderer()
    return r.create_surface(w, h, color)


def surface_size(surface: Any) -> tuple[int, int]:
    """Get (width, height) from any renderer surface."""
    return ImageService.renderer().surface_size(surface)


def get_pixel(surface: Any, x: int, y: int) -> tuple[int, ...]:
    """Get pixel color from any renderer surface.

    Returns (r, g, b) for RGB surfaces, (r, g, b, a) for RGBA.
    """
    img: QImage = surface
    color = QColor(img.pixel(x, y))
    if img.hasAlphaChannel():
        return (color.red(), color.green(), color.blue(), color.alpha())
    return (color.red(), color.green(), color.blue())

# =========================================================================
# Tier 0: Environment — disable live IPC daemon + builder
# =========================================================================

@pytest.fixture(autouse=True)
def _restore_renderer():
    """Save and restore ImageService._renderer around every test.

    ImageService._renderer is class-level state. Any test that calls
    set_renderer() — directly or via builder.build_lcd() / app.set_renderer()
    — would permanently corrupt it for all subsequent tests in the same
    worker process, causing unrelated tests to fail with MagicMock instead
    of a real Renderer.  This fixture makes the renderer restore automatic.
    """
    from trcc.legacy.services.image import ImageService
    saved = ImageService._renderer
    yield
    ImageService.set_renderer(saved)

@pytest.fixture
def mock_platform():
    """MagicMock PlatformAdapter — inject into ControllerBuilder for tests."""
    return MagicMock()


@pytest.fixture
def _mock_builder(_real_trcc_empty):
    """Alias kept for tests that request the legacy fixture by name.

    Phase 9 renamed ``_mock_builder`` → ``_real_trcc_empty`` because the
    fixture no longer mocks anything (it's a real Trcc). Tests still
    requesting ``_mock_builder`` get the real Trcc transparently.
    """
    return _real_trcc_empty


@pytest.fixture(autouse=True)
def _real_trcc_empty(tmp_config, mock_platform):
    """Production-shape `Trcc` cached in ``_boot``, no devices by default.

    Per ``feedback_tests_emulate_app.md``: tests use the same DI flow as
    production. We construct a real ``Trcc`` against a noop ``MagicMock``
    platform — no MockPlatform pollution, no devices to enumerate, but
    real facades, real EventBus, real lifecycle.

    Tests that need a connected LCD or LED request ``trcc_with_lcd`` /
    ``trcc_with_led`` / ``trcc_with_both`` (defined below). Tests that
    test pure-domain code don't touch this fixture and aren't affected.

    The cached Trcc is cleared before AND after each test — singleton
    state never leaks between tests.
    """
    from trcc import _boot
    from trcc.legacy.core.trcc import Trcc

    _boot._cached = None
    trcc = Trcc(mock_platform, renderer=ImageService.renderer())
    _boot._cached = trcc
    yield trcc
    _boot._cached = None


@pytest.fixture
def trcc_with_lcd(tmp_path, monkeypatch):
    """Real `Trcc` with one LCD device connected via real DI flow.

    Builds the same way ``_boot.trcc()`` does in production: real
    ``MockPlatform``, real ``ControllerBuilder``, real ``QtRenderer``,
    real ``Trcc.discover()`` connecting the noop SCSI device.
    """
    from mock_platform import MockPlatform

    from trcc import _boot
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.trcc import Trcc

    spec = [{"type": "lcd", "vid": "0402", "pid": "3922",
             "resolution": "320x320", "pm": 100}]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    platform = MockPlatform(spec, root=root)
    init_settings(platform)

    _boot._cached = None
    trcc = Trcc(platform, renderer=QtRenderer())
    trcc.discover(ensure_data=False)
    _boot._cached = trcc
    yield trcc
    _boot._cached = None


@pytest.fixture
def trcc_with_led(tmp_path, monkeypatch):
    """Real `Trcc` with one LED device connected via real DI flow."""
    from mock_platform import MockPlatform

    from trcc import _boot
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.trcc import Trcc

    spec = [{"type": "led", "vid": "0416", "pid": "8001",
             "model": "AX120_DIGITAL"}]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    platform = MockPlatform(spec, root=root)
    init_settings(platform)

    _boot._cached = None
    trcc = Trcc(platform, renderer=QtRenderer())
    trcc.discover(ensure_data=False)
    _boot._cached = trcc
    yield trcc
    _boot._cached = None


@pytest.fixture
def trcc_with_both(tmp_path, monkeypatch):
    """Real `Trcc` with one LCD and one LED connected via real DI flow."""
    from mock_platform import MockPlatform

    from trcc import _boot
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.trcc import Trcc

    spec = [
        {"type": "lcd", "vid": "0402", "pid": "3922",
         "resolution": "320x320", "pm": 100},
        {"type": "led", "vid": "0416", "pid": "8001",
         "model": "AX120_DIGITAL"},
    ]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    platform = MockPlatform(spec, root=root)
    init_settings(platform)

    _boot._cached = None
    trcc = Trcc(platform, renderer=QtRenderer())
    trcc.discover(ensure_data=False)
    _boot._cached = trcc
    yield trcc
    _boot._cached = None

# =========================================================================
# Tier 1: Data factories
# =========================================================================

@pytest.fixture
def device_info():
    """Factory fixture: create DeviceInfo with sensible defaults."""
    def _make(
        path: str = "/dev/sg0",
        name: str = "LCD",
        vid: int = 0x87CD,
        pid: int = 0x70DB,
        protocol: str = "scsi",
        resolution: tuple[int, int] = (320, 320),
        **kw,
    ) -> DeviceInfo:
        return DeviceInfo(
            name=name, path=path, vid=vid, pid=pid,
            protocol=protocol, resolution=resolution, **kw,
        )
    return _make


@pytest.fixture
def mock_device():
    """Factory fixture: MagicMock DetectedDevice."""
    def _make(
        path: str = "/dev/sg0",
        name: str = "LCD",
        vid: int = 0x87CD,
        pid: int = 0x70DB,
        protocol: str = "scsi",
    ) -> MagicMock:
        dev = MagicMock()
        dev.scsi_device = path
        dev.product_name = name
        dev.vid = vid
        dev.pid = pid
        dev.protocol = protocol
        dev.usb_path = "1-2"
        dev.vendor_name = "Thermalright"
        return dev
    return _make


@pytest.fixture
def mock_service(device_info):
    """Factory fixture: mock DeviceService with pre-selected device."""
    def _make(device=None) -> MagicMock:
        svc = MagicMock()
        dev = device or device_info()
        svc.selected = dev
        svc.devices = [dev]
        svc.detect.return_value = svc.devices
        svc.send_frame.return_value = True
        return svc
    return _make


@pytest.fixture
def test_image():
    """Factory fixture: native renderer surface for testing."""
    def _make(w: int = 320, h: int = 320,
              color: tuple[int, ...] = (128, 0, 0)) -> Any:
        return make_test_surface(w, h, color)
    return _make


# =========================================================================
# Tier 2: Filesystem fixtures
# =========================================================================

@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    """Isolated config dir — patches CONFIG_DIR/CONFIG_PATH to tmp_path.

    Autouse: no test should ever read from or write to the real ~/.trcc/config.json.
    Initializes Settings with a platform path resolver pointing to tmp_path.
    Also redirects the log file and strips any real file handlers from the root
    logger so test-generated log messages never bleed into ~/.trcc/trcc.log.
    """
    import logging
    from pathlib import Path

    config_dir = str(tmp_path / "trcc")
    config_path = str(tmp_path / "trcc" / "config.json")
    handshake_path = str(tmp_path / "trcc" / "last_handshake.json")
    os.makedirs(config_dir, exist_ok=True)
    monkeypatch.setattr("trcc.legacy.conf.CONFIG_DIR", config_dir)
    monkeypatch.setattr("trcc.legacy.conf.CONFIG_PATH", config_path)
    monkeypatch.setattr("trcc.legacy.conf._HANDSHAKE_CACHE_PATH", handshake_path)

    # Redirect log file so StandardLoggingConfigurator never writes to the real
    # ~/.trcc/trcc.log during tests.
    test_log = Path(config_dir) / "trcc.log"
    monkeypatch.setattr(
        "trcc.legacy.adapters.infra.diagnostics._DEFAULT_LOG_FILE", test_log,
    )
    # Strip any real file handlers the root logger may have accumulated from a
    # previous configure() call (e.g. from a prior test that bootstrapped the app).
    root = logging.getLogger()
    real_log = Path.home() / ".trcc" / "trcc.log"
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == real_log:
            root.removeHandler(h)
            h.close()

    # Initialize Settings with a test path resolver (DI)
    # Uses tmp_path so tests never touch real ~/.trcc/
    from unittest.mock import MagicMock

    from trcc.legacy.conf import init_settings

    resolver = MagicMock()
    resolver.config_dir.return_value = config_dir
    resolver.data_dir.return_value = str(tmp_path / "trcc" / "data")
    resolver.user_content_dir.return_value = str(tmp_path / "trcc-user")
    resolver.theme_dir.side_effect = lambda w, h: str(tmp_path / "trcc" / "data" / f"theme{w}{h}")
    resolver.web_dir.side_effect = lambda w, h: str(tmp_path / "trcc" / "data" / "web" / f"{w}{h}")
    resolver.web_masks_dir.side_effect = lambda w, h: str(tmp_path / "trcc" / "data" / "web" / f"zt{w}{h}")
    resolver.user_masks_dir.side_effect = lambda w, h: str(tmp_path / "trcc-user" / "data" / "web" / f"zt{w}{h}")
    os.makedirs(str(tmp_path / "trcc" / "data"), exist_ok=True)
    init_settings(resolver)

    return tmp_path


@pytest.fixture
def theme_dir(tmp_path):
    """Factory fixture: create a valid theme directory structure."""
    def _make(name: str = "TestTheme", *, has_bg: bool = True,
              has_dc: bool = False, has_mask: bool = False) -> Path:
        td = tmp_path / name
        td.mkdir(exist_ok=True)
        if has_bg:
            make_test_surface(320, 320, (0, 0, 0)).save(str(td / "00.png"))
        if has_dc:
            # Minimal 0xDD format stub
            (td / "config1.dc").write_bytes(b"\xDD" + b"\x00" * 100)
        if has_mask:
            make_test_surface(320, 320, (255, 255, 255, 128)).save(
                str(td / "mask.png"))
        return td
    return _make


@pytest.fixture
def png_factory(tmp_path):
    """Factory fixture: write a minimal PNG and return its path."""
    def _make(filename: str = "test.png", w: int = 320, h: int = 320) -> str:
        path = str(tmp_path / filename)
        make_test_surface(w, h, (128, 0, 0)).save(path, "PNG")
        return path
    return _make


# =========================================================================
# Tier 2b: DI fixtures — injectable ports for pure hexagonal tests
# =========================================================================

@pytest.fixture
def settings_with_resolution(tmp_config):
    """Settings pre-configured with a device resolution (FBL_PROFILES[100] = 320×320).

    Mirrors what production does after a device connects: settings.set_resolution()
    is called with the handshake result. Widget tests that check initial state
    dependent on the active resolution should request this fixture.
    """
    from trcc.legacy.conf import settings
    from trcc.legacy.core.models import FBL_PROFILES

    p = FBL_PROFILES[100]
    settings.set_resolution(p.width, p.height)
    return tmp_config

@pytest.fixture
def failed_lcd_device():
    """Mock LCD Device whose methods always return failure dicts.

    Use to test failure paths in LCD operations without needing a real device.
    """
    from unittest.mock import MagicMock

    lcd = MagicMock()
    _fail = {"success": False, "error": "simulated failure"}
    lcd.set_brightness.return_value = _fail
    lcd.set_rotation.return_value = _fail
    lcd.set_split_mode.return_value = _fail
    lcd.send_image.return_value = _fail
    lcd.send_color.return_value = _fail
    lcd.restore_last_theme.return_value = _fail
    lcd.select.return_value = _fail
    lcd.load_mask_standalone.return_value = _fail
    return lcd


@pytest.fixture
def failed_led_device():
    """Mock LED Device whose methods always return failure dicts.

    Use to test failure paths in LED operations without needing a real device.
    """
    from unittest.mock import MagicMock

    led = MagicMock()
    _fail = {"success": False, "error": "simulated failure"}
    led.update_color.return_value = _fail
    led.update_mode.return_value = _fail
    led.update_brightness.return_value = _fail
    led.turn_off.return_value = _fail
    return led


# ═════════════════════════════════════════════════════════════════════════════
# DI device fixtures — real builder flow, noop protocol
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_lcd_platform(tmp_path):
    """MockPlatform with one LCD device (320x320 SCSI)."""
    from mock_platform import MockPlatform
    spec = [{"type": "lcd", "vid": "0402", "pid": "3922",
             "resolution": "320x320", "pm": 100}]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    return MockPlatform(spec, root=root)


@pytest.fixture
def mock_led_platform(tmp_path):
    """MockPlatform with one LED device (AX120)."""
    from mock_platform import MockPlatform
    spec = [{"type": "led", "vid": "0416", "pid": "8001",
             "model": "AX120_DIGITAL"}]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    return MockPlatform(spec, root=root)


@pytest.fixture
def lcd_device(mock_lcd_platform, tmp_config):
    """Connected LCD Device through real builder flow."""
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.builder import ControllerBuilder

    init_settings(mock_lcd_platform)
    builder = ControllerBuilder(mock_lcd_platform).with_renderer(QtRenderer())
    detected = mock_lcd_platform.detect_devices()[0]
    device = builder.build_device(detected)
    device.connect(detected)
    return device


@pytest.fixture
def led_device(mock_led_platform, tmp_config):
    """Connected LED Device through real builder flow."""
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.builder import ControllerBuilder

    init_settings(mock_led_platform)
    builder = ControllerBuilder(mock_led_platform)
    detected = mock_led_platform.detect_devices()[0]
    device = builder.build_device(detected)
    device.connect(detected)
    return device


@pytest.fixture
def fake_detect():
    """No-hardware detect callable. Inject into any function that accepts detect_fn.

    Set fake_detect.return_value = [devices] to control the device list.
    Tests never reach into internal routing — pure DI.

    Example::

        def test_no_devices(fake_detect):
            result = detect(detect_fn=fake_detect)
            assert result == 1
    """
    return MagicMock(return_value=[])


# =========================================================================
# Tier 3: Qt fixture
# =========================================================================

@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for all GUI tests (offscreen).

    Reuses the module-level _app created at conftest import time.
    """
    return _app


# =========================================================================
# Legacy factory functions — used by test_cli.py and test_integration.py
# =========================================================================

def make_device_info(
    path: str = "/dev/sg0",
    name: str = "LCD",
    vid: int = 0x87CD,
    pid: int = 0x70DB,
    protocol: str = "scsi",
    resolution: tuple[int, int] = (320, 320),
    **kw,
) -> DeviceInfo:
    """Create DeviceInfo with sensible defaults. Used by test_cli."""
    return DeviceInfo(
        name=name, path=path, vid=vid, pid=pid,
        protocol=protocol, resolution=resolution, **kw,
    )


def make_mock_service(device: DeviceInfo | None = None) -> MagicMock:
    """Create mock DeviceService with pre-selected device. Used by test_cli."""
    svc = MagicMock()
    dev = device or make_device_info()
    svc.selected = dev
    svc.devices = [dev]
    svc.detect.return_value = svc.devices
    svc.send_frame.return_value = True
    return svc


def save_test_png(path: str, w: int = 320, h: int = 320) -> None:
    """Write a minimal PNG at path. Used by test_integration."""
    make_test_surface(w, h).save(path, "PNG")


# =========================================================================
# Tier 5: Platform builder fixtures — real adapters, no patching
#
# The autouse _mock_builder patches ControllerBuilder.for_current_os for all
# tests. These fixtures bypass that by constructing a real ControllerBuilder
# with the appropriate PlatformAdapter injected directly.
#
# Available everywhere (root conftest) — adapters, services, core, cli, api
# tests all share the same platform contract.
# =========================================================================

@pytest.fixture()
def linux_builder():
    """ControllerBuilder wired with the real LinuxPlatform."""
    from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
    from trcc.legacy.core.builder import ControllerBuilder
    return ControllerBuilder(LinuxPlatform())


@pytest.fixture()
def windows_builder():
    """ControllerBuilder wired with the real WindowsPlatform."""
    from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
    from trcc.legacy.core.builder import ControllerBuilder
    return ControllerBuilder(WindowsPlatform())


@pytest.fixture()
def macos_builder():
    """ControllerBuilder wired with the real MacOSPlatform."""
    from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
    from trcc.legacy.core.builder import ControllerBuilder
    return ControllerBuilder(MacOSPlatform())


@pytest.fixture()
def bsd_builder():
    """ControllerBuilder wired with the real BSDPlatform."""
    from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
    from trcc.legacy.core.builder import ControllerBuilder
    return ControllerBuilder(BSDPlatform())


def make_device_service(**overrides):
    """Create a DeviceService with mock adapter deps (no RuntimeError).

    Use this in tests that construct DeviceService directly. All adapter
    callables default to MagicMock so construction never raises.
    """
    from trcc.legacy.services import DeviceService

    defaults = {
        'detect_fn': MagicMock(return_value=[]),
        'probe_led_fn': MagicMock(return_value=None),
        'get_protocol': MagicMock(return_value=MagicMock()),
        'get_protocol_info': MagicMock(return_value=None),
    }
    defaults.update(overrides)
    return DeviceService(**defaults)
