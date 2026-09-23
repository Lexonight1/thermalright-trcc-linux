"""PyUSB ``find()`` seam — uses libusb_package's bundled backend on Windows.

PyUSB needs ``libusb-1.0`` userspace to talk to USB devices.  Linux/macOS/BSD
ship it via the system package manager; **Windows has no system libusb**, so
pip-installed *and* PyInstaller-frozen users hit
``usb.core.NoBackendError: No backend available`` (#131, and again #187/#188 once
the GUI launch crash was fixed and discovery actually ran).

The canonical fix is `libusb-package <https://github.com/pyocd/libusb-package>`_
(pyocd's project): a Windows wheel that bundles ``libusb-1.0.dll`` and exposes a
``find()`` that loads that bundled DLL **explicitly by path** — not via the
flaky ``ctypes``/PATH search that bare ``usb.core.find`` relies on.  It's a
Windows-only dependency in ``pyproject.toml``.

This module is the ONE seam every ``usb.core.find`` call goes through, so there
is exactly one place that knows about the Windows backend quirk (the cutover
dropped this seam and reverted every call site to bare ``usb.core.find``, which
is what reintroduced the crash).  Importing ``libusb_package`` here is also what
makes PyInstaller bundle the module + its DLL into the frozen Windows build.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from libusb_package import find as _find  # pyright: ignore[reportMissingImports]
    log.debug("pyusb backend: libusb-package (bundled libusb-1.0)")
except ImportError:
    from usb.core import find as _find
    log.debug("pyusb backend: system libusb via usb.core.find")


def find(*args: Any, **kwargs: Any) -> Any:
    """Drop-in for ``usb.core.find()`` with a Windows-compatible backend.

    Identical signature + return type.  Routes through ``libusb_package.find``
    where it's installed (Windows, via the bundled DLL) and falls back to
    ``usb.core.find`` everywhere else (Linux/macOS/BSD system libusb).
    """
    log.debug("find")
    return _find(*args, **kwargs)


def usb_path(dev: Any) -> str | None:
    """Where this unit is PLUGGED IN, as a stable string — or ``None``.

    Two identical coolers share a VID/PID and ship no serial, so nothing in
    the USB descriptor tells them apart (#287).  What does is the topology:
    they are in different ports.  ``usb_find(find_all=True)`` already yields
    one object per physical unit and that object carries the answer; this
    tree read ``iSerialNumber`` and ``bcdDevice`` off it and dropped the rest.

    Format is the kernel's own USB topology name — ``1-14``, ``1-2.3`` —
    because it is the one a user can check against ``lsusb -t`` and ``dmesg``
    when a report says which panel did what.

    **Port chain first, address second, and the difference matters.**  The
    port chain is where the cable is: it survives a replug and a reboot, so it
    can key persisted settings.  ``address`` is handed out by the host
    controller and is reassigned on every replug — usable to tell two units
    apart *right now*, never to remember which was which.  It is the fallback
    only, spelled ``@`` so the two can never be confused on sight.

    ``None`` is a real answer.  PyUSB sets ``bus`` / ``address`` /
    ``port_number`` to ``None`` whenever the backend does not supply them
    (``usb.core.Device.__init__``), so this is guarded on EVERY platform, not
    just the ones we cannot test on.  A caller that gets ``None`` has learned
    that this host cannot tell two identical units apart.
    """
    bus = getattr(dev, "bus", None)
    if bus is None:
        log.debug("usb_path: backend supplied no bus — not addressable")
        return None
    ports = getattr(dev, "port_numbers", None) or ()
    if ports:
        path = f"{int(bus)}-{'.'.join(str(int(p)) for p in ports)}"
        log.debug("usb_path: %s (port chain — stable across replug)", path)
        return path
    address = getattr(dev, "address", None)
    if address is None:
        log.debug("usb_path: bus %s but no ports and no address", bus)
        return None
    path = f"{int(bus)}@{int(address)}"
    log.debug("usb_path: %s (ADDRESS — changes on replug)", path)
    return path


def find_unit(vid: int, pid: int, unit: str) -> Any:
    """The device of *vid:pid* plugged into *unit*, or ``None``.

    ``find(idVendor=, idProduct=)`` returns whichever unit libusb lists
    first, which is why two identical coolers both opened the same panel and
    the second ``claim_interface`` failed with "in use by another process"
    (#287).  This asks for a specific one instead.

    An empty *unit* means "the only one of this model" and is NOT a match-all
    here — callers that mean that call :func:`find` directly, so this function
    can never silently hand back an arbitrary device when the caller asked for
    a named one.
    """
    if not unit:
        log.debug("find_unit: no unit asked for")
        return None
    for dev in find(find_all=True, idVendor=vid, idProduct=pid) or []:
        if usb_path(dev) == unit:
            log.info("find_unit: %04x:%04x at %s", vid, pid, unit)
            return dev
    log.warning("find_unit: %04x:%04x has no unit at %s — it may have been "
                "unplugged or moved to another port", vid, pid, unit)
    return None
