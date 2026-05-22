"""Tagged logger factory — stamps a per-device label on the project's
custom logger subclass.

Used across core/device/, services/, and ui/gui/ to attach a `.dev`
field that the diagnostics formatter reads. Pure Python; no project
deps so anyone may import it without crossing layer boundaries.
"""
from __future__ import annotations

import logging


def tagged_logger(namespace: str, label: str = '') -> logging.Logger:
    """Get a logger and stamp a device label on its `.dev` attr.

    The project's custom logger subclass exposes a `.dev` field that the
    diagnostics formatter reads. Standard `logging.Logger` doesn't have
    it, so we guard with hasattr and use setattr (no type: ignore needed).

    With a label, returns a child logger named ``{namespace}.{label}`` with
    ``.dev = label``. Without a label, returns the bare namespace logger
    with ``.dev = '-'`` (the diagnostics formatter's empty-state token).
    """
    log = logging.getLogger(f'{namespace}.{label}' if label else namespace)
    if hasattr(log, 'dev'):
        setattr(log, 'dev', label or '-')  # noqa: B010 — setattr is the pyright-clean way; direct assignment to a non-declared attribute is a type error
    return log
