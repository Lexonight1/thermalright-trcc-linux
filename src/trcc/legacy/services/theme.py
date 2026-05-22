"""Theme discovery, loading, saving, export/import service.

Pure Python (filesystem + Qt renderer), no direct Qt widget dependencies.
LCDDevice.ThemeOps delegates to this service.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..core.models import MaskInfo, ThemeData, ThemeDir, ThemeInfo, ThemeType
from ..core.paths import theme_dir_name
from .image import ImageService
from .overlay import OverlayService

log = logging.getLogger(__name__)

# Probe-boundary exception tuples — ffprobe subprocess + JSON config parsing.
_SUBPROCESS_EXC: tuple[type[BaseException], ...] = (
    OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError,
)


def theme_info_from_directory(
    path: Path, resolution: tuple[int, int] = (320, 320),
) -> ThemeInfo:
    """Create ThemeInfo from a theme directory by scanning filesystem.

    Moved from theme_info_from_directory() — models.py must be zero I/O.
    """
    td = ThemeDir(path)
    if td.zt.exists():
        is_animated = True
        animation_path = td.zt
    else:
        mp4_files = list(path.glob('*.mp4'))
        if mp4_files:
            is_animated = True
            animation_path = mp4_files[0]
        else:
            is_animated = False
            animation_path = None

    return ThemeInfo(
        name=path.name,
        path=path,
        theme_type=ThemeType.LOCAL,
        background_path=td.bg if td.bg.exists() else None,
        mask_path=td.mask if td.mask.exists() else None,
        thumbnail_path=td.preview if td.preview.exists() else (
            td.bg if td.bg.exists() else None),
        animation_path=animation_path,
        config_path=td.dc if td.dc.exists() else None,
        resolution=resolution,
        is_animated=is_animated,
        is_mask_only=not td.bg.exists() and td.mask.exists(),
    )


def _copy_flat_files(src: Path, dest: Path) -> None:
    """Copy all files (not subdirs) from src to dest."""
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(str(f), str(dest / f.name))


def _theme_sort_key(theme: ThemeInfo) -> tuple[bool, str]:
    """Sort: built-in themes first (False < True), then alphabetical by name."""
    return (theme.name.startswith(('User', 'Custom')), theme.name)


class ThemeService:
    """Theme lifecycle: discover, load, save, export, import.

    Holds theme list, selection, filter state, and directory paths.
    """

    # Category mappings (Windows prefix → display name)
    CATEGORIES = {
        'all': 'All',
        'a': 'Gallery',
        'b': 'Tech',
        'c': 'HUD',
        'd': 'Light',
        'e': 'Nature',
        'y': 'Aesthetic',
    }

    def __init__(self,
                 export_theme_fn: Any = None,
                 import_theme_fn: Any = None,
                 load_config_json_fn: Any = None,
                 dc_config_cls: Any = None) -> None:
        self._themes: list[ThemeInfo] = []
        self._selected: ThemeInfo | None = None
        self._filter_mode: str = 'all'
        self._category: str | None = None
        self._local_dir: Path | None = None
        self._web_dir: Path | None = None
        self._masks_dir: Path | None = None
        # Injected adapter callables (hexagonal purity)
        self._export_theme_fn = export_theme_fn
        self._import_theme_fn = import_theme_fn
        self._load_config_json_fn = load_config_json_fn
        self._dc_config_cls = dc_config_cls

    # ── State ─────────────────────────────────────────────────────────

    @property
    def themes(self) -> list[ThemeInfo]:
        return self._themes

    @property
    def selected(self) -> ThemeInfo | None:
        return self._selected

    def select(self, theme: ThemeInfo) -> None:
        self._selected = theme

    def set_filter(self, mode: str) -> None:
        self._filter_mode = mode

    def set_category(self, category: str) -> None:
        self._category = category if category != 'all' else None

    def set_directories(self,
                        local_dir: Path | None = None,
                        web_dir: Path | None = None,
                        masks_dir: Path | None = None) -> None:
        if local_dir is not None:
            self._local_dir = local_dir
        if web_dir is not None:
            self._web_dir = web_dir
        if masks_dir is not None:
            self._masks_dir = masks_dir

    @property
    def local_dir(self) -> Path | None:
        return self._local_dir

    @property
    def web_dir(self) -> Path | None:
        return self._web_dir

    @property
    def masks_dir(self) -> Path | None:
        return self._masks_dir

    # ── Directory setup ──────────────────────────────────────────────

    # ── Discovery ────────────────────────────────────────────────────

    def load_local_themes(self, resolution: tuple[int, int] = (0, 0)) -> list[ThemeInfo]:
        """Discover local themes and store them. Returns the list."""
        if self._local_dir:
            self._themes = ThemeService.discover_local(
                self._local_dir, resolution, self._filter_mode)
        else:
            self._themes = []
        return self._themes

    def load_cloud_themes(self) -> list[ThemeInfo]:
        """Discover cloud themes and store them. Returns the list."""
        if self._web_dir:
            self._themes = ThemeService.discover_cloud(
                self._web_dir, self._category)
        else:
            self._themes = []
        return self._themes

    @staticmethod
    def discover_local(
        theme_dir: Path,
        resolution: tuple[int, int] = (0, 0),
        filter_mode: str = 'all',
    ) -> list[ThemeInfo]:
        """Load themes from a local directory.

        Args:
            theme_dir: Path to Theme{W}{H}/ directory.
            resolution: LCD resolution tuple.
            filter_mode: 'all', 'default', or 'user'.

        Returns:
            List of ThemeInfo objects.
        """
        themes: list[ThemeInfo] = []
        if not theme_dir or not theme_dir.exists():
            return themes

        for item in sorted(theme_dir.iterdir()):
            if item.is_dir() and (
                (item / 'Theme.png').exists()
                or (item / 'config1.dc').exists()
                or (item / '00.png').exists()
            ):
                theme = theme_info_from_directory(item, resolution)
                if ThemeService._passes_filter(theme, filter_mode):
                    themes.append(theme)

        return themes

    @staticmethod
    def discover_cloud(
        web_dir: Path,
        category: str | None = None,
    ) -> list[ThemeInfo]:
        """Load cloud video themes from web directory.

        Args:
            web_dir: Path to Web/{W}{H}/ directory.
            category: Category filter ('a', 'b', etc.) or None for all.
        """
        themes: list[ThemeInfo] = []
        if not web_dir or not web_dir.exists():
            return themes

        for video_file in sorted(web_dir.glob('*.mp4')):
            preview_path = web_dir / f"{video_file.stem}.png"
            theme = ThemeInfo.from_video(
                video_file,
                preview_path if preview_path.exists() else None,
            )
            if category and category != 'all':
                if theme.category != category:
                    continue
            themes.append(theme)

        return themes

    @staticmethod
    def discover_local_merged(
        primary_dir: Path,
        user_content_dir: Path | None = None,
        resolution: tuple[int, int] = (0, 0),
        filter_mode: str = 'all',
    ) -> list[ThemeInfo]:
        """Discover local themes from primary + user content directories.

        Scans primary_dir first, then the matching subdirectory under
        user_content_dir.  Deduplicates by name (first seen wins) and
        sorts stock themes before user themes (Custom_/User prefix).
        """
        dirs_to_scan: list[Path] = []
        if primary_dir and primary_dir.exists():
            dirs_to_scan.append(primary_dir)
        if user_content_dir is not None and primary_dir is not None:
            user_theme_dir = user_content_dir / primary_dir.name
            if user_theme_dir != primary_dir and user_theme_dir.exists():
                dirs_to_scan.append(user_theme_dir)

        seen_names: set[str] = set()
        themes: list[ThemeInfo] = []

        for scan_dir in dirs_to_scan:
            for item in sorted(scan_dir.iterdir()):
                if item.is_dir() and item.name not in seen_names:
                    if ((item / 'Theme.png').exists()
                            or (item / 'config1.dc').exists()
                            or (item / '00.png').exists()):
                        theme = theme_info_from_directory(item, resolution)
                        if ThemeService._passes_filter(theme, filter_mode):
                            seen_names.add(item.name)
                            themes.append(theme)

        themes.sort(key=_theme_sort_key)
        return themes

    @staticmethod
    def discover_masks(
        cloud_masks_dir: Path | None = None,
        user_masks_dir: Path | None = None,
    ) -> list[MaskInfo]:
        """Discover local masks from user + cloud-cache directories.

        User masks appear first (custom content), then cloud-cached masks.
        Deduplicates by name (first seen wins).
        """
        masks: list[MaskInfo] = []
        seen: set[str] = set()

        def _scan(directory: Path | None, is_custom: bool) -> None:
            if directory is None or not directory.exists():
                return
            for item in sorted(directory.iterdir()):
                if not item.is_dir() or item.name in seen:
                    continue
                thumb = item / 'Theme.png'
                mask_file = item / '01.png'
                if thumb.exists() or mask_file.exists():
                    seen.add(item.name)
                    masks.append(MaskInfo(
                        name=item.name,
                        path=item,
                        preview_path=thumb if thumb.exists() else mask_file,
                        is_custom=is_custom,
                    ))

        _scan(user_masks_dir, is_custom=True)
        _scan(cloud_masks_dir, is_custom=False)
        return masks

    # ── Load ─────────────────────────────────────────────────────────

    def load(
        self,
        theme: ThemeInfo,
        working_dir: Path,
        lcd_size: tuple[int, int],
    ) -> ThemeData:
        """Load a theme and return all data needed to display it.

        Handles both reference-based (config.json) and copy-based themes.

        Args:
            theme: ThemeInfo to load.
            working_dir: Temporary working directory.
            lcd_size: (width, height) of the LCD.

        Returns:
            ThemeData bundle with background, mask, overlay config, etc.
        """
        assert theme.path is not None
        td = ThemeDir(theme.path)
        w, h = lcd_size
        data = ThemeData()

        # Reference-based theme (config.json exists)
        if td.json.exists():
            opts = self._load_dc_display_options(td.dc, w, h)

            mask_ref = opts.get('mask_path')
            if mask_ref:
                self._load_mask_into(data, ThemeDir(mask_ref), w, h)

            bg_ref = opts.get('background_path')
            if bg_ref:
                bg_path = Path(bg_ref)
                if bg_path.exists():
                    if bg_path.suffix in ('.mp4', '.avi', '.mkv', '.webm', '.zt'):
                        data.animation_path = bg_path
                        data.is_animated = True
                    elif bg_path.suffix == '.gif':
                        try:
                            import subprocess

                            from ..core.platform import SUBPROCESS_NO_WINDOW as _NO_WINDOW
                            probe = subprocess.run([
                                'ffprobe', '-v', 'error',
                                '-select_streams', 'v:0',
                                '-show_entries', 'stream=nb_frames',
                                '-of', 'default=noprint_wrappers=1:nokey=1',
                                str(bg_path),
                            ], capture_output=True, timeout=5, text=True,
                               creationflags=_NO_WINDOW)
                            nb_frames = probe.stdout.strip()
                            if probe.returncode == 0 and nb_frames.isdigit() and int(nb_frames) > 1:
                                data.animation_path = bg_path
                                data.is_animated = True
                        except _SUBPROCESS_EXC as e:
                            # ffprobe missing/failed — animation detection
                            # falls back to "static" silently. Logged at
                            # debug so users with -vv see the cause.
                            log.debug(
                                "theme: ffprobe animation-detect failed for %s (%s) — treating as static",
                                bg_path, e,
                            )
                    else:
                        data.background = ThemeService._open_image(
                            bg_path, w, h)

            return data

        # Copy-based theme (original behavior)
        ThemeService._copy_dir(theme.path, working_dir)
        wd = ThemeDir(working_dir)

        opts = self._load_dc_display_options(wd.dc, w, h)

        # Determine background / animation
        anim_path, static_path, is_mask_only = ThemeService._resolve_content(
            theme, opts, wd, working_dir)
        if anim_path:
            data.animation_path = anim_path
            data.is_animated = True
        elif static_path:
            data.background = ThemeService._open_image(static_path, w, h)
        elif is_mask_only:
            data.background = ThemeService._black_image(w, h)

        # Mask from working dir
        if wd.mask.exists():
            data.mask_source_dir = theme.path
            self._load_mask_into(
                data, wd, w, h, dc_path=wd.dc if wd.dc.exists() else None)

        return data

    @staticmethod
    def _resolve_content(
        theme: ThemeInfo,
        opts: dict,
        wd: ThemeDir,
        working_dir: Path,
    ) -> tuple[Path | None, Path | None, bool]:
        """Resolve animation/background source for a copy-based theme.

        Returns (animation_path, static_bg_path, is_mask_only).
        At most one of animation_path / static_bg_path is set.
        """
        anim_file = opts.get('animation_file')
        if anim_file:
            anim_path = working_dir / anim_file
            if anim_path.exists():
                return anim_path, None, False
            if theme.is_animated and theme.animation_path:
                return Path(theme.animation_path), None, False
        elif theme.is_animated and theme.animation_path:
            wd_copy = working_dir / Path(theme.animation_path).name
            path = wd_copy if wd_copy.exists() else Path(theme.animation_path)
            return path, None, False
        elif wd.zt.exists():
            return wd.zt, None, False
        elif wd.bg.exists():
            mp4_files = list(working_dir.glob('*.mp4'))
            if mp4_files:
                return mp4_files[0], None, False
            return None, wd.bg, False
        elif theme.is_mask_only:
            return None, None, True
        return None, None, False

    # ── Save ─────────────────────────────────────────────────────────

    @staticmethod
    def save(
        name: str,
        data_dir: Path,
        lcd_size: tuple[int, int],
        *,
        background: Any,
        preview: Any | None = None,
        overlay_config: dict,
        mask: Any | None = None,
        mask_source: Path | None = None,
        mask_position: tuple[int, int] | None = None,
        video_path: Path | None = None,
        current_theme_path: Path | None = None,
    ) -> tuple[bool, str]:
        """Save current config as a custom theme with path references.

        Returns (success, message).
        """
        if not background:
            return False, "No image to save"

        w, h = lcd_size
        safe_name = f'Custom_{name}' if not name.startswith('Custom_') else name
        theme_path = data_dir / theme_dir_name(w, h) / safe_name

        try:
            theme_path.mkdir(parents=True, exist_ok=True)
            td = ThemeDir(theme_path)

            # Thumbnail from rendered preview (with overlay)
            from .image import ImageService
            r = ImageService.renderer()
            thumb_src = preview or background
            src_w, src_h = r.surface_size(thumb_src)
            scale = min(120 / src_w, 120 / src_h, 1.0)
            thumb = r.resize(r.copy_surface(thumb_src),
                             max(1, int(src_w * scale)),
                             max(1, int(src_h * scale)))
            thumb.save(str(td.preview))

            # Save current frame as 00.png
            background.save(str(td.bg))

            # Determine background source path (copy video into theme dir
            # so it survives reboots — source may be in a temp directory)
            background_path: str | None = None
            if video_path:
                video_src = Path(video_path)
                if video_src.exists():
                    # Preserve original extension so the correct decoder is
                    # used on reload (.zt → ThemeZtDecoder, .mp4 → VideoDecoder)
                    dest = theme_path / f'Theme{video_src.suffix}'
                    if video_src.resolve() != dest.resolve():
                        shutil.copy2(str(video_src), str(dest))
                    background_path = str(dest)
            else:
                # Reference the saved 00.png — always exists at this point
                background_path = str(td.bg)

            # Determine mask source path
            mask_path_str = None
            log.debug(
                "ThemeService.save: mask=%s (type=%s), mask_source=%s, "
                "mask_source exists=%s",
                bool(mask), type(mask).__name__,
                mask_source,
                mask_source.exists() if mask_source else 'N/A',
            )
            if mask and mask_source:
                mask_file = ThemeDir(mask_source).mask
                log.debug(
                    "ThemeService.save: mask_file=%s, exists=%s",
                    mask_file, mask_file.exists(),
                )
                if mask_file.exists():
                    mask_path_str = str(mask_source)

            config_json = {
                'background': background_path,
                'mask': mask_path_str,
                'dc': overlay_config or {},
            }
            if mask_path_str and mask_position:
                config_json['mask_position'] = list(mask_position)

            from trcc.legacy.core.io import atomic_write_json
            atomic_write_json(td.json, config_json)

            return True, f"Saved: {safe_name}"
        except (OSError, ValueError, TypeError) as e:
            log.warning("save theme failed: %s", e)
            return False, f"Save failed: {e}"

    # ── Export / Import ──────────────────────────────────────────────

    def export_tr(self, theme_path: Path, export_path: Path) -> tuple[bool, str]:
        """Export theme as .tr file."""
        if self._export_theme_fn is None:
            return False, "Export not available (dc_writer not injected)"
        try:
            self._export_theme_fn(str(theme_path), str(export_path))
            return True, f"Exported: {export_path.name}"
        except Exception as e:
            # Injected dc_writer can throw arbitrary errors from C# parity logic.
            log.warning("export_tr failed: %s", e)
            return False, f"Export failed: {e}"

    def import_tr(
        self,
        import_path: Path,
        data_dir: Path,
        lcd_size: tuple[int, int],
    ) -> tuple[bool, str | ThemeInfo]:
        """Import theme from .tr file.

        Returns (success, message_or_theme_info).
        On success, second element is a ThemeInfo that can be loaded.
        """
        if self._import_theme_fn is None:
            return False, "Import not available (dc_writer not injected)"
        try:
            w, h = lcd_size
            name = import_path.stem
            theme_path = data_dir / theme_dir_name(w, h) / name
            self._import_theme_fn(str(import_path), str(theme_path))
            theme = theme_info_from_directory(theme_path)

            # Warn if imported theme resolution doesn't match device
            if (isinstance(theme, ThemeInfo)
                    and theme.resolution != (0, 0)
                    and theme.resolution != lcd_size):
                log.warning(
                    "Imported theme resolution %s doesn't match "
                    "device resolution %s", theme.resolution, lcd_size)

            return True, theme
        except Exception as e:
            # Injected dc_writer can throw arbitrary errors from C# parity logic.
            log.warning("import_tr failed: %s", e)
            return False, f"Import failed: {e}"

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _passes_filter(theme: ThemeInfo, filter_mode: str) -> bool:
        if filter_mode == 'all':
            return True
        elif filter_mode == 'default':
            return (theme.theme_type == ThemeType.LOCAL
                    and not theme.name.startswith(('User', 'Custom')))
        elif filter_mode == 'user':
            return (theme.theme_type == ThemeType.USER
                    or theme.name.startswith(('User', 'Custom')))
        return True

    @staticmethod
    def _copy_dir(src: Path, dest: Path) -> None:
        """Copy theme files to working dir (Windows CopyDireToDire)."""
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        _copy_flat_files(src, dest)

    @staticmethod
    def _open_image(path: Path, w: int, h: int) -> Any:
        """Open and resize an image to LCD dimensions."""
        return ImageService.open_and_resize(path, w, h)

    @staticmethod
    def _black_image(w: int, h: int) -> Any:
        """Create a black background image."""
        return ImageService.solid_color(0, 0, 0, w, h)

    def _load_dc_display_options(self, dc_path: Path, w: int, h: int) -> dict:
        """Load DC config and return display_options dict."""
        json_path = ThemeDir(dc_path.parent).json if dc_path else None
        if json_path and json_path.exists() and self._load_config_json_fn is not None:
            try:
                result = self._load_config_json_fn(str(json_path))
                if result is not None:
                    _, display_options = result
                    return display_options
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("Failed to load config.json: %s", e)

        if not dc_path or not dc_path.exists():
            return {}
        if self._dc_config_cls is None:
            return {}
        try:
            dc = self._dc_config_cls(dc_path)
            return dc.display_options
        except Exception as e:
            # Injected DC parser class; binary parsing surface varies (struct.error, ValueError, KeyError, OSError).
            log.error("Failed to parse DC file: %s", e)
            return {}

    def _parse_mask_position(
        self,
        dc_path: Path | None,
        mask_w: int,
        mask_h: int,
        lcd_w: int,
        lcd_h: int,
    ) -> tuple[int, int] | None:
        return OverlayService.calculate_mask_position(
            self._dc_config_cls, dc_path, (mask_w, mask_h), (lcd_w, lcd_h))

    def _load_mask_into(
        self,
        data: ThemeData,
        td: ThemeDir,
        w: int,
        h: int,
        dc_path: Path | None = None,
    ) -> None:
        """Load mask image and position into ThemeData."""
        mask_file = td.mask
        if not mask_file.exists():
            return
        try:
            from .image import ImageService
            r = ImageService.renderer()
            mask_img = r.open_image(str(mask_file))
            mask_w, mask_h = r.surface_size(mask_img)
            position = self._parse_mask_position(
                dc_path or (td.dc if td.dc.exists() else None),
                mask_w, mask_h, w, h)
            data.mask = mask_img
            data.mask_position = position
            data.mask_source_dir = td.path
        except (OSError, ValueError, RuntimeError) as e:
            log.error("Failed to load mask: %s", e)
