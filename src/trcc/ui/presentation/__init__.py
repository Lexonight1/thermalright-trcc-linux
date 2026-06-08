"""Presentation Models (Fowler) — toolkit-free interaction layer.

A Presentation Model holds the *state and behaviour* of a view independent
of any GUI toolkit: it observes the dispatchers (``EventBus`` /
``SensorsUpdated``), dispatches Commands, and exposes plain-Python state.
Thin Views (``ui/gui``, ``ui/qtgui``, a future TUI/web front-end) bind to a
PM and sync to it manually via their own signals — there is no Qt here.

This package is the shared home for those models so every presentation reuses
the same interaction logic instead of re-deriving it.  Because the models are
Qt-free they are unit-testable with plain ``pytest`` (no ``QApplication``).
"""
from __future__ import annotations

from .overlay_model import OverlayModel

__all__ = ["OverlayModel"]
