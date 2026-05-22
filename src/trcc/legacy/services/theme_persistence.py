"""Theme persistence — save, export, import theme configurations.

Extracted from DisplayService (SRP). All static methods that operate on
provided data — no mutable state.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .overlay import OverlayService
from .theme import ThemeService

log = logging.getLogger(__name__)


class ThemePersistence:
    """Theme save/export/import operations.

    Accepts a ThemeService instance for export/import (hexagonal DI).
    """

    def __init__(self, theme_svc: ThemeService | None = None) -> None:
        self._theme_svc = theme_svc

    @staticmethod
    def save(
        name: str, data_dir: Path, lcd_size: tuple[int, int],
        current_image: Any, overlay: OverlayService,
        mask_source_dir: Path | None,
        media_source_path: Any, media_is_playing: bool,
        current_theme_path: Path | None,
    ) -> tuple[bool, str]:
        """Save current config as a custom theme."""
        if not current_image:
            return False, "No image to save"

        # Render overlay for thumbnail; save clean bg as 00.png
        rendered = overlay.render(current_image)
        mask_img, mask_pos = overlay.get_mask()
        overlay_config = overlay.config

        log.debug(
            "ThemePersistence.save: mask_source_dir=%s, mask_img=%s (type=%s), "
            "mask_pos=%s, overlay_config keys=%s",
            mask_source_dir,
            bool(mask_img), type(mask_img).__name__,
            mask_pos,
            list(overlay_config.keys()) if overlay_config else None,
        )

        return ThemeService.save(
            name, data_dir, lcd_size,
            background=current_image,
            preview=rendered,
            overlay_config=overlay_config,
            mask=mask_img,
            mask_source=mask_source_dir,
            mask_position=mask_pos,
            video_path=media_source_path if media_is_playing else None,
            current_theme_path=current_theme_path,
        )

    def export_config(
        self,
        export_path: Path,
        current_theme_path: Path | None,
        lcd_width: int, lcd_height: int,
    ) -> tuple[bool, str]:
        """Export current theme as .tr or JSON file."""
        if not current_theme_path:
            return False, "No theme loaded"

        if str(export_path).endswith('.tr'):
            if self._theme_svc is None:
                return False, "Export not available (ThemeService not configured)"
            return self._theme_svc.export_tr(current_theme_path, export_path)

        # JSON export
        config = {
            'theme_path': str(current_theme_path),
            'resolution': f'{lcd_width}x{lcd_height}',
        }
        try:
            with open(str(export_path), 'w') as f:
                json.dump(config, f, indent=2)
            return True, f"Exported: {export_path.name}"
        except Exception as e:
            return False, f"Export failed: {e}"

    def import_config(
        self,
        import_path: Path, data_dir: Path,
        lcd_size: tuple[int, int],
    ) -> tuple[bool, Any]:
        """Import theme from .tr or JSON file.

        Returns (success, result) where result is either a ThemeInfo to load
        or an error string.
        """
        if str(import_path).endswith('.tr'):
            if self._theme_svc is None:
                return False, "Import not available (ThemeService not configured)"
            return self._theme_svc.import_tr(import_path, data_dir, lcd_size)

        # JSON import
        try:
            with open(str(import_path)) as f:
                config = json.load(f)
            tp = config.get('theme_path')
            if tp and Path(tp).exists():
                from .theme import theme_info_from_directory
                theme = theme_info_from_directory(Path(tp))
                return True, theme
            return False, "Theme path in config not found"
        except Exception as e:
            return False, f"Import failed: {e}"
