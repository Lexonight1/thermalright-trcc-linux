"""Shared fixtures for tests/next.

Provides fakes at the transport boundary so tests exercise real
protocol logic (ScsiLcd.connect, DisplayService.render) without
touching USB / SG_IO / ioctl.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from trcc.core.ports import (
    AutostartManager,
    BulkTransport,
    CpuSource,
    GpuSource,
    HotplugMonitor,
    MemorySource,
    Paths,
    Platform,
    ScsiTransport,
    SensorEnumerator,
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

    def write(self, endpoint: int, data: bytes, timeout_ms: int = 100) -> int:
        self.writes.append((endpoint, bytes(data)))
        return len(data)

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


class FakePlatform(Platform):
    """Minimal Platform fake — bulk/scsi transports replayable from tests."""

    def __init__(self, tmp_home: Path) -> None:
        self.bulk = FakeBulkTransport()
        self.scsi = FakeScsiTransport()
        self._paths = FakePaths(tmp_home)
        self._autostart = FakeAutostart()
        self._sensors: Optional[SensorEnumerator] = None

    def open_bulk(self, vid, pid, serial=None) -> BulkTransport:
        return self.bulk

    def open_scsi(self, vid, pid, serial=None) -> ScsiTransport:
        return self.scsi

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


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect $HOME + XDG_CONFIG_HOME to a per-test tmp dir.

    Keeps LinuxAutostart, LinuxPaths, etc. from touching the user's
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


class _CliRenderer:
    """Stand-in for QtRenderer used by CLI command bodies.

    CLI rarely renders anything itself, but ``display color`` /
    ``display boot-anim`` / etc. exercise the DisplayService which
    needs *some* renderer.  This one short-circuits every method to
    a trivial result so tests stay headless.
    """

    def create_surface(self, width, height, color=None):
        return _Surface(width, height)

    def open_image(self, path):
        return _Surface(100, 100)

    def surface_size(self, surface):
        return (surface.w, surface.h)

    def composite(self, base, overlay, position, mask=None):
        return base

    def resize(self, surface, width, height):
        return _Surface(width, height)

    def rotate(self, surface, degrees):
        return surface

    def apply_brightness(self, surface, percent):
        return surface

    def draw_text(self, surface, x, y, text, color, size,
                  bold=False, italic=False):
        pass

    def encode_rgb565(self, surface):
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface, quality=95, max_size=0):
        return b""

    def from_raw_rgb24(self, frame):
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
