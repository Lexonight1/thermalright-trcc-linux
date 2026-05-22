# Re-export stub — all report code lives in diagnostics.py
from .diagnostics import _KNOWN_VIDS, DebugReport, _Section

__all__ = ["_KNOWN_VIDS", "DebugReport", "_Section"]
