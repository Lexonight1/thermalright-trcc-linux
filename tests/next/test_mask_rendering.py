"""DisplayService — mask rendering consumption.

Phase A follow-up: SetMaskPosition / SetMaskVisible / ApplyMask Commands
persist state in ``DeviceSettings``, but until this work landed the
render pipeline still hard-coded the theme's mask at position (0, 0).
These tests lock the consumption side:

  1. mask_visible=False skips the mask layer entirely.
  2. A user-supplied mask_path overrides the theme's bundled mask.
  3. mask_position offsets the composite (default (0, 0)).
  4. A nonexistent user override falls back to the theme's bundled mask.
  5. The bg+mask cache key includes mask state so settings changes
     rebuild the layer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.next.core.models import Kind, ProductInfo, Theme, Wire
from trcc.next.core.ports import Renderer
from trcc.next.services.display import DisplayService
from trcc.next.services.media import MediaService
from trcc.next.services.overlay import OverlayService
from trcc.next.services.settings import Settings
from trcc.next.services.theme import ThemeService

from .conftest import FakePaths

_KEY = "0402:3922"


# ── Recording renderer (same shape as test_display_rotation.py) ───────


class _Surface:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h


class RecordingRenderer(Renderer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        self._record("create_surface", width, height, color)
        return _Surface(width, height)

    def open_image(self, path: Path) -> Any:
        self._record("open_image", path)
        return _Surface(100, 100)

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        self._record("composite", base, overlay, position, mask)
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        self._record("resize", surface, width, height)
        return _Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        self._record("rotate", surface, degrees)
        if degrees % 180 == 90:
            return _Surface(surface.h, surface.w)
        return _Surface(surface.w, surface.h)

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        pass

    def encode_rgb565(self, surface: Any) -> bytes:
        return b"\x00" * (surface.w * surface.h * 2)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b"\xff\xd8"

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _Surface(frame.width, frame.height)


class _StubOverlay(OverlayService):
    """Skip text rendering — return the canvas unchanged."""

    def render(self, canvas: Any, config: Any, sensors: dict[str, float],
               clock: dict[str, str] | None = None) -> Any:
        return canvas


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def renderer() -> RecordingRenderer:
    return RecordingRenderer()


@pytest.fixture
def settings(tmp_home: Path) -> Settings:
    return Settings(FakePaths(tmp_home))


@pytest.fixture
def display(renderer: RecordingRenderer, settings: Settings) -> DisplayService:
    return DisplayService(
        renderer=renderer,
        themes=ThemeService(),
        overlay=_StubOverlay(renderer),
        settings=settings,
        media=MediaService(),
    )


def _info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="320×320 LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )


def _theme_with_mask(tmp_home: Path) -> Theme:
    """Theme directory with a real mask.png file ThemeService.mask_path() sees."""
    theme_dir = tmp_home / "themes" / "with_mask"
    theme_dir.mkdir(parents=True)
    (theme_dir / "mask.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return Theme(path=theme_dir, name="with_mask",
                 resolution=(320, 320), config={"elements": []})


def _theme_without_mask(tmp_home: Path) -> Theme:
    theme_dir = tmp_home / "themes" / "bare"
    theme_dir.mkdir(parents=True)
    return Theme(path=theme_dir, name="bare",
                 resolution=(320, 320), config={"elements": []})


def _composite_calls_with_image(
    renderer: RecordingRenderer,
) -> list[tuple[Any, ...]]:
    """Composite calls whose 'overlay' arg came from open_image (mask layer).

    The opened-image surfaces are 100×100 from RecordingRenderer.open_image;
    background fits are 320×320 (resized to canvas). Easy distinction.
    """
    return [
        args for name, args in renderer.calls
        if name == "composite" and args[1].w == 100 and args[1].h == 100
    ]


# ── 1. mask_visible=False skips the mask layer ────────────────────────


def test_mask_visible_false_skips_layer(
    display: DisplayService, renderer: RecordingRenderer,
    settings: Settings, tmp_home: Path,
) -> None:
    """A device whose mask_visible=False renders no mask composite."""
    settings.set_mask_visible(_KEY, False)
    display.build_frame(
        info=_info(), theme=_theme_with_mask(tmp_home), sensors={},
    )
    assert _composite_calls_with_image(renderer) == [], (
        "mask_visible=False must skip the mask composite entirely"
    )


# ── 2. Theme mask renders at default position (0, 0) ──────────────────


def test_theme_mask_used_at_default_position(
    display: DisplayService, renderer: RecordingRenderer,
    tmp_home: Path,
) -> None:
    """No override, no position → use theme mask at (0, 0)."""
    display.build_frame(
        info=_info(), theme=_theme_with_mask(tmp_home), sensors={},
    )
    mask_calls = _composite_calls_with_image(renderer)
    assert len(mask_calls) == 1, f"expected one mask composite, got {mask_calls}"
    # position is args[2] from composite(base, overlay, position, mask)
    assert mask_calls[0][2] == (0, 0)


# ── 3. User-supplied mask_path replaces the theme's bundled mask ──────


def test_user_override_replaces_theme_mask(
    display: DisplayService, renderer: RecordingRenderer,
    settings: Settings, tmp_home: Path,
) -> None:
    """ApplyMask-stored override path takes precedence over theme.mask_path()."""
    user_mask = tmp_home / "custom_mask.png"
    user_mask.write_bytes(b"\x89PNG\r\n\x1a\n")
    settings.set_mask_path(_KEY, str(user_mask.resolve()))

    display.build_frame(
        info=_info(), theme=_theme_with_mask(tmp_home), sensors={},
    )

    opened = [args[0] for name, args in renderer.calls if name == "open_image"]
    assert user_mask.resolve() in opened, (
        f"expected user override mask in open_image calls, got {opened}"
    )
    # Theme's bundled mask must NOT have been opened.
    theme_mask = tmp_home / "themes" / "with_mask" / "mask.png"
    assert theme_mask not in opened, (
        "user override must replace, not stack with, theme mask"
    )


# ── 4. mask_position offsets the composite call ───────────────────────


def test_mask_position_applies_offset(
    display: DisplayService, renderer: RecordingRenderer,
    settings: Settings, tmp_home: Path,
) -> None:
    """SetMaskPosition state must reach the composite as the position arg."""
    settings.set_mask_position(_KEY, (40, 60))

    display.build_frame(
        info=_info(), theme=_theme_with_mask(tmp_home), sensors={},
    )

    mask_calls = _composite_calls_with_image(renderer)
    assert len(mask_calls) == 1
    assert mask_calls[0][2] == (40, 60)


# ── 5. Missing user override falls back to the theme's bundled mask ───


def test_missing_user_mask_falls_back_to_theme(
    display: DisplayService, renderer: RecordingRenderer,
    settings: Settings, tmp_home: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale user override path → log a warning, render theme's mask."""
    settings.set_mask_path(_KEY, str(tmp_home / "deleted.png"))

    with caplog.at_level("WARNING"):
        display.build_frame(
            info=_info(), theme=_theme_with_mask(tmp_home), sensors={},
        )

    opened = [args[0] for name, args in renderer.calls if name == "open_image"]
    theme_mask = tmp_home / "themes" / "with_mask" / "mask.png"
    assert theme_mask in opened
    assert any("does not exist" in r.message for r in caplog.records)


# ── 6. No theme mask + no override = no mask layer ────────────────────


def test_no_mask_anywhere_skips_composite(
    display: DisplayService, renderer: RecordingRenderer,
    tmp_home: Path,
) -> None:
    """Theme without a mask.png and no override → no mask composite."""
    display.build_frame(
        info=_info(), theme=_theme_without_mask(tmp_home), sensors={},
    )
    assert _composite_calls_with_image(renderer) == []


# ── 7. Mask state participates in the bg+mask cache key ───────────────


def test_mask_state_change_rebuilds_bg_layer(
    display: DisplayService, renderer: RecordingRenderer,
    settings: Settings, tmp_home: Path,
) -> None:
    """A SetMaskPosition between two renders must rebuild bg_mask, not reuse.

    The Commands invalidate the cache directly; this test bypasses Commands
    and changes settings under DisplayService's nose to lock the defensive
    cache-key contract.
    """
    theme = _theme_with_mask(tmp_home)
    info = _info()

    display.build_frame(info=info, theme=theme, sensors={})
    first_count = sum(1 for n, _ in renderer.calls if n == "open_image")

    # Mutate settings WITHOUT going through Commands (no explicit invalidate).
    # The mask layer must still rebuild because mask state is in the cache key.
    settings.for_device(_KEY).mask_position = (10, 20)

    display.build_frame(info=info, theme=theme, sensors={})
    second_count = sum(1 for n, _ in renderer.calls if n == "open_image")

    assert second_count > first_count, (
        "mask state change must invalidate bg+mask cache and re-open the mask"
    )
