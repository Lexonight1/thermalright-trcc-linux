"""ThemeService — theme discovery and metadata parsing.

A theme in TRCC is a directory containing:
    config.json       element layout, fonts, colors, overlay config
    background.png    (or .jpg) the base image
    optional extras   mask images, animation frames, fonts

This service provides:
    load(path)          → Theme (metadata, not pixels; pixels rendered later)
    list(directory)     → list[Theme] of themes found under a directory
    export(src, dst)    → zip/archive a theme for sharing
    import_(src, dst)   → unpack a shared theme archive

Config resolution:
    config.json   — preferred native format
    config1.dc    — binary legacy format (read-only fallback);
                    auto-migrated to config.json on first load.

Rendering (turning a Theme into frame bytes) is DisplayService's job.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.errors import ThemeError
from ..core.models import Theme
from ._dc_reader import load_dc_as_theme_config

log = logging.getLogger(__name__)


_CONFIG_FILE = "config.json"
_DC_CONFIG_FILE = "config1.dc"
_BACKGROUND_CANDIDATES = (
    # next/ native names
    "background.mp4", "background.mov", "background.webm",
    "background.png", "background.jpg", "background.jpeg",
    # Legacy theme naming (Windows TRCC)
    "Theme.mp4", "Theme.mov", "Theme.webm",
    "Theme.png", "Theme.jpg", "Theme.jpeg",
)
_MASK_CANDIDATES = (
    "mask.png", "mask.jpg", "mask.jpeg",
    "Mask.png", "Mask.jpg", "Mask.jpeg",
)


class ThemeService:
    """Theme discovery + parsing.

    Pure file I/O + JSON parsing — no rendering, no device talk.  Builds
    Theme metadata that later services consume.
    """

    def load(self, path: Path) -> Theme:
        """Load a theme directory into a Theme dataclass.

        Raises ThemeError if the directory is missing, unreadable, or
        the config.json is invalid.
        """
        if not path.exists():
            raise ThemeError(f"Theme directory does not exist: {path}")
        if not path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {path}")

        config = self._load_config(path)
        resolution = self._resolution_from_config(config)
        name = config.get("name") or path.name

        return Theme(
            path=path,
            name=name,
            resolution=resolution,
            config=config,
        )

    def list(self, directory: Path) -> List[Theme]:
        """Return every theme found directly under *directory*.

        A subdirectory is a theme iff it contains config.json OR
        config1.dc.  Invalid themes are skipped with a warning, not
        raised — list() never fails on one bad theme.
        """
        if not directory.exists() or not directory.is_dir():
            return []

        themes: List[Theme] = []
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            if not ((entry / _CONFIG_FILE).exists()
                    or (entry / _DC_CONFIG_FILE).exists()):
                continue
            try:
                themes.append(self.load(entry))
            except ThemeError as e:
                log.warning("Skipping invalid theme %s: %s", entry, e)
        return themes

    def background_path(self, theme: Theme) -> Optional[Path]:
        """Return the theme's background path (video or image), or None."""
        for candidate in _BACKGROUND_CANDIDATES:
            path = theme.path / candidate
            if path.exists():
                return path
        return None

    def mask_path(self, theme: Theme) -> Optional[Path]:
        """Return the theme's mask image path, or None if absent."""
        for candidate in _MASK_CANDIDATES:
            path = theme.path / candidate
            if path.exists():
                return path
        return None

    def export(self, theme_path: Path, archive_path: Path) -> None:
        """Archive a theme directory for sharing.  Phase 12."""
        raise ThemeError("ThemeService.export not yet implemented (Phase 12)")

    def import_(self, archive_path: Path, into_dir: Path) -> Theme:
        """Unpack a shared theme archive.  Phase 12."""
        raise ThemeError("ThemeService.import_ not yet implemented (Phase 12)")

    # ── internals ─────────────────────────────────────────────────────

    def _load_config(self, path: Path) -> dict:
        """Load theme config, preferring JSON and falling back to DC.

        On first successful DC load, writes a `config.json` alongside
        so subsequent loads skip the binary path.  Migration failure
        (read-only dir, permission, etc.) is logged but doesn't prevent
        the theme from loading.
        """
        json_path = path / _CONFIG_FILE
        if json_path.exists():
            try:
                return json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ThemeError(f"Invalid theme config {json_path}: {e}") from e

        dc_path = path / _DC_CONFIG_FILE
        if dc_path.exists():
            config = load_dc_as_theme_config(dc_path)
            self._try_migrate(json_path, config)
            return config

        raise ThemeError(
            f"No {_CONFIG_FILE} or {_DC_CONFIG_FILE} in {path}"
        )

    @staticmethod
    def _try_migrate(json_path: Path, config: dict) -> None:
        """Write the JSON form alongside the DC file; skip quietly on error."""
        try:
            json_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            log.info("Migrated %s → %s", _DC_CONFIG_FILE, json_path)
        except OSError as e:
            log.warning("Could not migrate DC→JSON at %s: %s", json_path, e)

    def _resolution_from_config(self, config: dict) -> Tuple[int, int]:
        """Extract (width, height) from config; fall back to (0, 0) if absent."""
        width = int(config.get("width", 0))
        height = int(config.get("height", 0))
        return (width, height)
