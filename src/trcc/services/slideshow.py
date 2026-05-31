"""SlideshowService — rotate through a list of themes on a timer.

Per-device cursor that the render tick advances when the configured
interval has elapsed.  No background thread — the existing
``RenderAndSend`` ticker calls ``service.advance(key, now)`` once per
tick, the service decides whether to swap themes, and (if so) it tells
the App to dispatch ``LoadTheme``.

State is intentionally transient:
* Persisted: the slideshow config (which themes, what interval, whether
  enabled) lives on ``DeviceSettings.slideshow`` via Settings.set_slideshow.
* Transient: the cursor index + last-swap timestamp.  Service rebuilds
  these on first ``advance`` of a process.

Why a service: keeps ``ConfigureSlideshow`` / ``SetSlideshow`` Commands
short (delegate to the service) and gives the GUI an obvious place to
subscribe for "the theme just changed" events.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class _SlideshowState:
    """Transient cursor for one device's slideshow."""
    cursor: int = 0
    last_swap_at: float = 0.0


@dataclass
class SlideshowConfig:
    """Persisted slideshow settings (lives on DeviceSettings.slideshow)."""
    enabled: bool = False
    interval_s: float = 60.0
    themes: list[str] = field(default_factory=list)

    def normalised(self) -> SlideshowConfig:
        """Defensive clamp — caller-controlled fields stay in sane ranges."""
        log.debug("normalised: enabled=%s interval_s=%s themes=%d",
                  self.enabled, self.interval_s, len(self.themes))
        return SlideshowConfig(
            enabled=self.enabled,
            interval_s=max(1.0, float(self.interval_s)),
            themes=[t for t in self.themes if t],
        )


class SlideshowService:
    """Per-device slideshow cursor + advance logic."""

    def __init__(self) -> None:
        self._state: dict[str, _SlideshowState] = {}

    def reset(self, key: str) -> None:
        """Drop the cursor for *key* — call after ConfigureSlideshow
        changes the theme list."""
        log.info("reset: key=%s", key)
        self._state.pop(key, None)

    def advance(
        self,
        key: str,
        config: SlideshowConfig,
        *,
        now: float | None = None,
    ) -> str | None:
        """Return the next theme name to load, or None if no swap is due.

        Idempotent — repeated calls within the interval window all
        return None.  The cursor wraps when it walks past the last theme.
        """
        log.debug("advance: key=%s enabled=%s themes=%d interval_s=%s",
                  key, config.enabled, len(config.themes), config.interval_s)
        if not config.enabled or not config.themes:
            return None
        if now is None:
            now = time.monotonic()
        state = self._state.setdefault(key, _SlideshowState())
        if state.last_swap_at == 0.0:
            # First tick of this process — pick the current theme,
            # mark "swapped now" so we don't rotate again immediately.
            state.last_swap_at = now
            return config.themes[state.cursor % len(config.themes)]
        if (now - state.last_swap_at) < config.interval_s:
            return None
        state.cursor = (state.cursor + 1) % len(config.themes)
        state.last_swap_at = now
        return config.themes[state.cursor]

    def current(
        self, key: str, config: SlideshowConfig,
    ) -> str | None:
        """Return the theme name the cursor currently points at, or None
        when the slideshow has no themes."""
        log.debug("current: key=%s themes=%d", key, len(config.themes))
        if not config.themes:
            return None
        state = self._state.get(key)
        idx = state.cursor if state else 0
        return config.themes[idx % len(config.themes)]
