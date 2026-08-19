"""Shared OS base — one abstract skeleton; factory children supply the internals.

There is only *the OS* (the :class:`~trcc.core.ports.Platform` port).  Every
concrete OS has the **same method names** (the contract); the plumbing that is
identical across OSes lives here **once**, and each factory child overrides only
the method bodies whose internals genuinely differ.

Adding a new OS — Solaris, Haiku, whatever ships next — is therefore one
subclass that names its key in its own class line — ``class HaikuPlatform(BaseOS,
key="haiku")`` — implements the abstract hooks (``_make_paths`` /
``_build_sensors`` / ``_build_autostart`` / ``_build_hotplug``) plus
``_open_scsi`` and the handful of genuinely-divergent bodies, with nothing
copied.  That is the future-proofing: new OS = new subclass, no touched callers.

:data:`PLATFORMS` lives here rather than in the package ``__init__`` so the base
class can register its own children without importing its own package (a cycle).
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import usb.util  # pyright: ignore[reportMissingImports]

from ...core.factory import FallBackTo, Registry
from ...core.models import DeviceInfo, Wire
from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    Paths,
    Platform,
    ScsiTransport,
    SensorEnumerator,
    Transport,
)
from ...core.registry import ALL_DEVICES
from ..device._pyusb_find import find as usb_find
from ..device.transport import PyUsbBulkTransport

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
    to abstract hooks the factory children implement.  Still abstract: the
    per-OS bodies (``_open_scsi`` / ``setup`` / ``check_permissions`` /
    ``distro_name``) keep it from instantiating on its own.
    """

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        """Register the subclass under the ``key=`` it declares.

        An OS states its own ``sys.platform`` key in its class line::

            class LinuxPlatform(BaseOS, key="linux"): ...

        ``key=None`` means "intermediate base, don't register".
        """
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate OS base, not registered", cls.__name__)
            return
        log.debug("%s: registering as OS %r", cls.__name__, key)
        PLATFORMS.register(key)(cls)

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
        return f"{tool} not found — install it:\n  {hint}"
