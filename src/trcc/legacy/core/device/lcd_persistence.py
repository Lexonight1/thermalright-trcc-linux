"""LCDPersistence — per-device config writes for an LCDDevice.

Composed into LCDDevice via constructor injection.  Owns the contract
between an LCD's mutable state (brightness, rotation, content dirs) and
the on-disk config; the device facade delegates all writes here so the
persistence concern stays in one place.

No back-reference to ``LCDDevice``: the device passes geometry values as
parameters to ``persist_dirs``, and reads back validated settings via
``restored_settings`` to apply through its own setters.  This keeps the
hexagonal direction one-way (device → persistence) and lets the helper
be unit-tested without spinning up an entire LCDDevice.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models import DEFAULT_BRIGHTNESS_LEVEL

log = logging.getLogger(__name__)


class LCDPersistence:
    """Per-device config persistence for an LCDDevice."""

    __slots__ = ('_device_svc', '_lcd_config', 'log')

    def __init__(self, device_svc: Any, lcd_config: Any) -> None:
        self._device_svc = device_svc
        self._lcd_config = lcd_config
        self.log: logging.Logger = log

    # ── Single-field write ────────────────────────────────────────────────

    def persist(self, field: str, value: object) -> None:
        """Write one field to the active device's config slot."""
        dev = self._device_svc.selected if self._device_svc else None
        if not dev:
            self.log.debug("persist: skipped %s — no device selected", field)
            return
        if not self._lcd_config:
            self.log.debug("persist: skipped %s — no lcd_config", field)
            return
        self._lcd_config.persist(dev, field, value)
        self.log.debug("persist: %s = %r", field, value)

    # ── Multi-field dir snapshot ──────────────────────────────────────────

    def persist_dirs(
        self,
        *,
        theme_dir: Any,
        web_dir: Path | None,
        masks_dir: Path | None,
        native_resolution: tuple[int, int],
    ) -> None:
        """Snapshot the device's active per-orientation dirs to config.

        Skips when the device has no native resolution yet (pre-handshake)
        or no theme_dir resolution (no data_root configured).  Caller is
        expected to pass the live values via the device's geometry
        delegates so non-square devices started rotated persist the
        portrait-variant paths the runtime is actually using.
        """
        if theme_dir is None:
            return
        if not all(native_resolution):
            return
        self.persist(
            'theme_dir',
            str(theme_dir.path) if theme_dir.path.exists() else None,
        )
        self.persist('web_dir', str(web_dir) if web_dir else None)
        self.persist('masks_dir', str(masks_dir) if masks_dir else None)

    # ── Read-back ─────────────────────────────────────────────────────────

    def restored_settings(self) -> dict[str, Any] | None:
        """Return validated saved (brightness, rotation) for the active device.

        ``rotation`` is ``None`` when the saved value isn't one of
        ``{0, 90, 180, 270}`` so the caller can skip the apply call.
        Returns ``None`` when there's no active device or no config service.
        """
        dev = self._device_svc.selected if self._device_svc else None
        if not dev or not self._lcd_config:
            return None
        cfg = self._lcd_config.get_config(dev)
        raw_brightness = cfg.get('brightness_level', DEFAULT_BRIGHTNESS_LEVEL)
        rotation = cfg.get('rotation', 0)
        return {
            'brightness_level': (
                raw_brightness if 0 <= raw_brightness <= 100 else 100
            ),
            'rotation': rotation if rotation in (0, 90, 180, 270) else None,
        }
