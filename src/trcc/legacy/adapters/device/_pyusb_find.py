"""PyUSB find() wrapper — uses libusb_package's bundled backend on Windows.

PyUSB needs ``libusb-1.0`` userspace to talk to USB devices. On Linux and
macOS the system package manager ships it. On Windows there is no system
libusb — pip-installed users hit ``usb.core.NoBackendError: No backend
available`` (issue #131, lallemandgianni-boop on Windows 11 v9.6.1).

The canonical fix is `libusb-package <https://github.com/pyocd/libusb-package>`_
(pyocd's project) — a Windows wheel that bundles ``libusb-1.0.dll`` and
exposes ``find()`` as a drop-in for ``usb.core.find()``.  pyproject.toml
adds it as a Windows-only dependency::

    libusb-package>=1.0.26.2; sys_platform == 'win32'

This module exports a single :func:`find` shim that uses
``libusb_package.find`` when available (Windows wheel installed) and
falls back to ``usb.core.find`` otherwise (Linux/macOS users with system
libusb, or anyone who installed the optional wheel themselves).  Every
``usb.core.find`` call site in ``trcc.adapters.device`` goes through
this seam so there's exactly one place that knows about the Windows
quirk.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from libusb_package import find as _find  # type: ignore[import-not-found]
    log.debug("pyusb backend: libusb-package (bundled libusb-1.0)")
except ImportError:
    from usb.core import find as _find  # type: ignore[no-redef]
    log.debug("pyusb backend: system libusb via usb.core.find")


def find(*args: Any, **kwargs: Any) -> Any:
    """Drop-in for ``usb.core.find()`` with Windows-compatible backend.

    Identical signature, identical return type. Routes through
    ``libusb_package.find`` on platforms where it's installed (Windows
    via pip), otherwise calls ``usb.core.find`` directly (Linux, macOS,
    or any environment where the user manages libusb themselves).
    """
    return _find(*args, **kwargs)
