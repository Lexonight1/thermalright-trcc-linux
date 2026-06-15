"""Per-resolution data install — port of legacy ``DataManager.ensure_all``.

Three archives per resolution:

  * Stock themes        → ``paths.theme_dir(w, h)``
  * Cloud preview thumbs → ``paths.cloud_theme_dir(w, h)``
  * Cloud masks          → ``paths.cloud_mask_dir(w, h)``

``DataInstallService.ensure_all((w, h))`` fans out to all three; each
is idempotent (skips if the target dir already has content).  Settings
tracks installed resolutions so we don't re-check archives on every
device discovery — first launch downloads, subsequent launches are
no-ops.

Non-square resolutions ensure both orientations too (e.g. 1600×720
also installs 720×1600 web/masks) so portrait rotation immediately
shows local content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..adapters.repo.data_install import DataInstaller
from ..core.ports import Paths

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnsureDataResult:
    """One ``ensure_all`` invocation summary."""
    resolution: tuple[int, int]
    themes_ok: bool
    web_ok: bool
    masks_ok: bool

    @property
    def ok(self) -> bool:
        return self.themes_ok and self.web_ok and self.masks_ok


class DataInstallService:
    """Per-resolution data archive installer."""

    __slots__ = ("_installer", "_paths")

    def __init__(self, paths: Paths, installer: DataInstaller) -> None:
        self._paths = paths
        self._installer = installer

    def ensure_themes(self, width: int, height: int) -> bool:
        log.info("ensure_themes: %dx%d", width, height)
        return self._installer.install(
            archive_name=f"theme{width}{height}.7z",
            target_dir=self._paths.theme_dir(width, height),
        )

    def ensure_web(self, width: int, height: int) -> bool:
        log.info("ensure_web: %dx%d", width, height)
        return self._installer.install(
            archive_name=f"{width}{height}.7z",
            target_dir=self._paths.cloud_theme_dir(width, height),
            subpath="web",
        )

    def ensure_masks(self, width: int, height: int) -> bool:
        log.info("ensure_masks: %dx%d", width, height)
        return self._installer.install(
            archive_name=f"zt{width}{height}.7z",
            target_dir=self._paths.cloud_mask_dir(width, height),
            subpath="web",
        )

    def ensure_all(self, resolution: tuple[int, int]) -> EnsureDataResult:
        """Install all three archives for ``resolution`` + the rotated
        counterpart when non-square.  Idempotent."""
        w, h = resolution
        log.info("ensure_all: starting %dx%d", w, h)
        themes_ok = self.ensure_themes(w, h)
        web_ok = self.ensure_web(w, h)
        masks_ok = self.ensure_masks(w, h)
        if w != h:
            log.info("ensure_all: non-square — also installing %dx%d", h, w)
            # Themes too, not just web+masks — a rotated panel loads its
            # background/theme from the oriented dir (theme480854), so without
            # this the portrait orientation had no theme catalog and fell back
            # to the landscape one.
            self.ensure_themes(h, w)
            self.ensure_web(h, w)
            self.ensure_masks(h, w)
        result = EnsureDataResult(
            resolution=resolution,
            themes_ok=themes_ok, web_ok=web_ok, masks_ok=masks_ok,
        )
        if result.ok:
            log.info("ensure_all: %dx%d ready", w, h)
        else:
            log.warning(
                "ensure_all: %dx%d partial — themes=%s web=%s masks=%s",
                w, h, themes_ok, web_ok, masks_ok,
            )
        return result
