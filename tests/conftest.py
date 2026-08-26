"""Shared fixtures for tests/next.

Provides fakes at the transport boundary so tests exercise real
protocol logic (ScsiLcd.connect, DisplayService.render) without
touching USB / SG_IO / ioctl.
"""
from __future__ import annotations

import inspect
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from trcc.core.models import RawFrame, UsbPowerState, Wire
from trcc.core.ports import (
    AutostartManager,
    BulkTransport,
    CpuSource,
    GpuSource,
    HotplugMonitor,
    MemorySource,
    PackageManager,
    Paths,
    Platform,
    Renderer,
    ScreenCapture,
    ScsiTransport,
    SensorEnumerator,
    Transport,
    WriteBuffer,
)

# ── Transport fakes ──────────────────────────────────────────────────


class FakeBulkTransport(BulkTransport):
    """In-memory BulkTransport — records writes, yields scripted reads."""

    def __init__(self) -> None:
        self._open = False
        self.writes: List[Tuple[int, bytes]] = []
        self.read_script: List[bytes] = []

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def write(self, endpoint: int, data: WriteBuffer, timeout_ms: int = 100) -> int:
        payload = bytes(data)
        self.writes.append((endpoint, payload))
        return len(payload)

    def read(self, endpoint: int, length: int, timeout_ms: int = 100) -> bytes:
        if not self.read_script:
            return b""
        buf = self.read_script.pop(0)
        return buf[:length]


class FakeScsiTransport(ScsiTransport):
    """In-memory ScsiTransport — records CDBs, yields scripted read data."""

    def __init__(self) -> None:
        self._open = False
        self.sent: List[Tuple[bytes, bytes]] = []
        self.read_script: List[bytes] = []
        self.send_should_succeed = True

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def send_cdb(self, cdb: bytes, data: bytes, timeout_ms: int = 5000) -> bool:
        self.sent.append((bytes(cdb), bytes(data)))
        return self.send_should_succeed

    def read_cdb(self, cdb: bytes, length: int, timeout_ms: int = 5000) -> bytes:
        if not self.read_script:
            return b""
        buf = self.read_script.pop(0)
        return buf[:length]


# ── Platform fake ────────────────────────────────────────────────────


class FakePaths(Paths):
    def __init__(self, root: Path) -> None:
        self._root = root

    def config_dir(self) -> Path:
        return self._root

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._root / "user"

    def log_file(self) -> Path:
        return self._root / "trcc.log"


class FakeAutostart(AutostartManager):
    def __init__(self) -> None:
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def refresh(self) -> None:
        pass


class FakeCpu(CpuSource):
    def __init__(self) -> None:
        self.values = {"temp": 42.0, "usage": 15.0, "freq": 3200.0, "power": 65.0}

    @property
    def name(self) -> str:
        return "Fake CPU"

    def temp(self) -> Optional[float]:
        return self.values["temp"]

    def usage(self) -> Optional[float]:
        return self.values["usage"]

    def freq(self) -> Optional[float]:
        return self.values["freq"]

    def power(self) -> Optional[float]:
        return self.values["power"]


class FakeMemory(MemorySource):
    def used(self) -> Optional[float]:
        return 8192.0

    def available(self) -> Optional[float]:
        return 24576.0

    def total(self) -> Optional[float]:
        return 32768.0

    def percent(self) -> Optional[float]:
        return 25.0


class FakeGpu(GpuSource):
    def __init__(self, index: int, discrete: bool = True,
                 vendor: str = "test") -> None:
        self._index = index
        self._discrete = discrete
        self._vendor = vendor
        self.values = {
            "temp": 55.0, "usage": 30.0, "clock": 1800.0, "power": 180.0,
            "fan": 42.0, "vram_used": 1024.0, "vram_total": 8192.0,
        }

    @property
    def key(self) -> str:
        return f"{self._vendor}:{self._index}"

    @property
    def name(self) -> str:
        return f"Fake {self._vendor.upper()} GPU {self._index}"

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    def temp(self) -> Optional[float]:
        return self.values["temp"]

    def usage(self) -> Optional[float]:
        return self.values["usage"]

    def clock(self) -> Optional[float]:
        return self.values["clock"]

    def power(self) -> Optional[float]:
        return self.values["power"]

    def fan(self) -> Optional[float]:
        return self.values["fan"]

    def vram_used(self) -> Optional[float]:
        return self.values["vram_used"]

    def vram_total(self) -> Optional[float]:
        return self.values["vram_total"]


class FakeScreenCapture(ScreenCapture):
    """Deterministic desktop grab — a solid frame of the requested size.

    Records every region asked for, so a test can assert WHICH rectangle a
    caller grabbed rather than only that it grabbed something.
    """

    def __init__(self, fill: int = 0x40) -> None:
        self.regions: List[tuple] = []
        self._fill = fill

    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        self.regions.append((x, y, width, height))
        return RawFrame(
            data=bytes([self._fill]) * (width * height * 3),
            width=width, height=height,
        )


class FakePlatform(Platform):
    """Minimal Platform fake — bulk/scsi transports replayable from tests."""

    def __init__(self, tmp_home: Path) -> None:
        self.bulk = FakeBulkTransport()
        self.scsi = FakeScsiTransport()
        self._paths = FakePaths(tmp_home)
        self._autostart = FakeAutostart()
        self._sensors: Optional[SensorEnumerator] = None
        self.capture = FakeScreenCapture()

    def screen_capture(self) -> ScreenCapture:
        return self.capture

    def open_transport(self, wire, vid, pid, serial=None) -> Transport:
        return self.scsi if wire is Wire.SCSI else self.bulk

    def scan_devices(self) -> List:
        return []

    def paths(self) -> Paths:
        return self._paths

    def sensors(self) -> SensorEnumerator:
        if self._sensors is None:
            from trcc.adapters.sensors.aggregator import BaselineSensors
            self._sensors = BaselineSensors(
                cpu=FakeCpu(), memory=FakeMemory(),
                gpus=[FakeGpu(0, discrete=True, vendor="nvidia")],
                fans=[],
            )
        return self._sensors

    def autostart(self) -> AutostartManager:
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        from trcc.adapters.system._hotplug import NoopHotplugMonitor
        if not hasattr(self, "_hotplug_monitor"):
            self._hotplug_monitor = NoopHotplugMonitor(reason="test fake")
        return self._hotplug_monitor

    def setup(self, interactive: bool = True) -> int:
        return 0

    def check_permissions(self) -> List[str]:
        return []

    def distro_name(self) -> str:
        return "Fake Linux"

    def install_method(self) -> str:
        return "test"

    # ── The rest of the port ──────────────────────────────────────────
    #
    # This double stays on ``Platform`` rather than extending ``BaseOS``,
    # deliberately: ``BaseOS.scan_devices`` calls libusb, and the fake exists
    # to be an OS without being a real one.  The price is answering the whole
    # contract here — and that price is the point.  When the port grows a
    # question, this class stops instantiating with a ``TypeError`` naming it,
    # which is the same message a new OS's author gets, delivered to us first.

    def usb_power_state(self, vid: int, pid: int) -> Optional[UsbPowerState]:
        return None

    def packages(self) -> PackageManager:
        """Answers "cannot be asked" — a fake must not invent a package DB."""
        from trcc.adapters.system._packages import NoPackageManager
        return NoPackageManager()

    def package_manager(self) -> str:
        return ""

    def upgrade_command(self) -> Tuple[str, ...]:
        return ()

    def software_install_hint(self, tool: str) -> str:
        return f"fake platform: install {tool}"

    def no_devices_hint(self) -> str:
        return "fake platform: no devices attached"

    def permission_denied_hint(self) -> str:
        return "fake platform: no USB permission"

    def minimize_on_close(self) -> bool:
        return False

    def configure_stdout(self) -> None:
        """Nothing to rewrap — the test runner's streams are already UTF-8."""

    def worker_thread_context(self) -> AbstractContextManager[None]:
        return nullcontext()

    def memory_info(self) -> List[Dict[str, str]]:
        return []

    def disk_info(self) -> List[Dict[str, str]]:
        return []


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect $HOME + XDG_CONFIG_HOME to a per-test tmp dir.

    Keeps XdgDesktopAutostart, LinuxPaths, etc. from touching the user's
    real filesystem during tests.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


@pytest.fixture
def fake_platform(tmp_home: Path) -> FakePlatform:
    return FakePlatform(tmp_home)


@pytest.fixture
def fake_bulk() -> FakeBulkTransport:
    return FakeBulkTransport()


@pytest.fixture
def fake_scsi() -> FakeScsiTransport:
    return FakeScsiTransport()


def assert_stub_matches(real: Callable[..., Any], stub: Callable[..., Any]) -> None:
    """Fail loudly when a monkeypatched stub stops matching what it replaces.

    A stub restates a signature, so it drifts — and it drifts SILENTLY: the
    caller raises ``TypeError``, something upstream catches it, and the suite
    stays green while the stub stubs nothing.  ``_stub_data_install`` spent a
    release in exactly that state after ``ensure_all`` gained two parameters
    (0709ad5f), and the only symptom was an ERROR line nobody read.

    Compares everything after the receiver by name, kind and required-ness, so
    a renamed ``self`` is fine and a new parameter is not.  Call this from any
    fixture that patches a real method with a hand-written replacement.
    """
    def shape(fn: Callable[..., Any]) -> list[tuple[str, object, bool]]:
        params = list(inspect.signature(fn).parameters.values())[1:]
        return [(p.name, p.kind, p.default is p.empty) for p in params]

    if shape(real) != shape(stub):
        raise AssertionError(
            f"stub signature drifted from {real.__qualname__}:\n"
            f"  real: {inspect.signature(real)}\n"
            f"  stub: {inspect.signature(stub)}\n"
            "Update the stub — a mismatched one raises TypeError at the call "
            "site and is swallowed, leaving the suite green and unstubbed."
        )


@pytest.fixture(autouse=True)
def _stub_data_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test downloads theme archives over the network.

    Both ``DiscoverDevices`` and (now) ``ConnectDevice`` call
    ``DataInstallService.ensure_all``, which would otherwise run the real
    ``UrllibHttpFetcher``.  Stub it at the class so every test is offline-safe;
    tests that assert on the call replace ``app.data_install`` locally.
    """
    from trcc.services.data_install import DataInstallService, EnsureDataResult

    def _noop(
        _self: object, resolution: tuple[int, int],
        variant: str = "", mask_variant: str = "",
    ) -> EnsureDataResult:
        # Signature MUST track ``DataInstallService.ensure_all``.  It gained
        # ``variant`` / ``mask_variant`` with the per-SKU artwork libraries
        # (0709ad5f) and this stub did not, so every call raised TypeError,
        # ``data_install_runner`` swallowed it, and the suite stayed green
        # while the stub stubbed nothing.
        del variant, mask_variant
        return EnsureDataResult(
            resolution=resolution, themes_ok=True, web_ok=True, masks_ok=True,
        )

    assert_stub_matches(DataInstallService.ensure_all, _noop)
    monkeypatch.setattr(DataInstallService, "ensure_all", _noop)

    # ...and keep the install SYNCHRONOUS under test.  Production runs it on a
    # daemon worker so the GUI's startup never waits on ~30 MB of archives
    # (#275); a background thread in the suite would make every assertion on
    # installed data a race.  Same shape as SyncSendScheduler.  App imports
    # ThreadDataInstallRunner lazily inside __init__, so patching the module
    # attribute here reaches every App built after this fixture runs.
    from trcc.adapters.infra import data_install_runner

    monkeypatch.setattr(
        data_install_runner, "ThreadDataInstallRunner",
        data_install_runner.SyncDataInstallRunner,
    )


@pytest.fixture(scope="session", autouse=True)
def _qapplication() -> Iterator[object]:
    """One full QApplication per session (per xdist worker), made BEFORE any test.

    ``QtRenderer._ensure_qt_app`` creates a bare ``QGuiApplication`` when no app
    exists — fine for offscreen QPainter rendering, but a ``QGuiApplication``
    cannot host ``QWidget``s and Qt forbids a second app instance.  So if a
    renderer-building test ran before a GUI-panel test in the same worker, the
    panel's QWidget construction ABORTED ("Cannot create a QWidget without
    QApplication") — an order-dependent flake under ``pytest -n`` (xdist).

    Creating the QApplication first (idempotent) removes the ordering
    dependency: every test shares one QApplication, which also satisfies
    offscreen rendering (QApplication is-a QGuiApplication).
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Session end: GUI tests build widgets/timers parented to this long-lived
    # QApplication; left in place they're destroyed during interpreter
    # finalization — where a still-running daemon thread (``MetricsLoop`` /
    # ``LedAnimationLoop`` / hotplug from an App a fixture didn't close) makes
    # the destroy land off the main thread, tripping Qt's
    # ``QObject::killTimer: Timers cannot be stopped from another thread``.
    # Destroying them HERE — on the main thread, before finalization — kills
    # their timers on the owning thread, silently and deterministically.
    for w in list(QApplication.topLevelWidgets()):
        w.deleteLater()
    QApplication.processEvents()
    import gc
    gc.collect()
    QApplication.processEvents()
    # No quit() — other Qt tests share this process; tearing down breaks them.


@pytest.fixture(autouse=True)
def _release_trcc_singleton() -> Iterator[None]:
    """Release the ``TRCCApp`` process-singleton after each test.

    ``TRCCApp.__new__`` enforces one instance per process; production releases
    it in ``closeEvent``, which tests don't trigger.  So a test that builds a
    ``TRCCApp`` leaves ``_instance`` set and the NEXT one in the same xdist
    worker hits 'TRCCApp is a singleton'.  Reset it here (and delete the window
    on the main thread) so every test starts clean.  Guarded on the module
    already being imported, so non-GUI tests pay nothing — no forced import.
    """
    yield
    import sys
    mod = sys.modules.get("trcc.ui.gui.trcc_app")
    if mod is None:
        return
    inst = mod.TRCCApp._instance
    if inst is None:
        return
    inst.deleteLater()
    mod.TRCCApp._instance = None
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


# =========================================================================
# CLI fixtures — typer.testing.CliRunner + _ctx App override
# =========================================================================
#
# Moved here from test_cli_commands.py so future per-command test files
# (test_cli_display.py, test_cli_led.py, …) consume them directly.
# The fixture is the foundation; per-file tests are the example.


@pytest.fixture
def cli_runner():
    """typer.testing.CliRunner — captures stdout + exit codes."""
    from typer.testing import CliRunner

    return CliRunner()


class _CliRenderer(Renderer):
    """Stand-in for QtRenderer used by CLI command bodies.

    CLI rarely renders anything itself, but ``display color`` /
    ``display boot-anim`` / etc. exercise the DisplayService which
    needs *some* renderer.  This one short-circuits every method to
    a trivial result so tests stay headless.

    A real ``Renderer`` subclass (not a duck type) so it inherits the
    port's concrete defaults — ``list_fonts`` → ``[]`` etc. — and any
    future port method automatically, instead of breaking when one lands.
    """

    def create_surface(self, width, height, color=None):
        return _Surface(width, height)

    def open_image(self, path):
        return _Surface(100, 100)

    def surface_size(self, surface):
        return (surface.w, surface.h)

    def surface_nbytes(self, surface):
        return surface.w * surface.h * 4

    def composite(self, base, overlay, position, mask=None):
        return base

    def resize(self, surface, width, height):
        return _Surface(width, height)

    def rotate(self, surface, degrees):
        return surface

    def flip_horizontal(self, surface):
        return surface

    def apply_brightness(self, surface, percent):
        return surface

    def draw_text(self, surface, x, y, text, color, size,
                  bold=False, italic=False, family=""):
        pass

    def encode_rgb565(self, surface, byte_order=">"):
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface, quality=95, max_size=0):
        return b""

    def from_raw_rgb24(self, frame):
        return _Surface(100, 100)

    def to_raw_rgb24(self, surface):
        # The inverse the port now requires.  Test doubles carry no pixels,
        # so this reports the surface's DIMENSIONS with blank bytes — enough
        # for a caller that only needs a correctly-sized RawFrame.
        w, h = self.surface_size(surface)
        return RawFrame(data=bytes(w * h * 3), width=w, height=h)

    def decode_image(self, data):
        return _Surface(100, 100)


class _Surface:
    def __init__(self, w: int = 100, h: int = 100) -> None:
        self.w, self.h = w, h


@pytest.fixture(autouse=False)
def cli_app(fake_platform):
    """Pre-wire the CLI's lru_cached App so every command body runs
    against FakePlatform + a smoke renderer.

    Not autouse — tests that don't touch the CLI shouldn't pay the
    fixture cost.  The CLI test files opt in.
    """
    from trcc.ui.cli import _ctx

    _ctx.set_platform(fake_platform)
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    yield _ctx.get_app()
    _ctx.get_app.cache_clear()
    _ctx._platform_override = None
    _ctx._renderer_override = None
