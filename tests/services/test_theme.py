"""Tests for ThemeService — theme discovery, loading, saving, export/import."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_test_surface
from PySide6.QtGui import QImage

from trcc.legacy.adapters.infra.data_repository import ThemeDir
from trcc.legacy.core.models import ThemeData, ThemeInfo, ThemeType
from trcc.legacy.services.theme import ThemeService, _copy_flat_files, theme_info_from_directory

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def svc() -> ThemeService:
    """Fresh ThemeService instance."""
    return ThemeService()


@pytest.fixture()
def lcd_size() -> tuple[int, int]:
    return (320, 320)


@pytest.fixture()
def test_image() -> QImage:
    """Small 4x4 RGB image for testing."""
    return make_test_surface(4, 4, (255, 0, 0))


@pytest.fixture()
def big_image() -> Any:
    """320x320 native surface — same as LCD size."""
    return make_test_surface(320, 320, (0, 0, 255))


def _make_theme_dir(
    base: Path,
    name: str,
    *,
    has_bg: bool = True,
    has_mask: bool = False,
    has_dc: bool = False,
    has_preview: bool = True,
    has_zt: bool = False,
    has_mp4: bool = False,
    has_json: bool = False,
    json_content: dict | None = None,
) -> Path:
    """Create a theme directory with optional files."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    if has_bg:
        make_test_surface(4, 4, (255, 0, 0)).save(str(d / '00.png'), "PNG")
    if has_mask:
        make_test_surface(4, 4, (0, 0, 0, 128)).save(str(d / '01.png'), "PNG")
    if has_dc:
        (d / 'config1.dc').write_bytes(b'\x00' * 16)
    if has_preview:
        make_test_surface(4, 4, (0, 255, 0)).save(str(d / 'Theme.png'), "PNG")
    if has_zt:
        (d / 'Theme.zt').write_bytes(b'\x00' * 64)
    if has_mp4:
        (d / 'video.mp4').write_bytes(b'\x00' * 64)
    if has_json:
        content = json_content or {'dc': {}, 'background': None, 'mask': None}
        (d / 'config.json').write_text(json.dumps(content))
    return d


def _make_cloud_dir(base: Path, videos: list[str]) -> Path:
    """Create a cloud (web) directory with .mp4 + optional .png preview files."""
    base.mkdir(parents=True, exist_ok=True)
    for name in videos:
        stem = name.removesuffix('.mp4')
        (base / f'{stem}.mp4').write_bytes(b'\x00' * 32)
        make_test_surface(4, 4, (0, 0, 0)).save(str(base / f'{stem}.png'), "PNG")
    return base


# ── __init__ defaults ─────────────────────────────────────────────────────────


class TestInit:

    def test_defaults(self, svc: ThemeService) -> None:
        assert svc.themes == []
        assert svc.selected is None
        assert svc.local_dir is None
        assert svc.web_dir is None
        assert svc.masks_dir is None
        assert svc._filter_mode == 'all'
        assert svc._category is None

    def test_categories_populated(self) -> None:
        assert 'all' in ThemeService.CATEGORIES
        assert ThemeService.CATEGORIES['a'] == 'Gallery'
        assert len(ThemeService.CATEGORIES) >= 7


# ── State methods ─────────────────────────────────────────────────────────────


class TestState:

    def test_select(self, svc: ThemeService) -> None:
        theme = ThemeInfo(name='test')
        svc.select(theme)
        assert svc.selected is theme

    def test_set_filter(self, svc: ThemeService) -> None:
        svc.set_filter('user')
        assert svc._filter_mode == 'user'

    def test_set_category_specific(self, svc: ThemeService) -> None:
        svc.set_category('a')
        assert svc._category == 'a'

    def test_set_category_all_resets_to_none(self, svc: ThemeService) -> None:
        svc.set_category('b')
        svc.set_category('all')
        assert svc._category is None

    def test_set_directories_local(self, svc: ThemeService, tmp_path: Path) -> None:
        svc.set_directories(local_dir=tmp_path)
        assert svc.local_dir == tmp_path
        assert svc.web_dir is None
        assert svc.masks_dir is None

    def test_set_directories_web(self, svc: ThemeService, tmp_path: Path) -> None:
        svc.set_directories(web_dir=tmp_path)
        assert svc.web_dir == tmp_path

    def test_set_directories_masks(self, svc: ThemeService, tmp_path: Path) -> None:
        svc.set_directories(masks_dir=tmp_path)
        assert svc.masks_dir == tmp_path

    def test_set_directories_all(self, svc: ThemeService, tmp_path: Path) -> None:
        a, b, c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
        a.mkdir()
        b.mkdir()
        c.mkdir()
        svc.set_directories(local_dir=a, web_dir=b, masks_dir=c)
        assert svc.local_dir == a
        assert svc.web_dir == b
        assert svc.masks_dir == c

    def test_set_directories_none_does_not_overwrite(
        self, svc: ThemeService, tmp_path: Path
    ) -> None:
        svc.set_directories(local_dir=tmp_path)
        svc.set_directories()  # all None — should not clear
        assert svc.local_dir == tmp_path


# ── setup_dirs ────────────────────────────────────────────────────────────────


class TestSetupDirs:

    def test_setup_dirs_removed(self) -> None:
        """ThemeService no longer owns data download (setup_dirs / ensure_data_fn removed).

        Data download is now handled by EnsureDataCommand dispatched through the
        CommandBus on device connect or via POST /themes/init.
        """
        svc = ThemeService()
        assert not hasattr(svc, 'setup_dirs'), "setup_dirs was removed in the command-bus refactor"


# ── _copy_flat_files ──────────────────────────────────────────────────────────


class TestCopyFlatFiles:

    def test_copies_files_not_subdirs(self, tmp_path: Path) -> None:
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'a.txt').write_text('hello')
        (src / 'b.png').write_bytes(b'\x89PNG')
        subdir = src / 'nested'
        subdir.mkdir()
        (subdir / 'c.txt').write_text('nested')

        dest = tmp_path / 'dest'
        dest.mkdir()
        _copy_flat_files(src, dest)

        assert (dest / 'a.txt').read_text() == 'hello'
        assert (dest / 'b.png').exists()
        assert not (dest / 'nested').exists()
        assert not (dest / 'c.txt').exists()


# ── _copy_dir ─────────────────────────────────────────────────────────────────


class TestCopyDir:

    def test_removes_dest_and_copies(self, tmp_path: Path) -> None:
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'file.txt').write_text('data')

        dest = tmp_path / 'dest'
        dest.mkdir()
        (dest / 'old.txt').write_text('stale')

        ThemeService._copy_dir(src, dest)

        assert (dest / 'file.txt').read_text() == 'data'
        assert not (dest / 'old.txt').exists()

    def test_creates_dest_if_missing(self, tmp_path: Path) -> None:
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'x.txt').write_text('y')

        dest = tmp_path / 'dest'
        ThemeService._copy_dir(src, dest)

        assert (dest / 'x.txt').exists()


# ── _passes_filter ────────────────────────────────────────────────────────────


class TestPassesFilter:

    def test_all_always_passes(self) -> None:
        theme = ThemeInfo(name='anything', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'all') is True

    def test_default_local_no_prefix_passes(self) -> None:
        theme = ThemeInfo(name='CoolTheme', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'default') is True

    def test_default_excludes_custom_prefix(self) -> None:
        theme = ThemeInfo(name='Custom_mine', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'default') is False

    def test_default_excludes_user_prefix(self) -> None:
        theme = ThemeInfo(name='UserTheme', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'default') is False

    def test_default_excludes_non_local(self) -> None:
        theme = ThemeInfo(name='CloudTheme', theme_type=ThemeType.CLOUD)
        assert ThemeService._passes_filter(theme, 'default') is False

    def test_user_matches_user_type(self) -> None:
        theme = ThemeInfo(name='MyTheme', theme_type=ThemeType.USER)
        assert ThemeService._passes_filter(theme, 'user') is True

    def test_user_matches_custom_prefix(self) -> None:
        theme = ThemeInfo(name='Custom_saved', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'user') is True

    def test_user_matches_user_prefix(self) -> None:
        theme = ThemeInfo(name='UserCreated', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'user') is True

    def test_user_excludes_regular_local(self) -> None:
        theme = ThemeInfo(name='Default01', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'user') is False

    def test_unknown_mode_passes(self) -> None:
        """Unknown filter mode falls through to True."""
        theme = ThemeInfo(name='test', theme_type=ThemeType.LOCAL)
        assert ThemeService._passes_filter(theme, 'nonexistent') is True


# ── discover_local ────────────────────────────────────────────────────────────


class TestDiscoverLocal:

    def test_valid_themes(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'Theme01')
        _make_theme_dir(tmp_path, 'Theme02')

        themes = ThemeService.discover_local(tmp_path, (320, 320))
        assert len(themes) == 2
        names = [t.name for t in themes]
        assert 'Theme01' in names
        assert 'Theme02' in names

    def test_sorted_order(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'Zebra')
        _make_theme_dir(tmp_path, 'Alpha')

        themes = ThemeService.discover_local(tmp_path)
        assert themes[0].name == 'Alpha'
        assert themes[1].name == 'Zebra'

    def test_skips_invalid_dirs(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'Valid')
        # Invalid dir: no preview, no dc, no bg
        invalid = tmp_path / 'Invalid'
        invalid.mkdir()
        (invalid / 'random.txt').write_text('not a theme')

        themes = ThemeService.discover_local(tmp_path)
        assert len(themes) == 1
        assert themes[0].name == 'Valid'

    def test_empty_dir(self, tmp_path: Path) -> None:
        themes = ThemeService.discover_local(tmp_path)
        assert themes == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        themes = ThemeService.discover_local(tmp_path / 'nope')
        assert themes == []

    def test_none_dir(self) -> None:
        themes = ThemeService.discover_local(None)  # type: ignore[arg-type]
        assert themes == []

    def test_filter_default_excludes_custom(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'Normal')
        _make_theme_dir(tmp_path, 'Custom_mine')

        themes = ThemeService.discover_local(tmp_path, (320, 320), 'default')
        names = [t.name for t in themes]
        assert 'Normal' in names
        assert 'Custom_mine' not in names

    def test_filter_user_only_custom(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'Normal')
        _make_theme_dir(tmp_path, 'Custom_mine')
        _make_theme_dir(tmp_path, 'UserTheme')

        themes = ThemeService.discover_local(tmp_path, (320, 320), 'user')
        names = [t.name for t in themes]
        assert 'Normal' not in names
        assert 'Custom_mine' in names
        assert 'UserTheme' in names

    def test_skips_files_not_dirs(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'RealTheme')
        (tmp_path / 'not_a_dir.txt').write_text('nope')

        themes = ThemeService.discover_local(tmp_path)
        assert len(themes) == 1

    def test_resolution_propagated(self, tmp_path: Path) -> None:
        _make_theme_dir(tmp_path, 'T01')
        themes = ThemeService.discover_local(tmp_path, (480, 480))
        assert themes[0].resolution == (480, 480)


# ── discover_cloud ────────────────────────────────────────────────────────────


class TestDiscoverCloud:

    def test_finds_mp4_themes(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4', 'b002.mp4'])

        themes = ThemeService.discover_cloud(tmp_path)
        assert len(themes) == 2
        assert all(t.theme_type == ThemeType.CLOUD for t in themes)
        assert all(t.is_animated for t in themes)

    def test_category_filter(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4', 'a002.mp4', 'b003.mp4'])

        themes = ThemeService.discover_cloud(tmp_path, category='a')
        assert len(themes) == 2
        assert all(t.category == 'a' for t in themes)

    def test_category_all_returns_everything(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4', 'b002.mp4'])

        themes = ThemeService.discover_cloud(tmp_path, category='all')
        assert len(themes) == 2

    def test_no_category_returns_everything(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4', 'b002.mp4'])

        themes = ThemeService.discover_cloud(tmp_path, category=None)
        assert len(themes) == 2

    def test_empty_dir(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        themes = ThemeService.discover_cloud(tmp_path)
        assert themes == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        themes = ThemeService.discover_cloud(tmp_path / 'nope')
        assert themes == []

    def test_none_dir(self) -> None:
        themes = ThemeService.discover_cloud(None)  # type: ignore[arg-type]
        assert themes == []

    def test_preview_linked_when_exists(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4'])
        themes = ThemeService.discover_cloud(tmp_path)
        assert themes[0].thumbnail_path is not None
        assert themes[0].thumbnail_path.name == 'a001.png'

    def test_preview_none_when_missing(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / 'a001.mp4').write_bytes(b'\x00')
        # No .png file

        themes = ThemeService.discover_cloud(tmp_path)
        assert themes[0].thumbnail_path is None

    def test_sorted_order(self, tmp_path: Path) -> None:
        _make_cloud_dir(tmp_path, ['c010.mp4', 'a001.mp4'])
        themes = ThemeService.discover_cloud(tmp_path)
        assert themes[0].name == 'a001'
        assert themes[1].name == 'c010'


# ── load_local_themes / load_cloud_themes (instance methods) ──────────────────


class TestInstanceDiscovery:

    def test_load_local_themes_with_dir(
        self, svc: ThemeService, tmp_path: Path
    ) -> None:
        _make_theme_dir(tmp_path, 'T1')
        svc.set_directories(local_dir=tmp_path)

        result = svc.load_local_themes((320, 320))
        assert len(result) == 1
        assert svc.themes == result

    def test_load_local_themes_no_dir(self, svc: ThemeService) -> None:
        result = svc.load_local_themes()
        assert result == []
        assert svc.themes == []

    def test_load_cloud_themes_with_dir(
        self, svc: ThemeService, tmp_path: Path
    ) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4'])
        svc.set_directories(web_dir=tmp_path)

        result = svc.load_cloud_themes()
        assert len(result) == 1
        assert svc.themes == result

    def test_load_cloud_themes_no_dir(self, svc: ThemeService) -> None:
        result = svc.load_cloud_themes()
        assert result == []

    def test_load_cloud_themes_uses_category(
        self, svc: ThemeService, tmp_path: Path
    ) -> None:
        _make_cloud_dir(tmp_path, ['a001.mp4', 'b002.mp4'])
        svc.set_directories(web_dir=tmp_path)
        svc.set_category('a')

        result = svc.load_cloud_themes()
        assert len(result) == 1
        assert result[0].category == 'a'

    def test_load_local_themes_uses_filter(
        self, svc: ThemeService, tmp_path: Path
    ) -> None:
        _make_theme_dir(tmp_path, 'Normal')
        _make_theme_dir(tmp_path, 'Custom_x')
        svc.set_directories(local_dir=tmp_path)
        svc.set_filter('default')

        result = svc.load_local_themes()
        names = [t.name for t in result]
        assert 'Custom_x' not in names


# ── load (reference-based) ────────────────────────────────────────────────────


class TestLoadReferenceBased:

    def test_reference_video_background(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        video = tmp_path / 'bg.mp4'
        video.write_bytes(b'\x00' * 32)
        td = _make_theme_dir(
            tmp_path, 'Ref',
            has_json=True,
            json_content={
                'dc': {},
                'background': str(video),
                'mask': None,
            },
        )
        theme = ThemeInfo(name='Ref', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'background_path': str(video)},
        ):
            data = ThemeService().load(theme, tmp_path / 'work', lcd_size)

        assert data.is_animated is True
        assert data.animation_path == video

    def test_reference_image_background(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        bg = tmp_path / 'bg.png'
        big_image.save(str(bg))
        td = _make_theme_dir(
            tmp_path, 'RefImg',
            has_json=True,
            json_content={'dc': {}, 'background': str(bg), 'mask': None},
        )
        theme = ThemeInfo(name='RefImg', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'background_path': str(bg)},
        ), patch.object(
            ThemeService, '_open_image', return_value=big_image,
        ):
            data = ThemeService().load(theme, tmp_path / 'work', lcd_size)

        assert data.background is big_image
        assert data.is_animated is False

    def test_reference_with_mask(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        mask_dir = _make_theme_dir(tmp_path, 'MaskSrc', has_mask=True, has_bg=False, has_preview=False)
        # Need preview or dc or bg for is_valid, but for mask ref loading we just
        # need the mask file to exist
        td = _make_theme_dir(
            tmp_path, 'RefMask',
            has_json=True,
            json_content={'dc': {}, 'background': None, 'mask': str(mask_dir)},
        )
        theme = ThemeInfo(name='RefMask', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'mask_path': str(mask_dir)},
        ), patch.object(ThemeService, '_load_mask_into') as mock_mask:
            ThemeService().load(theme, tmp_path / 'work', lcd_size)

        mock_mask.assert_called_once()

    def test_reference_bg_not_exists(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'RefMissing',
            has_json=True,
            json_content={'dc': {}, 'background': '/nonexistent/bg.png', 'mask': None},
        )
        theme = ThemeInfo(name='RefMissing', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'background_path': '/nonexistent/bg.png'},
        ):
            data = ThemeService().load(theme, tmp_path / 'work', lcd_size)

        assert data.background is None
        assert data.is_animated is False

    def test_reference_no_bg_no_mask(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'RefEmpty',
            has_json=True,
            json_content={'dc': {}, 'background': None, 'mask': None},
        )
        theme = ThemeInfo(name='RefEmpty', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={},
        ):
            data = ThemeService().load(theme, tmp_path / 'work', lcd_size)

        assert data.background is None
        assert data.is_animated is False

    def test_reference_zt_background(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        zt = tmp_path / 'Theme.zt'
        zt.write_bytes(b'\x00' * 32)
        td = _make_theme_dir(
            tmp_path, 'RefZt',
            has_json=True,
            json_content={'dc': {}, 'background': str(zt), 'mask': None},
        )
        theme = ThemeInfo(name='RefZt', path=td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'background_path': str(zt)},
        ):
            data = ThemeService().load(theme, tmp_path / 'work', lcd_size)

        assert data.is_animated is True
        assert data.animation_path == zt


# ── load (copy-based) ─────────────────────────────────────────────────────────


class TestLoadCopyBased:

    def test_static_bg(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        td = _make_theme_dir(tmp_path, 'Static', has_bg=True)
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ), patch.object(
            ThemeService, '_open_image', return_value=big_image,
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.background is big_image
        assert data.is_animated is False

    def test_zt_animation(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'Animated', has_bg=False, has_zt=True, has_preview=True
        )
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.is_animated is True
        assert data.animation_path is not None
        assert data.animation_path.name == 'Theme.zt'

    def test_mp4_animation_in_dir(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'Mp4Theme', has_bg=True, has_mp4=True
        )
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.is_animated is True
        assert data.animation_path is not None
        assert str(data.animation_path).endswith('.mp4')

    def test_mask_only_theme(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'MaskOnly',
            has_bg=False, has_mask=True, has_preview=True
        )
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ), patch.object(
            ThemeService, '_black_image', return_value=big_image,
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.background is big_image

    def test_anim_file_from_dc_options(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        td = _make_theme_dir(tmp_path, 'DcAnim', has_bg=True)
        # Create the animation file that the DC references
        (td / 'clip.mp4').write_bytes(b'\x00' * 16)
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'animation_file': 'clip.mp4'},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.is_animated is True
        assert data.animation_path is not None
        assert data.animation_path.name == 'clip.mp4'

    def test_anim_file_from_dc_fallback_to_theme(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        """DC references animation_file that doesn't exist in working dir,
        but theme.animation_path has a fallback."""
        td = _make_theme_dir(tmp_path, 'DcFallback', has_bg=True, has_zt=True)
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)
        # theme.is_animated=True, theme.animation_path=Theme.zt from from_directory

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'animation_file': 'missing.mp4'},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.is_animated is True

    def test_no_anim_file_theme_animated_with_animation_path(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        """No animation_file in DC, but theme.is_animated=True with animation_path."""
        td = _make_theme_dir(tmp_path, 'ThemeAnim', has_bg=True, has_zt=True)
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)
        # theme.is_animated=True, theme.animation_path from the .zt file

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        assert data.is_animated is True

    def test_mask_loaded_from_working_dir(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'WithMask', has_bg=True, has_mask=True
        )
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ), patch.object(
            ThemeService, '_open_image', return_value=big_image,
        ), patch.object(ThemeService, '_load_mask_into') as mock_mask:
            ThemeService().load(theme, work, lcd_size)

        mock_mask.assert_called_once()

    def test_copy_based_mask_with_dc(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        td = _make_theme_dir(
            tmp_path, 'MaskDc', has_bg=True, has_mask=True, has_dc=True
        )
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ), patch.object(
            ThemeService, '_open_image', return_value=big_image,
        ), patch.object(ThemeService, '_load_mask_into') as mock_mask:
            ThemeService().load(theme, work, lcd_size)

        # dc_path should be passed when dc file exists
        call_kwargs = mock_mask.call_args
        assert call_kwargs is not None

    def test_no_anim_file_theme_animated_wd_copy_not_exists(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        """No animation_file in DC, theme.is_animated with animation_path,
        but wd_copy doesn't exist -> falls back to theme.animation_path."""
        td = _make_theme_dir(tmp_path, 'FallbackAnim', has_bg=True, has_preview=True)
        work = tmp_path / 'work'
        # Create theme with animation pointing to a file outside the theme dir
        external_anim = tmp_path / 'external' / 'clip.mp4'
        external_anim.parent.mkdir()
        external_anim.write_bytes(b'\x00' * 16)

        theme = ThemeInfo(
            name='FallbackAnim',
            path=td,
            theme_type=ThemeType.LOCAL,
            is_animated=True,
            animation_path=external_anim,
        )

        with patch.object(
            ThemeService, '_load_dc_display_options', return_value={},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        # wd_copy (work/clip.mp4) doesn't exist, so falls back to theme.animation_path
        assert data.is_animated is True
        assert data.animation_path == external_anim

    def test_anim_file_missing_and_theme_not_animated(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        """DC references animation_file that doesn't exist, theme is NOT animated
        -> anim_file block matched but both inner branches fail, no bg set."""
        td = _make_theme_dir(tmp_path, 'NoAnim', has_bg=True)
        work = tmp_path / 'work'
        theme = theme_info_from_directory(td)
        # theme.is_animated=False because no .zt or .mp4

        with patch.object(
            ThemeService, '_load_dc_display_options',
            return_value={'animation_file': 'gone.mp4'},
        ):
            data = ThemeService().load(theme, work, lcd_size)

        # anim_file block consumed the if-elif chain; both inner conditions False
        # -> no background or animation set
        assert data.is_animated is False
        assert data.background is None


# ── save ──────────────────────────────────────────────────────────────────────


class TestSave:

    def test_success_with_image(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        ok, msg = ThemeService.save(
            'MyTheme',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={'elements': []},
        )
        assert ok is True
        assert 'Custom_MyTheme' in msg

        theme_path = tmp_path / 'theme320320' / 'Custom_MyTheme'
        assert theme_path.exists()
        assert (theme_path / 'Theme.png').exists()
        assert (theme_path / '00.png').exists()
        assert (theme_path / 'config.json').exists()

        config = json.loads((theme_path / 'config.json').read_text())
        assert config['background'] == str(theme_path / '00.png')
        assert config['dc'] == {'elements': []}

    def test_already_custom_prefix(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        ok, msg = ThemeService.save(
            'Custom_already',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
        )
        assert ok is True
        assert 'Custom_already' in msg
        assert 'Custom_Custom_already' not in msg

    def test_no_background_fails(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        ok, msg = ThemeService.save(
            'Fail',
            tmp_path,
            lcd_size,
            background=None,
            overlay_config={},
        )
        assert ok is False
        assert 'No image' in msg

    def test_video_path_copied(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        video = tmp_path / 'source.mp4'
        video.write_bytes(b'\x00' * 64)

        ok, msg = ThemeService.save(
            'VidTheme',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            video_path=video,
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_VidTheme'
        # Extension preserved from source (.mp4 stays .mp4, not renamed to .zt)
        saved_video = theme_path / 'Theme.mp4'
        assert saved_video.exists()

        config = json.loads((theme_path / 'config.json').read_text())
        assert config['background'] == str(saved_video)

    def test_video_path_same_as_dest_no_copy(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """When video source already IS the dest .zt, don't copy over itself."""
        theme_path = tmp_path / 'theme320320' / 'Custom_SameVid'
        theme_path.mkdir(parents=True)
        zt = theme_path / 'Theme.zt'
        zt.write_bytes(b'\x00' * 64)

        ok, msg = ThemeService.save(
            'Custom_SameVid',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            video_path=zt,
        )
        assert ok is True

    def test_video_path_not_exists(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """video_path provided but file doesn't exist -> background_path is None."""
        ok, msg = ThemeService.save(
            'VidGone',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            video_path=tmp_path / 'nonexistent.mp4',
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_VidGone'
        config = json.loads((theme_path / 'config.json').read_text())
        assert config['background'] is None

    def test_mask_source_saved(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskSource', has_mask=True, has_bg=False, has_preview=False
        )

        ok, msg = ThemeService.save(
            'Masked',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            mask=big_image,
            mask_source=mask_dir,
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_Masked'
        config = json.loads((theme_path / 'config.json').read_text())
        assert config['mask'] == str(mask_dir)

    def test_mask_position_saved(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskPos', has_mask=True, has_bg=False, has_preview=False
        )

        ok, msg = ThemeService.save(
            'MaskPos',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            mask=big_image,
            mask_source=mask_dir,
            mask_position=(50, 60),
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_MaskPos'
        config = json.loads((theme_path / 'config.json').read_text())
        assert config['mask_position'] == [50, 60]

    def test_mask_without_mask_source_file(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """mask_source directory that doesn't contain 01.png => mask not saved."""
        empty_dir = tmp_path / 'EmptyMask'
        empty_dir.mkdir()

        ok, msg = ThemeService.save(
            'NoMask',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            mask=big_image,
            mask_source=empty_dir,
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_NoMask'
        config = json.loads((theme_path / 'config.json').read_text())
        assert config['mask'] is None

    def test_exception_returns_false(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        bad_bg = MagicMock()
        bad_bg.__bool__ = lambda self: True
        bad_bg.copy.side_effect = RuntimeError('boom')

        ok, msg = ThemeService.save(
            'Explode',
            tmp_path,
            lcd_size,
            background=bad_bg,
            overlay_config={},
        )
        assert ok is False
        assert 'Save failed' in msg

    def test_no_mask_position_without_mask_path(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """mask_position should NOT appear in config when mask is None."""
        ok, msg = ThemeService.save(
            'NoMaskNoPos',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
            mask_position=(10, 20),  # position provided but no mask
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_NoMaskNoPos'
        config = json.loads((theme_path / 'config.json').read_text())
        assert 'mask_position' not in config

    def test_preview_used_for_thumbnail(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """When preview is provided, Theme.png should differ from 00.png."""
        preview_img = make_test_surface(320, 320, (0, 255, 0))

        ok, msg = ThemeService.save(
            'WithPreview',
            tmp_path,
            lcd_size,
            background=big_image,
            preview=preview_img,
            overlay_config={},
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_WithPreview'
        assert (theme_path / '00.png').exists()
        assert (theme_path / 'Theme.png').exists()

        # 00.png = clean background (blue), Theme.png = preview (green)
        bg_data = (theme_path / '00.png').read_bytes()
        thumb_data = (theme_path / 'Theme.png').read_bytes()
        assert bg_data != thumb_data, "Thumbnail should differ from background"

    def test_no_preview_falls_back_to_background(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """Without preview, thumbnail uses background (existing behavior)."""
        ok, msg = ThemeService.save(
            'NoPreview',
            tmp_path,
            lcd_size,
            background=big_image,
            overlay_config={},
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_NoPreview'
        assert (theme_path / '00.png').exists()
        assert (theme_path / 'Theme.png').exists()

    def test_background_saved_as_clean_image(
        self, tmp_path: Path, lcd_size: tuple[int, int], big_image: Any
    ) -> None:
        """00.png must be the clean background, not a rendered preview."""
        preview_img = make_test_surface(320, 320, (255, 255, 0))

        ok, msg = ThemeService.save(
            'CleanBg',
            tmp_path,
            lcd_size,
            background=big_image,
            preview=preview_img,
            overlay_config={},
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_CleanBg'
        # 00.png should exist and be a valid image
        saved_bg = QImage(str(theme_path / '00.png'))
        assert (saved_bg.width(), saved_bg.height()) == (320, 320)


# ── export_tr / import_tr ─────────────────────────────────────────────────────


class TestExportImport:

    def test_export_success(self, tmp_path: Path) -> None:
        theme_path = tmp_path / 'theme'
        theme_path.mkdir()
        export_path = tmp_path / 'out.tr'

        mock_export = MagicMock()
        svc = ThemeService(export_theme_fn=mock_export)
        ok, msg = svc.export_tr(theme_path, export_path)

        assert ok is True
        assert 'out.tr' in msg
        mock_export.assert_called_once_with(str(theme_path), str(export_path))

    def test_export_failure(self, tmp_path: Path) -> None:
        mock_export = MagicMock(side_effect=RuntimeError('disk full'))
        svc = ThemeService(export_theme_fn=mock_export)
        ok, msg = svc.export_tr(
            tmp_path / 'bad', tmp_path / 'out.tr'
        )

        assert ok is False
        assert 'Export failed' in msg

    def test_import_success(self, tmp_path: Path, lcd_size: tuple[int, int]) -> None:
        import_file = tmp_path / 'mytheme.tr'
        import_file.write_bytes(b'\x00')

        fake_theme = ThemeInfo(name='mytheme', resolution=(320, 320))

        mock_import = MagicMock()
        svc = ThemeService(import_theme_fn=mock_import)
        with patch(
            'trcc.legacy.services.theme.theme_info_from_directory', return_value=fake_theme,
        ):
            ok, result = svc.import_tr(import_file, tmp_path, lcd_size)

        assert ok is True
        assert isinstance(result, ThemeInfo)

    def test_import_failure(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        mock_import = MagicMock(side_effect=ValueError('bad header'))
        svc = ThemeService(import_theme_fn=mock_import)
        ok, msg = svc.import_tr(
            tmp_path / 'bad.tr', tmp_path, lcd_size
        )

        assert ok is False
        assert 'Import failed' in msg

    def test_import_resolution_mismatch_warning(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        import_file = tmp_path / 'big.tr'
        import_file.write_bytes(b'\x00')

        mismatched_theme = ThemeInfo(
            name='big', resolution=(480, 480)
        )

        mock_import = MagicMock()
        svc = ThemeService(import_theme_fn=mock_import)
        with patch(
            'trcc.legacy.services.theme.theme_info_from_directory', return_value=mismatched_theme,
        ), patch('trcc.legacy.services.theme.log') as mock_log:
            ok, result = svc.import_tr(import_file, tmp_path, lcd_size)

        assert ok is True
        mock_log.warning.assert_called_once()
        assert '480' in str(mock_log.warning.call_args)

    def test_import_zero_resolution_no_warning(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        """Theme with (0, 0) resolution should NOT trigger mismatch warning."""
        import_file = tmp_path / 'zero.tr'
        import_file.write_bytes(b'\x00')

        zero_theme = ThemeInfo(name='zero', resolution=(0, 0))

        mock_import = MagicMock()
        svc = ThemeService(import_theme_fn=mock_import)
        with patch(
            'trcc.legacy.services.theme.theme_info_from_directory', return_value=zero_theme,
        ), patch('trcc.legacy.services.theme.log') as mock_log:
            ok, result = svc.import_tr(import_file, tmp_path, lcd_size)

        assert ok is True
        mock_log.warning.assert_not_called()

    def test_import_matching_resolution_no_warning(
        self, tmp_path: Path, lcd_size: tuple[int, int]
    ) -> None:
        import_file = tmp_path / 'match.tr'
        import_file.write_bytes(b'\x00')

        matching_theme = ThemeInfo(name='match', resolution=(320, 320))

        mock_import = MagicMock()
        svc = ThemeService(import_theme_fn=mock_import)
        with patch(
            'trcc.legacy.services.theme.theme_info_from_directory', return_value=matching_theme,
        ), patch('trcc.legacy.services.theme.log') as mock_log:
            ok, _ = svc.import_tr(import_file, tmp_path, lcd_size)

        assert ok is True
        mock_log.warning.assert_not_called()


# ── _load_dc_display_options ──────────────────────────────────────────────────


class TestLoadDcDisplayOptions:

    def test_config_json_exists(self, tmp_path: Path) -> None:
        """When config.json exists and parses, use it."""
        td = _make_theme_dir(
            tmp_path, 'JsonTheme',
            has_json=True,
            json_content={
                'dc': {'elements': []},
                'background': '/some/bg.png',
                'mask': '/some/mask',
            },
        )
        dc_path = td / 'config1.dc'

        mock_load = MagicMock(return_value=(
            {'elements': []},
            {'background_path': '/some/bg.png'},
        ))
        svc = ThemeService(load_config_json_fn=mock_load)
        opts = svc._load_dc_display_options(dc_path, 320, 320)
        assert 'background_path' in opts
        assert opts['background_path'] == '/some/bg.png'

    def test_config_json_none_result(self, tmp_path: Path) -> None:
        """config.json exists but load_config_json returns None -> fall through to DC."""
        td = _make_theme_dir(
            tmp_path, 'BadJson',
            has_json=True, has_dc=True,
            json_content='not a valid config',  # type: ignore[arg-type]
        )
        # Overwrite with invalid JSON structure
        (td / 'config.json').write_text('"just a string"')
        dc_path = td / 'config1.dc'

        mock_load = MagicMock(return_value=None)
        MockDcCls = MagicMock()
        mock_dc = MagicMock()
        mock_dc.display_options = {'rotation': 90}
        MockDcCls.return_value = mock_dc

        svc = ThemeService(load_config_json_fn=mock_load, dc_config_cls=MockDcCls)
        opts = svc._load_dc_display_options(dc_path, 320, 320)

        assert opts == {'rotation': 90}

    def test_config_json_exception(self, tmp_path: Path) -> None:
        """Exception during config.json parse -> fall through to DC.

        Phase 11.6: the broad-except in `_load_dc_display_options` was
        narrowed to `(OSError, ValueError, KeyError, TypeError)` — the
        realistic surface of `json.load` + `dict[k]` access.  Use
        ``ValueError`` here (covers `json.JSONDecodeError`, the actual
        parse failure path) instead of ``RuntimeError``.
        """
        td = _make_theme_dir(
            tmp_path, 'ExcJson',
            has_json=True, has_dc=True,
            json_content={'dc': {}},
        )
        dc_path = td / 'config1.dc'

        mock_load = MagicMock(side_effect=ValueError('parse error'))
        MockDcCls = MagicMock()
        mock_dc = MagicMock()
        mock_dc.display_options = {'mode': 2}
        MockDcCls.return_value = mock_dc

        svc = ThemeService(load_config_json_fn=mock_load, dc_config_cls=MockDcCls)
        opts = svc._load_dc_display_options(dc_path, 320, 320)

        assert opts == {'mode': 2}

    def test_dc_file_parse(self, tmp_path: Path) -> None:
        """No config.json, but DC file exists -> use DcConfig."""
        td = _make_theme_dir(
            tmp_path, 'DcOnly',
            has_dc=True, has_json=False,
        )
        dc_path = td / 'config1.dc'

        MockDcCls = MagicMock()
        mock_dc = MagicMock()
        mock_dc.display_options = {'background_display': True}
        MockDcCls.return_value = mock_dc

        svc = ThemeService(dc_config_cls=MockDcCls)
        opts = svc._load_dc_display_options(dc_path, 320, 320)

        assert opts == {'background_display': True}

    def test_dc_file_exception(self, tmp_path: Path) -> None:
        """DC file exists but parsing fails -> empty dict."""
        td = _make_theme_dir(
            tmp_path, 'BadDc',
            has_dc=True, has_json=False,
        )
        dc_path = td / 'config1.dc'

        MockDcCls = MagicMock(side_effect=ValueError('corrupt dc'))
        svc = ThemeService(dc_config_cls=MockDcCls)
        opts = svc._load_dc_display_options(dc_path, 320, 320)

        assert opts == {}

    def test_no_dc_no_json(self, tmp_path: Path) -> None:
        """Neither config.json nor DC file -> empty dict."""
        td = _make_theme_dir(
            tmp_path, 'NoDcNoJson',
            has_dc=False, has_json=False,
        )
        dc_path = td / 'config1.dc'
        # dc_path doesn't exist

        svc = ThemeService()
        opts = svc._load_dc_display_options(dc_path, 320, 320)
        assert opts == {}

    def test_none_dc_path(self) -> None:
        """dc_path=None -> empty dict."""
        svc = ThemeService()
        opts = svc._load_dc_display_options(None, 320, 320)  # type: ignore[arg-type]
        assert opts == {}


# ── _parse_mask_position ──────────────────────────────────────────────────────


class TestParseMaskPosition:

    def test_full_size_mask_returns_zero_zero(self) -> None:
        svc = ThemeService()
        pos = svc._parse_mask_position(None, 320, 320, 320, 320)
        assert pos == (0, 0)

    def test_oversized_mask_returns_zero_zero(self) -> None:
        svc = ThemeService()
        pos = svc._parse_mask_position(None, 400, 400, 320, 320)
        assert pos == (0, 0)

    def test_small_mask_no_dc_centers(self) -> None:
        svc = ThemeService()
        pos = svc._parse_mask_position(None, 100, 100, 320, 320)
        assert pos == (110, 110)

    def test_small_mask_dc_not_exists_centers(self, tmp_path: Path) -> None:
        svc = ThemeService()
        pos = svc._parse_mask_position(
            tmp_path / 'nonexistent.dc', 100, 100, 320, 320
        )
        assert pos == (110, 110)

    def test_small_mask_dc_with_position(self, tmp_path: Path) -> None:
        dc_path = tmp_path / 'config1.dc'
        dc_path.write_bytes(b'\x00')

        mock_dc = MagicMock()
        mock_dc.mask_enabled = True
        mock_dc.mask_settings = {'mask_position': [200, 150]}

        MockDcCls = MagicMock(return_value=mock_dc)
        svc = ThemeService(dc_config_cls=MockDcCls)
        pos = svc._parse_mask_position(dc_path, 100, 100, 320, 320)

        # center_pos=(200,150), mask 100x100 -> top-left = (200-50, 150-50) = (150, 100)
        assert pos == (150, 100)

    def test_small_mask_dc_mask_not_enabled_centers(self, tmp_path: Path) -> None:
        dc_path = tmp_path / 'config1.dc'
        dc_path.write_bytes(b'\x00')

        mock_dc = MagicMock()
        mock_dc.mask_enabled = False
        mock_dc.mask_settings = {}

        MockDcCls = MagicMock(return_value=mock_dc)
        svc = ThemeService(dc_config_cls=MockDcCls)
        pos = svc._parse_mask_position(dc_path, 100, 100, 320, 320)

        assert pos == (110, 110)

    def test_small_mask_dc_no_position_key_centers(self, tmp_path: Path) -> None:
        dc_path = tmp_path / 'config1.dc'
        dc_path.write_bytes(b'\x00')

        mock_dc = MagicMock()
        mock_dc.mask_enabled = True
        mock_dc.mask_settings = {}  # no 'mask_position' key

        MockDcCls = MagicMock(return_value=mock_dc)
        svc = ThemeService(dc_config_cls=MockDcCls)
        pos = svc._parse_mask_position(dc_path, 100, 100, 320, 320)

        assert pos == (110, 110)

    def test_dc_parse_exception_centers(self, tmp_path: Path) -> None:
        dc_path = tmp_path / 'config1.dc'
        dc_path.write_bytes(b'\x00')

        MockDcCls = MagicMock(side_effect=ValueError('corrupt'))
        svc = ThemeService(dc_config_cls=MockDcCls)
        pos = svc._parse_mask_position(dc_path, 100, 100, 320, 320)

        assert pos == (110, 110)


# ── _load_mask_into ───────────────────────────────────────────────────────────


class TestLoadMaskInto:

    def test_success(self, tmp_path: Path) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'Mask', has_mask=True, has_bg=False, has_preview=False
        )
        td = ThemeDir(mask_dir)
        data = ThemeData()

        svc = ThemeService()
        with patch.object(
            svc, '_parse_mask_position', return_value=(10, 20),
        ):
            svc._load_mask_into(data, td, 320, 320)

        assert data.mask is not None
        assert data.mask_position == (10, 20)
        assert data.mask_source_dir == mask_dir

    def test_mask_not_exists(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / 'empty'
        empty_dir.mkdir()
        td = ThemeDir(empty_dir)
        data = ThemeData()

        svc = ThemeService()
        svc._load_mask_into(data, td, 320, 320)

        assert data.mask is None
        assert data.mask_position is None

    def test_with_dc_path(self, tmp_path: Path) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskDc',
            has_mask=True, has_dc=True, has_bg=False, has_preview=False,
        )
        td = ThemeDir(mask_dir)
        data = ThemeData()
        dc_path = mask_dir / 'config1.dc'

        svc = ThemeService()
        with patch.object(
            svc, '_parse_mask_position', return_value=(5, 5),
        ) as mock_parse:
            svc._load_mask_into(data, td, 320, 320, dc_path=dc_path)

        # dc_path should be passed to _parse_mask_position
        assert mock_parse.call_args[0][0] == dc_path

    def test_falls_back_to_td_dc(self, tmp_path: Path) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskFallback',
            has_mask=True, has_dc=True, has_bg=False, has_preview=False,
        )
        td = ThemeDir(mask_dir)
        data = ThemeData()

        svc = ThemeService()
        with patch.object(
            svc, '_parse_mask_position', return_value=None,
        ) as mock_parse:
            svc._load_mask_into(data, td, 320, 320)

        # dc_path=None, so it should use td.dc since td.dc.exists()
        first_arg = mock_parse.call_args[0][0]
        assert first_arg == td.dc

    def test_no_dc_passes_none(self, tmp_path: Path) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskNoDc',
            has_mask=True, has_dc=False, has_bg=False, has_preview=False,
        )
        td = ThemeDir(mask_dir)
        data = ThemeData()

        svc = ThemeService()
        with patch.object(
            svc, '_parse_mask_position', return_value=None,
        ) as mock_parse:
            svc._load_mask_into(data, td, 320, 320)

        # dc_path=None and td.dc doesn't exist -> None
        first_arg = mock_parse.call_args[0][0]
        assert first_arg is None

    def test_exception_logged(self, tmp_path: Path) -> None:
        mask_dir = _make_theme_dir(
            tmp_path, 'MaskErr',
            has_mask=True, has_bg=False, has_preview=False,
        )
        td = ThemeDir(mask_dir)
        data = ThemeData()

        svc = ThemeService()
        mock_renderer = MagicMock()
        mock_renderer.open_image.side_effect = OSError('corrupt')
        with patch('trcc.legacy.services.theme.ImageService.renderer', return_value=mock_renderer), \
             patch('trcc.legacy.services.theme.log') as mock_log:
            svc._load_mask_into(data, td, 320, 320)

        assert data.mask is None
        mock_log.error.assert_called_once()


# ── _open_image / _black_image ────────────────────────────────────────────────


class TestImageHelpers:

    def test_open_image_delegates(self) -> None:
        with patch(
            'trcc.legacy.services.theme.ImageService.open_and_resize',
            return_value='sentinel',
        ) as mock_open:
            result = ThemeService._open_image(Path('/fake.png'), 320, 320)

        mock_open.assert_called_once_with(Path('/fake.png'), 320, 320)
        assert result == 'sentinel'

    def test_black_image_delegates(self) -> None:
        with patch(
            'trcc.legacy.services.theme.ImageService.solid_color',
            return_value='black_sentinel',
        ) as mock_solid:
            result = ThemeService._black_image(320, 320)

        mock_solid.assert_called_once_with(0, 0, 0, 320, 320)
        assert result == 'black_sentinel'
