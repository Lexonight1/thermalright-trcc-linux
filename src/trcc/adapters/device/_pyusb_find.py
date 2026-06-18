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
    return _find(*args, **kwargs)
