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

from ...core.logs import per_frame
from ...core.models import OverlayElementConfig, new_overlay_id

log = logging.getLogger(__name__)
#: Per-tick readers — their records must never be CONSTRUCTED at
#: default verbosity.  73 ns/call short-circuited, measured.
frame_log = per_frame(__name__)

# Matches the 7×6 grid (legacy UCXiTongXianShi) — at most 42 elements.
MAX_ELEMENTS = 42


class OverlayModel:
    """Overlay editor state: element list + selection + enabled flag.

    No Qt, no rendering — just the interaction model.  Mutations return a
    ``bool`` so the View knows whether to repaint + emit; the View owns the
    signals.
    """

    def __init__(self) -> None:
        log.debug("__init__")
        self._configs: list[OverlayElementConfig] = []
        self._selected_index: int = -1
        self._enabled: bool = True

    # ── Enabled ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        log.debug("enabled")
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        log.debug("OverlayModel.set_enabled: %s → %s", self._enabled, enabled)
        self._enabled = enabled

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        log.debug("selected_index")
        return self._selected_index

    @property
    def selected_config(self) -> OverlayElementConfig | None:
        log.debug("selected_config")
        if 0 <= self._selected_index < len(self._configs):
            return self._configs[self._selected_index]
        return None

    def select(self, index: int) -> OverlayElementConfig | None:
        """Select an existing element; return it, or ``None`` if out of range
        (selection cleared)."""
        log.debug("select: index=%s", index)
        if 0 <= index < len(self._configs):
            self._selected_index = index
            return self._configs[index]
        self._selected_index = -1
        return None

    def clear_selection(self) -> None:
        log.debug("clear_selection")
        self._selected_index = -1

    # ── Query ─────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._configs)

    def all_configs(self) -> list[OverlayElementConfig]:
        """Shallow copy of the element list (callers must not mutate internals)."""
        log.debug("all_configs")
        return list(self._configs)

    def config_at(self, index: int) -> OverlayElementConfig | None:
        log.debug("config_at: index=%s", index)
        if 0 <= index < len(self._configs):
            return self._configs[index]
        return None

    def find_nearest(self, x: int, y: int) -> int:
        """Index of the element nearest (x, y) by squared distance; -1 if empty."""
        log.debug("find_nearest: x=%s y=%s", x, y)
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
        """Append a COPY of *config* as a new element with its own id, selecting it.

        A copy because a caller may hand the same object twice (the sensor
        sidebar keeps one per sensor), which put one object in two cells.
        No-op (``False``) when full.
        """
        if len(self._configs) >= MAX_ELEMENTS:
            log.debug("OverlayModel.add: at MAX_ELEMENTS=%d — refused", MAX_ELEMENTS)
            return False
        self._configs.append(replace(config, id=new_overlay_id()))
        log.debug("OverlayModel.add: %s as %s", config.mode.name,
                  self._configs[-1].id)
        self._selected_index = len(self._configs) - 1
        return True

    def delete(self, index: int) -> bool:
        """Remove element at ``index``; clamp selection to the new last index
        (``-1`` when the list becomes empty)."""
        log.debug("delete: index=%s", index)
        if not 0 <= index < len(self._configs):
            return False
        self._configs.pop(index)
        if self._selected_index >= len(self._configs):
            self._selected_index = len(self._configs) - 1
        return True

    def update(self, index: int, config: OverlayElementConfig) -> bool:
        """Replace the element at ``index``.  ``False`` if out of range."""
        log.debug("update: index=%s config=%s", index, config)
        if not 0 <= index < len(self._configs):
            return False
        self._configs[index] = config
        return True

    def load(self, configs: list[OverlayElementConfig]) -> None:
        """Replace the list (copied, capped at ``MAX_ELEMENTS``).

        The selected element stays selected wherever it now sits, matched by
        id: the grid reloads on every change any UI makes, including this
        one's own drag, and clearing the selection there left the next move
        with nothing to move.  One that is gone clears it.
        """
        frame_log.debug("load: configs=%s", configs)
        kept = self.selected_config
        self._configs = [replace(c) for c in configs[:MAX_ELEMENTS]]
        self._selected_index = next(
            (i for i, c in enumerate(self._configs)
             if kept is not None and kept.id and c.id == kept.id),
            -1,
        )

    def clear(self) -> None:
        log.debug("clear")
        self._configs.clear()
        self._selected_index = -1
