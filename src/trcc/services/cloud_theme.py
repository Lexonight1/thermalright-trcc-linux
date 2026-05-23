"""``CloudThemeService`` — orchestrator that turns a cloud theme into a
local theme directory ready for :class:`LoadTheme`.

Composition: takes a ``CzhordeCatalog`` (the network adapter), a ``Paths``
(for resolving the user's content dir + cache root), and writes a
minimal next/-style theme dir under
``user_content_dir / cloud / <resolution> / <theme_id>``.  Each cloud
theme is a single-MP4 background with no overlay elements; users layer
their own via the overlay-element Commands.

Why a service: keeps the ``LoadCloudTheme`` Command short (delegate to
``service.materialise``) and gives the GUI an obvious place to subscribe
for download progress / list refresh.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..adapters.theme.cloud import (
    CloudCategory,
    CloudThemeEntry,
    CzhordeCatalog,
)
from ..core.ports import Paths

log = logging.getLogger(__name__)


class CloudThemeService:
    """Cloud catalog reads + theme materialisation."""

    def __init__(
        self,
        catalog: CzhordeCatalog,
        paths: Paths,
    ) -> None:
        self._catalog = catalog
        self._paths = paths

    # ── Read-only catalog ─────────────────────────────────────────────

    def categories(self) -> tuple[CloudCategory, ...]:
        return self._catalog.categories()

    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        return self._catalog.list_themes(category)

    # ── Network + materialisation ─────────────────────────────────────

    def materialise(
        self, theme_id: str, resolution: tuple[int, int],
    ) -> Path:
        """Download the cloud theme and lay it out as a next/ theme dir.

        Returns the directory path; ``LoadTheme(path=that_dir)`` then
        renders it through the normal pipeline (video decode, etc.).

        Idempotent — re-running with the same id returns the existing
        directory.  The MP4 is cached by the catalog; here we just stage
        the dir under ``paths.cloud_theme_dir(w, h)`` (resolution-keyed,
        matches legacy layout) with a minimal config.
        """
        log.info("materialise: %s @ %dx%d", theme_id, *resolution)
        mp4_path = self._catalog.download_theme(theme_id)

        theme_dir = self._theme_dir_for(theme_id, resolution)
        existed = theme_dir.is_dir()
        theme_dir.mkdir(parents=True, exist_ok=True)
        log.info("materialise: theme_dir=%s (existed=%s)",
                 theme_dir, existed)

        # Stage the background under the canonical name DisplayService
        # looks for (any *.mp4 in the theme dir is the background).
        target_mp4 = theme_dir / mp4_path.name
        if not target_mp4.is_file():
            log.info("materialise: staging mp4 %s → %s",
                     mp4_path, target_mp4)
            target_mp4.write_bytes(mp4_path.read_bytes())
        else:
            log.debug("materialise: %s already staged", target_mp4)

        config_path = theme_dir / "trcc.json"
        if not config_path.is_file():
            log.info("materialise: writing minimal trcc.json at %s",
                     config_path)
            config_path.write_text(
                json.dumps(_minimal_config(theme_id), indent=2) + "\n",
                encoding="utf-8",
            )
        return theme_dir

    # ── Internals ─────────────────────────────────────────────────────

    def _theme_dir_for(
        self, theme_id: str, resolution: tuple[int, int],
    ) -> Path:
        w, h = resolution
        return self._paths.cloud_theme_dir(w, h) / theme_id


def _minimal_config(theme_id: str) -> dict:
    """Bare-bones next/ theme config — no overlay elements, no width/height.

    DisplayService falls back to the device's native resolution when the
    config is missing dimensions, so we leave them out and let the
    handshake-derived profile drive the canvas size.
    """
    return {
        "name": f"cloud:{theme_id}",
        "elements": [],
    }
