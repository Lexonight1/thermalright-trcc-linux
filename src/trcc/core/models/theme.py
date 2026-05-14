"""Theme models — browser items, theme data, theme config."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .overlay import DisplayElement


# =============================================================================
# Browser Item Dataclasses (replace raw dicts in theme/mask panels)
# =============================================================================


@dataclass(slots=True)
class ThemeItem:
    """Base for all theme browser items."""
    name: str
    is_local: bool = True


@dataclass(slots=True)
class LocalThemeItem(ThemeItem):
    """Item in the local themes browser (UCThemeLocal)."""
    path: str = ""
    thumbnail: str = ""
    is_user: bool = False
    index: int = 0  # position in unfiltered list


@dataclass(slots=True)
class CloudThemeItem(ThemeItem):
    """Item in the cloud themes browser (UCThemeWeb)."""
    id: str = ""
    video: str | None = None
    preview: str | None = None


@dataclass(slots=True)
class MaskItem(ThemeItem):
    """Item in the masks browser (UCThemeMask)."""
    path: str | None = None
    preview: str | None = None
    is_custom: bool = False  # User-uploaded mask (enables delete in context menu)


@dataclass(slots=True)
class MaskInfo:
    """Mask overlay info returned by ThemeService.discover_masks().

    Pure domain object — adapters (GUI, API) convert to their own types.
    """
    name: str
    path: Path | None = None
    preview_path: Path | None = None
    is_custom: bool = False  # User-created vs cloud-downloaded


# =============================================================================
# Theme Model
# =============================================================================

@dataclass(slots=True)
class ThemeData:
    """Bundle returned after loading a theme — everything needed to display it."""
    background: Any = None               # native surface (QImage)
    animation_path: Path | None = None  # video/zt path
    is_animated: bool = False
    mask: Any = None                     # native surface (QImage)
    mask_position: tuple[int, int] | None = None
    mask_source_dir: Path | None = None


class ThemeDir:
    """Standard theme directory layout — pure domain value object.

    Path-construction properties only. Zero I/O, zero logic.
    Filesystem operations (resolve_theme_dir, has_themes) live in core/paths.py.

    Usage::

        td = ThemeDir(some_path)
        td.bg          # Path to 00.png
        td.mask        # Path to 01.png
        td.dc          # Path to config1.dc
    """

    __slots__ = ('path',)

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    @property
    def bg(self) -> Path:
        """Background image (00.png)."""
        return self.path / '00.png'

    @property
    def mask(self) -> Path:
        """Mask overlay image (01.png)."""
        return self.path / '01.png'

    @property
    def preview(self) -> Path:
        """Thumbnail preview (Theme.png)."""
        return self.path / 'Theme.png'

    @property
    def dc(self) -> Path:
        """Binary overlay config (config1.dc)."""
        return self.path / 'config1.dc'

    @property
    def json(self) -> Path:
        """JSON config for custom themes (config.json)."""
        return self.path / 'config.json'

    @property
    def zt(self) -> Path:
        """Theme.zt animation file."""
        return self.path / 'Theme.zt'

    def __truediv__(self, other: str) -> Path:
        """Allow ThemeDir / 'subpath' to return a Path."""
        return self.path / other

    def __str__(self) -> str:
        return str(self.path)


class ThemeType(Enum):
    """Type of theme."""
    LOCAL = auto()      # Local theme from Theme{resolution}/ directory
    CLOUD = auto()      # Cloud theme (video) from Web/{W}{H}/ directory
    MASK = auto()       # Mask overlay from Web/zt{W}{H}/ directory
    USER = auto()       # User-created theme


@dataclass(slots=True)
class ThemeInfo:
    """
    Information about a single theme.

    Matches Windows FormCZTV theme data structure.
    """
    name: str
    path: Path | None = None
    theme_type: ThemeType = ThemeType.LOCAL

    # Files within theme directory
    background_path: Path | None = None      # 00.png
    mask_path: Path | None = None            # 01.png
    thumbnail_path: Path | None = None       # Theme.png
    animation_path: Path | None = None       # Theme.zt or video file
    config_path: Path | None = None          # config1.dc

    # Metadata
    resolution: tuple[int, int] = (320, 320)
    is_animated: bool = False
    is_mask_only: bool = False

    # Cloud theme specific
    video_url: str | None = None
    preview_url: str | None = None
    category: str | None = None  # a=Gallery, b=Tech, c=HUD, etc.

    @classmethod
    def from_video(cls, video_path: Path, preview_path: Path | None = None) -> ThemeInfo:
        """Create ThemeInfo from a cloud video file."""
        name = video_path.stem
        category = name[0] if name else None

        return cls(
            name=name,
            path=video_path.parent,
            theme_type=ThemeType.CLOUD,
            animation_path=video_path,
            thumbnail_path=preview_path,
            is_animated=True,
            category=category,
        )


# =============================================================================
# Theme Config DTOs (dc_writer save/export format)
# =============================================================================

@dataclass(slots=True)
class ThemeConfig:
    """Complete theme configuration for saving."""
    # Display elements (UCXiTongXianShiSubArray)
    elements: list[DisplayElement] = field(default_factory=list)

    # System info global enable
    system_info_enabled: bool = True

    # Display options
    background_display: bool = True    # myBjxs
    transparent_display: bool = False  # myTpxs
    rotation: int = 0                  # directionB (0/90/180/270)
    ui_mode: int = 0                   # myUIMode
    display_mode: int = 0              # myMode

    # Overlay settings
    overlay_enabled: bool = True       # myYcbk
    overlay_x: int = 0                 # JpX
    overlay_y: int = 0                 # JpY
    overlay_w: int = 320               # JpW
    overlay_h: int = 320               # JpH

    # Mask settings
    mask_enabled: bool = False         # myMbxs
    mask_x: int = 0                    # XvalMB
    mask_y: int = 0                    # YvalMB


def _default_theme_indices() -> list[int]:
    """6 carousel slots, each pointing to "unset" (-1) until the user assigns."""
    return [-1, -1, -1, -1, -1, -1]


@dataclass(slots=True)
class CarouselConfig:
    """Carousel/slideshow configuration."""
    current_theme: int = 0             # myTheme - index of current theme
    enabled: bool = False              # isLunbo
    interval_seconds: int = 3          # myLunBoTimer (minimum 3)
    count: int = 0                     # lunBoCount
    theme_indices: list[int] = field(default_factory=_default_theme_indices)
    lcd_rotation: int = 1              # myLddVal (1-3): split mode style, NOT rotation


__all__ = [
    'CarouselConfig',
    'CloudThemeItem',
    'LocalThemeItem',
    'MaskInfo',
    'MaskItem',
    'ThemeConfig',
    'ThemeData',
    'ThemeDir',
    'ThemeInfo',
    'ThemeItem',
    'ThemeType',
]
