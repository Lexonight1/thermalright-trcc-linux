"""Shared OS base — one abstract skeleton; factory children supply the internals.

There is only *the OS* (the :class:`~trcc.core.ports.Platform` port).  Every
concrete OS has the **same method names** (the contract); the plumbing that is
identical across OSes lives here **once**, and each factory child overrides only
the method bodies whose internals genuinely differ.

Adding a new OS — Solaris, Haiku, whatever ships next — is therefore one
subclass that names its key in its own class line — ``class HaikuPlatform(BaseOS,
key="haiku")`` — and implements the twelve members this class leaves abstract,
with nothing copied.  Five are internal hooks (``_make_paths`` /
``_build_sensors`` / ``_build_autostart`` / ``_build_hotplug`` / ``_open_scsi``);
seven are the port's own questions that have no shared answer (``setup`` /
``check_permissions`` / ``distro_name`` / ``memory_info`` / ``disk_info`` /
``no_devices_hint`` / ``permission_denied_hint``).  Miss one and the class
refuses to instantiate, naming it — that ``TypeError`` is the to-do list, and
for this layer it is the only one there is: TRCC's C# original has exactly one
OS and never abstracted it, so there is no oracle to check a port against.
That is the future-proofing: new OS = new subclass, no touched callers.

:data:`PLATFORMS` lives here rather than in the package ``__init__`` so the base
class can register its own children without importing its own package (a cycle).
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import usb.util  # pyright: ignore[reportMissingImports]

from ...core.factory import FallBackTo, Registry
from ...core.models import DeviceInfo, Wire
from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    PackageManager,
    Paths,
    Platform,
    ScreenCapture,
    ScsiTransport,
    SensorEnumerator,
    Transport,
)
from ...core.registry import ALL_DEVICES
from ..device._pyusb_find import find as usb_find
from ..device.transport import PyUsbBulkTransport

if TYPE_CHECKING:
    from ...core.models import UsbPowerState

log = logging.getLogger(__name__)

# The OS table.  A miss falls back to Linux with a warning rather than raising,
# so a debug session on a niche OS reaches a usable app instead of dying at the
# composition root.  See ``core.factory.FallBackTo``.
PLATFORMS: Registry[str, type[Platform]] = Registry(
    "platform", on_missing=FallBackTo("linux"),
)


class BasePaths(Paths):
    """User-data locations, all derived from ``_root`` / ``_user_content``.

    The four accessors are identical across every OS — they differ only in the
    two roots (and macOS's ``Logs/`` subdir).  Subclasses set the two roots in
    ``__init__``; macOS additionally overrides :meth:`log_file`.  Defined once
    here so the accessors — and their resolved-value debug logs — never drift.
    """

    _root: Path
    _user_content: Path

    def config_dir(self) -> Path:
        log.debug("%s.config_dir → %s", type(self).__name__, self._root)
        return self._root

    def data_dir(self) -> Path:
        path = self._root / "data"
        log.debug("%s.data_dir → %s", type(self).__name__, path)
        return path

    def user_content_dir(self) -> Path:
        log.debug("%s.user_content_dir → %s",
                  type(self).__name__, self._user_content)
        return self._user_content

    def log_file(self) -> Path:
        path = self._root / "trcc.log"
        log.debug("%s.log_file → %s", type(self).__name__, path)
        return path


class BaseOS(Platform):
    """Shared skeleton for every concrete OS :class:`Platform`.

    Owns the behaviour identical across OSes — USB bulk transport, device
    scan, memoised sensor/autostart/hotplug enumerators, install detection,
    dict-based install hints — and delegates the genuinely-different internals
    to abstract hooks the factory children implement.  Twelve members stay
    abstract (see the module docstring), which is what keeps this class from
    instantiating on its own and what tells a new OS's author what to write.
    """

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        """Register the subclass under the ``key=`` it declares.

        An OS states its own ``sys.platform`` key in its class line::

            class LinuxOS(BaseOS, key="linux"): ...

        ``key=None`` means "intermediate base, don't register".
        """
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate OS base, not registered", cls.__name__)
            return
        log.debug("%s: registering as OS %r", cls.__name__, key)
        PLATFORMS.register(key)(cls)

    # ── Shared answers to port questions ─────────────────────────────────
    #
    # The port asks every OS all 21 of its questions (``core/ports.py``); these
    # are the ones with a real shared answer, so they live here — one MRO step
    # below the contract.  A new OS inherits them and overrides only what
    # genuinely differs, which is the whole point of this class.
    #
    # The port deliberately keeps NO body of its own: a default up there let an
    # unimplemented method return a plausible value, making "this OS cannot
    # tell" indistinguishable from "nobody wrote it yet".  Down here the same
    # default is a decision an OS layer made, not a hole the contract papered
    # over.  The four with no shared answer — ``memory_info`` / ``disk_info`` /
    # ``no_devices_hint`` / ``permission_denied_hint``, all four of which every
    # OS already implements — get no body here either, so they stay abstract
    # alongside ``setup`` / ``check_permissions`` / ``distro_name`` and a new OS
    # is told about them at instantiation.

    def package_manager(self) -> str:
        """The system package manager, or "" when this OS has none of ours.

        Only Linux ships trcc through a distro manager, so the other OSes
        answer "" — an honest "not applicable", not a guess.
        """
        log.debug("%s.package_manager: none", type(self).__name__)
        return ""

    def upgrade_command(self) -> tuple[str, ...]:
        """Argv that upgrades trcc on this OS, or empty when there is none."""
        log.debug("%s.upgrade_command: none", type(self).__name__)
        return ()

    def usb_power_state(self, vid: int, pid: int) -> UsbPowerState | None:
        """Not exposed by default — only Linux publishes runtime PM per device.

        An honest ``None`` beats a stub inventing a value: this exists so a
        timed-out handshake can be told apart from a SUSPENDED panel (#150),
        and a wrong answer would mislead exactly the debugging it serves.
        """
        log.debug("%s.usb_power_state: not exposed on this OS (%04x:%04x)",
                  type(self).__name__, vid, pid)
        return None

    def minimize_on_close(self) -> bool:
        """Hide-to-tray — Linux / macOS / BSD behaviour.  Windows overrides."""
        log.debug("%s.minimize_on_close: False (hide to tray)",
                  type(self).__name__)
        return False

    def configure_stdout(self) -> None:
        """Nothing to do — this OS's console already speaks UTF-8.

        Windows overrides to rewrap cp1252 streams before the logging
        StreamHandler attaches to them.
        """
        log.debug("%s.configure_stdout: console already UTF-8, no-op",
                  type(self).__name__)

    def worker_thread_context(self) -> AbstractContextManager[None]:
        """No per-thread setup needed.  Windows overrides (COM apartment)."""
        log.debug("%s.worker_thread_context: null context",
                  type(self).__name__)
        return nullcontext()

    #: tool → install one-liner; the default :meth:`software_install_hint`
    #: reads this.  Dict-driven OSes (macOS/Windows/BSD) set it; Linux
    #: overrides the whole method (distro package-manager detection).
    _INSTALL_HINTS: dict[str, str] = {}

    def __init__(self) -> None:
        log.info("%s: initialising", type(self).__name__)
        self._paths: Paths = self._make_paths()
        self._sensors: SensorEnumerator | None = None
        self._autostart: AutostartManager | None = None
        self._hotplug: HotplugMonitor | None = None
        self._packages: PackageManager | None = None
        self._screen_capture: ScreenCapture | None = None

    # ── Abstract hooks — the per-OS internals ────────────────────────────

    @abstractmethod
    def _make_paths(self) -> Paths:
        """Build this OS's :class:`Paths` (its user-data location scheme)."""

    @abstractmethod
    def _build_sensors(self) -> SensorEnumerator:
        """Build this OS's sensor enumerator (called once, then memoised)."""

    @abstractmethod
    def _build_autostart(self) -> AutostartManager:
        """Build this OS's autostart manager (called once, then memoised)."""

    @abstractmethod
    def _build_hotplug(self) -> HotplugMonitor:
        """Build this OS's hotplug monitor (called once, then memoised)."""

    def _build_packages(self) -> PackageManager:
        """Build this OS's package-manager query surface.

        Concrete, unlike its five siblings, and deliberately: the default is
        "this OS cannot be asked", which is the truthful answer for every OS
        whose manager nobody has run.  Making it abstract would force each OS
        to write a stub, and a stub is where a confident wrong answer comes
        from -- which is the failure this port exists to remove.
        """
        from ._packages import NoPackageManager
        log.debug("%s._build_packages: none for this OS", type(self).__name__)
        return NoPackageManager()

    def _build_screen_capture(self) -> ScreenCapture:
        """Build this OS's capture source (called once, then memoised).

        Concrete, and truthfully so — unlike the defaults deleted on
        2026-08-21, this one WORKS everywhere rather than answering on an
        OS's behalf.  ``QtScreenCapture`` tries Qt's native grab first and
        falls through to ``grim`` / ``scrot`` only where they exist, so an
        OS that has neither still captures.  Override when a native path
        beats it.
        """
        from ..screencast import QtScreenCapture

        log.info("%s._build_screen_capture: QtScreenCapture (Qt native → "
                 "grim → scrot → full-grab+crop)", type(self).__name__)
        return QtScreenCapture()

    # ── Shared transport / scan ──────────────────────────────────────────

    def _transport_openers(
        self,
    ) -> Mapping[Wire, Callable[[int, int, str | None], Transport]]:
        """Wire → the opener that serves it.  **Unlisted wires use bulk.**

        The same shape as ``_udev._WIRE_SUBSYSTEMS``: a per-``(OS, wire)`` fact
        stated as a table row rather than as control flow.  Only SCSI needs a
        native path; HID / BULK / LY / LED all speak plain USB bulk
        via libusb, identically everywhere, so they need no rows at all.

        A new wire needing a new kernel interface is one row **here** — not a
        new abstract method on the port and an implementation in every OS.  An
        OS whose mapping genuinely differs overrides this one method.
        """
        openers = {Wire.SCSI: self._open_scsi}
        log.debug("%s._transport_openers: %s (all other wires -> bulk)",
                  type(self).__name__, [w.value for w in openers])
        return openers

    def open_transport(self, wire: Wire, vid: int, pid: int,
                       serial: str | None = None) -> Transport:
        """Return an unopened transport for *wire* — the port's one entry point."""
        opener = self._transport_openers().get(wire, self._open_bulk)
        log.info("%s.open_transport: wire=%s %04x:%04x serial=%r → %s",
                 type(self).__name__, wire.value, vid, pid, serial,
                 getattr(opener, "__name__", opener))
        return opener(vid, pid, serial)

    def _open_bulk(self, vid: int, pid: int,
                   serial: str | None = None) -> Transport:
        """Open a bulk transport — identical on every OS (libusb)."""
        log.debug("%s._open_bulk: %04x:%04x serial=%r",
                  type(self).__name__, vid, pid, serial)
        return PyUsbBulkTransport(vid, pid, serial)

    @abstractmethod
    def _open_scsi(self, vid: int, pid: int,
                   serial: str | None = None) -> ScsiTransport:
        """Open this OS's native SCSI passthrough — the one divergent path."""

    def scan_devices(self) -> list[DeviceInfo]:
        """Walk :data:`ALL_DEVICES` and return a DeviceInfo per present pair.

        The bcdDevice fingerprint is read on every OS (harmless where absent);
        the composition root uses it to resolve per-firmware quirks (#228).
        """
        log.info("%s.scan_devices: scanning %d known VID/PID pairs",
                 type(self).__name__, len(ALL_DEVICES))
        found: list[DeviceInfo] = []
        for (vid, pid) in ALL_DEVICES:
            for dev in (usb_find(find_all=True, idVendor=vid, idProduct=pid) or []):
                serial_idx = getattr(dev, "iSerialNumber", 0)
                serial = ""
                try:
                    if serial_idx:
                        serial = usb.util.get_string(dev, serial_idx) or ""
                except Exception:
                    serial = ""
                bcd = int(getattr(dev, "bcdDevice", 0) or 0)
                found.append(DeviceInfo(vid=vid, pid=pid, serial=serial or None,
                                        bcd_device=bcd))
                log.info("  found %04x:%04x serial=%r bcdDevice=%#06x",
                         vid, pid, serial, bcd)
        log.info("%s.scan_devices: %d device(s) total",
                 type(self).__name__, len(found))
        return found

    # ── Filesystem + memoised enumerators ────────────────────────────────

    def paths(self) -> Paths:
        log.debug("%s.paths()", type(self).__name__)
        return self._paths

    def sensors(self) -> SensorEnumerator:
        if self._sensors is None:
            log.info("%s.sensors: building enumerator", type(self).__name__)
            self._sensors = self._build_sensors()
        else:
            log.debug("%s.sensors: returning cached enumerator",
                      type(self).__name__)
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            log.info("%s.autostart: building manager", type(self).__name__)
            self._autostart = self._build_autostart()
        else:
            log.debug("%s.autostart: returning cached manager",
                      type(self).__name__)
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        if self._hotplug is None:
            log.info("%s.hotplug: building monitor", type(self).__name__)
            self._hotplug = self._build_hotplug()
        else:
            log.debug("%s.hotplug: returning cached monitor",
                      type(self).__name__)
        return self._hotplug

    def screen_capture(self) -> ScreenCapture:
        if self._screen_capture is None:
            log.info("%s.screen_capture: building capture source",
                     type(self).__name__)
            self._screen_capture = self._build_screen_capture()
        else:
            log.debug("%s.screen_capture: returning cached source",
                      type(self).__name__)
        return self._screen_capture

    def packages(self) -> PackageManager:
        if self._packages is None:
            log.info("%s.packages: building query surface",
                     type(self).__name__)
            self._packages = self._build_packages()
        else:
            log.debug("%s.packages: returning cached query surface",
                      type(self).__name__)
        return self._packages

    # ── Install detection (OS-agnostic) ──────────────────────────────────

    def install_method(self) -> str:
        """How this package got here — the one honest, OS-agnostic detector."""
        from ..diagnostics.install import detect_installer
        method = detect_installer()
        log.info("%s.install_method: %s", type(self).__name__, method)
        return method

    def software_install_hint(self, tool: str) -> str:
        """Default dict-based hint from :attr:`_INSTALL_HINTS`.

        Linux overrides this with distro package-manager detection.
        """
        log.debug("%s.software_install_hint: tool=%s", type(self).__name__, tool)
        hint = self._INSTALL_HINTS.get(tool)
        if hint is None:
            return f"Install {tool} and ensure it is on PATH"
        # ONE LINE.  Every consumer appends this to a list that is later
        # "\n".join-ed under a `hint: ` label -- doctor.py:39,
        # debug_report.py:522, qtgui/system_panel.py:182 -- so an embedded
        # newline broke the second half out of both the indent and the label,
        # in the very output reporters paste to us.  The "{tool} not found"
        # preamble is dropped with it: the renderer already printed the
        # message ("7z not on PATH") on the line above.
        return hint
