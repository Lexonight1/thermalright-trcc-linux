"""Shared fixtures for tests/next.

Provides fakes at the transport boundary so tests exercise real
protocol logic (ScsiLcd.connect, DisplayService.render) without
touching USB / SG_IO / ioctl.
"""
from __future__ import annotations

import os

# The suite runs OFFSCREEN, and that is a GUARANTEE — not a default.
#
# Five places used to write ``os.environ.setdefault("QT_QPA_PLATFORM",
# "offscreen")``, which does NOTHING when a contributor's shell already
# exports the variable.  Five tests assert offscreen behaviour outright
# (``test_offscreen_qpa_is_set``, three in ``test_screencast_capture_chain``
# asserting Qt declines to grab, and the video-cut close test, which needs
# ``QWidget.close()`` to deliver ``closeEvent`` synchronously — an xcb window
# does not).  Under a real plugin all five fail, which is what a contributor
# reported on 2026-09-21 as an unexplained environment difference.
#
# Assignment, and here at import time: this module is imported before any test
# module, so it lands before the first PySide6 import in any worker.  The
# production ``adapters/render/qt.py`` keeps ``setdefault`` on purpose — a real
# user needs their real plugin.
os.environ["QT_QPA_PLATFORM"] = "offscreen"


import inspect
import ipaddress
import logging
import socket
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from trcc.core.models import (
    DEFAULT_AUTOSTART_TARGET,
    DisplayServer,
    DisplaySession,
    RawFrame,
    UsbPowerState,
    Wire,
)
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
    """In-memory BulkTransport — records both halves, yields scripted reads.

    ``reads`` records the *request* side of a read — the endpoint and the
    length asked for — which ``read_script`` alone cannot show.  Half of what
    a handshake puts on the wire is the size it asks back (the C# gives LY 512
    and LY1 511, from the same 16-byte command), and until this list existed
    that number was unobservable, so nothing could gate it.  Recording only;
    the reply still comes from ``read_script`` unconditioned on the request.
    """

    def __init__(self) -> None:
        self._open = False
        self.writes: List[Tuple[int, bytes]] = []
        self.reads: List[Tuple[int, int]] = []
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
        self.reads.append((endpoint, length))
        if not self.read_script:
            return b""
        buf = self.read_script.pop(0)
        return buf[:length]


class FakeScsiTransport(ScsiTransport):
    """In-memory ScsiTransport — records both CDB paths, yields scripted data.

    ``sent`` holds ``send_cdb`` (data-out); ``reads`` holds ``read_cdb``
    (data-in).  They are separate lists because callers count ``sent`` to mean
    "frames written", and a poll folded into it would move every one of those
    numbers.  ``read_cdb`` recorded nothing at all until this existed, so
    ``ScsiLcd``'s POLL command — the first thing it puts on the wire — was
    invisible to every test; the one assertion that looked like it covered the
    poll is reading ``sent[0]``, which is the INIT.
    """

    def __init__(self) -> None:
        self._open = False
        self.sent: List[Tuple[bytes, bytes]] = []
        self.reads: List[Tuple[bytes, int]] = []
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
        self.reads.append((bytes(cdb), length))
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
    """In-memory autostart entry that models the PORT's contract.

    ``command`` is what an entry written right now would launch;
    ``installed_command`` is what the installed entry actually says.  They
    diverge exactly when an install has moved — the #201 state ``refresh``
    exists to repair, and the only thing about a refresh a test can SEE.

    Without them ``refresh`` was ``pass``, which satisfied every assertion in
    the suite by accident: ``RefreshAutostart`` could stop calling the port
    altogether and 4266 tests stayed green, the 38 real-adapter ones included.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._target: str | None = None
        #: What an entry written NOW would launch.  Move it to simulate the
        #: relocated install that makes an existing entry stale.
        self.command: str = "trcc"
        #: What the installed entry says — stale until the next refresh.
        self.installed_command: str | None = None

    def is_enabled(self) -> bool:
        return self._enabled

    def entry_location(self) -> str:
        return "/fake/autostart/trcc.desktop"

    def installed_target(self) -> str | None:
        return self._target if self._enabled else None

    def enable(self, target: str | None = None) -> None:
        self._enabled = True
        self._target = target or DEFAULT_AUTOSTART_TARGET
        self.installed_command = self.command

    def disable(self) -> None:
        self._enabled = False
        self._target = None
        self.installed_command = None

    def refresh(self) -> None:
        # The shape XDG, Windows and macOS all share: nothing installed -> do
        # nothing; otherwise re-render with the CURRENT command and the
        # INSTALLED target, so a repair never changes the user's choice.
        if not self._enabled:
            return
        self.enable(self.installed_target())


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


class FakeMic:
    """``App.audio`` without PortAudio — counts the lifecycle calls.

    **Not a convenience.**  The real :class:`~trcc.services.audio.AudioCapture`
    opens an ``sd.InputStream`` with a callback on a native thread, and a test
    that dispatches ``StartScreencast(audio=True)`` against a real ``App``
    leaves that stream open for the rest of the session.  Measured
    2026-09-23: one such test killed an xdist worker with ``Fatal Python
    error: Illegal instruction``, and the test that DIED was a different one
    each run — whichever happened to follow it.  Assign this over
    ``app.audio`` (a plain attribute, ``app.py:216``) in any test that turns
    screencast audio on.

    The counts are the point: a fake that never stops still reads
    ``running=True``, so only ``starts``/``stops`` can prove that nothing
    released the microphone.
    """

    def __init__(self) -> None:
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self) -> bool:
        self.running = True
        self.starts += 1
        return True

    def stop(self) -> None:
        self.running = False
        self.stops += 1

    def get_spectrum(self) -> tuple[float, ...]:
        return (0.0, 0.5, 1.0, 0.5)


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

    def open_transport(self, wire, vid, pid, serial=None,
                       unit="") -> Transport:
        # *unit* accepted and ignored: one fake transport per wire is the
        # point of this double.  Tests that care WHICH unit was asked for
        # spy on this method (see test_usb_unit_path).
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

    def setup(self, dry_run: bool = False) -> int:
        return 0

    def check_permissions(self) -> List[str]:
        return []

    #: What ``display_session`` answers; a test sets these to simulate a
    #: Wayland or headless session without touching the environment.
    display_server: DisplayServer = DisplayServer.X11
    desktops: tuple[str, ...] = ("fake",)

    def display_session(self) -> DisplaySession:
        return DisplaySession(self.display_server, self.desktops)

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
        """Scriptable via ``platform.memory_slots`` — default empty, the
        honest answer for a platform with no probe."""
        return getattr(self, "memory_slots", [])

    def disk_partitions(self) -> List[tuple]:
        """Two partitions on one drive — the shape that matters.

        Deliberately NOT one-per-drive: a physical disk supplies several
        partitions, which is exactly why ``ListDisks`` and ``disk_info``
        cannot index each other.
        """
        return [("/dev/fake0p1", "/"), ("/dev/fake0p2", "/home")]

    def disk_info(self) -> List[Dict[str, str]]:
        return []


# ── Fixtures ─────────────────────────────────────────────────────────


# ── The scope trap ───────────────────────────────────────────────────
#
# pytest sets HIGHER-scoped fixtures up FIRST.  A function-scoped autouse
# guard therefore does NOT protect anything a session-, package-, module- or
# class-scoped fixture does in its own setup — that work runs before the guard
# exists.  Measured 2026-09-11: a module-scoped fixture sees the real
# ``HOME=/home/<user>`` and the REAL ``DataInstallService.ensure_all``, while
# the test body it feeds sees the tmp_path and the stub.
#
# That is not hypothetical.  ``tests/test_integration_pipeline.py`` drives the
# full-pipeline smoke from a MODULE-scoped fixture, so ``_stub_data_install``
# below — whose docstring says "every test is offline-safe" — was never in
# effect for it, and every suite run downloaded 23.4 MB of theme archives from
# GitHub.  With no route the suite failed outright.
#
# So a guard that must hold EVERYWHERE is session-scoped, and the per-test
# guards below narrow it further.  Six module-scoped fixtures exist in tests/.


@pytest.fixture(scope="session", autouse=True)
def _the_suite_is_hermetic() -> Iterator[None]:
    """Nothing in the suite talks to the network.

    ``ConnectionRefusedError`` rather than something louder so production error
    handling behaves exactly as it does on a machine with no route:
    ``UrllibHttpFetcher`` turns ``OSError`` into ``HttpFetchError`` and every
    caller degrades the way it was written to.  A green suite then means the
    same thing online and offline, which is the property that was missing.

    Loopback stays open — blocking it would break any future in-process server
    — but nothing reaches off the machine.  Measured before landing this: the
    only outbound connections in the whole suite were to GitHub.
    """
    real_connect = socket.socket.connect

    def _is_local(family: int, address: object) -> bool:
        if family == getattr(socket, "AF_UNIX", None):
            return True
        if not (isinstance(address, tuple) and address):
            return False
        host = address[0]
        if not isinstance(host, str):
            return False
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host == "localhost"

    def connect(self: socket.socket, address: Any) -> Any:
        if not _is_local(self.family, address):
            raise ConnectionRefusedError(
                f"the trcc test suite is hermetic — blocked an outbound "
                f"connection to {address!r}.  Stub the port (HttpFetcher, "
                f"DataInstallService, GithubReleases) instead of reaching "
                f"the network.",
            )
        return real_connect(self, address)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", connect)
        yield


@pytest.fixture(scope="session", autouse=True)
def _home_is_never_the_real_one_at_any_scope(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Cover the window the per-test HOME guard cannot reach.

    See "The scope trap" above: the function-scoped guard below is set up
    AFTER every higher-scoped fixture, so a module-scoped one that resolves a
    log path or a data dir does it against the developer's real ``~``.  This
    closes that window for the whole session; the per-test guard still gives
    each test its own fresh directory.
    """
    root = tmp_path_factory.mktemp("session-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(root))
        mp.setenv("XDG_CONFIG_HOME", str(root / ".config"))
        yield


@pytest.fixture(autouse=True)
def _home_is_never_the_real_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No test may touch the developer's own ``~``.  Autouse, for cause.

    Function-scoped, so it does NOT reach module/session fixture setup — see
    "The scope trap" above; ``_home_is_never_the_real_one_at_any_scope``
    covers that window.

    ``tmp_home`` below has done this since forever, but only for the tests that
    ASKED for it, and the ones that write are not the ones that ask: anything
    reaching a real entry point resolves its log path through the REAL platform
    (``ensure_configured`` -> ``current_platform().paths().log_file()``), so it
    lands in ``~/.trcc/`` regardless of what fixtures the test declared.

    MEASURED 2026-09-10 with ``HOME`` pointed at an empty directory and a
    per-test tracer: a full run left ``trcc.log``, ``trcc.latest.log``, two
    lock files, a ``.run`` marker and a ``data/`` tree there, from tests spread
    across at least a dozen unrelated files — API routes, device catalog smoke,
    geometry, quirks, the Windows WMI seam.  It is diffuse, which is exactly
    why it has to be a blanket guard rather than a fixture each of them
    remembers to request.

    That matters beyond tidiness: ``~/.trcc/trcc.log`` is the file ``trcc
    report`` tails, so a developer who ran the suite and then produced a report
    was pasting test output into it.

    I first measured this over EIGHT files, found one culprit
    (``test_gui_entry_exit.py``) and concluded it was a one-file problem worth
    a one-file fix.  The full trace overturned that.  Recorded because the
    narrow measurement looked every bit as conclusive as the wide one.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """The per-test HOME as a value.

    The redirection itself is autouse above; this just names the directory for
    the tests that want to read or seed it.
    """
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

    Function-scoped, so "every test" is literally true and no more: work done
    in a module- or session-scoped fixture's SETUP runs before this exists.
    See "The scope trap" above — that gap really did ship.  The hermetic
    guard is the session-scoped backstop.
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


# =========================================================================
# Global logging state — restored around every test
# =========================================================================

@pytest.fixture(autouse=True)
def _logging_state_is_not_global() -> Iterator[None]:
    """Undo what a test does to the ROOT logger, because it is shared.

    ``configure_logging`` sets the root level, sets the per-frame family's
    level, and attaches three handlers -- and nothing put it back.  Two things
    call it during the suite: ``test_diagnostics`` directly (16 times), and
    EVERY CLI test that invokes a real command, because the Typer root callback
    configures logging (measured: one ``CliRunner().invoke`` leaves root at
    DEBUG with 3 handlers).  That is 125 invocations across five files.

    Any test scheduled after one of those in the same xdist worker inherited
    DEBUG, which is not hypothetical: it made
    ``test_a_vanished_pin_warns_ONCE_across_many_polls`` and its neighbour fail
    roughly one run in two, because an INFO line they did not expect became
    visible to ``caplog``.  Those two were hardened in `cb524768`; this closes
    the vector so the next victim never appears.

    Handlers a test opened are CLOSED, not merely detached: ``configure_logging``
    drops its own tagged handlers without closing them, so a suite that
    reconfigures 141 times leaks that many file descriptors.
    """
    from trcc.adapters.infra.logging import _HANDLER_TAG
    from trcc.core.logs import PER_FRAME_ROOT

    root = logging.getLogger()
    frame = logging.getLogger(PER_FRAME_ROOT)
    before = (root.level, frame.level, list(root.handlers))
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in before[2] and getattr(handler, _HANDLER_TAG, False):
                handler.close()
        root.handlers[:] = before[2]
        root.setLevel(before[0])
        frame.setLevel(before[1])
