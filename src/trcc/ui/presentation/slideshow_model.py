"""SlideshowModel — toolkit-free slideshow/carousel state for the theme browser.

The local-theme panel's slideshow state — the ordered theme-name array (max 6),
the enabled flag, and the interval — used to live as raw attrs on
``UCThemeLocal`` (``_lunbo_array`` / ``_slideshow`` / ``_slideshow_interval``)
that ``LCDHandler._restore_slideshow`` reached into directly
(``local._lunbo_array = …``).  Lifting it here gives the panel a clean public
API to back, dissolves the handler reach-ins, and makes the add/remove-cap,
interval-clamp and badge-position rules unit-testable without Qt.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MAX_SLIDESHOW = 6   # Windows LunBoArrayCount
MIN_INTERVAL = 3    # Windows minimum slideshow interval (seconds)


class SlideshowModel:
    """Ordered theme-name array + enabled flag + interval (no Qt)."""

    def __init__(self) -> None:
        self._themes: list[str] = []
        self._enabled = False
        self._interval = MIN_INTERVAL

    # ── Queries ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def themes(self) -> list[str]:
        """Theme names in slideshow order (copy)."""
        return list(self._themes)

    def badge_position(self, name: str) -> int:
        """1-based position of ``name`` in the array, or 0 if not included."""
        return self._themes.index(name) + 1 if name in self._themes else 0

    # ── Mutation ──────────────────────────────────────────────────────

    def toggle_enabled(self) -> bool:
        """Flip slideshow mode; return the new state."""
        self._enabled = not self._enabled
        return self._enabled

    def toggle_theme(self, name: str) -> bool:
        """Add/remove ``name`` from the array (capped at MAX_SLIDESHOW).

        Returns True if the theme is now included, False otherwise (removed,
        or refused because the array is full).
        """
        if name in self._themes:
            self._themes.remove(name)
            return False
        if len(self._themes) < MAX_SLIDESHOW:
            self._themes.append(name)
            return True
        log.warning(
            "SlideshowModel.toggle_theme: array full (max=%d) — %r not added",
            MAX_SLIDESHOW, name,
        )
        return False

    def remove_theme(self, name: str) -> None:
        """Drop ``name`` from the array if present (e.g. on theme delete)."""
        if name in self._themes:
            self._themes.remove(name)

    def set_interval(self, raw: object) -> int:
        """Parse + clamp a user-entered interval to an int >= MIN_INTERVAL.

        Stores and returns the clamped value; non-numeric input falls to
        MIN_INTERVAL (matches the panel's old ``max(3, int(text))`` rule).
        """
        try:
            val = int(raw)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            val = MIN_INTERVAL
        val = max(MIN_INTERVAL, val)
        self._interval = val
        return val

    def restore(self, themes: list[str], enabled: bool, interval: int) -> None:
        """Restore persisted state (caller supplies an already-validated
        interval; the handler keeps its ``max(1, …)`` restore rule)."""
        self._themes = list(themes)[:MAX_SLIDESHOW]
        self._enabled = enabled
        self._interval = interval
