"""LCD config persistence — per-device settings save/load.

Concrete DeviceConfigService for LCD. Adds apply_format_prefs
(LCD-specific overlay format preferences).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..core.ports import DeviceConfigService

log = logging.getLogger(__name__)

_VIDEO_EXTS = frozenset({'.mp4', '.avi', '.mkv', '.webm'})
_IMAGE_EXTS = frozenset({'.png', '.jpg', '.jpeg', '.bmp', '.gif'})


class LCDConfigService(DeviceConfigService):
    """LCD per-device config persistence — injected into LCDDevice.

    Inherits device_key, persist, get_config from DeviceConfigService.
    Adds apply_format_prefs for LCD overlay format preferences.
    """

    def __init__(
        self,
        config_key_fn: Callable[..., str],
        save_setting_fn: Callable[..., None],
        get_config_fn: Callable[..., dict],
        apply_format_prefs_fn: Callable[..., dict | None],
    ) -> None:
        super().__init__(config_key_fn, save_setting_fn, get_config_fn)
        self._apply_prefs_fn = apply_format_prefs_fn

    def apply_format_prefs(self, overlay_cfg: dict) -> None:
        """Apply user format preferences to an overlay config. LCD-specific."""
        self._apply_prefs_fn(overlay_cfg)

    @staticmethod
    def normalize_legacy_theme(cfg: dict) -> dict:
        """Backfill theme_name + theme_type from legacy theme_path entries.

        Old configs only stored ``theme_path``; modern configs split into
        ``theme_name`` + ``theme_type`` (cloud/image/local). Returns a new
        dict with the legacy fields filled in when ``theme_name`` is missing.
        """
        if cfg.get("theme_name") or not (old_path := cfg.get("theme_path")):
            return cfg
        suffix = Path(old_path).suffix.lower()
        if suffix in _VIDEO_EXTS:
            theme_name, theme_type = Path(old_path).stem, "cloud"
        elif suffix in _IMAGE_EXTS:
            theme_name, theme_type = Path(old_path).name, "image"
        else:
            theme_name, theme_type = Path(old_path).name, "local"
        log.info("normalize_legacy_theme: %s → name=%s type=%s",
                 old_path, theme_name, theme_type)
        return {**cfg, "theme_name": theme_name, "theme_type": theme_type}
