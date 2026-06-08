"""OverlayModel — toolkit-free presentation model for the overlay editor.

Pure Python (no Qt).  Owns the overlay element list, the selected index, and
the enabled flag, plus the CRUD / selection / nearest-element logic that used
to live on ``OverlayGridPanel`` (a ``QFrame``).  The Qt panel is now a thin
View that delegates here and renders the result; a TUI / web View can bind to
the same model.

Serialization to/from the renderer dict + Command-bus shapes lives in
:mod:`.overlay_serialization`, so this model depends only on ``core.models``.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from ...core.models import OverlayElementConfig

log = logging.getLogger(__name__)

# Matches the 7×6 grid (legacy UCXiTongXianShi) — at most 42 elements.
MAX_ELEMENTS = 42


class OverlayModel:
    """Overlay editor state: element list + selection + enabled flag.

    No Qt, no rendering — just the interaction model.  Mutations return a
    ``bool`` so the View knows whether to repaint + emit; the View owns the
    signals.
    """

    def __init__(self) -> None:
        self._configs: list[OverlayElementConfig] = []
        self._selected_index: int = -1
        self._enabled: bool = True

    # ── Enabled ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        log.debug("OverlayModel.set_enabled: %s → %s", self._enabled, enabled)
        self._enabled = enabled

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def selected_config(self) -> OverlayElementConfig | None:
        if 0 <= self._selected_index < len(self._configs):
            return self._configs[self._selected_index]
        return None

    def select(self, index: int) -> OverlayElementConfig | None:
        """Select an existing element; return it, or ``None`` if out of range
        (selection cleared)."""
        if 0 <= index < len(self._configs):
            self._selected_index = index
            return self._configs[index]
        self._selected_index = -1
        return None

    def clear_selection(self) -> None:
        self._selected_index = -1

    # ── Query ─────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._configs)

    def all_configs(self) -> list[OverlayElementConfig]:
        """Shallow copy of the element list (callers must not mutate internals)."""
        return list(self._configs)

    def config_at(self, index: int) -> OverlayElementConfig | None:
        if 0 <= index < len(self._configs):
            return self._configs[index]
        return None

    def find_nearest(self, x: int, y: int) -> int:
        """Index of the element nearest (x, y) by squared distance; -1 if empty."""
        if not self._configs:
            return -1
        best_idx, best_dist = -1, float("inf")
        for i, cfg in enumerate(self._configs):
            d = (cfg.x - x) ** 2 + (cfg.y - y) ** 2
            if d < best_dist:
                best_dist, best_idx = d, i
        return best_idx

    # ── Mutation ──────────────────────────────────────────────────────

    def add(self, config: OverlayElementConfig) -> bool:
        """Append an element, selecting it.  No-op (``False``) when full."""
        if len(self._configs) >= MAX_ELEMENTS:
            log.debug("OverlayModel.add: at MAX_ELEMENTS=%d — refused", MAX_ELEMENTS)
            return False
        self._configs.append(config)
        self._selected_index = len(self._configs) - 1
        return True

    def delete(self, index: int) -> bool:
        """Remove element at ``index``; clamp selection to the new last index
        (``-1`` when the list becomes empty)."""
        if not 0 <= index < len(self._configs):
            return False
        self._configs.pop(index)
        if self._selected_index >= len(self._configs):
            self._selected_index = len(self._configs) - 1
        return True

    def update(self, index: int, config: OverlayElementConfig) -> bool:
        """Replace the element at ``index``.  ``False`` if out of range."""
        if not 0 <= index < len(self._configs):
            return False
        self._configs[index] = config
        return True

    def load(self, configs: list[OverlayElementConfig]) -> None:
        """Replace the list (copied, capped at ``MAX_ELEMENTS``); clear selection."""
        self._configs = [replace(c) for c in configs[:MAX_ELEMENTS]]
        self._selected_index = -1

    def clear(self) -> None:
        self._configs.clear()
        self._selected_index = -1
