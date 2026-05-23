"""Integration tests for DisplayService + Device with real services.

Tests state wiring that mocks can't catch — the exact class of bug
that caused v8.0.1 theme save failures. Real OverlayService, real
ImageService/Renderer, mocked DeviceService + MediaService.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import get_pixel

from trcc.legacy.core.device.lcd import LCDDevice as Device
from trcc.legacy.core.models import ThemeInfo, ThemeType
from trcc.legacy.services.display import DisplayService
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.overlay import OverlayService
from trcc.legacy.services.theme_persistence import ThemePersistence

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def renderer() -> Any:
    return ImageService.renderer()


@pytest.fixture()
def mock_media() -> MagicMock:
    media = MagicMock()
    media._frames = []
    media.has_frames = False
    media.is_playing = False
    media.source_path = None
    media.get_frame.return_value = None
    media.frame_interval_ms = 33
    return media


@pytest.fixture()
def mock_path_resolver(tmp_path: Path) -> MagicMock:
    """Mock PlatformSetup path resolver — returns dirs under tmp_path."""
    resolver = MagicMock()
    resolver.data_dir.return_value = str(tmp_path / 'data')
    resolver.user_content_dir.return_value = str(tmp_path / 'user')
    resolver.web_dir = lambda w, h: str(tmp_path / 'data' / 'web' / f'{w}{h}')
    resolver.web_masks_dir = lambda w, h: str(tmp_path / 'data' / 'web' / f'zt{w}{h}')
    resolver.user_masks_dir = lambda w, h: str(tmp_path / 'user' / 'data' / 'web' / f'zt{w}{h}')
    return resolver


@pytest.fixture()
def display_svc(renderer: Any, mock_media: MagicMock, mock_path_resolver: MagicMock) -> DisplayService:
    """Real DisplayService with real OverlayService, mocked device/media."""
    devices = MagicMock()
    devices.selected.encoding_params = ('scsi', (320, 320), None, False)
    overlay = OverlayService(320, 320, renderer=renderer)
    svc = DisplayService(devices, overlay, mock_media, path_resolver=mock_path_resolver)
    svc.set_resolution(320, 320)
    return svc


@pytest.fixture()
def lcd(display_svc: DisplayService, renderer: Any) -> Device:
    """Device wired to real display_svc."""
    return Device(
        device_svc=display_svc.devices,
        display_svc=display_svc,
        theme_svc=MagicMock(),
        renderer=renderer,
    )


@pytest.fixture()
def static_theme(tmp_path: Path, renderer: Any) -> ThemeInfo:
    """Local theme with a blue 00.png background."""
    theme_dir = tmp_path / 'TestTheme'
    theme_dir.mkdir()
    bg = renderer.create_surface(320, 320, (0, 0, 255))
    bg.save(str(theme_dir / '00.png'))
    return ThemeInfo(
        name='TestTheme', path=theme_dir, theme_type=ThemeType.LOCAL,
        background_path=theme_dir / '00.png',
    )


@pytest.fixture()
def mask_theme(tmp_path: Path, renderer: Any) -> tuple[ThemeInfo, Path]:
    """Local theme with background + mask + DC config."""
    theme_dir = tmp_path / 'MaskTheme'
    theme_dir.mkdir()
    # Blue background
    bg = renderer.create_surface(320, 320, (0, 0, 255))
    bg.save(str(theme_dir / '00.png'))
    # Semi-transparent mask
    mask = renderer.create_surface(320, 320, (255, 255, 255, 128))
    mask.save(str(theme_dir / '01.png'))
    theme = ThemeInfo(
        name='MaskTheme', path=theme_dir, theme_type=ThemeType.LOCAL,
        background_path=theme_dir / '00.png',
        mask_path=theme_dir / '01.png',
    )
    return theme, theme_dir


# ═════════════════════════════════════════════════════════════════════════
# Group 1: load_local_theme() state wiring
# ═════════════════════════════════════════════════════════════════════════


class TestLoadLocalThemeStateWiring:
    """Verify that load_local_theme correctly wires internal state."""

    def test_static_theme_sets_clean_background(
        self, display_svc: DisplayService, static_theme: ThemeInfo,
    ) -> None:
        """Loading a static theme must set _clean_background."""
        result = display_svc.load_local_theme(static_theme)

        assert result['image'] is not None
        assert display_svc._clean_background is not None
        # Clean bg should be the loaded image (not overlay-composited)
        assert display_svc._clean_background is display_svc.current_image

    def test_static_theme_sets_current_theme_path(
        self, display_svc: DisplayService, static_theme: ThemeInfo,
    ) -> None:
        """Loading a theme must set current_theme_path to theme dir."""
        display_svc.load_local_theme(static_theme)

        assert display_svc.current_theme_path == static_theme.path

    def test_mask_theme_sets_mask_source_dir(
        self, display_svc: DisplayService,
        mask_theme: tuple[ThemeInfo, Path],
    ) -> None:
        """Theme with mask must set _mask_source_dir."""
        theme, theme_dir = mask_theme
        display_svc.load_local_theme(theme)

        assert display_svc._mask_source_dir == theme_dir

    def test_no_mask_theme_clears_stale_mask_source_dir(
        self, display_svc: DisplayService, static_theme: ThemeInfo,
        mask_theme: tuple[ThemeInfo, Path],
    ) -> None:
        """Loading theme WITHOUT mask after one WITH mask clears _mask_source_dir."""
        theme_with_mask, _ = mask_theme
        display_svc.load_local_theme(theme_with_mask)
        assert display_svc._mask_source_dir is not None

        # Now load theme without mask
        display_svc.load_local_theme(static_theme)
        assert display_svc._mask_source_dir is None

    def test_animated_theme_sets_clean_background_from_first_frame(
        self, display_svc: DisplayService, mock_media: MagicMock,
        renderer: Any, tmp_path: Path,
    ) -> None:
        """Animated theme must set _clean_background from first video frame."""
        # Create a theme dir with a video reference
        theme_dir = tmp_path / 'AnimTheme'
        theme_dir.mkdir()
        # No 00.png — animated themes get their bg from first frame

        frame = renderer.create_surface(320, 320, (50, 100, 150))
        mock_media.get_frame.return_value = frame
        mock_media.has_frames = True

        # Patch the loader to return an animated result
        result = {
            'image': None, 'is_animated': True,
            'status': 'Theme: AnimTheme',
            'mask_source_dir': None, 'theme_path': theme_dir,
        }
        with patch.object(display_svc._loader, 'load_local_theme', return_value=result):
            display_svc.load_local_theme(MagicMock())

        assert display_svc._clean_background is frame


# ═════════════════════════════════════════════════════════════════════════
# Group 2: set_brightness / set_rotation with real images
# ═════════════════════════════════════════════════════════════════════════


class TestBrightnessRotationRealImages:
    """Verify actual pixel-level transformations."""

    def test_brightness_25_darkens_image(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_brightness(25) must darken a white image."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        display_svc._clean_background = white

        result = display_svc.set_brightness(25)
        assert result is not None

        r, g, b = get_pixel(result, 160, 160)[:3]
        # 25% brightness on white should be significantly darker
        assert r < 200, f"Expected darkened pixel, got r={r}"
        assert g < 200, f"Expected darkened pixel, got g={g}"
        assert b < 200, f"Expected darkened pixel, got b={b}"

    def test_brightness_100_is_identity(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_brightness(100) must not change pixels."""
        red = renderer.create_surface(320, 320, (200, 50, 30))
        display_svc.current_image = red
        display_svc._clean_background = red

        result = display_svc.set_brightness(100)
        assert result is not None

        r, g, b = get_pixel(result, 160, 160)[:3]
        assert abs(r - 200) <= 1
        assert abs(g - 50) <= 1
        assert abs(b - 30) <= 1

    def test_rotation_90_moves_pixels(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_rotation(90) must move pixels to rotated positions."""
        # Create image with red top-left quadrant, blue elsewhere
        from PySide6.QtGui import QColor, QImage
        img = QImage(320, 320, QImage.Format.Format_RGB32)
        img.fill(QColor(0, 0, 255))
        # Paint top-left 10x10 red
        red = QColor(255, 0, 0)
        for x in range(10):
            for y in range(10):
                img.setPixelColor(x, y, red)

        display_svc.current_image = img
        display_svc._clean_background = img

        result = display_svc.set_rotation(90)
        assert result is not None

        # After 90 CW rotation, top-left red should NOT still be at (0, 0)
        r, g, b = get_pixel(result, 0, 0)[:3]
        # Top-left of rotated image should be blue (from bottom-left of original)
        assert r < 50, f"Expected blue after rotation, got r={r}"

    def test_rotation_0_is_identity(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_rotation(0) must not change pixels."""
        green = renderer.create_surface(320, 320, (0, 200, 0))
        display_svc.current_image = green
        display_svc._clean_background = green
        display_svc.brightness = 100  # avoid brightness dimming

        result = display_svc.set_rotation(0)
        assert result is not None

        r, g, b = get_pixel(result, 160, 160)[:3]
        assert abs(g - 200) <= 1


class TestRotationCanvasAlwaysSwaps:
    """Post-10B.0b: canvas always swaps for non-square at 90/270.

    has_portrait_themes only affects which theme dir we read from and
    whether image_rotation needs to fire pixel rotation. The previous
    "canvas stays landscape when no portrait theme dir" behavior is gone —
    it was the source of every rotation bug we hit.
    """

    def test_rotation_90_canvas_swaps_for_non_square(
        self, renderer: Any, mock_media: MagicMock, mock_path_resolver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Non-square 90° always swaps canvas — even without portrait theme dirs."""
        devices = MagicMock()
        devices.selected.encoding_params = ('scsi', (1280, 480), None, False)
        overlay = OverlayService(1280, 480, renderer=renderer)
        svc = DisplayService(devices, overlay, mock_media, path_resolver=mock_path_resolver)
        svc.set_resolution(1280, 480)
        # Portrait web dir exists but no portrait theme dir
        web_dir = tmp_path / 'data' / 'web' / '4801280'
        web_dir.mkdir(parents=True)

        bg = renderer.create_surface(1280, 480, (100, 50, 200))
        svc._clean_background = bg
        svc.current_image = bg

        svc.set_rotation(90)

        # Canvas swaps to portrait
        assert svc.canvas_size == (480, 1280)
        # Theme image stays landscape (no portrait theme dir on disk)
        bg_w, bg_h = renderer.surface_size(svc._clean_background)
        assert (bg_w, bg_h) == (1280, 480)
        # image_rotation returns actual degrees for pixel-rotate onto portrait canvas
        assert svc._image_rotation == 90

    def test_image_rotation_zero_when_portrait_themes_present(
        self, renderer: Any, mock_media: MagicMock, mock_path_resolver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """has_portrait_themes=True at 90° → no pixel rotation (theme already portrait)."""
        devices = MagicMock()
        devices.selected.encoding_params = ('scsi', (1280, 480), None, False)
        overlay = OverlayService(1280, 480, renderer=renderer)
        svc = DisplayService(devices, overlay, mock_media, path_resolver=mock_path_resolver)
        svc.set_resolution(1280, 480)
        svc._has_portrait_themes = True

        svc.set_rotation(90)
        assert svc._image_rotation == 0

    def test_image_rotation_actual_when_no_portrait_themes(
        self, renderer: Any, mock_media: MagicMock, mock_path_resolver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """has_portrait_themes=False at 90° → pixel rotation = rotation degrees."""
        devices = MagicMock()
        devices.selected.encoding_params = ('scsi', (1280, 480), None, False)
        overlay = OverlayService(1280, 480, renderer=renderer)
        svc = DisplayService(devices, overlay, mock_media, path_resolver=mock_path_resolver)
        svc.set_resolution(1280, 480)
        svc._has_portrait_themes = False

        svc.set_rotation(90)
        assert svc._image_rotation == 90

    def test_cloud_theme_decodes_at_overlay_dims(
        self, renderer: Any, mock_media: MagicMock, mock_path_resolver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Cloud theme video frames decode at overlay's current dims, not canvas."""
        devices = MagicMock()
        devices.selected.encoding_params = ('scsi', (640, 480), None, False)
        overlay = OverlayService(640, 480, renderer=renderer)
        svc = DisplayService(devices, overlay, mock_media, path_resolver=mock_path_resolver)
        svc.set_resolution(640, 480)

        bg = renderer.create_surface(640, 480, (0, 0, 0))
        svc._clean_background = bg
        svc.current_image = bg

        svc.set_rotation(90)

        # Simulate mask apply setting overlay to portrait
        overlay.set_resolution(480, 640)

        # Load cloud theme — should decode at overlay dims (480x640), not canvas (640x480)
        theme = MagicMock()
        theme.name = "test_cloud"
        theme.theme_type = MagicMock()
        theme.animation_path = str(tmp_path / "test.mp4")
        (tmp_path / "test.mp4").touch()

        svc.load_cloud_theme(theme)

        # Media target must match overlay (portrait), not canvas (landscape)
        mock_media.set_target_size.assert_called_with(480, 640)


# ═════════════════════════════════════════════════════════════════════════
# Group 3: Theme load -> save round-trip
# ═════════════════════════════════════════════════════════════════════════


class TestThemeSaveRoundTrip:
    """Verify save captures correct state after various operations."""

    def test_save_uses_clean_bg_not_overlay(
        self, display_svc: DisplayService, renderer: Any,
        tmp_path: Path,
    ) -> None:
        """Save after overlay must write clean bg to 00.png, not composited."""
        blue = renderer.create_surface(320, 320, (0, 0, 255))
        display_svc.current_image = blue
        display_svc._clean_background = blue

        # Enable overlay with text (changes the rendered image)
        display_svc.overlay.enabled = True
        display_svc.overlay.set_config({
            'label': {'x': 10, 'y': 10, 'text': 'HELLO',
                      'color': '#FF0000', 'enabled': True},
        })

        ok, msg = display_svc.save_theme('OverlayTest')
        assert ok is True

        # 00.png should be clean blue bg (saved to user_content_dir/data/)
        user_dir = Path(display_svc._path_resolver.user_content_dir())
        theme_path = user_dir / 'data' / 'theme320320' / 'Custom_OverlayTest'
        bg_path = theme_path / '00.png'
        assert bg_path.exists()

        saved_bg = renderer.open_image(bg_path)
        r, g, b = get_pixel(saved_bg, 160, 160)[:3]
        assert b > 200, f"00.png should be blue (clean bg), got b={b}"

    def test_save_after_mask_includes_mask_path(
        self, display_svc: DisplayService, renderer: Any,
        tmp_path: Path,
    ) -> None:
        """Save after mask load must include mask dir in config.json."""
        blue = renderer.create_surface(320, 320, (0, 0, 255))
        display_svc.current_image = blue
        display_svc._clean_background = blue

        mask_dir = tmp_path / 'masks' / 'TestMask'
        mask_dir.mkdir(parents=True)
        mask = renderer.create_surface(320, 320, (0, 0, 0, 128))
        mask.save(str(mask_dir / '01.png'))

        # Load mask into overlay (as load_mask_standalone would)
        mask_img = renderer.open_image(mask_dir / '01.png')
        display_svc.overlay.set_mask(mask_img, (0, 0))
        display_svc._mask_source_dir = mask_dir
        display_svc.overlay.enabled = True

        ok, msg = display_svc.save_theme('MaskSave')
        assert ok is True

        user_dir = Path(display_svc._path_resolver.user_content_dir())
        config = json.loads(
            (user_dir / 'data' / 'theme320320' / 'Custom_MaskSave' / 'config.json').read_text())
        assert config['mask'] == str(mask_dir)

    def test_cloud_load_preserves_mask_source_dir(
        self, display_svc: DisplayService, renderer: Any,
        mock_media: MagicMock, tmp_path: Path,
    ) -> None:
        """Cloud load must preserve mask source dir (video-only background)."""
        # User applied a mask before loading cloud video
        mask_dir = Path('/applied/mask/dir')
        display_svc._mask_source_dir = mask_dir
        display_svc.current_image = renderer.create_surface(320, 320, (0, 0, 0))
        display_svc._clean_background = display_svc.current_image

        # Cloud theme load — video only, no mask of its own
        frame = renderer.create_surface(320, 320, (50, 50, 50))
        mock_media.get_frame.return_value = frame
        mock_media.has_frames = True
        cloud_result = {
            'image': None, 'is_animated': True,
            'status': 'Cloud Theme: vid',
            'mask_source_dir': None, 'theme_path': None,
        }
        with patch.object(display_svc._loader, 'load_cloud_theme', return_value=cloud_result):
            display_svc.load_cloud_theme(MagicMock())

        # Mask source dir preserved — not wiped by cloud load
        assert display_svc._mask_source_dir == mask_dir

        # Save should reference the mask
        with patch.object(ThemePersistence, 'save', return_value=(True, 'ok')) as mock_save:
            display_svc.save_theme('CloudSave')
        assert mock_save.call_args.kwargs.get('mask_source_dir') == mask_dir

    def test_save_after_brightness_uses_clean_bg(
        self, display_svc: DisplayService, renderer: Any,
        tmp_path: Path,
    ) -> None:
        """Save after brightness change must write undimmed clean bg."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        display_svc._clean_background = white

        display_svc.set_brightness(25)

        ok, msg = display_svc.save_theme('BrightSave')
        assert ok is True

        user_dir = Path(display_svc._path_resolver.user_content_dir())
        theme_path = user_dir / 'data' / 'theme320320' / 'Custom_BrightSave'
        saved_bg = renderer.open_image(theme_path / '00.png')
        r, g, b = get_pixel(saved_bg, 160, 160)[:3]
        # Clean bg is white, NOT dimmed
        assert r > 240, f"00.png should be white (clean bg), got r={r}"
        assert g > 240, f"00.png should be white (clean bg), got g={g}"
        assert b > 240, f"00.png should be white (clean bg), got b={b}"


# ═════════════════════════════════════════════════════════════════════════
# Group 4: Device facade integration
# ═════════════════════════════════════════════════════════════════════════


class TestDeviceIntegration:
    """Test Device methods with real services underneath."""

    def test_select_theme_wires_state(
        self, lcd: Device, display_svc: DisplayService,
        static_theme: ThemeInfo,
    ) -> None:
        """lcd.select() must wire current_image and current_theme_path."""
        result = lcd.select(static_theme)

        assert result['success'] is True
        assert lcd.current_image is not None
        assert lcd.current_theme_path == static_theme.path

    def test_set_brightness_returns_transformed_image(
        self, lcd: Device, display_svc: DisplayService,
        renderer: Any,
    ) -> None:
        """lcd.set_brightness() must return an actually transformed image."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        display_svc._clean_background = white

        with patch.object(lcd, '_persist'):
            result = lcd.set_brightness(1)  # level 1 = 25%
        assert result['success'] is True
        assert result['image'] is not None

        r, g, b = get_pixel(result['image'], 160, 160)[:3]
        assert r < 200, f"Expected dimmed image, got r={r}"

    def test_save_round_trip(
        self, lcd: Device, display_svc: DisplayService,
        renderer: Any, tmp_path: Path,
    ) -> None:
        """lcd.save() must write correct files to disk."""
        blue = renderer.create_surface(320, 320, (0, 0, 255))
        display_svc.current_image = blue
        display_svc._clean_background = blue

        result = lcd.save('RoundTrip')
        assert result['success'] is True

        user_dir = Path(display_svc._path_resolver.user_content_dir())
        theme_path = user_dir / 'data' / 'theme320320' / 'Custom_RoundTrip'
        assert (theme_path / '00.png').exists()
        assert (theme_path / 'Theme.png').exists()
        assert (theme_path / 'config.json').exists()

        # 00.png should be blue
        saved = renderer.open_image(theme_path / '00.png')
        r, g, b = get_pixel(saved, 160, 160)[:3]
        assert b > 200


# =============================================================================
# Group 5: run_video_loop() — blocking video+overlay pipeline
# =============================================================================


class TestRunVideoLoop:
    """Tests for DisplayService.run_video_loop()."""

    def test_returns_error_for_missing_file(
        self, display_svc: DisplayService, mock_media: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_media.load.return_value = False
        result = display_svc.run_video_loop(tmp_path / 'nonexistent.gif')
        assert result['success'] is False
        assert 'Failed to load' in result['error']

    def test_plays_frames_and_calls_on_frame(
        self, display_svc: DisplayService, mock_media: MagicMock,
        renderer: Any, tmp_path: Path,
    ) -> None:
        """Simulate a 3-frame video and verify on_frame is called."""
        # Set up mock media to return 3 frames then stop
        frames_sent: list[Any] = []
        frame = renderer.create_surface(320, 320, (255, 0, 0))

        call_count = 0

        def mock_tick():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return frame, True, None
            return None, False, None

        mock_media.load.return_value = True
        mock_media.tick.side_effect = mock_tick
        mock_media.is_playing = True
        mock_media.frame_interval_ms = 0
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 3
        mock_media._state.fps = 30
        mock_media._state.loop = False
        mock_media._frames = []

        result = display_svc.run_video_loop(
            tmp_path / 'fake.gif',
            on_frame=lambda img: frames_sent.append(img),
        )
        assert result['success'] is True
        assert len(frames_sent) == 3

    def test_overlay_config_enables_overlay(
        self, display_svc: DisplayService, mock_media: MagicMock,
        renderer: Any, tmp_path: Path,
    ) -> None:
        """Overlay should be enabled when config is provided."""
        mock_media.load.return_value = True
        mock_media.tick.return_value = (None, False, None)
        mock_media.is_playing = False
        mock_media.frame_interval_ms = 33
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 0
        mock_media._state.fps = 16
        mock_media._frames = []

        overlay_config = {
            'test': {
                'x': 10, 'y': 10,
                'color': '#ffffff',
                'font': {'size': 14, 'style': 'regular', 'name': 'Arial'},
                'enabled': True, 'metric': 'cpu_temp',
            }
        }

        display_svc.run_video_loop(
            tmp_path / 'fake.gif',
            overlay_config=overlay_config,
        )
        assert display_svc.overlay.enabled is True

    def test_duration_limit(
        self, display_svc: DisplayService, mock_media: MagicMock,
        renderer: Any, tmp_path: Path,
    ) -> None:
        """Loop should stop after duration limit."""
        frame = renderer.create_surface(320, 320, (0, 255, 0))
        mock_media.load.return_value = True
        mock_media.tick.return_value = (frame, True, None)
        mock_media.is_playing = True
        mock_media.frame_interval_ms = 0
        mock_media._state = MagicMock()
        mock_media._state.total_frames = 100
        mock_media._state.fps = 30
        mock_media._frames = []

        frames_sent: list[Any] = []
        result = display_svc.run_video_loop(
            tmp_path / 'fake.gif',
            on_frame=lambda img: frames_sent.append(img),
            duration=0.05,
        )
        assert result['success'] is True
        assert len(frames_sent) > 0  # at least some frames


class TestDevicePlayVideoLoop:
    """Tests for Device.play_video_loop() delegation."""

    def test_delegates_to_display_service(
        self, lcd: Device, display_svc: DisplayService, tmp_path: Path,
    ) -> None:
        """play_video_loop should delegate to DisplayService.run_video_loop."""
        with patch.object(display_svc, 'run_video_loop',
                          return_value={'success': True, 'message': 'Done'}) as mock_run:
            result = lcd.play_video_loop(tmp_path / 'test.gif')
            assert result['success'] is True
            mock_run.assert_called_once()

    def test_returns_error_without_display_svc(self) -> None:
        """play_video_loop should return error if no display service."""
        lcd = Device()
        result = lcd.play_video_loop('/tmp/test.gif')
        assert result['success'] is False
        assert 'not initialized' in result['error']


# =============================================================================
# Group 6: DisplayService unit contracts
# =============================================================================


class TestDisplayServiceContracts:
    """Unit-level contracts for individual DisplayService methods."""

    def test_initialize_sets_media_and_overlay_sizes(
        self, display_svc: DisplayService,
    ) -> None:
        """initialize() calls media.set_target_size and overlay.set_resolution."""
        data_dir = Path('/tmp/trcc_test_data')
        # Resolution already set to 320x320 by fixture
        display_svc.media.reset_mock()
        display_svc.initialize(data_dir)
        display_svc.media.set_target_size.assert_called_once_with(320, 320)

    def test_setup_dirs_populates_theme_dir(
        self, display_svc: DisplayService,
    ) -> None:
        """_setup_dirs populates theme_dir via Orientation."""
        display_svc._setup_dirs(320, 320)
        assert display_svc.theme_dir is not None

    def test_cleanup_removes_working_dir(self, display_svc: DisplayService) -> None:
        """cleanup() removes the working directory."""
        wd = display_svc.working_dir
        assert wd.exists()
        display_svc.cleanup()
        assert not wd.exists()

    def test_set_resolution_same_is_noop(
        self, display_svc: DisplayService,
    ) -> None:
        """set_resolution with same dimensions does not call sub-service methods."""
        display_svc.media.reset_mock()
        display_svc.set_resolution(320, 320)
        display_svc.media.set_target_size.assert_not_called()

    def test_set_resolution_different_updates_sub_services(
        self, display_svc: DisplayService,
    ) -> None:
        """set_resolution with new dimensions updates media and overlay sizes."""
        display_svc.media.reset_mock()
        display_svc.set_resolution(480, 480)
        display_svc.media.set_target_size.assert_called_once_with(480, 480)

    def test_set_rotation_updates_rotation_and_renders(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_rotation updates self.rotation and calls _render_and_process."""
        img = renderer.create_surface(320, 320, (100, 100, 100))
        display_svc.current_image = img
        display_svc._clean_background = img
        display_svc.brightness = 100
        display_svc.set_rotation(90)
        assert display_svc.rotation == 90

    def test_set_brightness_clamps_to_zero(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_brightness clamps values below 0 to 0."""
        img = renderer.create_surface(320, 320, (100, 100, 100))
        display_svc.current_image = img
        display_svc._clean_background = img
        display_svc.set_brightness(-50)
        assert display_svc.brightness == 0

    def test_set_brightness_clamps_to_100(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_brightness clamps values above 100 to 100."""
        img = renderer.create_surface(320, 320, (100, 100, 100))
        display_svc.current_image = img
        display_svc._clean_background = img
        display_svc.set_brightness(150)
        assert display_svc.brightness == 100

    def test_set_split_mode_invalid_defaults_to_zero(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_split_mode with invalid mode (e.g. 99) defaults split_mode to 0."""
        img = renderer.create_surface(320, 320, (50, 50, 50))
        display_svc.current_image = img
        display_svc._clean_background = img
        display_svc.set_split_mode(99)
        assert display_svc.split_mode == 0

    def test_is_widescreen_split_true_for_1600x720(
        self, display_svc: DisplayService,
    ) -> None:
        """is_widescreen_split is True when resolution is 1600x720."""
        display_svc.set_resolution(1600, 720)
        assert display_svc.is_widescreen_split is True

    def test_is_widescreen_split_false_for_320x320(
        self, display_svc: DisplayService,
    ) -> None:
        """is_widescreen_split is False for standard 320x320 resolution."""
        assert display_svc.is_widescreen_split is False

    def test_convert_media_frames_passes_through_native(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """_convert_media_frames leaves already-native surfaces unchanged."""
        native = renderer.create_surface(320, 320, (10, 20, 30))
        display_svc.media._frames = [native]
        display_svc._convert_media_frames()
        assert display_svc.media._frames[0] is native

    def test_convert_media_frames_converts_raw_frames(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """_convert_media_frames converts RawFrame objects to native surfaces."""
        from trcc.legacy.core.ports import RawFrame
        raw = RawFrame(data=bytes(320 * 320 * 3), width=320, height=320)
        display_svc.media._frames = [raw]
        display_svc._convert_media_frames()
        # After conversion, frames should not be RawFrame instances
        assert not isinstance(display_svc.media._frames[0], RawFrame)

    def test_load_image_file_calls_render_and_process(
        self, display_svc: DisplayService, renderer: Any,
        tmp_path: Path,
    ) -> None:
        """load_image_file loads the image and calls _render_and_process."""
        img_path = tmp_path / 'test.png'
        img = renderer.create_surface(320, 320, (0, 100, 200))
        img.save(str(img_path))
        display_svc.load_image_file(img_path)
        assert display_svc.current_image is not None
        assert display_svc._clean_background is not None

    def test_set_clean_background_sets_both_fields(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_clean_background sets both current_image and _clean_background."""
        img = renderer.create_surface(320, 320, (0, 0, 128))
        display_svc.set_clean_background(img)
        assert display_svc._clean_background is img
        assert display_svc.current_image is img

    def test_create_black_background_sets_current_image(
        self, display_svc: DisplayService,
    ) -> None:
        """_create_black_background sets current_image to a black surface."""
        display_svc._create_black_background()
        assert display_svc.current_image is not None

    def test_render_overlay_creates_black_bg_when_no_background(
        self, display_svc: DisplayService,
    ) -> None:
        """render_overlay creates a black background when no background is set."""
        display_svc.current_image = None
        display_svc._clean_background = None
        result = display_svc.render_overlay()
        # Should not raise and should return something (black bg was created)
        assert result is not None

    def test_apply_adjustments_noop_at_full_brightness_no_rotation(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """_apply_adjustments is a no-op when brightness=100, rotation=0, split=0."""
        img = renderer.create_surface(320, 320, (200, 100, 50))
        display_svc.brightness = 100
        display_svc.rotation = 0
        display_svc.split_mode = 0
        result = display_svc._apply_adjustments(img)
        assert result is img

    def test_apply_adjustments_applies_brightness(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """_apply_adjustments applies brightness when less than 100."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.brightness = 50
        display_svc.rotation = 0
        display_svc.split_mode = 0
        result = display_svc._apply_adjustments(white)
        r, g, b = get_pixel(result, 160, 160)[:3]
        assert r < 200

    @pytest.mark.skip(reason="Phase 11 rotation refactor: rotation moved out of _apply_adjustments and into encode_for_device — single-source-of-truth fix for #137")
    def test_apply_adjustments_applies_rotation(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        pass

    def test_set_video_fit_mode_updates_current_image_on_success(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """set_video_fit_mode updates current_image when media.set_fit_mode returns True."""
        frame = renderer.create_surface(320, 320, (50, 150, 200))
        display_svc.media.set_fit_mode.return_value = True
        display_svc.media.get_frame.return_value = frame
        display_svc.media._frames = [frame]
        display_svc.set_video_fit_mode('fill')
        assert display_svc.current_image is frame

    def test_video_tick_returns_none_when_no_frame(
        self, display_svc: DisplayService,
    ) -> None:
        """video_tick returns None when media.tick returns no frame."""
        display_svc.media.tick.return_value = (None, False, None)
        result = display_svc.video_tick()
        assert result is None

    def test_video_tick_returns_dict_with_frame(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """video_tick returns a result dict when media.tick returns a frame."""
        frame = renderer.create_surface(320, 320, (100, 100, 100))
        display_svc.media.tick.return_value = (frame, True, 0.5)
        result = display_svc.video_tick()
        assert result is not None
        assert 'preview' in result

    def test_send_current_image_returns_none_without_image(
        self, display_svc: DisplayService,
    ) -> None:
        """send_current_image returns None when no current_image is set."""
        display_svc.current_image = None
        result = display_svc.send_current_image()
        assert result is None

    def test_send_current_image_raises_without_device(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """send_current_image raises RuntimeError when no device is selected."""
        display_svc.current_image = renderer.create_surface(320, 320, (0, 0, 0))
        display_svc.devices.selected = None
        import pytest as _pytest
        with _pytest.raises(RuntimeError, match='no device selected'):
            display_svc.send_current_image()

    def test_send_current_image_encodes_when_device_present(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """send_current_image returns encoded bytes when device is available."""
        display_svc.current_image = renderer.create_surface(320, 320, (128, 0, 0))
        display_svc.devices.selected.encoding_params = ('scsi', (320, 320), None, False)
        result = display_svc.send_current_image()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_save_theme_delegates_to_persistence(
        self, display_svc: DisplayService, renderer: Any,
        tmp_path: Path,
    ) -> None:
        """save_theme delegates to ThemePersistence.save with correct args."""
        img = renderer.create_surface(320, 320, (0, 0, 200))
        display_svc.current_image = img
        display_svc._clean_background = img
        with patch.object(ThemePersistence, 'save', return_value=(True, 'ok')) as m:
            display_svc.save_theme('Saved')
        m.assert_called_once()
        _, kwargs = m.call_args
        assert kwargs.get('current_image') is img or m.call_args[0][3] is img

    def test_export_config_delegates_to_persistence(
        self, display_svc: DisplayService, tmp_path: Path,
    ) -> None:
        """export_config delegates to ThemePersistence.export_config."""
        display_svc.current_theme_path = tmp_path / 'theme'
        display_svc._persistence = MagicMock()
        display_svc._persistence.export_config.return_value = (True, 'Exported: out.json')
        export_path = tmp_path / 'out.json'
        ok, msg = display_svc.export_config(export_path)
        display_svc._persistence.export_config.assert_called_once()
        assert ok is True

    def test_import_config_writable_uses_given_dir(
        self, display_svc: DisplayService, tmp_path: Path,
    ) -> None:
        """import_config uses given data_dir when it is writable."""
        display_svc._persistence = MagicMock()
        display_svc._persistence.import_config.return_value = (False, 'Theme path not found')

        ok, msg = display_svc.import_config(tmp_path / 'in.json', tmp_path)

        display_svc._persistence.import_config.assert_called_once()
        call_data_dir = display_svc._persistence.import_config.call_args[0][1]
        assert call_data_dir == tmp_path

    def test_import_config_unwritable_falls_back_to_data_dir(
        self, display_svc: DisplayService, tmp_path: Path,
    ) -> None:
        """import_config uses path_resolver.data_dir when given dir is not writable."""
        display_svc._persistence = MagicMock()
        display_svc._persistence.import_config.return_value = (False, 'Theme path not found')

        readonly_dir = Path('/root/not_writable')
        with patch('os.access', return_value=False):
            display_svc.import_config(tmp_path / 'in.json', readonly_dir)

        call_data_dir = display_svc._persistence.import_config.call_args[0][1]
        assert call_data_dir == Path(display_svc._path_resolver.data_dir())

    def test_local_dir_property(self, display_svc: DisplayService, tmp_path: Path) -> None:
        """local_dir delegates to orientation.theme_dir (must exist on disk)."""
        # data_root set by set_resolution via mock_path_resolver → tmp_path/data
        theme_path = tmp_path / 'data' / 'theme320320'
        theme_path.mkdir(parents=True, exist_ok=True)
        assert display_svc.local_dir == theme_path

    def test_web_dir_property(self, display_svc: DisplayService, tmp_path: Path) -> None:
        """web_dir delegates to orientation."""
        # data_root set by set_resolution via mock_path_resolver → tmp_path/data
        web_path = tmp_path / 'data' / 'web' / '320320'
        web_path.mkdir(parents=True, exist_ok=True)
        assert display_svc.web_dir == web_path

    def test_masks_dir_property(self, display_svc: DisplayService, tmp_path: Path) -> None:
        """masks_dir delegates to orientation."""
        # data_root set by set_resolution via mock_path_resolver → tmp_path/data
        masks_path = tmp_path / 'data' / 'web' / 'zt320320'
        masks_path.mkdir(parents=True, exist_ok=True)
        assert display_svc.masks_dir == masks_path


# ═════════════════════════════════════════════════════════════════════════
# Group 6: run_static_loop (keepalive for bulk/LY devices)
# ═════════════════════════════════════════════════════════════════════════


class TestRunStaticLoop:
    """DisplayService.run_static_loop — blocking frame resend for CLI/API."""

    def test_no_image_returns_error(self, display_svc: DisplayService) -> None:
        """No current_image → returns error dict."""
        display_svc.current_image = None
        result = display_svc.run_static_loop(duration=0.01)
        assert result["success"] is False
        assert "no image" in result["error"].lower()

    def test_sends_frames_to_device(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """Loop sends frames via devices.send_frame for duration."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white

        result = display_svc.run_static_loop(interval=0.01, duration=0.05)

        assert result["success"] is True
        assert display_svc.devices.send_frame.call_count >= 2

    def test_polls_metrics_when_overlay_enabled(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """With overlay enabled + metrics_fn, metrics are polled."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        display_svc.overlay.enabled = True
        display_svc.overlay.set_config({"elem0": {"x": 0, "y": 0, "text": "test"}})

        from trcc.legacy.core.models import HardwareMetrics
        metrics_fn = MagicMock(return_value=HardwareMetrics())

        result = display_svc.run_static_loop(
            interval=0.01, duration=0.05, metrics_fn=metrics_fn)

        assert result["success"] is True
        assert metrics_fn.call_count >= 1

    def test_skips_metrics_when_overlay_disabled(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """With overlay disabled, metrics_fn is never called."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        display_svc.overlay.enabled = False

        metrics_fn = MagicMock()

        result = display_svc.run_static_loop(
            interval=0.01, duration=0.05, metrics_fn=metrics_fn)

        assert result["success"] is True
        metrics_fn.assert_not_called()

    def test_on_frame_callback(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """on_frame callback is invoked each iteration."""
        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white
        on_frame = MagicMock()

        display_svc.run_static_loop(interval=0.01, duration=0.03, on_frame=on_frame)

        assert on_frame.call_count >= 1

    def test_duration_stops_loop(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """duration parameter limits loop execution time."""
        import time as _time

        white = renderer.create_surface(320, 320, (255, 255, 255))
        display_svc.current_image = white

        start = _time.monotonic()
        display_svc.run_static_loop(interval=0.01, duration=0.1)
        elapsed = _time.monotonic() - start

        assert elapsed < 0.5  # Should finish well within 500ms
