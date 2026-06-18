"""Every USB scan must go through the libusb-backend seam (#187/#188, #131).

The cutover reverted every call site to bare ``usb.core.find``, which on Windows
(no system libusb) raises ``usb.core.NoBackendError``.  ``_pyusb_find.find``
routes through ``libusb_package``'s bundled DLL there.  This guards that no
production module reintroduces a direct ``usb.core.find`` call, and that the seam
itself is importable + callable on this box.
"""
from __future__ import annotations

from pathlib import Path

from trcc.adapters.device._pyusb_find import find

_SRC = Path(__file__).resolve().parent.parent / "src" / "trcc"
_SEAM = _SRC / "adapters" / "device" / "_pyusb_find.py"


def test_no_direct_usb_core_find_outside_the_seam() -> None:
    """Only ``_pyusb_find`` may name the raw backend; everyone else uses it."""
    offenders: list[str] = []
    for py in _SRC.rglob("*.py"):
        if py == _SEAM:
            continue
        text = py.read_text(encoding="utf-8")
        if "usb.core.find(" in text:
            offenders.append(str(py.relative_to(_SRC)))
    assert not offenders, (
        "direct usb.core.find() calls bypass the libusb-backend seam — "
        f"route them through adapters.device._pyusb_find.find: {offenders}"
    )


def test_seam_is_callable_without_backend_error() -> None:
    """find() resolves a backend on this box (system libusb) — a bogus VID/PID
    yields no devices, but must NOT raise NoBackendError."""
    result = find(find_all=True, idVendor=0xFFFF, idProduct=0xFFFF)
    assert list(result or []) == []
