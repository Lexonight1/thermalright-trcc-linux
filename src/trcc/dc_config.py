"""Unified DcConfig class for TRCC config1.dc files.

Merges dc_parser (read) and dc_writer (write) into a single class.

Usage:
    dc = DcConfig("config1.dc")        # parse from file
    dc = DcConfig()                     # empty config
    dc.save("config1.dc")              # write to file
    overlay = dc.to_overlay_config()    # convert for renderer
    dc = DcConfig.from_overlay_config(overlay_dict)  # create from overlay
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import dc_parser
from .dc_parser import DisplayElement, FontConfig

log = logging.getLogger(__name__)

# Single source of truth for hardware sensor ↔ metric name mapping.
# dc_parser.get_hardware_metric_name() and dc_writer._metric_to_hardware_ids()
# both duplicate this data — prefer importing from here.
HARDWARE_METRICS: dict[tuple[int, int], str] = {
    (0, 1): 'cpu_temp',
    (0, 2): 'cpu_percent',
    (0, 3): 'cpu_freq',
    (0, 4): 'cpu_power',
    (1, 1): 'gpu_temp',
    (1, 2): 'gpu_usage',
    (1, 3): 'gpu_clock',
    (1, 4): 'gpu_power',
    (2, 1): 'mem_percent',
    (2, 2): 'mem_clock',
    (3, 1): 'disk_activity',
}

_METRIC_TO_IDS: dict[str, tuple[int, int]] = {v: k for k, v in HARDWARE_METRICS.items()}


def get_hardware_metric_name(main_count: int, sub_count: int) -> str:
    """Map hardware sensor indices to metric name."""
    return HARDWARE_METRICS.get((main_count, sub_count), f'sensor_{main_count}_{sub_count}')


def metric_to_hardware_ids(metric: str) -> tuple[int, int]:
    """Map metric name to hardware (main_count, sub_count) IDs."""
    return _METRIC_TO_IDS.get(metric, (0, 0))


class DcConfig:
    """TRCC config1.dc — unified parse + write.

    Replaces the separate parse_dc_file() → dict → write_dc_file(ThemeConfig)
    workflow with a single object that holds all config state.

    Attributes mirror ThemeConfig for writing and parsed dict fields for reading.
    """

    def __init__(self, filepath: str | Path | None = None):
        # ── Write-side fields (matches ThemeConfig) ──
        self.elements: list[DisplayElement] = []
        self.system_info_enabled: bool = True

        # Display options
        self.background_display: bool = True
        self.transparent_display: bool = False
        self.rotation: int = 0
        self.ui_mode: int = 0
        self.display_mode: int = 0

        # Overlay
        self.overlay_enabled: bool = True
        self.overlay_x: int = 0
        self.overlay_y: int = 0
        self.overlay_w: int = 320
        self.overlay_h: int = 320

        # Mask
        self.mask_enabled: bool = False
        self.mask_x: int = 0
        self.mask_y: int = 0

        # ── Parse-side fields (populated by _load) ──
        self.version: int = 0
        self.fonts: list[FontConfig] = []
        self.flags: dict = {}
        self.custom_text: str = ""
        self.legacy_elements: dict = {}  # name → ElementConfig (0xDC only)
        self.display_options: dict = {}
        self.mask_settings: dict = {}

        if filepath is not None:
            self._load(str(filepath))

    # ── Load ──

    def _load(self, filepath: str) -> None:
        """Parse a config1.dc file and populate all fields."""
        parsed = dc_parser.parse_dc_file(filepath)

        # Raw parsed dict fields
        self.version = parsed.get('version', 0)
        self.fonts = parsed.get('fonts', [])
        self.flags = parsed.get('flags', {})
        self.custom_text = parsed.get('custom_text', '')
        self.legacy_elements = parsed.get('elements', {})
        self.elements = parsed.get('display_elements', [])
        self.display_options = parsed.get('display_options', {})
        self.mask_settings = parsed.get('mask_settings', {})

        # Map display_options → flat attributes
        opts = self.display_options
        self.system_info_enabled = self.flags.get('system_info', True)
        self.background_display = opts.get('background_display', True)
        self.transparent_display = opts.get(
            'transparent_display', opts.get('screencast_display', False))
        self.rotation = opts.get('direction', 0)
        self.ui_mode = opts.get('ui_mode', 0)
        self.display_mode = opts.get('mode', opts.get('display_mode', 0))

        # Map mask_settings → flat attributes
        ms = self.mask_settings
        self.overlay_enabled = ms.get('overlay_enabled', True)
        rect = ms.get('overlay_rect')
        if rect and len(rect) == 4:
            self.overlay_x, self.overlay_y, self.overlay_w, self.overlay_h = rect
        self.mask_enabled = ms.get('mask_enabled', False)
        pos = ms.get('mask_position')
        if pos and len(pos) == 2:
            self.mask_x, self.mask_y = pos

    # ── Save ──

    def save(self, filepath: str | Path) -> None:
        """Write config1.dc in 0xDD binary format."""
        from .dc_writer import write_dc_file

        write_dc_file(self._to_theme_config(), str(filepath))

    def _to_theme_config(self):
        """Convert to dc_writer.ThemeConfig for write_dc_file()."""
        from .dc_writer import ThemeConfig

        return ThemeConfig(
            elements=self.elements,
            system_info_enabled=self.system_info_enabled,
            background_display=self.background_display,
            transparent_display=self.transparent_display,
            rotation=self.rotation,
            ui_mode=self.ui_mode,
            display_mode=self.display_mode,
            overlay_enabled=self.overlay_enabled,
            overlay_x=self.overlay_x,
            overlay_y=self.overlay_y,
            overlay_w=self.overlay_w,
            overlay_h=self.overlay_h,
            mask_enabled=self.mask_enabled,
            mask_x=self.mask_x,
            mask_y=self.mask_y,
        )

    # ── Overlay conversion ──

    def to_overlay_config(self, width: int = 320, height: int = 320) -> dict:
        """Convert to overlay renderer config dict."""
        parsed = {
            'elements': self.legacy_elements,
            'display_elements': self.elements,
            'custom_text': self.custom_text,
            'flags': self.flags,
        }
        return dc_parser.dc_to_overlay_config(parsed, width, height)

    @classmethod
    def from_overlay_config(cls, overlay_config: dict,
                            width: int = 320, height: int = 320) -> DcConfig:
        """Create DcConfig from an overlay renderer config dict."""
        from .dc_writer import overlay_config_to_theme

        tc = overlay_config_to_theme(overlay_config, width, height)
        dc = cls()
        dc.elements = tc.elements
        dc.overlay_w = tc.overlay_w
        dc.overlay_h = tc.overlay_h
        return dc

    # ── Dict compat (for callers that still expect parse_dc_file dict) ──

    def to_dict(self) -> dict:
        """Return parsed-dict representation for backward compatibility."""
        return {
            'version': self.version,
            'elements': self.legacy_elements,
            'fonts': self.fonts,
            'flags': self.flags,
            'display_elements': self.elements,
            'custom_text': self.custom_text,
            'display_options': self.display_options,
            'mask_settings': self.mask_settings,
        }

    def __repr__(self) -> str:
        fmt = '0xDD' if (self.version & 0xFF) == 0xDD else '0xDC'
        return (f"DcConfig(format={fmt}, elements={len(self.elements)}, "
                f"rotation={self.rotation})")
