"""Shared, toolkit-free formatting for device errors.

Both the gui and qtgui Views render the same bus data (a failed
``ConnectResult`` or an ``ErrorOccurred`` event) through this one function,
so the wording lives once (DRY).  No Qt here — the View decides *where* to
show the string (dialog, tray balloon, status line); this decides *what* it
says: the message, plus any per-OS hints the Platform supplied.
"""
from __future__ import annotations

from typing import Any


def format_device_error(obj: Any) -> str:
    """``message`` + per-OS ``hints`` → one human string.

    Duck-typed over anything carrying ``.message`` and ``.hints`` — both
    ``ConnectResult`` and ``ErrorOccurred`` qualify.
    """
    message = str(getattr(obj, "message", "") or "")
    hints = list(getattr(obj, "hints", []) or [])
    if hints:
        return message + "\n" + "\n".join(hints)
    return message
