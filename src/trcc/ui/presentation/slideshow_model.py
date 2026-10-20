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
        log.debug("__init__")
        self._themes: list[str] = []
        self._enabled = False
        self._interval = MIN_INTERVAL

    # ── Queries ───────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        log.debug("enabled")
        return self._enabled

    @property
    def interval(self) -> int:
        log.debug("interval")
        return self._interval

    @property
    def themes(self) -> list[str]:
        """Theme names in slideshow order (copy)."""
        log.debug("themes")
        return list(self._themes)

    def badge_position(self, name: str) -> int:
        """1-based position of ``name`` in the array, or 0 if not included."""
        log.debug("badge_position: name=%s", name)
        return self._themes.index(name) + 1 if name in self._themes else 0

    # ── Mutation ──────────────────────────────────────────────────────

    def toggle_enabled(self) -> bool:
        """Flip slideshow mode; return the new state."""
        log.debug("toggle_enabled")
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
        log.debug("remove_theme: name=%s", name)
        if name in self._themes:
            self._themes.remove(name)

    def set_interval(self, raw: str | int) -> int:
        """Parse + clamp a user-entered interval to an int >= MIN_INTERVAL.

        Stores and returns the clamped value; non-numeric input falls to
        MIN_INTERVAL (matches the panel's old ``max(3, int(text))`` rule).
        """
        log.debug("set_interval: raw=%s", raw)
        try:
            val = int(raw)
        except (ValueError, TypeError):
            val = MIN_INTERVAL
        val = max(MIN_INTERVAL, val)
        self._interval = val
        return val

    def restore(self, themes: list[str], enabled: bool, interval: int) -> None:
        """Restore persisted state (caller supplies an already-validated
        interval; the handler keeps its ``max(1, …)`` restore rule)."""
        log.debug("restore: themes=%s enabled=%s", themes, enabled)
        self._themes = list(themes)[:MAX_SLIDESHOW]
        self._enabled = enabled
        self._interval = interval
