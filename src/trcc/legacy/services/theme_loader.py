"""Theme loading orchestrator — loads local, cloud, and mask themes.

Extracted from DisplayService (SRP). Receives injected OverlayService +
MediaService. Returns result dicts; DisplayService wires up state.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from ..core.models import ThemeDir
from .image import ImageService
from .media import MediaService
from .overlay import OverlayService
from .theme import ThemeService, _copy_flat_files

log = logging.getLogger(__name__)


class ThemeLoader:
    """Loads themes from local dirs, cloud, and mask overlays.

    Stateless except for injected service references. All mutable display
    state stays in DisplayService.
    """

    def __init__(self, overlay: OverlayService, media: MediaService,
                 theme_svc: ThemeService | None = None) -> None:
        self._overlay = overlay
        self._theme_svc = theme_svc
        self._media = media

    def load_local_theme(
        self, theme, lcd_size: tuple[int, int],
        working_dir: Path,
    ) -> dict:
        """Load a local theme with DC config, mask, and overlay.

        Returns dict with keys:
            'image': native surface (QImage, rendered preview) or None
            'is_animated': bool
            'status': str
            'mask_source_dir': Path or None (for caller to store)
            'theme_path': Path
        """
        log.info("Loading local theme: %s", theme.path)
        self._media.stop()

        # Full reset — overlay gets reconfigured from this theme's DC
        log.info("ThemeLoader: resetting overlay for new theme")
        self._overlay.enabled = False
        self._overlay.set_background(None)
        self._overlay.set_mask(None)
        self._overlay.set_config({})

        assert theme.path is not None
        td = ThemeDir(theme.path)

        # Reference-based theme (config.json exists)
        if td.json.exists():
            return self._load_reference_theme(theme, td, lcd_size, working_dir)

        # Copy-based theme (original behavior)
        return self._load_copy_theme(theme, td, lcd_size, working_dir)

    def _load_reference_theme(
        self, theme, td: ThemeDir,
        lcd_size: tuple[int, int], working_dir: Path,
    ) -> dict:
        """Load theme by path references (config.json)."""
        display_opts = self._overlay.load_from_dc(td.dc)

        if display_opts.get('overlay_enabled'):
            self._overlay.enabled = True

        mask_source_dir: Path | None = None

        # Load mask by reference
        if (mask_ref := display_opts.get('mask_path')):
            mask_td = ThemeDir(mask_ref)
            if mask_td.mask.exists():
                mask_source_dir = mask_td.path
                self._load_mask(mask_td.mask, None, lcd_size)
                if (mask_pos := display_opts.get('mask_position')):
                    mask_img, _ = self._overlay.get_mask()
                    self._overlay.set_mask(mask_img, mask_pos)

        # Load background by reference
        result: dict[str, Any] = {
            'image': None, 'is_animated': False,
            'status': f"Theme: {theme.name}",
            'mask_source_dir': mask_source_dir,
            'theme_path': theme.path,
        }
        if (bg_ref := display_opts.get('background_path')):
            bg_path = Path(bg_ref)
            if not bg_path.exists() and td.bg.exists():
                bg_path = td.bg
            if bg_path.exists():
                if bg_path.suffix in ('.mp4', '.avi', '.mkv', '.webm', '.zt') or (bg_path.suffix == '.gif' and self._is_animated_gif(bg_path)):
                    self._load_and_play_video(bg_path)
                    result['is_animated'] = True
                else:
                    result['image'] = self._load_static_image(bg_path, lcd_size)
        elif td.bg.exists():
            result['image'] = self._load_static_image(td.bg, lcd_size)

        return result

    def _load_copy_theme(
        self, theme, td: ThemeDir,
        lcd_size: tuple[int, int], working_dir: Path,
    ) -> dict:
        """Load theme by copying to working dir (original behavior)."""
        # Clear and copy
        if working_dir.exists():
            shutil.rmtree(working_dir)
        working_dir.mkdir(parents=True, exist_ok=True)
        _copy_flat_files(theme.path, working_dir)

        wd = ThemeDir(working_dir)
        display_opts = self._overlay.load_from_dc(wd.dc)

        mask_source_dir: Path | None = None
        result: dict[str, Any] = {
            'image': None, 'is_animated': False,
            'status': f"Theme: {theme.name}",
            'mask_source_dir': None,
            'theme_path': theme.path,
        }

        # Load background / animation
        anim_path, static_path, is_mask_only = ThemeService._resolve_content(
            theme, display_opts, wd, working_dir)
        if anim_path:
            self._load_and_play_video(anim_path)
            result['is_animated'] = True
        elif static_path:
            result['image'] = self._load_static_image(static_path, lcd_size)
        elif is_mask_only:
            result['image'] = ImageService.solid_color(0, 0, 0, *lcd_size)

        # Load mask from working dir
        if wd.mask.exists():
            mask_source_dir = theme.path
            self._load_mask(wd.mask, wd.dc if wd.dc.exists() else None, lcd_size)

        result['mask_source_dir'] = mask_source_dir
        return result

    def load_cloud_theme(self, theme, working_dir: Path) -> dict:
        """Load a cloud video theme as background."""
        log.info("ThemeLoader: loading cloud theme %s (overlay untouched)", theme.name)
        self._media.stop()

        if theme.animation_path:
            video_path = Path(theme.animation_path)
            if video_path.exists():
                dest = working_dir / video_path.name
                if not dest.exists():
                    shutil.copy2(str(video_path), str(dest))
            self._load_and_play_video(theme.animation_path)

        return {
            'image': None,
            'is_animated': True,
            'status': f"Cloud Theme: {theme.name}",
            'mask_source_dir': None,
            'theme_path': None,
        }

    def apply_mask(
        self, mask_dir: Path, working_dir: Path,
        lcd_size: tuple[int, int],
    ) -> Path | None:
        """Apply a mask overlay on top of current content.

        Returns the mask source dir (for caller to store), or None.
        """
        log.info("ThemeLoader.apply_mask: mask_dir=%s lcd_size=%s", mask_dir, lcd_size)
        if not mask_dir or not mask_dir.exists():
            return None

        self._overlay.set_theme_mask(None)
        _copy_flat_files(mask_dir, working_dir)

        wd = ThemeDir(working_dir)
        self._overlay.load_from_dc(wd.dc)

        if wd.mask.exists():
            self._load_mask(wd.mask, wd.dc if wd.dc.exists() else None, lcd_size)

        self._overlay.enabled = True
        return mask_dir

    # ── Internal helpers ─────────────────────────────────────────────

    def _load_static_image(self, path: Path, lcd_size: tuple[int, int]) -> Any | None:
        """Load and resize a static image to LCD dimensions."""
        try:
            return ImageService.open_and_resize(path, *lcd_size)
        except (OSError, ValueError, RuntimeError) as e:
            log.error("Failed to load image: %s", e)
            return None

    def _load_and_play_video(self, path: Path) -> None:
        """Load video and start playback."""
        self._media.load(path)
        self._media.play()

    @staticmethod
    def _is_animated_gif(path: Path) -> bool:
        """Check if a GIF file has multiple frames (ffprobe)."""
        import subprocess

        from ..core.platform import SUBPROCESS_NO_WINDOW as _NO_WINDOW
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=nb_frames',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(path),
            ], capture_output=True, timeout=5, text=True, creationflags=_NO_WINDOW)
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip()) > 1
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            # ffprobe missing or failed — assume static and move on.
            log.debug(
                "theme_loader: ffprobe frame-count failed for %s (%s) — treating as static",
                path, e,
            )
        return False

    def _load_mask(self, mask_path: Path, dc_path: Path | None,
                   lcd_size: tuple[int, int]) -> None:
        """Load mask image with position from DC config."""
        try:
            from .image import ImageService
            r = ImageService.renderer()
            mask_img = r.open_image(mask_path)
            mask_w, mask_h = r.surface_size(mask_img)
            position = self._parse_mask_position(
                dc_path, mask_w, mask_h, lcd_size)
            self._overlay.set_mask(mask_img, position)
        except (OSError, ValueError, RuntimeError) as e:
            log.error("Failed to load mask: %s", e)

    def _parse_mask_position(self, dc_path: Path | None,
                             mask_w: int, mask_h: int,
                             lcd_size: tuple[int, int]) -> tuple[int, int] | None:
        dc_cls = self._theme_svc._dc_config_cls if self._theme_svc else None
        return OverlayService.calculate_mask_position(
            dc_cls, dc_path, (mask_w, mask_h), lcd_size)
