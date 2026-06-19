"""WMI handle factory — the sanctioned ``wmi.WMI()`` call site.

``wmi.WMI()`` needs ``pythoncom.CoInitialize`` to have run on the *current
thread* first.  The long-lived sensor-poll thread gets that from
``WindowsPlatform.worker_thread_context`` (a COM apartment held for its
lifetime), but the **one-shot** platform probes — device scan, memory /
disk / SMART, GPU enumeration — run on whatever thread a Command lands on
(main, daemon RPC, a CLI invocation).  Off a COM-initialized thread those
raise ``wmi.x_wmi_uninitialised_thread`` (the #131 symptom).

Centralizing here gives those sites one COM-safe construction point:
``CoInitialize`` (idempotent — the second call per thread returns
``S_FALSE`` which pythoncom raises and we swallow) then ``wmi.WMI(**kwargs)``.
``wmi_kwargs`` forward to ``wmi.WMI`` (e.g. ``namespace='root\\WMI'``).

Mirrors legacy ``adapters/system/_windows_wmi.py``.  The thread-local-cached
poll sources (``_lhm`` / ``_msacpi``) keep their own handles by design — the
apartment they're born in is held open by ``worker_thread_context`` — so
this seam is for the one-shot probes, not the per-tick hot path.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def wmi_handle(**wmi_kwargs: Any) -> Any:
    """Return a ``wmi.WMI(**wmi_kwargs)`` handle with COM initialized.

    Raises ``ImportError`` when the ``wmi`` package is absent — callers
    that have a non-WMI fallback (psutil) catch it; the rest let it bubble
    to their existing broad guard.
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
        # pythoncom ships with pywin32 (Windows-only); on non-Windows or a
        # test context without it, skip COM init — wmi.WMI() raises its own
        # informative error if it actually needs an apartment.
        log.debug("wmi_handle: pythoncom unavailable, skipping CoInitialize")
    return wmi.WMI(**wmi_kwargs)
