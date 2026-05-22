"""WMI handle factory — single sanctioned ``wmi.WMI()`` call site.

``wmi.WMI()`` requires ``pythoncom.CoInitialize`` to have been called for the
*current thread* before the WMI object is constructed.  Every USB-detect
or sensor-poll path that reaches WMI from a worker thread (Qt event loop,
background poller, daemon-mode RPC) without that init raises
``wmi.x_wmi_uninitialised_thread`` — the symptom reporter
`lallemandgianni-boop` filed as #131.

Centralizing here means the COM-init discipline lives in one place; all
other Windows-specific modules ``from ._windows_wmi import wmi_handle`` and
never construct ``wmi.WMI()`` directly.  ``dev/smoke_anything.py`` enforces
this with the ``windows.wmi.coinit`` probe.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def wmi_handle(**wmi_kwargs: Any) -> Any:
    """Return a ``wmi.WMI(**wmi_kwargs)`` handle with COM initialized.

    Calling ``CoInitialize`` is idempotent in practice — the second call in
    the same thread returns ``S_FALSE`` which pythoncom raises as
    ``com_error``; we swallow that branch and proceed.

    ``wmi_kwargs`` are forwarded to ``wmi.WMI`` (e.g. ``namespace='root\\WMI'``).
    """
    import wmi  # pyright: ignore[reportMissingImports]

    try:
        import pythoncom  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
        try:
            pythoncom.CoInitialize()
        except pythoncom.com_error:
            # Already initialized for this thread — fine, continue.
            pass
    except ImportError:
        # pythoncom is part of pywin32 (Windows-only); on non-Windows or
        # in test contexts where it isn't installed, skip the COM init —
        # wmi.WMI() will raise its own informative error if it actually
        # needs COM.
        log.debug("wmi_handle: pythoncom unavailable, skipping CoInitialize")
    return wmi.WMI(**wmi_kwargs)
